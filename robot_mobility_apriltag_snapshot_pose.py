#!/usr/bin/env python3
import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from pupil_apriltags import Detector

from config import (
    get_apriltag_camera_profile,
    apply_apriltag_calibration,
)


def rotation_matrix_to_yaw_deg(R: np.ndarray) -> float:
    """
    Legacy library yaw extraction.
    Kept for comparison/debugging, but calibration output uses geometry yaw.
    """
    yaw_rad = math.atan2(R[1, 0], R[0, 0])
    return math.degrees(yaw_rad)


def geometry_yaw_deg(
    avg_width_px: float,
    avg_height_px: float,
    edge_left_px: float,
    edge_right_px: float,
) -> float:
    """
    Geometry-based tag yaw estimate.

    Magnitude:
      tag yaw compresses apparent tag width.
      yaw_abs ~= acos(avg_width_px / avg_height_px)

    Sign:
      uses left/right edge asymmetry. Keep this as a convention; if later
      ground-truth convention is opposite, flip globally after calibration.
    """
    if avg_height_px <= 0:
        return 0.0

    ratio = avg_width_px / avg_height_px
    ratio = max(min(ratio, 1.0), -1.0)

    yaw_abs_deg = math.degrees(math.acos(ratio))

    # Avoid false yaw when tag is almost frontal.
    if yaw_abs_deg < 12.0:
        yaw_abs_deg = 0.0

    if edge_right_px < edge_left_px:
        return yaw_abs_deg
    return -yaw_abs_deg


def analyze_snapshot(
    image_path: str,
    camera_role: str = "front",
    fx: float | None = None,
    fy: float | None = None,
    cx: float | None = None,
    cy: float | None = None,
    tag_size_m: float | None = None,
) -> dict:
    profile = get_apriltag_camera_profile(camera_role)

    fx = float(profile["fx"] if fx is None else fx)
    fy = float(profile["fy"] if fy is None else fy)
    cx = float(profile["cx"] if cx is None else cx)
    cy = float(profile["cy"] if cy is None else cy)
    tag_size_m = float(profile["tag_size_m"] if tag_size_m is None else tag_size_m)

    p = Path(image_path)
    if not p.exists():
        return {
            "ok": False,
            "error": f"image_not_found: {image_path}",
            "count": 0,
            "tags": [],
            "camera_role": camera_role,
        }

    img = cv2.imread(str(p))
    if img is None:
        return {
            "ok": False,
            "error": f"image_load_failed: {image_path}",
            "count": 0,
            "tags": [],
            "camera_role": camera_role,
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
        # 1) Library pose estimate: distance and camera bearing
        # -------------------------------------------------------------
        t = np.asarray(r.pose_t).reshape(-1)
        x, y, z = float(t[0]), float(t[1]), float(t[2])

        distance_m = math.sqrt(x * x + y * y + z * z)
        angle_deg = math.degrees(math.atan2(x, z))

        R = np.asarray(r.pose_R)
        library_yaw_deg = rotation_matrix_to_yaw_deg(R)

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

        yaw_deg = geometry_yaw_deg(
            avg_width_px=avg_width_px,
            avg_height_px=avg_height_px,
            edge_left_px=edge_left_px,
            edge_right_px=edge_right_px,
        )

        (
            distance_cal_m,
            angle_cal_deg,
            yaw_cal_deg,
        ) = apply_apriltag_calibration(
            camera_role=camera_role,
            distance_m=distance_m,
            angle_deg=angle_deg,
            yaw_deg=yaw_deg,
        )

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

        tags.append({
            "id": int(r.tag_id),

            "library_pose": {
                "distance_m": round(distance_m, 4),
                "angle_deg": round(angle_deg, 4),
                "yaw_deg": round(yaw_deg, 4),
            },

            "calibrated_pose": {
                "distance_m": round(distance_cal_m, 4),
                "angle_deg": round(angle_cal_deg, 4),
                "yaw_deg": round(yaw_cal_deg, 4),
            },
        })

    return {
        "ok": True,
        "error": "",
        "image_path": str(p),
        "camera_role": camera_role,
        "image_width": int(img.shape[1]),
        "image_height": int(img.shape[0]),
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
    ap = argparse.ArgumentParser()
    ap.add_argument("image_path", help="Snapshot image path")
    ap.add_argument(
        "--camera-role",
        choices=["front", "rear"],
        default="front",
        help="Camera profile to use for AprilTag pose",
    )
    args = ap.parse_args()

    out = analyze_snapshot(
        image_path=args.image_path,
        camera_role=args.camera_role,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
