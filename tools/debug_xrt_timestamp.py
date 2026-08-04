#!/usr/bin/env python3
"""Measure raw XRoboToolkit body-data timestamp stability.

This intentionally bypasses SONIC Manager, PoseLoop, ZMQ, robot deploy, camera,
and VLA.  It only starts XRoboToolkit PC Service, polls the Python SDK, and
prints timestamp gap statistics for the body data stream that SONIC actually
consumes.
"""

from __future__ import annotations

import argparse
from collections import Counter
import math
import subprocess
import time

import numpy as np

try:
    import xrobotoolkit_sdk as xrt
except ImportError as exc:  # pragma: no cover - environment-specific
    raise SystemExit(
        "Failed to import xrobotoolkit_sdk. Run inside .venv_teleop."
    ) from exc


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=60.0, help="Sampling duration in seconds")
    parser.add_argument(
        "--poll-sleep-ms",
        type=float,
        default=0.5,
        help="Sleep between SDK polls in milliseconds (default: 0.5)",
    )
    parser.add_argument(
        "--gap-threshold-ms",
        type=float,
        default=50.0,
        help="Print individual gaps above this timestamp delta (default: 50ms)",
    )
    parser.add_argument(
        "--no-data-threshold-ms",
        type=float,
        default=30.0,
        help="Print no-body-data stalls above this duration (default: 30ms)",
    )
    parser.add_argument(
        "--same-timestamp-threshold-ms",
        type=float,
        default=50.0,
        help="Print repeated timestamp stalls above this duration (default: 50ms)",
    )
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

    print("Waiting for XRoboToolkit body data...")
    while not xrt.is_body_data_available():
        time.sleep(0.01)

    print(
        f"Sampling raw XRT timestamps for {args.duration:.1f}s "
        f"(gap>{args.gap_threshold_ms:.1f}ms will be printed)"
    )

    end_time = time.monotonic() + args.duration
    sleep_s = max(0.0, args.poll_sleep_ms * 1e-3)
    no_data_threshold_s = args.no_data_threshold_ms * 1e-3

    last_stamp_ns: int | None = None
    last_sample_mono: float | None = None
    no_data_since: float | None = None
    same_stamp_since: float | None = None
    same_stamp_last_log: float = 0.0
    same_stamp_events_ms: list[float] = []

    device_dts_ms: list[float] = []
    pc_dts_ms: list[float] = []
    read_times_ms: list[float] = []
    gap_events: list[tuple[float, float]] = []
    no_data_events_ms: list[float] = []
    same_timestamp_polls = 0
    total_polls = 0
    available_polls = 0
    body_pose_reads = 0

    try:
        while time.monotonic() < end_time:
            total_polls += 1
            now_mono = time.monotonic()

            if not xrt.is_body_data_available():
                if no_data_since is None:
                    no_data_since = now_mono
                elif now_mono - no_data_since >= no_data_threshold_s:
                    duration_ms = (now_mono - no_data_since) * 1000.0
                    no_data_events_ms.append(duration_ms)
                    print(f"[XRT DIAG] no_body_data duration={duration_ms:.1f}ms")
                    no_data_since = now_mono
                time.sleep(sleep_s)
                continue

            available_polls += 1
            if no_data_since is not None:
                no_data_ms = (now_mono - no_data_since) * 1000.0
                if no_data_ms >= args.no_data_threshold_ms:
                    print(f"[XRT DIAG] body_data_recovered after {no_data_ms:.1f}ms")
                no_data_since = None

            stamp_ns = int(xrt.get_time_stamp_ns())
            if last_stamp_ns is not None and stamp_ns == last_stamp_ns:
                same_timestamp_polls += 1
                if same_stamp_since is None:
                    same_stamp_since = now_mono
                same_ms = (now_mono - same_stamp_since) * 1000.0
                if (
                    same_ms >= args.same_timestamp_threshold_ms
                    and now_mono - same_stamp_last_log >= 1.0
                ):
                    same_stamp_last_log = now_mono
                    same_stamp_events_ms.append(same_ms)
                    print(
                        f"[XRT DIAG] same_timestamp_stall "
                        f"duration={same_ms:.1f}ms stamp_ns={stamp_ns}"
                    )
                time.sleep(sleep_s)
                continue

            if same_stamp_since is not None:
                same_ms = (now_mono - same_stamp_since) * 1000.0
                if same_ms >= args.same_timestamp_threshold_ms:
                    print(
                        f"[XRT DIAG] timestamp_recovered after same_timestamp_stall "
                        f"duration={same_ms:.1f}ms"
                    )
                same_stamp_since = None

            if last_stamp_ns is not None:
                device_dt_ms = (stamp_ns - last_stamp_ns) * 1e-6
                pc_dt_ms = (
                    (now_mono - last_sample_mono) * 1000.0
                    if last_sample_mono is not None
                    else float("nan")
                )
                device_dts_ms.append(device_dt_ms)
                pc_dts_ms.append(pc_dt_ms)
                if device_dt_ms >= args.gap_threshold_ms:
                    gap_events.append((device_dt_ms, pc_dt_ms))
                    print(
                        f"[XRT DIAG] timestamp_gap "
                        f"device_dt={device_dt_ms:.2f}ms pc_dt={pc_dt_ms:.2f}ms"
                    )

            read_start = time.perf_counter()
            _ = xrt.get_body_joints_pose()
            read_ms = (time.perf_counter() - read_start) * 1000.0
            read_times_ms.append(read_ms)
            body_pose_reads += 1

            last_stamp_ns = stamp_ns
            last_sample_mono = now_mono
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        print("\nInterrupted by user; printing partial summary.")

    rounded_gaps = Counter(round(dt / 11.111) for dt, _pc_dt in gap_events if math.isfinite(dt))

    print("\n=== XRT timestamp summary ===")
    print(f"total_polls: {total_polls}")
    print(f"available_polls: {available_polls}")
    print(f"body_pose_reads: {body_pose_reads}")
    print(f"same_timestamp_polls: {same_timestamp_polls}")
    print(f"no_data_events: {len(no_data_events_ms)}")
    print(f"timestamp_gap_events > {args.gap_threshold_ms:.1f}ms: {len(gap_events)}")
    print(f"dt_ms mean: {np.mean(device_dts_ms) if device_dts_ms else float('nan'):.2f}")
    print(f"dt_ms p50: {_percentile(device_dts_ms, 50):.2f}")
    print(f"dt_ms p90: {_percentile(device_dts_ms, 90):.2f}")
    print(f"dt_ms p95: {_percentile(device_dts_ms, 95):.2f}")
    print(f"dt_ms p99: {_percentile(device_dts_ms, 99):.2f}")
    print(f"dt_ms max: {max(device_dts_ms) if device_dts_ms else float('nan'):.2f}")
    print(f"pc_dt_ms p95: {_percentile(pc_dts_ms, 95):.2f}")
    print(f"read_ms p95: {_percentile(read_times_ms, 95):.3f}")
    print(f"read_ms max: {max(read_times_ms) if read_times_ms else float('nan'):.3f}")
    if rounded_gaps:
        top = ", ".join(f"~{k} frames:{v}" for k, v in rounded_gaps.most_common(10))
        print(f"gap multiples of 11.1ms: {top}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
