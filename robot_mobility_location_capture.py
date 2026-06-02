#!/usr/bin/env python3
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import (
    CAMERA_ROLE_FRONT,
    CAMERA_ROLE_REAR,
    get_apriltag_camera_profile,
)

AV_SERVICE = "scanner-avstream.service"

SNAPSHOT_SCRIPT = "/opt/_RunScanner/robot_mobility_snapshot_capture.py"
APRILTAG_SCRIPT = "/opt/_RunScanner/robot_mobility_apriltag_snapshot_pose.py"

SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"
PYTHON = "/usr/bin/python3"

CAMERA_ROLES = [CAMERA_ROLE_FRONT, CAMERA_ROLE_REAR]

# Keep images for audit/debug.
SNAPSHOT_PREFIX = "/tmp/location"

# Camera/open timing
AV_RELEASE_SLEEP_SEC = 3.0
BETWEEN_CAMERA_SLEEP_SEC = 1.5
CAPTURE_RETRY = 3
CAPTURE_RETRY_SLEEP_SEC = 1.0


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


def _new_snapshot_path(camera_role: str) -> str:
    ts = int(time.time() * 1000)
    return f"{SNAPSHOT_PREFIX}_{camera_role}_{ts}.jpg"


def capture_snapshot(camera_role: str, snapshot_path: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Capture one camera by role using the camera profile in config.py.
    Old files are not deleted because every snapshot path is timestamped.
    """
    try:
        prof = get_apriltag_camera_profile(camera_role)
    except Exception as e:
        return False, {
            "ok": False,
            "stage": "snapshot",
            "camera_role": camera_role,
            "snapshot_path": snapshot_path,
            "error": f"bad_camera_profile: {type(e).__name__}: {e}",
        }

    video_dev = str(prof["video_dev"])
    width = int(prof["width"])
    height = int(prof["height"])

    p = Path(snapshot_path)
    t0 = time.time()
    last_err = ""
    last_out = ""

    for attempt in range(1, CAPTURE_RETRY + 1):
        rc, out, err = run_cmd(
            [
                PYTHON,
                SNAPSHOT_SCRIPT,
                snapshot_path,
                "--video-dev", video_dev,
                "--width", str(width),
                "--height", str(height),
            ],
            timeout=30,
        )

        # Success means: command succeeded, file exists, and file is new.
        if rc == 0 and p.exists() and p.stat().st_mtime >= t0:
            try:
                detail = json.loads(out)
            except Exception:
                detail = out

            return True, {
                "ok": True,
                "stage": "snapshot",
                "camera_role": camera_role,
                "snapshot_path": snapshot_path,
                "video_dev": video_dev,
                "width": width,
                "height": height,
                "attempt": attempt,
                "detail": detail,
                "error": "",
            }

        last_err = err
        last_out = out
        time.sleep(CAPTURE_RETRY_SLEEP_SEC)

    return False, {
        "ok": False,
        "stage": "snapshot",
        "camera_role": camera_role,
        "snapshot_path": snapshot_path,
        "video_dev": video_dev,
        "width": width,
        "height": height,
        "error": last_err or last_out or "snapshot_failed_after_retries",
    }


def analyze_snapshot(camera_role: str, snapshot_path: str) -> Tuple[bool, Dict[str, Any]]:
    rc, out, err = run_cmd(
        [
            PYTHON,
            APRILTAG_SCRIPT,
            snapshot_path,
            "--camera-role", camera_role,
        ],
        timeout=30,
    )

    if rc != 0:
        return False, {
            "ok": False,
            "stage": "apriltag",
            "camera_role": camera_role,
            "snapshot_path": snapshot_path,
            "error": err or out or "apriltag_failed",
            "tags": [],
            "count": 0,
        }

    try:
        data = json.loads(out)
    except Exception:
        return False, {
            "ok": False,
            "stage": "apriltag",
            "camera_role": camera_role,
            "snapshot_path": snapshot_path,
            "error": "apriltag_output_not_json",
            "raw_output": out,
            "tags": [],
            "count": 0,
        }

    # Make role/path explicit even if child script changes later.
    data["camera_role"] = camera_role
    data["snapshot_path"] = snapshot_path
    return bool(data.get("ok", False)), data


def capture_and_analyze(camera_role: str) -> Dict[str, Any]:
    snapshot_path = _new_snapshot_path(camera_role)

    ok_snap, snap_info = capture_snapshot(camera_role, snapshot_path)
    if not ok_snap:
        return {
            "ok": False,
            "camera_role": camera_role,
            "snapshot": snap_info,
            "apriltag": {
                "ok": False,
                "stage": "apriltag",
                "camera_role": camera_role,
                "snapshot_path": snapshot_path,
                "error": "snapshot_failed",
                "tags": [],
                "count": 0,
            },
            "snapshot_path": snapshot_path,
            "tags": [],
            "count": 0,
            "error": snap_info.get("error", "snapshot_failed"),
        }

    ok_tag, tag_info = analyze_snapshot(camera_role, snapshot_path)

    return {
        "ok": ok_snap and ok_tag,
        "camera_role": camera_role,
        "snapshot": snap_info,
        "apriltag": tag_info,
        "snapshot_path": snapshot_path,
        "tags": tag_info.get("tags", []),
        "count": int(tag_info.get("count", 0) or 0),
        "error": "" if ok_tag else tag_info.get("error", "apriltag_failed"),
    }


def build_merged_tags(camera_results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Flatten front/rear detections into one list.
    Each tag keeps camera_role so NMS can handle camera extrinsics later.
    """
    merged = []

    for role in CAMERA_ROLES:
        result = camera_results.get(role, {})
        for tag in result.get("tags", []):
            item = dict(tag)
            item["camera_role"] = role
            item["snapshot_path"] = result.get("snapshot_path", "")
            merged.append(item)

    return merged


def main():
    result = {
        "ok": False,
        "capture_mode": "dual_camera",
        "camera_roles": CAMERA_ROLES,
        "av_was_active": False,
        "av_restarted": False,
        "snapshots": {},
        "front": None,
        "rear": None,
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

            # Let ffmpeg release cameras before OpenCV opens them.
            time.sleep(AV_RELEASE_SLEEP_SEC)

        camera_results: Dict[str, Dict[str, Any]] = {}

        for i, role in enumerate(CAMERA_ROLES):
            camera_results[role] = capture_and_analyze(role)
            result[role] = camera_results[role]
            result["snapshots"][role] = camera_results[role].get("snapshot_path", "")

            if i < len(CAMERA_ROLES) - 1:
                time.sleep(BETWEEN_CAMERA_SLEEP_SEC)

        merged_tags = build_merged_tags(camera_results)

        front = camera_results.get(CAMERA_ROLE_FRONT, {})
        rear = camera_results.get(CAMERA_ROLE_REAR, {})

        apriltag_ok = bool(front.get("ok", False) or rear.get("ok", False))
        detection_count = len(merged_tags)
        unique_tag_ids = sorted({
            int(t["id"])
            for t in merged_tags
            if t.get("id") is not None
        })

        result["apriltag"] = {
            "ok": apriltag_ok,
            "capture_mode": "dual_camera",
            "count": detection_count,
            "unique_count": len(unique_tag_ids),
            "unique_tag_ids": unique_tag_ids,
            "tags": merged_tags,
            "front": front.get("apriltag", {}),
            "rear": rear.get("apriltag", {}),
            "front_count": int(front.get("count", 0) or 0),
            "rear_count": int(rear.get("count", 0) or 0),
        }

        result["ok"] = apriltag_ok
        if not apriltag_ok:
            result["error"] = "both_camera_location_capture_failed"
        else:
            result["error"] = ""

    finally:
        if av_was_active:
            ok, out, err = av_start()
            result["av_restarted"] = ok

    print(json.dumps(result, ensure_ascii=False, indent=2))

    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
