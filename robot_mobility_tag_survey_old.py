#!/usr/bin/env python3
import argparse
import json
import subprocess
import time
from pathlib import Path

from config import get_apriltag_camera_profile

PYTHON = "/usr/bin/python3"
SNAPSHOT_SCRIPT = "/opt/_RunScanner/robot_mobility_snapshot_capture.py"
POSE_SCRIPT = "/opt/_RunScanner/robot_mobility_apriltag_calibration_pose.py"

SNAP_FRONT = "/tmp/tag_survey_front.jpg"
SNAP_REAR = "/tmp/tag_survey_rear.jpg"


def run_cmd(cmd, timeout=25):
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def capture_and_analyze(camera_role: str, snapshot_path: str):
    prof = get_apriltag_camera_profile(camera_role)

    video_dev = prof["video_dev"]
    width = int(prof["width"])
    height = int(prof["height"])

    rc, out, err = run_cmd([
        PYTHON,
        SNAPSHOT_SCRIPT,
        snapshot_path,
        "--video-dev", video_dev,
        "--width", str(width),
        "--height", str(height),
    ])

    if rc != 0:
        return {
            "ok": False,
            "camera_role": camera_role,
            "snapshot_path": snapshot_path,
            "error": err or out or "snapshot_failed",
            "tags": [],
        }

    rc, out, err = run_cmd([
        PYTHON,
        POSE_SCRIPT,
        snapshot_path,
        "--camera-role", camera_role,
    ])

    if rc != 0:
        return {
            "ok": False,
            "camera_role": camera_role,
            "snapshot_path": snapshot_path,
            "error": err or out or "pose_failed",
            "tags": [],
        }

    try:
        data = json.loads(out)
    except Exception:
        return {
            "ok": False,
            "camera_role": camera_role,
            "snapshot_path": snapshot_path,
            "error": "pose_output_not_json",
            "raw": out,
            "tags": [],
        }

    return {
        "ok": True,
        "camera_role": camera_role,
        "snapshot_path": snapshot_path,
        "error": "",
        "tags": data.get("tags", []),
        "camera_params": data.get("camera_params", {}),
    }


def simplify_tag(camera_role, tag):
    lp = tag.get("library_pose", {})
    ig = tag.get("image_geometry", {})

    return {
        "camera": camera_role,
        "id": tag.get("id"),
        "distance_m": lp.get("distance_m"),
        "angle_deg": lp.get("angle_deg"),
        "yaw_deg": lp.get("yaw_deg"),
        "center_x": ig.get("center_x"),
        "center_y": ig.get("center_y"),
        "w": ig.get("avg_width_px"),
        "h": ig.get("avg_height_px"),
        "wh": ig.get("width_height_ratio"),
        "skew": ig.get("perspective_skew_lr"),
    }


def print_brief(position_id, front, rear):
    rows = []

    for t in front.get("tags", []):
        rows.append(simplify_tag("front", t))

    for t in rear.get("tags", []):
        rows.append(simplify_tag("rear", t))

    unique_ids = sorted(set(r["id"] for r in rows if r["id"] is not None))

    print(
        f"pos={position_id} "
        f"front_count={len(front.get('tags', []))} "
        f"rear_count={len(rear.get('tags', []))} "
        f"unique_count={len(unique_ids)} "
        f"unique_tags={unique_ids}"
    )

    for r in rows:
        print(
            f"  cam={r['camera']} "
            f"tag={r['id']} "
            f"dist={r['distance_m']} "
            f"angle={r['angle_deg']} "
            f"yaw={r['yaw_deg']} "
            f"cx={r['center_x']} "
            f"cy={r['center_y']} "
            f"w={r['w']} "
            f"h={r['h']} "
            f"wh={r['wh']} "
            f"skew={r['skew']}"
        )


def print_csv(position_id, front, rear, header=False):
    if header:
        print("position_id,camera,tag_id,distance_m,angle_deg,yaw_deg,center_x,center_y,w,h,wh,skew")

    for camera_role, result in [("front", front), ("rear", rear)]:
        for t in result.get("tags", []):
            r = simplify_tag(camera_role, t)
            print(
                f"{position_id},"
                f"{r['camera']},"
                f"{r['id']},"
                f"{r['distance_m']},"
                f"{r['angle_deg']},"
                f"{r['yaw_deg']},"
                f"{r['center_x']},"
                f"{r['center_y']},"
                f"{r['w']},"
                f"{r['h']},"
                f"{r['wh']},"
                f"{r['skew']}"
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos", required=True, help="Survey position ID, e.g. L01")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--header", action="store_true")
    args = ap.parse_args()

    front = capture_and_analyze("front", SNAP_FRONT)
    time.sleep(0.3)
    rear = capture_and_analyze("rear", SNAP_REAR)

    result = {
        "ok": front.get("ok", False) or rear.get("ok", False),
        "position_id": args.pos,
        "front": front,
        "rear": rear,
        "snapshot_front": SNAP_FRONT,
        "snapshot_rear": SNAP_REAR,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.csv:
        print_csv(args.pos, front, rear, header=args.header)
    else:
        print_brief(args.pos, front, rear)

    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
    