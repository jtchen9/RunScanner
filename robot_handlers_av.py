#!/usr/bin/env python3
import json
import subprocess
from typing import Any, Dict, Tuple
from pathlib import Path

from config import (
    AV_DIR,
    AV_CFG_FILE,
    AV_DEFAULT_SERVER,
    AV_DEFAULT_RTSP_PORT,
    AV_DEFAULT_TRANSPORT,
    AV_DEFAULT_VIDEO_DEV,
    AV_DEFAULT_AUDIO_DEV,
    AV_DEFAULT_SIZE,
    AV_DEFAULT_FPS,
    SYSTEMCTL,
    SUDO,
)

SERVICE_AVSTREAM = "scanner-avstream.service"


def _run_systemctl(args: list[str]) -> Tuple[bool, str, str]:
    """Run systemctl. Try without sudo first; if that fails, retry with sudo -n."""
    try:
        cp = subprocess.run(
            [SYSTEMCTL] + args,
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return True, (cp.stdout or "").strip(), (cp.stderr or "").strip()
    except subprocess.CalledProcessError as e1:
        try:
            cp2 = subprocess.run(
                [SUDO, "-n", SYSTEMCTL] + args,
                check=True,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            )
            return True, (cp2.stdout or "").strip(), (cp2.stderr or "").strip()
        except subprocess.CalledProcessError as e2:
            return False, (e2.stdout or "").strip(), (e2.stderr or e1.stderr or "").strip()


def _ensure_dir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _write_json(p: Path, obj: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True, f"wrote {p}"
    except Exception as e:
        return False, f"write_json failed {p}: {type(e).__name__}: {e}"


def get_av_streaming_flag() -> int:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", SERVICE_AVSTREAM],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return 1 if r.stdout.strip() == "active" else 0
    except Exception:
        return 0


def exec_av_stream_start(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    _ensure_dir(AV_DIR)

    camera_role = (args.get("camera_role") or "front").strip().lower()
    if camera_role not in ("", "front", "rear"):
        return False, f"bad camera_role={camera_role}; expected front|rear"

    # Base config, backward compatible with old one-camera runner
    cfg = {
        "server": (args.get("server") or "").strip() or AV_DEFAULT_SERVER,
        "port": int(args.get("port") or AV_DEFAULT_RTSP_PORT),
        "transport": (args.get("transport") or "").strip() or AV_DEFAULT_TRANSPORT,
        "scanner": scanner,
        "fps": int(args.get("fps") or AV_DEFAULT_FPS),
    }

    if camera_role:
        # New role-based mode.
        # av_stream_runner.sh will resolve video/audio/path/codec by camera_role.
        cfg["camera_role"] = camera_role

        # Optional overrides, normally not needed
        if args.get("path"):
            cfg["path"] = str(args.get("path")).strip()
        if args.get("size"):
            cfg["size"] = str(args.get("size")).strip()
        if "audio_enabled" in args:
            cfg["audio_enabled"] = bool(args.get("audio_enabled"))

    else:
        # Legacy mode: preserve old behavior exactly
        cfg.update({
            "path": (args.get("path") or "").strip() or scanner,
            "video_dev": (args.get("video_dev") or "").strip() or AV_DEFAULT_VIDEO_DEV,
            "audio_dev": (args.get("audio_dev") or "").strip() or AV_DEFAULT_AUDIO_DEV,
            "size": (args.get("size") or "").strip() or AV_DEFAULT_SIZE,
        })

    ok, msg = _write_json(AV_CFG_FILE, cfg)
    if not ok:
        return False, msg

    # Restart, not start, so switching front <-> rear takes effect immediately.
    ok2, out, err = _run_systemctl(["restart", SERVICE_AVSTREAM])
    return (True, f"started {SERVICE_AVSTREAM} camera_role={camera_role or 'legacy'}") if ok2 else (False, f"start failed: {err or out}")


def exec_av_stream_stop() -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["stop", SERVICE_AVSTREAM])
    return (True, f"stopped {SERVICE_AVSTREAM}") if ok else (False, f"stop failed: {err or out}")
