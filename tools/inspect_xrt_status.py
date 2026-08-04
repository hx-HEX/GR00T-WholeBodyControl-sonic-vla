#!/usr/bin/env python3
"""Inspect XRoboToolkit data visible from the PC side.

This script does not connect to the robot and does not publish any SONIC ZMQ
commands.  It only starts/connects the XRoboToolkit PC service, reads the
Python SDK state, and prints a compact status line plus anomaly diagnostics.
"""

from __future__ import annotations

import argparse
import math
import subprocess
import time
from typing import Iterable

try:
    import xrobotoolkit_sdk as xrt
except ImportError as exc:  # pragma: no cover - environment-specific
    raise SystemExit(
        "Failed to import xrobotoolkit_sdk. Run inside .venv_teleop."
    ) from exc


JOINT_NAMES = [
    "Pelvis",
    "Left_Hip",
    "Right_Hip",
    "Spine1",
    "Left_Knee",
    "Right_Knee",
    "Spine2",
    "Left_Ankle",
    "Right_Ankle",
    "Spine3",
    "Left_Foot",
    "Right_Foot",
    "Neck",
    "Left_Collar",
    "Right_Collar",
    "Head",
    "Left_Shoulder",
    "Right_Shoulder",
    "Left_Elbow",
    "Right_Elbow",
    "Left_Wrist",
    "Right_Wrist",
    "Left_Hand",
    "Right_Hand",
]


def _finite_values(values: Iterable[float]) -> list[float]:
    return [float(v) for v in values if math.isfinite(float(v))]


def _norm_quat_xyzw(pose: Iterable[float]) -> float:
    vals = list(pose)
    if len(vals) < 7:
        return float("nan")
    return math.sqrt(vals[3] * vals[3] + vals[4] * vals[4] + vals[5] * vals[5] + vals[6] * vals[6])


def _safe_call(name: str, default=None):
    try:
        return getattr(xrt, name)()
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"[XRT STATUS] {name} failed: {exc}")
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--print-every", type=float, default=1.0)
    parser.add_argument("--poll-sleep-ms", type=float, default=1.0)
    parser.add_argument("--gap-threshold-ms", type=float, default=50.0)
    parser.add_argument("--joint-skew-threshold-ms", type=float, default=20.0)
    parser.add_argument("--bad-quat-threshold", type=float, default=0.2)
    parser.add_argument(
        "--start-service",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start /opt/apps/roboticsservice/runService.sh before xrt.init()",
    )
    args = parser.parse_args()

    if args.start_service:
        subprocess.Popen(["bash", "/opt/apps/roboticsservice/runService.sh"])

    xrt.init()
    print("Inspecting XRoboToolkit PC-side state. Ctrl+C to stop.")

    end_time = time.monotonic() + args.duration
    next_print = 0.0
    sleep_s = max(0.0, args.poll_sleep_ms * 1e-3)
    last_body_stamp_ns: int | None = None
    last_motion_stamp_ns: int | None = None
    last_pc_time: float | None = None
    last_body_dt_ms = float("nan")
    last_joint_skew_log = 0.0

    body_updates = 0
    body_gaps = 0
    motion_updates = 0
    motion_gaps = 0

    try:
        while time.monotonic() < end_time:
            now = time.monotonic()

            body_available = bool(_safe_call("is_body_data_available", False))
            body_stamp_ns = int(_safe_call("get_body_timestamp_ns", 0)) if body_available else 0
            generic_stamp_ns = int(_safe_call("get_time_stamp_ns", 0))
            effective_body_stamp_ns = body_stamp_ns or generic_stamp_ns
            timestamp_source = "body" if body_stamp_ns else "generic"

            body_dt_ms = float("nan")
            pc_dt_ms = float("nan")
            if (
                effective_body_stamp_ns
                and last_body_stamp_ns is not None
                and effective_body_stamp_ns != last_body_stamp_ns
            ):
                body_dt_ms = (effective_body_stamp_ns - last_body_stamp_ns) * 1e-6
                last_body_dt_ms = body_dt_ms
                pc_dt_ms = (now - last_pc_time) * 1000.0 if last_pc_time is not None else float("nan")
                body_updates += 1
                if body_dt_ms >= args.gap_threshold_ms:
                    body_gaps += 1
                    print(
                        f"[XRT STATUS DIAG] body_timestamp_gap "
                        f"body_dt={body_dt_ms:.2f}ms pc_dt={pc_dt_ms:.2f}ms"
                    )
                last_pc_time = now
            elif last_body_stamp_ns is None and effective_body_stamp_ns:
                last_pc_time = now

            if effective_body_stamp_ns and effective_body_stamp_ns != last_body_stamp_ns:
                last_body_stamp_ns = effective_body_stamp_ns

                poses = _safe_call("get_body_joints_pose", [])
                joint_ts = list(_safe_call("get_body_joints_timestamp", []))
                if joint_ts:
                    finite_ts = _finite_values(joint_ts)
                    if finite_ts:
                        skew_ms = (max(finite_ts) - min(finite_ts)) * 1e-6
                        if (
                            skew_ms >= args.joint_skew_threshold_ms
                            and now - last_joint_skew_log >= args.print_every
                        ):
                            last_joint_skew_log = now
                            min_idx = min(range(len(joint_ts)), key=lambda i: joint_ts[i])
                            max_idx = max(range(len(joint_ts)), key=lambda i: joint_ts[i])
                            min_name = JOINT_NAMES[min_idx] if min_idx < len(JOINT_NAMES) else str(min_idx)
                            max_name = JOINT_NAMES[max_idx] if max_idx < len(JOINT_NAMES) else str(max_idx)
                            print(
                                f"[XRT STATUS DIAG] body_joint_timestamp_skew={skew_ms:.2f}ms "
                                f"min={min_name}:{joint_ts[min_idx]} max={max_name}:{joint_ts[max_idx]}"
                            )

                bad_quats = []
                for idx, pose in enumerate(poses):
                    qn = _norm_quat_xyzw(pose)
                    if not math.isfinite(qn) or qn <= args.bad_quat_threshold:
                        name = JOINT_NAMES[idx] if idx < len(JOINT_NAMES) else str(idx)
                        bad_quats.append(f"{name}:{qn:.3f}")
                if bad_quats:
                    print("[XRT STATUS DIAG] bad_body_quat_norm " + ", ".join(bad_quats[:8]))

            motion_num = int(_safe_call("num_motion_data_available", 0))
            motion_stamp_ns = int(_safe_call("get_motion_timestamp_ns", 0)) if motion_num > 0 else 0
            if (
                motion_stamp_ns
                and last_motion_stamp_ns is not None
                and motion_stamp_ns != last_motion_stamp_ns
            ):
                motion_dt_ms = (motion_stamp_ns - last_motion_stamp_ns) * 1e-6
                motion_updates += 1
                if motion_dt_ms >= args.gap_threshold_ms:
                    motion_gaps += 1
                    print(f"[XRT STATUS DIAG] motion_timestamp_gap motion_dt={motion_dt_ms:.2f}ms")
            if motion_stamp_ns and motion_stamp_ns != last_motion_stamp_ns:
                last_motion_stamp_ns = motion_stamp_ns

            if now >= next_print:
                next_print = now + args.print_every
                serials = _safe_call("get_motion_tracker_serial_numbers", [])
                left_hand_active = _safe_call("get_left_hand_is_active", -1)
                right_hand_active = _safe_call("get_right_hand_is_active", -1)
                left_trigger = float(_safe_call("get_left_trigger", float("nan")))
                right_trigger = float(_safe_call("get_right_trigger", float("nan")))
                left_grip = float(_safe_call("get_left_grip", float("nan")))
                right_grip = float(_safe_call("get_right_grip", float("nan")))
                a = bool(_safe_call("get_A_button", False))
                b = bool(_safe_call("get_B_button", False))
                x = bool(_safe_call("get_X_button", False))
                y = bool(_safe_call("get_Y_button", False))
                headset_qn = _norm_quat_xyzw(_safe_call("get_headset_pose", []))

                print(
                    "[XRT STATUS] "
                    f"body={body_available} body_stamp={body_stamp_ns} generic_stamp={generic_stamp_ns} "
                    f"ts_source={timestamp_source} body_dt={last_body_dt_ms:.2f}ms "
                    f"body_updates={body_updates} body_gaps={body_gaps} "
                    f"motion_num={motion_num} motion_updates={motion_updates} motion_gaps={motion_gaps} "
                    f"serials={list(serials)} "
                    f"hand_active(L/R)={left_hand_active}/{right_hand_active} "
                    f"trigger(L/R)={left_trigger:.2f}/{right_trigger:.2f} "
                    f"grip(L/R)={left_grip:.2f}/{right_grip:.2f} "
                    f"ABXY={int(a)}{int(b)}{int(x)}{int(y)} "
                    f"head_qnorm={headset_qn:.3f}"
                )

            time.sleep(sleep_s)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    print(
        f"Summary: body_updates={body_updates}, body_gaps>{args.gap_threshold_ms:.1f}ms={body_gaps}, "
        f"motion_updates={motion_updates}, motion_gaps>{args.gap_threshold_ms:.1f}ms={motion_gaps}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
