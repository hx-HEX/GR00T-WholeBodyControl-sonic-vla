#!/usr/bin/env python3
"""Publish RealSense color frames using the GR00T camera ZMQ protocol.

This is a compatibility server for Unitree G1/origin images where the robot's
system Python 3.8 has a working ``pyrealsense2`` install, while the uv-managed
Python 3.10 camera venv cannot import the current pyrealsense2 wheel because of
the robot OS GLIBC version.

Run on the robot with system python3, not inside .venv_camera:

    python3 tools/realsense_color_zmq_server_py38.py --port 5555
"""

from __future__ import annotations

import argparse
import base64
import signal
import sys
import time

import cv2
import msgpack
import numpy as np
import pyrealsense2 as rs
import zmq


def encode_rgb_jpeg(image_rgb: np.ndarray, quality: int) -> str:
    """Match gear_sonic ImageUtils wire format: base64-encoded JPEG string.

    The official USB/RealSense paths hand RGB arrays to ImageUtils.encode_image.
    We do the same so ComposedCameraClientSensor/run_camera_viewer see the image
    as RGB after decode.
    """
    ok, buf = cv2.imencode(".jpg", image_rgb, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return base64.b64encode(buf).decode("utf-8")


def _timestamp_domain_name(domain) -> str:
    try:
        return str(domain).split(".")[-1]
    except Exception:
        return str(domain)


def _frame_timestamp_sec(frame) -> float:
    """Return RealSense frame timestamp in seconds."""
    return float(frame.get_timestamp()) / 1000.0


class RealSenseTimestampMapper:
    """Map RealSense frame timestamps onto robot-system time.

    RealSense frames carry a timestamp, but its clock domain can be hardware,
    system, or global time depending on device/driver configuration. For
    SYSTEM_TIME/GLOBAL_TIME we can use it directly as Unix seconds. For
    HARDWARE_CLOCK we estimate a fixed offset from frame timestamp to
    robot-local time using the first frames.
    """

    def __init__(self, timestamp_offset_sec: float, warmup_samples: int = 20):
        self.timestamp_offset_sec = float(timestamp_offset_sec)
        self.warmup_samples = int(max(1, warmup_samples))
        self.domain = None
        self.hardware_to_robot_offset = None
        self._offset_samples: list[float] = []
        self._printed = False

    def _is_epoch_like(self, frame_ts_sec: float) -> bool:
        # Unix epoch seconds in 2020+ are ~1.6e9. Hardware timestamps are
        # usually seconds since device boot and much smaller.
        return frame_ts_sec > 1_000_000_000.0

    def capture_time_pc(self, frame, read_time_robot: float) -> float:
        frame_ts_sec = _frame_timestamp_sec(frame)
        domain = frame.get_frame_timestamp_domain()
        self.domain = domain

        if domain in (rs.timestamp_domain.system_time, rs.timestamp_domain.global_time):
            if self._is_epoch_like(frame_ts_sec):
                capture_robot = frame_ts_sec
            else:
                # Defensive fallback: treat unexpected non-epoch system/global
                # timestamps as hardware-clock style timestamps.
                capture_robot = self._capture_time_from_hardware(frame_ts_sec, read_time_robot)
        else:
            capture_robot = self._capture_time_from_hardware(frame_ts_sec, read_time_robot)

        if not self._printed:
            print(
                "RealSense timestamp mapper: "
                f"domain={_timestamp_domain_name(domain)}, "
                f"frame_ts={frame_ts_sec:.6f}, "
                f"hw_to_robot_offset={self.hardware_to_robot_offset}, "
                f"pc_offset={self.timestamp_offset_sec:+.6f}s"
            )
            self._printed = True

        return capture_robot + self.timestamp_offset_sec

    def _capture_time_from_hardware(self, frame_ts_sec: float, read_time_robot: float) -> float:
        sample_offset = read_time_robot - frame_ts_sec
        if len(self._offset_samples) < self.warmup_samples:
            self._offset_samples.append(sample_offset)
            # Use the minimum offset: it corresponds to the lowest observed
            # read/queue latency, which is closest to the real clock mapping.
            self.hardware_to_robot_offset = min(self._offset_samples)
        elif self.hardware_to_robot_offset is None:
            self.hardware_to_robot_offset = sample_offset
        return frame_ts_sec + float(self.hardware_to_robot_offset)


def build_pipeline(width: int, height: int, fps: int) -> rs.pipeline:
    ctx = rs.context()
    devices = list(ctx.query_devices())
    if not devices:
        raise RuntimeError("No RealSense device found")

    print("Detected RealSense device(s):")
    for idx, dev in enumerate(devices):
        print(
            f"  [{idx}] {dev.get_info(rs.camera_info.name)} "
            f"serial={dev.get_info(rs.camera_info.serial_number)} "
            f"fw={dev.get_info(rs.camera_info.firmware_version)}"
        )

    devices = sorted(devices, key=lambda d: d.get_info(rs.camera_info.serial_number))
    serial = devices[0].get_info(rs.camera_info.serial_number)

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, width, height, rs.format.rgb8, fps)
    profile = pipeline.start(config)
    stream = profile.get_stream(rs.stream.color).as_video_stream_profile()
    intr = stream.get_intrinsics()
    print(
        f"Started color stream: serial={serial}, "
        f"{intr.width}x{intr.height}@{fps}, format=rgb8"
    )
    return pipeline


def wait_for_latest_color_frame(
    pipeline: rs.pipeline,
    timeout_ms: int,
) -> tuple[rs.video_frame | None, int]:
    """Return the latest currently available RealSense color frame.

    This mirrors the official OAK driver behaviour in
    ``gear_sonic/camera/drivers/oak.py``: consume all frames that are already
    queued and publish only the newest one.  This favours low-latency data
    collection over preserving every intermediate camera frame.
    """

    frames = pipeline.wait_for_frames(timeout_ms)
    latest_color = frames.get_color_frame()
    drained_frames = 0

    while True:
        queued_frames = pipeline.poll_for_frames()
        if not queued_frames:
            break

        color = queued_frames.get_color_frame()
        if color:
            latest_color = color
            drained_frames += 1

    return latest_color, drained_frames


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument("--topic-name", default="ego_view")
    parser.add_argument(
        "--disable-frame-timestamp",
        action="store_true",
        help=(
            "Publish read-time timestamps instead of RealSense frame timestamps. "
            "Use only for debugging."
        ),
    )
    parser.add_argument(
        "--timestamp-offset-sec",
        type=float,
        default=0.0,
        help=(
            "Offset added to robot-local time.time() before publishing image "
            "timestamps. Use PC_TIME - ROBOT_TIME when the data collector runs "
            "on the PC and the camera server runs on the robot."
        ),
    )
    args = parser.parse_args()

    stop = False

    def handle_signal(_sig, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    pipeline = build_pipeline(args.width, args.height, args.fps)
    timestamp_mapper = RealSenseTimestampMapper(args.timestamp_offset_sec)

    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUB)
    sock.setsockopt(zmq.SNDHWM, 20)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(f"tcp://*:{args.port}")
    print(f"Sensor server running at tcp://*:{args.port}")
    print(f"Publishing color stream as '{args.topic_name}'")
    print(f"Timestamp offset: {args.timestamp_offset_sec:+.6f}s")

    sent = 0
    drained_total = 0
    last_print = time.time()
    try:
        # Let SUB clients connect before first messages are dropped.
        time.sleep(0.5)
        while not stop:
            color, drained_frames = wait_for_latest_color_frame(pipeline, 5000)
            drained_total += drained_frames
            if not color:
                print("WARNING: no color frame")
                continue

            read_time_robot = time.time()
            image_rgb = np.asanyarray(color.get_data())
            if args.disable_frame_timestamp:
                capture_time_pc = read_time_robot + args.timestamp_offset_sec
            else:
                capture_time_pc = timestamp_mapper.capture_time_pc(color, read_time_robot)

            image_payload = encode_rgb_jpeg(image_rgb, args.jpeg_quality)
            publish_time_pc = time.time() + args.timestamp_offset_sec
            payload = {
                # Main timestamp is the corrected image capture time in the PC
                # timebase. Data collectors should treat this as the image time.
                "timestamps": {args.topic_name: capture_time_pc},
                "debug_timestamps": {
                    args.topic_name: {
                        "capture_time": capture_time_pc,
                        "read_time": read_time_robot + args.timestamp_offset_sec,
                        "publish_time": publish_time_pc,
                        "timestamp_domain": _timestamp_domain_name(
                            color.get_frame_timestamp_domain()
                        ),
                        "frame_timestamp_sec": _frame_timestamp_sec(color),
                        "drained_frames": drained_frames,
                    }
                },
                "images": {args.topic_name: image_payload},
            }
            sock.send(msgpack.packb(payload, use_bin_type=True), flags=zmq.NOBLOCK)
            sent += 1

            if sent % 30 == 0:
                t = time.time()
                dt = t - last_print
                print(
                    f"Published {sent} frames, recent fps={30 / dt:.2f}, "
                    f"drained_total={drained_total}"
                )
                last_print = t
    finally:
        print("Stopping RealSense color server...")
        pipeline.stop()
        sock.close()
        ctx.term()
    return 0


if __name__ == "__main__":
    sys.exit(main())
