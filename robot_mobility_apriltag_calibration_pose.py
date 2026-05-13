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
CX = 320.0
CY = 240.0
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
        # -------------------------------------------------------------
        # 1) Existing library pose estimate
        # -------------------------------------------------------------
        t = np.asarray(r.pose_t).reshape(-1)
        x, y, z = float(t[0]), float(t[1]), float(t[2])

        distance_m = math.sqrt(x * x + y * y + z * z)
        angle_deg = math.degrees(math.atan2(x, z))

        R = np.asarray(r.pose_R)
        yaw_deg = rotation_matrix_to_yaw_deg(R)

        # -------------------------------------------------------------
        # 2) Raw image geometry from AprilTag corners
        # -------------------------------------------------------------
        corners = np.asarray(r.corners, dtype=float)

        # pupil_apriltags usually returns corners in this order:
        # bottom-left, bottom-right, top-right, top-left
        c0, c1, c2, c3 = corners

        def px_dist(a, b):
            return float(np.linalg.norm(a - b))

        edge_bottom_px = px_dist(c0, c1)
        edge_right_px = px_dist(c1, c2)
        edge_top_px = px_dist(c2, c3)
        edge_left_px = px_dist(c3, c0)

        avg_width_px = (edge_top_px + edge_bottom_px) / 2.0
        avg_height_px = (edge_left_px + edge_right_px) / 2.0

        center = np.asarray(r.center, dtype=float)
        center_x = float(center[0])
        center_y = float(center[1])

        center_offset_x_px = center_x - cx
        center_offset_y_px = center_y - cy

        width_height_ratio = (
            avg_width_px / avg_height_px
            if avg_height_px > 0
            else None
        )

        perspective_skew_lr = (
            abs(edge_left_px - edge_right_px) / avg_height_px
            if avg_height_px > 0
            else None
        )

        perspective_skew_tb = (
            abs(edge_top_px - edge_bottom_px) / avg_width_px
            if avg_width_px > 0
            else None
        )

        # -------------------------------------------------------------
        # 3) Output both library pose and calibration geometry
        # -------------------------------------------------------------
        tags.append({
            "id": int(r.tag_id),

            "library_pose": {
                "distance_m": round(distance_m, 4),
                "angle_deg": round(angle_deg, 4),
                "yaw_deg": round(yaw_deg, 4),
            },

            "image_geometry": {
                "center_x": round(center_x, 2),
                "center_y": round(center_y, 2),
                "center_offset_x_px": round(center_offset_x_px, 2),
                "center_offset_y_px": round(center_offset_y_px, 2),

                "edge_top_px": round(edge_top_px, 2),
                "edge_bottom_px": round(edge_bottom_px, 2),
                "edge_left_px": round(edge_left_px, 2),
                "edge_right_px": round(edge_right_px, 2),

                "avg_width_px": round(avg_width_px, 2),
                "avg_height_px": round(avg_height_px, 2),

                "width_height_ratio": (
                    round(width_height_ratio, 4)
                    if width_height_ratio is not None
                    else None
                ),
                "perspective_skew_lr": (
                    round(perspective_skew_lr, 4)
                    if perspective_skew_lr is not None
                    else None
                ),
                "perspective_skew_tb": (
                    round(perspective_skew_tb, 4)
                    if perspective_skew_tb is not None
                    else None
                ),

                "corners": [
                    [round(float(px), 2), round(float(py), 2)]
                    for px, py in corners
                ],
            },
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
    