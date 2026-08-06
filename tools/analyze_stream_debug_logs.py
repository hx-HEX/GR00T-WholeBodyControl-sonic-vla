#!/usr/bin/env python3
"""Summarize SONIC/PICO streaming debug logs.

The parser is intentionally tolerant: it scans any *.log files in a log
directory and extracts the diagnostic lines we use while debugging XRoboToolkit
input stalls and SONIC ZMQ streaming delay.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
import statistics


FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"

PATTERNS = {
    "pico_timestamp_gap": re.compile(
        rf"\[PicoReader DIAG\] timestamp_gap device_dt=({FLOAT})ms pc_dt=({FLOAT})ms"
    ),
    "pico_pc_gap": re.compile(
        rf"\[PicoReader DIAG\] pc_gap_without_device_gap pc_dt=({FLOAT})ms device_dt=({FLOAT})ms"
    ),
    "pico_stall": re.compile(
        rf"\[PicoReader DIAG\] same_timestamp_stall duration=({FLOAT})ms"
    ),
    "pico_recovered": re.compile(
        rf"\[PicoReader DIAG\] timestamp_recovered after stall duration=({FLOAT})ms"
    ),
    "pico_periodic": re.compile(
        rf"\[PicoReader\] dt_ts: ({FLOAT}) ms, fps: ({FLOAT})(?:, pc_dt: ({FLOAT}) ms, read: ({FLOAT}) ms)?"
    ),
    "pose_drop": re.compile(
        rf"\[PoseLoop DROP\] fps=({FLOAT}) target=({FLOAT}).*skips=\{{([^}}]*)\}}"
    ),
    "pose_slow": re.compile(
        rf"\[PoseLoop SLOW\] total=({FLOAT})ms threshold=({FLOAT})ms"
    ),
    "streaming_diag": re.compile(
        rf"\[STREAMING DIAG\] input_update_age=({FLOAT})ms threshold=({FLOAT})ms mean=({FLOAT})ms std=({FLOAT})ms"
    ),
    "loop_timing": re.compile(
        rf"Streaming data mean delay: ({FLOAT})ms, Streaming data std delay: ({FLOAT})ms"
    ),
    "xrt_snapshot": re.compile(
        r"xrt_body_avail=(\d+) .*xrt_generic_stamp=(\d+) .*xrt_body_stamp=(\d+) .*xrt_motion_num=(\d+)"
    ),
    "xrt_joint": re.compile(
        rf"xrt_joint_zero=(\d+) xrt_joint_nonzero=(\d+)(?: xrt_joint_skew_nonzero_ms=({FLOAT}))?"
    ),
    "xrt_head_qnorm": re.compile(rf"xrt_head_qnorm=({FLOAT})"),
    "xrt_service": re.compile(r"service=([^\s]+)"),
    "zmq_rx_diag": re.compile(
        rf"\[ZMQ RX DIAG\] receive_interval=({FLOAT})ms .*receive_count=(\d+) overwrote_unconsumed=(\d+)"
    ),
}


@dataclass
class Metric:
    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)

    def summary(self) -> str:
        if not self.values:
            return "n=0"
        values = sorted(self.values)
        p50 = values[int(0.50 * (len(values) - 1))]
        p95 = values[int(0.95 * (len(values) - 1))]
        p99 = values[int(0.99 * (len(values) - 1))]
        return (
            f"n={len(values)} mean={statistics.fmean(values):.2f} "
            f"p50={p50:.2f} p95={p95:.2f} p99={p99:.2f} max={values[-1]:.2f}"
        )


def parse_log(path: Path) -> dict[str, Metric | int | dict[str, int]]:
    metrics: dict[str, Metric | int | dict[str, int]] = {
        "pico_device_gap_ms": Metric(),
        "pico_pc_gap_ms": Metric(),
        "pico_stall_ms": Metric(),
        "pico_recovered_ms": Metric(),
        "pico_dt_ms": Metric(),
        "pico_fps": Metric(),
        "pico_read_ms": Metric(),
        "pose_drop_fps": Metric(),
        "pose_slow_total_ms": Metric(),
        "streaming_age_ms": Metric(),
        "streaming_mean_delay_ms": Metric(),
        "streaming_std_delay_ms": Metric(),
        "loop_streaming_mean_delay_ms": Metric(),
        "loop_streaming_std_delay_ms": Metric(),
        "xrt_joint_zero": Metric(),
        "xrt_joint_nonzero": Metric(),
        "xrt_joint_skew_nonzero_ms": Metric(),
        "xrt_head_qnorm": Metric(),
        "zmq_rx_interval_ms": Metric(),
        "pose_skip_counts": {},
        "xrt_counts": {},
        "zmq_counts": {},
        "lines": 0,
    }

    skip_counts = metrics["pose_skip_counts"]
    assert isinstance(skip_counts, dict)
    xrt_counts = metrics["xrt_counts"]
    assert isinstance(xrt_counts, dict)
    zmq_counts = metrics["zmq_counts"]
    assert isinstance(zmq_counts, dict)

    with path.open("r", errors="replace") as f:
        for line in f:
            metrics["lines"] = int(metrics["lines"]) + 1

            if "[PicoReader DIAG]" in line and "xrt_body_avail=" in line:
                xrt_counts["snapshot_lines"] = xrt_counts.get("snapshot_lines", 0) + 1
                if m := PATTERNS["xrt_snapshot"].search(line):
                    body_avail = int(m.group(1))
                    generic_stamp = int(m.group(2))
                    body_stamp = int(m.group(3))
                    motion_num = int(m.group(4))
                    if body_avail == 0:
                        xrt_counts["body_unavailable"] = xrt_counts.get("body_unavailable", 0) + 1
                    if generic_stamp == 0:
                        xrt_counts["generic_stamp_zero"] = xrt_counts.get("generic_stamp_zero", 0) + 1
                    if body_stamp == 0:
                        xrt_counts["body_stamp_zero"] = xrt_counts.get("body_stamp_zero", 0) + 1
                    if motion_num == 0:
                        xrt_counts["motion_num_zero"] = xrt_counts.get("motion_num_zero", 0) + 1
                if m := PATTERNS["xrt_joint"].search(line):
                    cast(Metric, metrics["xrt_joint_zero"]).add(float(m.group(1)))
                    cast(Metric, metrics["xrt_joint_nonzero"]).add(float(m.group(2)))
                    if m.group(3) is not None:
                        cast(Metric, metrics["xrt_joint_skew_nonzero_ms"]).add(float(m.group(3)))
                if m := PATTERNS["xrt_head_qnorm"].search(line):
                    cast(Metric, metrics["xrt_head_qnorm"]).add(float(m.group(1)))
                if m := PATTERNS["xrt_service"].search(line):
                    service = m.group(1)
                    if service == "none":
                        xrt_counts["service_none"] = xrt_counts.get("service_none", 0) + 1
                    elif service.startswith("ERR:"):
                        xrt_counts["service_error"] = xrt_counts.get("service_error", 0) + 1
                    else:
                        xrt_counts["service_present"] = xrt_counts.get("service_present", 0) + 1

            if m := PATTERNS["pico_timestamp_gap"].search(line):
                cast(Metric, metrics["pico_device_gap_ms"]).add(float(m.group(1)))
                cast(Metric, metrics["pico_pc_gap_ms"]).add(float(m.group(2)))
                continue
            if m := PATTERNS["pico_pc_gap"].search(line):
                cast(Metric, metrics["pico_pc_gap_ms"]).add(float(m.group(1)))
                continue
            if m := PATTERNS["pico_stall"].search(line):
                cast(Metric, metrics["pico_stall_ms"]).add(float(m.group(1)))
                continue
            if m := PATTERNS["pico_recovered"].search(line):
                cast(Metric, metrics["pico_recovered_ms"]).add(float(m.group(1)))
                continue
            if m := PATTERNS["pico_periodic"].search(line):
                cast(Metric, metrics["pico_dt_ms"]).add(float(m.group(1)))
                cast(Metric, metrics["pico_fps"]).add(float(m.group(2)))
                if m.group(4) is not None:
                    cast(Metric, metrics["pico_read_ms"]).add(float(m.group(4)))
                continue
            if m := PATTERNS["pose_drop"].search(line):
                cast(Metric, metrics["pose_drop_fps"]).add(float(m.group(1)))
                for item in m.group(3).split(","):
                    if "=" not in item:
                        continue
                    key, value = item.strip().split("=", 1)
                    try:
                        skip_counts[key] = skip_counts.get(key, 0) + int(value)
                    except ValueError:
                        pass
                continue
            if m := PATTERNS["pose_slow"].search(line):
                cast(Metric, metrics["pose_slow_total_ms"]).add(float(m.group(1)))
                continue
            if m := PATTERNS["streaming_diag"].search(line):
                cast(Metric, metrics["streaming_age_ms"]).add(float(m.group(1)))
                cast(Metric, metrics["streaming_mean_delay_ms"]).add(float(m.group(3)))
                cast(Metric, metrics["streaming_std_delay_ms"]).add(float(m.group(4)))
                continue
            if m := PATTERNS["loop_timing"].search(line):
                cast(Metric, metrics["loop_streaming_mean_delay_ms"]).add(float(m.group(1)))
                cast(Metric, metrics["loop_streaming_std_delay_ms"]).add(float(m.group(2)))
                continue
            if m := PATTERNS["zmq_rx_diag"].search(line):
                cast(Metric, metrics["zmq_rx_interval_ms"]).add(float(m.group(1)))
                if int(m.group(3)) != 0:
                    zmq_counts["overwrote_unconsumed"] = zmq_counts.get("overwrote_unconsumed", 0) + 1
                else:
                    zmq_counts["receive_gap"] = zmq_counts.get("receive_gap", 0) + 1
                continue

    return metrics


def cast(_typ, value):
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "log_dir",
        nargs="?",
        default="logs/stream_debug/latest",
        help="Directory containing *.log files, default: logs/stream_debug/latest",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir).expanduser()
    if not log_dir.exists():
        raise SystemExit(f"Log directory does not exist: {log_dir}")

    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        raise SystemExit(f"No *.log files found in: {log_dir}")

    print(f"Analyzing log directory: {log_dir}")
    for path in log_files:
        print(f"\n=== {path.name} ===")
        metrics = parse_log(path)
        print(f"lines: {metrics['lines']}")

        for key in (
            "pico_device_gap_ms",
            "pico_pc_gap_ms",
            "pico_stall_ms",
            "pico_recovered_ms",
            "pico_dt_ms",
            "pico_fps",
            "pico_read_ms",
            "pose_drop_fps",
            "pose_slow_total_ms",
            "streaming_age_ms",
            "streaming_mean_delay_ms",
            "streaming_std_delay_ms",
            "loop_streaming_mean_delay_ms",
            "loop_streaming_std_delay_ms",
            "xrt_joint_zero",
            "xrt_joint_nonzero",
            "xrt_joint_skew_nonzero_ms",
            "xrt_head_qnorm",
            "zmq_rx_interval_ms",
        ):
            metric = metrics[key]
            assert isinstance(metric, Metric)
            if metric.values:
                print(f"{key}: {metric.summary()}")

        skip_counts = metrics["pose_skip_counts"]
        assert isinstance(skip_counts, dict)
        if skip_counts:
            top = ", ".join(
                f"{k}={v}" for k, v in sorted(skip_counts.items(), key=lambda kv: kv[1], reverse=True)
            )
            print(f"pose_skip_counts: {top}")

        xrt_counts = metrics["xrt_counts"]
        assert isinstance(xrt_counts, dict)
        if xrt_counts:
            top = ", ".join(
                f"{k}={v}" for k, v in sorted(xrt_counts.items(), key=lambda kv: kv[0])
            )
            print(f"xrt_counts: {top}")

        zmq_counts = metrics["zmq_counts"]
        assert isinstance(zmq_counts, dict)
        if zmq_counts:
            top = ", ".join(
                f"{k}={v}" for k, v in sorted(zmq_counts.items(), key=lambda kv: kv[0])
            )
            print(f"zmq_counts: {top}")

    print("\nInterpretation:")
    print("- pico_device_gap_ms/stall_ms high: XRoboToolkit/PICO source is not producing fresh timestamps.")
    print("- pose_drop_fps low with wait/stale skips: PoseLoop cannot synthesize target-rate frames from PICO samples.")
    print("- streaming_age_ms high: SONIC deploy did not receive/process fresh ZMQ input for >150ms.")
    print("- loop_streaming_* high without streaming_age_ms: rolling stats include earlier spikes; inspect timeline.")
    print("- xrt_counts from new logs distinguish SDK-visible body availability/stamps/service status during anomalies.")
    print("- zmq_rx_interval_ms high means SONIC is receiving pose packets late; overwrite counts mean callback outran the control consumer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
