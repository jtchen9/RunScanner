#!/usr/bin/env python3
import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Tuple

from config import get_apriltag_camera_profile

AV_SERVICE = "scanner-avstream.service"

SNAPSHOT_SCRIPT = "/opt/_RunScanner/robot_mobility_snapshot_capture.py"
CALIB_APRILTAG_SCRIPT = "/opt/_RunScanner/robot_mobility_apriltag_calibration_pose.py"

SNAPSHOT_PATH = "/tmp/robot_mobility_snapshot.jpg"

SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"
PYTHON = "/usr/bin/python3"


def run_cmd(cmd, timeout=20) -> Tuple[int, str, str]:
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def run_systemctl(args, timeout=15) -> Tuple[bool, str, str]:
    rc, out, err = run_cmd([SUDO, "-n", SYSTEMCTL] + args, timeout=timeout)
    return (rc == 0), out, err


def av_is_active() -> bool:
    rc, out, err = run_cmd([SUDO, "-n", SYSTEMCTL, "is-active", AV_SERVICE], timeout=10)
    return out.strip() == "active"


def av_stop() -> Tuple[bool, str, str]:
    return run_systemctl(["stop", AV_SERVICE], timeout=15)


def av_start() -> Tuple[bool, str, str]:
    return run_systemctl(["start", AV_SERVICE], timeout=15)


def _profile_for(camera_role: str) -> Dict[str, Any]:
    return get_apriltag_camera_profile(camera_role)


def capture_snapshot(camera_role: str):
    profile = _profile_for(camera_role)

    video_dev = str(profile["video_dev"])
    width = int(profile["width"])
    height = int(profile["height"])

    last_err = ""
    last_out = ""

    cmd = [
        PYTHON,
        SNAPSHOT_SCRIPT,
        SNAPSHOT_PATH,
        "--video-dev",
        video_dev,
        "--width",
        str(width),
        "--height",
        str(height),
    ]

    for attempt in range(1, 4):
        rc, out, err = run_cmd(cmd, timeout=25)

        if rc == 0 and Path(SNAPSHOT_PATH).exists():
            break

        last_err = err
        last_out = out
        time.sleep(1.0)
    else:
        return False, {
            "ok": False,
            "stage": "snapshot",
            "camera_role": camera_role,
            "video_dev": video_dev,
            "width": width,
            "height": height,
            "error": last_err or last_out or "snapshot_failed_after_retries",
        }

    try:
        data = json.loads(out)
    except Exception:
        data = None

    return True, {
        "ok": True,
        "stage": "snapshot",
        "camera_role": camera_role,
        "video_dev": video_dev,
        "width": width,
        "height": height,
        "snapshot_path": SNAPSHOT_PATH,
        "detail": data or out,
    }


def analyze_snapshot_for_calibration(camera_role: str):
    rc, out, err = run_cmd(
        [PYTHON, CALIB_APRILTAG_SCRIPT, SNAPSHOT_PATH, "--camera-role", camera_role],
        timeout=20,
    )
    if rc != 0:
        return False, {
            "ok": False,
            "stage": "apriltag_calibration",
            "camera_role": camera_role,
            "error": err or out or "apriltag_calibration_failed",
        }

    try:
        data = json.loads(out)
    except Exception:
        return False, {
            "ok": False,
            "stage": "apriltag_calibration",
            "camera_role": camera_role,
            "error": "apriltag_calibration_output_not_json",
            "raw_output": out,
        }

    return True, data


def print_brief(result: dict) -> None:
    tags = ((result.get("apriltag") or {}).get("tags") or [])

    if not tags:
        print(result.get("error") or "no_tags_detected")
        return

    for t in tags:
        lp = t.get("library_pose", {})
        print(
            f"tag={t.get('id')} "
            f"dist={lp.get('distance_m'):.3f}m "
            f"angle={lp.get('angle_deg'):.2f} "
            f"yaw={lp.get('yaw_deg'):.2f}"
        )


def print_yaw(result: dict) -> None:
    tags = ((result.get("apriltag") or {}).get("tags") or [])

    if not tags:
        print(result.get("error") or "no_tags_detected")
        return

    for t in tags:
        lp = t.get("library_pose", {})
        ig = t.get("image_geometry", {})
        print(
            f"tag={t.get('id')} "
            f"dist={lp.get('distance_m'):.3f} "
            f"angle={lp.get('angle_deg'):.2f} "
            f"yaw={lp.get('yaw_deg'):.2f} "
            f"cx={ig.get('center_x'):.1f} "
            f"cy={ig.get('center_y'):.1f} "
            f"w={ig.get('avg_width_px'):.1f} "
            f"h={ig.get('avg_height_px'):.1f} "
            f"wh={ig.get('width_height_ratio'):.3f} "
            f"skew={ig.get('perspective_skew_lr'):.3f}"
        )


def print_result(result: dict, args) -> None:
    if args.brief:
        print_brief(result)
    elif args.yaw:
        print_yaw(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "-b",
        "--brief",
        action="store_true",
        help="Brief output mode",
    )
    ap.add_argument(
        "-y",
        "--yaw",
        action="store_true",
        help="Yaw calibration output mode",
    )
    ap.add_argument(
        "--camera-role",
        choices=["front", "rear"],
        default="front",
        help="Camera role for AprilTag capture",
    )
    args = ap.parse_args()

    profile = _profile_for(args.camera_role)

    result = {
        "ok": False,
        "calibration_capture": True,
        "camera_role": args.camera_role,
        "camera_profile": {
            "video_dev": profile["video_dev"],
            "width": profile["width"],
            "height": profile["height"],
            "fx": profile["fx"],
            "fy": profile["fy"],
            "cx": profile["cx"],
            "cy": profile["cy"],
            "tag_size_m": profile["tag_size_m"],
        },
        "av_was_active": False,
        "av_restarted": False,
        "snapshot_path": SNAPSHOT_PATH,
        "apriltag": None,
        "error": "",
    }

    av_was_active = av_is_active()
    result["av_was_active"] = av_was_active

    try:
        if av_was_active:
            ok, out, err = av_stop()
            if not ok:
                result["error"] = f"av_stop_failed: {err or out}"
                print_result(result, args)
                raise SystemExit(1)

            # Give ffmpeg/systemd time to release the camera device.
            time.sleep(3.0)

        ok_snap, snap_info = capture_snapshot(args.camera_role)
        if not ok_snap:
            result["error"] = snap_info.get("error", "snapshot_failed")
            result["apriltag"] = snap_info
            print_result(result, args)
            raise SystemExit(1)

        result["snapshot"] = snap_info

        ok_tag, tag_info = analyze_snapshot_for_calibration(args.camera_role)
        result["apriltag"] = tag_info

        if not ok_tag:
            result["error"] = tag_info.get("error", "apriltag_calibration_failed")
            print_result(result, args)
            raise SystemExit(1)

        result["ok"] = True
        result["error"] = ""

    finally:
        if av_was_active:
            ok, out, err = av_start()
            result["av_restarted"] = ok
            time.sleep(1.0)

    print_result(result, args)
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
