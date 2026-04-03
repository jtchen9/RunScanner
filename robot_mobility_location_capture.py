#!/usr/bin/env python3
import json
import subprocess
import sys
import time
from pathlib import Path

AV_SERVICE = "scanner-avstream.service"

SNAPSHOT_SCRIPT = "/opt/_RunScanner/robot_mobility_snapshot_capture.py"
APRILTAG_SCRIPT = "/opt/_RunScanner/robot_mobility_apriltag_snapshot_pose.py"

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


def analyze_snapshot():
    rc, out, err = run_cmd([PYTHON, APRILTAG_SCRIPT, SNAPSHOT_PATH], timeout=20)
    if rc != 0:
        return False, {
            "ok": False,
            "stage": "apriltag",
            "error": err or out or "apriltag_failed",
        }

    try:
        data = json.loads(out)
    except Exception:
        return False, {
            "ok": False,
            "stage": "apriltag",
            "error": "apriltag_output_not_json",
            "raw_output": out,
        }

    return True, data


def main():
    result = {
        "ok": False,
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
                print(json.dumps(result, ensure_ascii=False, indent=2))
                raise SystemExit(1)

            # Let ffmpeg release /dev/video0
            time.sleep(1.0)

        ok_snap, snap_info = capture_snapshot()
        if not ok_snap:
            result["error"] = snap_info.get("error", "snapshot_failed")
            result["apriltag"] = snap_info
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(1)

        ok_tag, tag_info = analyze_snapshot()
        result["apriltag"] = tag_info

        if not ok_tag:
            result["error"] = tag_info.get("error", "apriltag_failed")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(1)

        result["ok"] = True
        result["error"] = ""

    finally:
        if av_was_active:
            ok, out, err = av_start()
            result["av_restarted"] = ok

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["ok"]:
        raise SystemExit(0)
    raise SystemExit(1)


if __name__ == "__main__":
    main()