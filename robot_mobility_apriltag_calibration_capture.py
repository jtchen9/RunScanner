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


def capture_snapshot():
    rc, out, err = run_cmd([PYTHON, SNAPSHOT_SCRIPT, SNAPSHOT_PATH], timeout=20)
    if rc != 0:
        return False, {
            "ok": False,
            "stage": "snapshot",
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
            "error": "snapshot_file_missing",
        }

    return True, {
        "ok": True,
        "stage": "snapshot",
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
    args = ap.parse_args()

    result = {
        "ok": False,
        "calibration_capture": True,
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

                if args.brief:
                    print(result["error"])
                else:
                    print(json.dumps(result, ensure_ascii=False, indent=2))

                raise SystemExit(1)

            time.sleep(1.0)

        ok_snap, snap_info = capture_snapshot()
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
    # Full JSON mode
    # ---------------------------------------------------------
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
