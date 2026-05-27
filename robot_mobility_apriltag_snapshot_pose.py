#!/usr/bin/env python3
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from pupil_apriltags import Detector


# ---------------------------------------------------------------------
# Starter camera calibration for Logitech C270 at 640x480
# Replace later with calibrated values when available.
# ---------------------------------------------------------------------
FX = 554.0
FY = 554.0
CX = 640.0
CY = 360.0
TAG_SIZE_M = 0.10   # 10 cm x 10 cm


def rotation_matrix_to_yaw_deg(R: np.ndarray) -> float:
    """
    Extract yaw angle in degrees from rotation matrix.
    We only care about the horizontal-plane orientation.
    """
    # Standard yaw extraction from rotation matrix
    yaw_rad = math.atan2(R[1, 0], R[0, 0])
    return math.degrees(yaw_rad)


def analyze_snapshot(
    image_path: str,
    fx: float = FX,
    fy: float = FY,
    cx: float = CX,
    cy: float = CY,
    tag_size_m: float = TAG_SIZE_M,
) -> dict:
    p = Path(image_path)
    if not p.exists():
        return {
            "ok": False,
            "error": f"image_not_found: {image_path}",
            "count": 0,
            "tags": [],
        }

    img = cv2.imread(str(p))
    if img is None:
        return {
            "ok": False,
            "error": f"image_load_failed: {image_path}",
            "count": 0,
            "tags": [],
        }

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    detector = Detector(
        families="tag36h11",
        nthreads=1,
        quad_decimate=2.0,
        quad_sigma=0.0,
        refine_edges=1,
        decode_sharpening=0.25,
    )

    results = detector.detect(
        gray,
        estimate_tag_pose=True,
        camera_params=(fx, fy, cx, cy),
        tag_size=tag_size_m,
    )

    tags = []
    for r in results:
        # pose_t is tag position relative to camera, usually shape (3,1)
        t = np.asarray(r.pose_t).reshape(-1)
        x, y, z = float(t[0]), float(t[1]), float(t[2])

        # Option A: full Euclidean distance
        distance_m = math.sqrt(x * x + y * y + z * z)

        # horizontal viewing angle of camera toward tag
        angle_deg = math.degrees(math.atan2(x, z))

        # tag orientation relative to camera view
        R = np.asarray(r.pose_R)
        yaw_deg = rotation_matrix_to_yaw_deg(R)

        tags.append({
            "id": int(r.tag_id),
            "distance_m": round(distance_m, 4),
            "angle_deg": round(angle_deg, 4),
            "yaw_deg": round(yaw_deg, 4),
        })

    return {
        "ok": True,
        "error": "",
        "image_path": str(p),
        "count": len(tags),
        "tags": tags,
        "camera_params": {
            "fx": fx,
            "fy": fy,
            "cx": cx,
            "cy": cy,
            "tag_size_m": tag_size_m,
        },
    }


def main():
    if len(sys.argv) < 2:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "usage: apriltag_snapshot_pose.py IMAGE_PATH",
                    "count": 0,
                    "tags": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2)

    image_path = sys.argv[1]
    out = analyze_snapshot(image_path=image_path)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
    