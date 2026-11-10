"""Robot health polling commands."""
import json
import subprocess
import tempfile
import time
from pathlib import Path


def check_cameras(args):
    result = {"schema": "camera_health_v1", "request_id": args.get("request_id", ""),
              "cameras": {}}
    def finish():
        result["finished_ts"] = time.time()
        ok = all(result["cameras"].get(r, {}).get("status") == "PASS"
                 for r in ("front", "rear"))
        return ("ok" if ok else "error"), json.dumps(result)
    def unavailable(reason):
        for role in ("front", "rear"):
            result["cameras"][role] = {"status": "UNKNOWN", "detail": reason}
        return finish()
    try:
        if time.time() > float(args.get("expires_ts", 0)):
            return unavailable("check expired; run tool again")
        state_path = Path("/tmp/mobility_state.json")
        if state_path.exists() and json.loads(state_path.read_text()).get("busy"):
            return unavailable("mobility busy; cameras not tested")
        from robot_mobility_location_capture import AV_SERVICE, SNAPSHOT_SCRIPT, PYTHON
        from config import get_apriltag_camera_profile
        service = subprocess.run(["/usr/bin/systemctl", "is-active", AV_SERVICE],
                                 capture_output=True, text=True, timeout=5)
        if service.stdout.strip() == "active":
            return unavailable("AV streaming active; cameras not tested")
        if service.stdout.strip() not in ("inactive", "failed", "unknown"):
            return unavailable("cannot determine AV camera ownership")
        with tempfile.TemporaryDirectory(prefix="camera_health_") as folder:
            for role in ("front", "rear"):
                try:
                    profile = get_apriltag_camera_profile(role)
                    device = str(profile["video_dev"])
                    if not Path(device).exists():
                        result["cameras"][role] = {"status": "FAIL", "detail": "device missing", "device": device}
                        continue
                    path = Path(folder) / (role + ".jpg")
                    cp = subprocess.run([PYTHON, SNAPSHOT_SCRIPT, str(path),
                        "--video-dev", device, "--width", str(profile["width"]),
                        "--height", str(profile["height"])], capture_output=True,
                        text=True, timeout=20)
                    try:
                        payload = json.loads(cp.stdout)
                    except (ValueError, TypeError):
                        payload = {}
                    ok = (cp.returncode == 0 and payload.get("ok") is True
                          and path.exists() and path.stat().st_size > 0)
                    detail = "fresh snapshot captured" if ok else str(
                        payload.get("error") or cp.stderr or cp.stdout or "snapshot failed")[-300:]
                    result["cameras"][role] = {"status": "PASS" if ok else "FAIL",
                                               "detail": detail, "device": device}
                except Exception as exc:
                    result["cameras"][role] = {"status": "FAIL", "detail": str(exc)[-300:]}
        return finish()
    except Exception as exc:
        return unavailable(str(exc)[-300:])

def check_static(args):
    """Run the same unattended static preflight used by the lab operator."""
    import math
    import sys
    result = {"schema": "static_health_v1", "request_id": args.get("request_id", "")}
    def finish(ok, reason):
        result.update(status="PASS" if ok else "FAIL", reason=reason)
        return ("ok" if ok else "error"), json.dumps(result)

    try:
        expires = float(args.get("expires_ts", time.time()+150))
        if not math.isfinite(expires) or expires <= time.time():
            return finish(False, "request expired")
        state = Path("/tmp/mobility_state.json")
        if state.exists() and json.loads(state.read_text()).get("busy"):
            return finish(False, "mobility busy")
        from robot_mobility_location_capture import AV_SERVICE
        service = subprocess.run(["/usr/bin/systemctl", "is-active", AV_SERVICE],
                                 capture_output=True, text=True, timeout=5)
        if service.stdout.strip() not in ("inactive", "failed"):
            return finish(False, "AV active or ownership unknown")
        script = Path(__file__).resolve().parent / "TestGyro" / "robot_mobility_preflight.py"
        with tempfile.TemporaryDirectory(prefix="static_health_") as directory:
            cp = subprocess.run([sys.executable, str(script), "--static-only",
                                 "--output-dir", directory], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True,
                                timeout=max(0.01,min(150, expires-time.time())))
            reports = list(Path(directory).glob("preflight_*.json"))
            if len(reports) != 1:
                return finish(False, "missing static report: " + (cp.stderr or cp.stdout)[-300:])
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            result["checks"] = {c["name"]:c["status"] for c in report["checks"]}
            ok = (cp.returncode == 0 and report.get("mode") == "static_only"
                  and report.get("status") == "pass" and time.time() <= expires)
            return finish(ok, "" if ok else str(report.get("failure") or "static check failed or expired"))
    except Exception as exc:
        return finish(False, str(exc)[-300:])
