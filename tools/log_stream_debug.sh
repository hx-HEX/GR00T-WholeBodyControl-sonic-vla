#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  tools/log_stream_debug.sh <name> -- <command> [args...]

Examples:
  tools/log_stream_debug.sh pico -- python gear_sonic/scripts/pico_manager_thread_server.py --manager
  tools/log_stream_debug.sh sonic -- ./deploy.sh --cp policy/low_latency/model --obs-config policy/low_latency/observation_config.yaml --input-type zmq_manager real

Logs are written to:
  logs/stream_debug/<session_timestamp>/<name>.log

The latest session directory is symlinked at:
  logs/stream_debug/latest
EOF
}

if [[ $# -lt 3 || "${2:-}" != "--" ]]; then
  usage >&2
  exit 2
fi

name="$1"
shift 2

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
session_root="${STREAM_DEBUG_LOG_DIR:-}"

if [[ -z "${session_root}" ]]; then
  timestamp="$(date +%Y%m%d_%H%M%S)"
  session_root="${repo_root}/logs/stream_debug/${timestamp}"
fi

mkdir -p "${session_root}"
ln -sfn "${session_root}" "${repo_root}/logs/stream_debug/latest"

log_file="${session_root}/${name}.log"
cmd_file="${session_root}/${name}.cmd.txt"

{
  printf 'cwd=%s\n' "$(pwd)"
  printf 'time=%s\n' "$(date --iso-8601=seconds)"
  printf 'command='
  printf '%q ' "$@"
  printf '\n'
} > "${cmd_file}"

echo "[log_stream_debug] session: ${session_root}"
echo "[log_stream_debug] log: ${log_file}"
echo "[log_stream_debug] command saved: ${cmd_file}"

set +e
if command -v script >/dev/null 2>&1; then
  cmd_string=""
  for arg in "$@"; do
    printf -v quoted '%q' "$arg"
    cmd_string+="${quoted} "
  done
  # Run under a pseudo-terminal so interactive launchers such as deploy.sh keep
  # their normal prompts/output behavior while still writing a complete log.
  PYTHONUNBUFFERED=1 script -q -f -e -c "${cmd_string}" "${log_file}"
  status=$?
else
  PYTHONUNBUFFERED=1 stdbuf -oL -eL "$@" 2>&1 \
    | tee -a "${log_file}"
  status=${PIPESTATUS[0]}
fi
set -e

echo "[log_stream_debug] command exited with status ${status}" | tee -a "${log_file}"
exit "${status}"
