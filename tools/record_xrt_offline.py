#!/usr/bin/env python3
"""Offline XRoboToolkit recorder for timestamp/pose stall diagnosis.

This tool does not connect to SONIC and does not publish robot commands. It
records the PC-visible XRoboToolkit SDK state so we can answer the key question:

    When XRoboToolkit timestamps stall, are the actual pose values still moving?

Outputs:
  - samples.csv: per-poll scalar diagnostics
  - samples.npz: numeric arrays, including pose snapshots if enabled
  - summary.txt: compact post-run statistics
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

import numpy as np

try:
    import xrobotoolkit_sdk as xrt
except ImportError as exc:  # pragma: no cover - environment-specific
    raise SystemExit("Failed to import xrobotoolkit_sdk. Run inside .venv_teleop.") from exc


def _safe_call(name: str, default=None):
    if not hasattr(xrt, name):
        return default
    try:
        return getattr(xrt, name)()
    except Exception:
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = float("nan")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pose_array(value) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float64)
    out = np.full((24, 7), np.nan, dtype=np.float64)
    if arr.ndim == 2 and arr.shape[1] >= 7:
        n = min(24, arr.shape[0])
        out[:n, :] = arr[:n, :7]
    return out


def _quat_norm_xyzw(q: np.ndarray) -> np.ndarray:
    return np.linalg.norm(q, axis=-1)


def _pose_delta(prev: np.ndarray | None, curr: np.ndarray) -> tuple[float, float, int]:
    if prev is None:
        return float("nan"), float("nan"), 0
    valid = np.isfinite(prev).all(axis=1) & np.isfinite(curr).all(axis=1)
    if not np.any(valid):
        return float("nan"), float("nan"), 0

    pos_delta = np.linalg.norm(curr[valid, :3] - prev[valid, :3], axis=1)

    prev_q = prev[valid, 3:7]
    curr_q = curr[valid, 3:7]
    prev_norm = _quat_norm_xyzw(prev_q)
    curr_norm = _quat_norm_xyzw(curr_q)
    q_valid = (prev_norm > 1e-6) & (curr_norm > 1e-6)
    quat_angle = np.array([float("nan")], dtype=np.float64)
    if np.any(q_valid):
        prev_qn = prev_q[q_valid] / prev_norm[q_valid, None]
        curr_qn = curr_q[q_valid] / curr_norm[q_valid, None]
        dots = np.abs(np.sum(prev_qn * curr_qn, axis=1))
        dots = np.clip(dots, 0.0, 1.0)
        quat_angle = 2.0 * np.arccos(dots)

    return float(np.nanmax(pos_delta)), float(np.nanmax(quat_angle)), int(np.sum(valid))


def _summary(values: list[float]) -> str:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return "n=0"
    p50 = clean[int(0.50 * (len(clean) - 1))]
    p95 = clean[int(0.95 * (len(clean) - 1))]
    p99 = clean[int(0.99 * (len(clean) - 1))]
    return (
        f"n={len(clean)} mean={statistics.fmean(clean):.3f} "
        f"p50={p50:.3f} p95={p95:.3f} p99={p99:.3f} max={clean[-1]:.3f}"
    )


def _write_summary(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    path.write_text(text)
    print(text, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--poll-sleep-ms", type=float, default=1.0)
    parser.add_argument("--gap-threshold-ms", type=float, default=50.0)
    parser.add_argument("--pose-pos-threshold-m", type=float, default=0.002)
    parser.add_argument("--pose-angle-threshold-rad", type=float, default=0.01)
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--start-service",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start /opt/apps/roboticsservice/runService.sh before xrt.init()",
    )
    parser.add_argument(
        "--save-poses",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Save full 24x7 body pose snapshots to samples.npz",
    )
    parser.add_argument(
        "--print-anomalies",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print timestamp stalls/gaps while recording",
    )
    args = parser.parse_args()

    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser()
    else:
        out_dir = Path("logs/xrt_offline") / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.start_service:
        subprocess.Popen(["bash", "/opt/apps/roboticsservice/runService.sh"])

    xrt.init()
    print(f"Recording XRoboToolkit offline data to: {out_dir}")

    rows: list[dict[str, float | int | str]] = []
    poses: list[np.ndarray] = []
    serial_snapshots: list[dict[str, object]] = []

    sleep_s = max(0.0, args.poll_sleep_ms * 1e-3)
    end_time = time.monotonic() + args.duration
    prev_pc_ns: int | None = None
    prev_effective_stamp_ns: int | None = None
    prev_pose: np.ndarray | None = None
    stall_start_pc_ns: int | None = None
    stall_warned = False

    effective_dt_ms_values: list[float] = []
    pc_dt_ms_values: list[float] = []
    same_stamp_pose_pos_values: list[float] = []
    same_stamp_pose_angle_values: list[float] = []
    changed_stamp_pose_pos_values: list[float] = []
    changed_stamp_pose_angle_values: list[float] = []

    body_available_count = 0
    effective_stamp_update_count = 0
    same_stamp_poll_count = 0
    same_stamp_pose_changed_count = 0
    body_stamp_zero_count = 0
    motion_num_zero_count = 0

    try:
        while time.monotonic() < end_time:
            poll_start = time.perf_counter()
            pc_mono_ns = time.monotonic_ns()
            pc_wall_ns = time.time_ns()

            body_available = bool(_safe_call("is_body_data_available", False))
            generic_stamp_ns = _as_int(_safe_call("get_time_stamp_ns", 0))
            body_stamp_ns = _as_int(_safe_call("get_body_timestamp_ns", 0))
            effective_stamp_ns = body_stamp_ns or generic_stamp_ns
            motion_num = _as_int(_safe_call("num_motion_data_available", 0))
            motion_stamp_ns = _as_int(_safe_call("get_motion_timestamp_ns", 0)) if motion_num > 0 else 0
            joint_ts = list(_safe_call("get_body_joints_timestamp", []) or [])
            joint_ts_int = [_as_int(v) for v in joint_ts]
            joint_zero = sum(1 for v in joint_ts_int if v == 0)
            joint_nonzero = sum(1 for v in joint_ts_int if v > 0)

            pose_read_start = time.perf_counter()
            pose = _pose_array(_safe_call("get_body_joints_pose", []))
            pose_read_ms = (time.perf_counter() - pose_read_start) * 1000.0

            pos_delta_m, quat_delta_rad, valid_joint_count = _pose_delta(prev_pose, pose)
            pc_dt_ms = (
                (pc_mono_ns - prev_pc_ns) * 1e-6 if prev_pc_ns is not None else float("nan")
            )
            effective_dt_ms = (
                (effective_stamp_ns - prev_effective_stamp_ns) * 1e-6
                if prev_effective_stamp_ns is not None
                and effective_stamp_ns
                and prev_effective_stamp_ns
                and effective_stamp_ns != prev_effective_stamp_ns
                else float("nan")
            )
            stamp_changed = (
                bool(effective_stamp_ns)
                and prev_effective_stamp_ns is not None
                and effective_stamp_ns != prev_effective_stamp_ns
            )
            stamp_same = (
                bool(effective_stamp_ns)
                and prev_effective_stamp_ns is not None
                and effective_stamp_ns == prev_effective_stamp_ns
            )

            if body_available:
                body_available_count += 1
            if body_stamp_ns == 0:
                body_stamp_zero_count += 1
            if motion_num == 0:
                motion_num_zero_count += 1
            if math.isfinite(pc_dt_ms):
                pc_dt_ms_values.append(pc_dt_ms)
            if stamp_changed:
                effective_stamp_update_count += 1
                effective_dt_ms_values.append(effective_dt_ms)
                changed_stamp_pose_pos_values.append(pos_delta_m)
                changed_stamp_pose_angle_values.append(quat_delta_rad)
                if (
                    args.print_anomalies
                    and math.isfinite(effective_dt_ms)
                    and effective_dt_ms >= args.gap_threshold_ms
                ):
                    print(
                        f"[XRT OFFLINE DIAG] timestamp_gap dt={effective_dt_ms:.2f}ms "
                        f"pc_dt={pc_dt_ms:.2f}ms pos_delta={pos_delta_m:.5f}m "
                        f"quat_delta={quat_delta_rad:.5f}rad"
                    )
                if stall_warned:
                    stall_ms = (
                        (pc_mono_ns - stall_start_pc_ns) * 1e-6
                        if stall_start_pc_ns is not None
                        else float("nan")
                    )
                    print(f"[XRT OFFLINE DIAG] timestamp_recovered stall={stall_ms:.1f}ms")
                stall_start_pc_ns = None
                stall_warned = False
            elif stamp_same:
                same_stamp_poll_count += 1
                same_stamp_pose_pos_values.append(pos_delta_m)
                same_stamp_pose_angle_values.append(quat_delta_rad)
                pose_changed = (
                    math.isfinite(pos_delta_m)
                    and math.isfinite(quat_delta_rad)
                    and (
                        pos_delta_m >= args.pose_pos_threshold_m
                        or quat_delta_rad >= args.pose_angle_threshold_rad
                    )
                )
                if pose_changed:
                    same_stamp_pose_changed_count += 1
                if stall_start_pc_ns is None:
                    stall_start_pc_ns = pc_mono_ns
                stall_ms = (pc_mono_ns - stall_start_pc_ns) * 1e-6
                if args.print_anomalies and stall_ms >= args.gap_threshold_ms and not stall_warned:
                    stall_warned = True
                    print(
                        f"[XRT OFFLINE DIAG] same_timestamp_stall stall={stall_ms:.1f}ms "
                        f"pos_delta={pos_delta_m:.5f}m quat_delta={quat_delta_rad:.5f}rad "
                        f"pose_changed={int(pose_changed)} stamp={effective_stamp_ns}"
                    )

            left_trigger = _as_float(_safe_call("get_left_trigger", float("nan")))
            right_trigger = _as_float(_safe_call("get_right_trigger", float("nan")))
            left_grip = _as_float(_safe_call("get_left_grip", float("nan")))
            right_grip = _as_float(_safe_call("get_right_grip", float("nan")))
            a = int(bool(_safe_call("get_A_button", False)))
            b = int(bool(_safe_call("get_B_button", False)))
            x = int(bool(_safe_call("get_X_button", False)))
            y = int(bool(_safe_call("get_Y_button", False)))
            head_pose = _safe_call("get_headset_pose", [])
            head_qnorm = float("nan")
            try:
                vals = list(head_pose)
                if len(vals) >= 7:
                    head_qnorm = math.sqrt(sum(float(v) * float(v) for v in vals[3:7]))
            except Exception:
                pass

            row = {
                "pc_mono_ns": pc_mono_ns,
                "pc_wall_ns": pc_wall_ns,
                "pc_dt_ms": pc_dt_ms,
                "body_available": int(body_available),
                "generic_stamp_ns": generic_stamp_ns,
                "body_stamp_ns": body_stamp_ns,
                "effective_stamp_ns": effective_stamp_ns,
                "effective_dt_ms": effective_dt_ms,
                "stamp_changed": int(stamp_changed),
                "stamp_same": int(stamp_same),
                "motion_num": motion_num,
                "motion_stamp_ns": motion_stamp_ns,
                "joint_zero": joint_zero,
                "joint_nonzero": joint_nonzero,
                "pose_read_ms": pose_read_ms,
                "pose_pos_delta_m": pos_delta_m,
                "pose_quat_delta_rad": quat_delta_rad,
                "valid_pose_joints": valid_joint_count,
                "left_trigger": left_trigger,
                "right_trigger": right_trigger,
                "left_grip": left_grip,
                "right_grip": right_grip,
                "A": a,
                "B": b,
                "X": x,
                "Y": y,
                "head_qnorm": head_qnorm,
                "poll_total_ms": (time.perf_counter() - poll_start) * 1000.0,
            }
            rows.append(row)
            if args.save_poses:
                poses.append(pose.astype(np.float32))

            if len(rows) == 1 or len(rows) % max(1, int(1.0 / max(sleep_s, 1e-6))) == 0:
                serial_snapshots.append(
                    {
                        "row": len(rows) - 1,
                        "pc_mono_ns": pc_mono_ns,
                        "serials": list(_safe_call("get_motion_tracker_serial_numbers", []) or []),
                    }
                )

            prev_pc_ns = pc_mono_ns
            if effective_stamp_ns:
                prev_effective_stamp_ns = effective_stamp_ns
            prev_pose = pose
            if sleep_s > 0:
                time.sleep(sleep_s)
    except KeyboardInterrupt:
        print("\nInterrupted by user; saving partial recording.")

    csv_path = out_dir / "samples.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)

    npz_payload = {
        key: np.asarray([row[key] for row in rows])
        for key in rows[0].keys()
    } if rows else {}
    if args.save_poses:
        npz_payload["body_poses"] = np.stack(poses, axis=0) if poses else np.zeros((0, 24, 7))
    np.savez_compressed(out_dir / "samples.npz", **npz_payload)
    (out_dir / "serial_snapshots.json").write_text(json.dumps(serial_snapshots, indent=2))

    total = len(rows)
    summary_lines = [
        "=== XRT offline recording summary ===",
        f"output_dir: {out_dir}",
        f"polls: {total}",
        f"body_available: {body_available_count}/{total}",
        f"body_stamp_zero: {body_stamp_zero_count}/{total}",
        f"motion_num_zero: {motion_num_zero_count}/{total}",
        f"timestamp_updates: {effective_stamp_update_count}",
        f"same_timestamp_polls: {same_stamp_poll_count}",
        f"same_timestamp_pose_changed: {same_stamp_pose_changed_count}",
        f"pc_dt_ms: {_summary(pc_dt_ms_values)}",
        f"effective_dt_ms_on_update: {_summary(effective_dt_ms_values)}",
        f"pose_pos_delta_m_when_same_stamp: {_summary(same_stamp_pose_pos_values)}",
        f"pose_quat_delta_rad_when_same_stamp: {_summary(same_stamp_pose_angle_values)}",
        f"pose_pos_delta_m_when_stamp_update: {_summary(changed_stamp_pose_pos_values)}",
        f"pose_quat_delta_rad_when_stamp_update: {_summary(changed_stamp_pose_angle_values)}",
    ]
    _write_summary(out_dir / "summary.txt", summary_lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
