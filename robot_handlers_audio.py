#!/usr/bin/env python3
import os
import time
import subprocess
from typing import Any, Dict, Tuple

from config import (
    BASE_DIR,
    MPV_BIN,
    AUDIO_AO_DEFAULT,
    AUDIO_DEVICE_DEFAULT,
    AUDIO_VOLUME_DEFAULT,
)

AUDIO_PID_FILE = "/tmp/scanner_audio_play.pid"
TTS_SCRIPT = str(BASE_DIR / "av" / "tts_say.sh")


def exec_audio_play(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    audio_file = (args.get("file") or "").strip()
    if not audio_file:
        return False, "audio.play missing args.file"

    stop_existing = bool(args.get("stop_existing", True))
    if stop_existing:
        _ = exec_audio_stop(scanner, {})

    ao = (args.get("ao") or AUDIO_AO_DEFAULT).strip()
    audio_dev = (args.get("audio_device") or AUDIO_DEVICE_DEFAULT).strip()
    vol = int(args.get("volume") or AUDIO_VOLUME_DEFAULT)

    cmd = [
        MPV_BIN,
        f"--ao={ao}",
        f"--audio-device={audio_dev}",
        "--no-video",
        f"--volume={vol}",
        audio_file,
    ]

    try:
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(AUDIO_PID_FILE, "w") as f:
            f.write(str(p.pid))
        return True, f"audio.play started pid={p.pid} file={audio_file}"
    except Exception as e:
        return False, f"audio.play exception: {type(e).__name__}: {e}"


def _read_pidfile(pidfile: str) -> int:
    try:
        with open(pidfile, "r") as f:
            return int((f.read() or "").strip())
    except Exception:
        return 0


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True


def _remove_pidfile(pidfile: str) -> None:
    try:
        os.remove(pidfile)
    except Exception:
        pass


def exec_audio_stop(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    pid = _read_pidfile(AUDIO_PID_FILE)

    if pid <= 0:
        _remove_pidfile(AUDIO_PID_FILE)
        return True, "audio.stop ok (no pidfile / no pid)"

    if not _pid_exists(pid):
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} already exited)"

    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} already exited)"
    except Exception:
        pass

    time.sleep(0.3)
    if not _pid_exists(pid):
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} terminated by SIGTERM)"

    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} already exited)"
    except Exception as e:
        return False, f"audio.stop failed: SIGKILL exception {type(e).__name__}: {e}"

    time.sleep(0.3)
    if not _pid_exists(pid):
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} killed by SIGKILL)"

    return False, f"audio.stop failed pid={pid} (still exists after SIGKILL)"


def exec_tts_say(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    text = (args.get("text") or "").strip()
    if not text:
        return False, "tts.say missing args.text"

    lead_ms = int(args.get("lead_silence_ms") or 300)
    vol = int(args.get("volume") or AUDIO_VOLUME_DEFAULT)

    try:
        cp = subprocess.run(
            ["/usr/bin/bash", TTS_SCRIPT, text, str(lead_ms), str(vol)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        if cp.returncode == 0:
            return True, f"tts.say ok text_len={len(text)} lead_ms={lead_ms}"
        return False, f"tts.say rc={cp.returncode} stderr={(cp.stderr or '')[:200].strip()}"
    except Exception as e:
        return False, f"tts.say exception: {type(e).__name__}: {e}"
    