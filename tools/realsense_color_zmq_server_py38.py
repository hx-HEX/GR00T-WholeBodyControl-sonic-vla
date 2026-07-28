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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument("--topic-name", default="ego_view")
    args = parser.parse_args()

    stop = False

    def handle_signal(_sig, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    pipeline = build_pipeline(args.width, args.height, args.fps)

    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUB)
    sock.setsockopt(zmq.SNDHWM, 20)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(f"tcp://*:{args.port}")
    print(f"Sensor server running at tcp://*:{args.port}")
    print(f"Publishing color stream as '{args.topic_name}'")

    sent = 0
    last_print = time.time()
    try:
        # Let SUB clients connect before first messages are dropped.
        time.sleep(0.5)
        while not stop:
            frames = pipeline.wait_for_frames(5000)
            color = frames.get_color_frame()
            if not color:
                print("WARNING: no color frame")
                continue

            image_rgb = np.asanyarray(color.get_data())
            now = time.time()
            payload = {
                "timestamps": {args.topic_name: now},
                "images": {args.topic_name: encode_rgb_jpeg(image_rgb, args.jpeg_quality)},
            }
            sock.send(msgpack.packb(payload, use_bin_type=True), flags=zmq.NOBLOCK)
            sent += 1

            if sent % 30 == 0:
                t = time.time()
                dt = t - last_print
                print(f"Published {sent} frames, recent fps={30 / dt:.2f}")
                last_print = t
    finally:
        print("Stopping RealSense color server...")
        pipeline.stop()
        sock.close()
        ctx.term()
    return 0


if __name__ == "__main__":
    sys.exit(main())
