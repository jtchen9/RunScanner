#!/usr/bin/env python3
import json
import subprocess
import time
from pathlib import Path
import argparse

AV_SERVICE = "scanner-avstream.service"

SNAPSHOT_SCRIPT = "/opt/_RunScanner/robot_mobility_snapshot_capture.py"
CALIB_APRILTAG_SCRIPT = "/opt/_RunScanner/robot_mobility_apriltag_calibration_pose.py"

SNAPSHOT_PATH = "/tmp/robot_mobility_snapshot.jpg"
FRONT_VIDEO = "/dev/v4l/by-id/usb-webcamvendor_webcamproduct_YGR80PU1200.23071717-video-index0"
REAR_VIDEO = "/dev/v4l/by-id/usb-046d_C270_HD_WEBCAM_200901010001-video-index0"

SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"
PYTHON = "/usr/bin/python3"

def run_cmd(cmd, timeout=20):
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def run_systemctl(args, timeout=15):
    rc, out, err = run_cmd([SUDO, "-n", SYSTEMCTL] + args, timeout=timeout)
    return (rc == 0), out, err


def av_is_active():
    rc, out, err = run_cmd([SUDO, "-n", SYSTEMCTL, "is-active", AV_SERVICE], timeout=10)
    return out.strip() == "active"


def av_stop():
    return run_systemctl(["stop", AV_SERVICE], timeout=15)


def av_start():
    return run_systemctl(["start", AV_SERVICE], timeout=15)


def capture_snapshot(camera_role: str):
    video_dev = FRONT_VIDEO if camera_role == "front" else REAR_VIDEO

    last_err = ""
    last_out = ""

    for attempt in range(1, 4):
        rc, out, err = run_cmd(
            [PYTHON, SNAPSHOT_SCRIPT, SNAPSHOT_PATH, "--video-dev", video_dev],
            timeout=25,
        )

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
            "error": last_err or last_out or "snapshot_failed_after_retries",
        }

    if rc != 0:
        return False, {
            "ok": False,
            "stage": "snapshot",
            "camera_role": camera_role,
            "video_dev": video_dev,
            "error": err or out or "snapshot_failed",
        }

    try:
        data = json.loads(out)
    except Exception:
        data = None

    if not Path(SNAPSHOT_PATH).exists():
        return False, {
            "ok": False,
            "stage": "snapshot",
            "camera_role": camera_role,
            "video_dev": video_dev,
            "error": "snapshot_file_missing",
        }

    return True, {
        "ok": True,
        "stage": "snapshot",
        "camera_role": camera_role,
        "video_dev": video_dev,
        "snapshot_path": SNAPSHOT_PATH,
        "detail": data or out,
    }


def analyze_snapshot_for_calibration():
    rc, out, err = run_cmd([PYTHON, CALIB_APRILTAG_SCRIPT, SNAPSHOT_PATH], timeout=20)
    if rc != 0:
        return False, {
            "ok": False,
            "stage": "apriltag_calibration",
            "error": err or out or "apriltag_calibration_failed",
        }

    try:
        data = json.loads(out)
    except Exception:
        return False, {
            "ok": False,
            "stage": "apriltag_calibration",
            "error": "apriltag_calibration_output_not_json",
            "raw_output": out,
        }

    return True, data


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

    result = {
        "ok": False,
        "calibration_capture": True,
        "camera_role": args.camera_role,
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

                # ---------------------------------------------------------
                # Brief output mode
                # ---------------------------------------------------------
                if args.brief:
                    tags = result["apriltag"].get("tags", [])

                    if not tags:
                        print("no_tags_detected")
                    else:
                        for t in tags:
                            lp = t.get("library_pose", {})

                            print(
                                f"tag={t.get('id')} "
                                f"dist={lp.get('distance_m'):.3f}m "
                                f"angle={lp.get('angle_deg'):.2f} "
                                f"yaw={lp.get('yaw_deg'):.2f}"
                            )

                # ---------------------------------------------------------
                # Yaw calibration mode
                # ---------------------------------------------------------
                elif args.yaw:
                    tags = result["apriltag"].get("tags", [])

                    if not tags:
                        print("no_tags_detected")
                    else:
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

                # ---------------------------------------------------------
                # Full JSON mode
                # ---------------------------------------------------------
                else:
                    print(json.dumps(result, ensure_ascii=False, indent=2))

                raise SystemExit(1)

            time.sleep(3.0)

        ok_snap, snap_info = capture_snapshot(args.camera_role)
        if not ok_snap:
            result["error"] = snap_info.get("error", "snapshot_failed")
            result["apriltag"] = snap_info

            if args.brief:
                print(result["error"])
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))

            raise SystemExit(1)

        ok_tag, tag_info = analyze_snapshot_for_calibration()
        result["apriltag"] = tag_info

        if not ok_tag:
            result["error"] = tag_info.get("error", "apriltag_calibration_failed")

            if args.brief:
                print(result["error"])
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))

            raise SystemExit(1)

        result["ok"] = True
        result["error"] = ""

    finally:
        if av_was_active:
            ok, out, err = av_start()
            result["av_restarted"] = ok
            time.sleep(1.0)

    # ---------------------------------------------------------
    # Brief output mode
    # ---------------------------------------------------------
    if args.brief:
        tags = result["apriltag"].get("tags", [])

        if not tags:
            print("no_tags_detected")
        else:
            for t in tags:
                lp = t.get("library_pose", {})
                print(
                    f"tag={t.get('id')} "
                    f"dist={lp.get('distance_m'):.3f}m "
                    f"angle={lp.get('angle_deg'):.2f} "
                    f"yaw={lp.get('yaw_deg'):.2f}"
                )

    # ---------------------------------------------------------
    # Yaw calibration output mode
    # ---------------------------------------------------------
    elif args.yaw:
        tags = result["apriltag"].get("tags", [])

        if not tags:
            print("no_tags_detected")
        else:
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

    # ---------------------------------------------------------
    # Full JSON mode
    # ---------------------------------------------------------
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
