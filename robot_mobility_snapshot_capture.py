#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path

import cv2


DEFAULT_CAMERA_INDEX = 0
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_WARMUP_SEC = 1.0


def capture_snapshot(
    output_path: str,
    camera_index: int = DEFAULT_CAMERA_INDEX,
    video_dev: str = "",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    warmup_sec: float = DEFAULT_WARMUP_SEC,
) -> dict:
    out = Path(output_path)

    cap_src = video_dev if video_dev else camera_index
    cap = cv2.VideoCapture(cap_src)
    if not cap.isOpened():
        return {
            "ok": False,
            "error": f"cannot_open_camera_{cap_src}",
            "output_path": str(out),
        }

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # Let webcam settle a bit
        time.sleep(warmup_sec)

        # Read a few frames and keep the latest one
        frame = None
        for _ in range(5):
            ret, img = cap.read()
            if ret and img is not None:
                frame = img
            time.sleep(0.05)

        if frame is None:
            return {
                "ok": False,
                "error": "frame_read_failed",
                "output_path": str(out),
            }

        out.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(out), frame)
        if not ok:
            return {
                "ok": False,
                "error": "image_write_failed",
                "output_path": str(out),
            }

        return {
            "ok": True,
            "error": "",
            "output_path": str(out),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
        }

    finally:
        cap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("output_path", help="Path to save snapshot image")
    ap.add_argument("--camera-index", type=int, default=DEFAULT_CAMERA_INDEX)
    ap.add_argument("--video-dev", default="")    
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    ap.add_argument("--warmup-sec", type=float, default=DEFAULT_WARMUP_SEC)
    args = ap.parse_args()

    result = capture_snapshot(
        output_path=args.output_path,
        camera_index=args.camera_index,
        video_dev=args.video_dev,
        width=args.width,
        height=args.height,
        warmup_sec=args.warmup_sec,
    )

    import json
    ...
    if result["ok"]:
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0)
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
    