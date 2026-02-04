#!/usr/bin/env python3
"""
Wave-2 Voice Output Helpers

- beep(): short audible tone (best-effort)
- say(): call existing /home/pi/_RunScanner/av/tts_say.sh

No long-running service here. This is pure utility.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Tuple

from voice_common import voice_log

BASE_DIR = Path("/home/pi/_RunScanner")
TTS_SCRIPT = str(BASE_DIR / "av" / "tts_say.sh")


def beep(duration_ms: int = 120, freq_hz: int = 880, volume: int = 30) -> Tuple[bool, str]:
    """
    Best-effort beep.
    Uses sox if available: play -n synth <sec> sine <freq>
    """
    sec = max(10, int(duration_ms)) / 1000.0
    freq = max(100, int(freq_hz))
    vol = max(0, min(100, int(volume)))

    cmd = [
        "/usr/bin/play",
        "-q",
        "-n",
        "synth",
        f"{sec}",
        "sine",
        f"{freq}",
        "vol",
        f"{vol/100.0}",
    ]

    try:
        cp = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=5,
        )
        if cp.returncode == 0:
            return True, f"beep ok dur_ms={duration_ms} freq={freq_hz} vol={vol}"
        return False, f"beep rc={cp.returncode} stderr={(cp.stderr or '')[:120].strip()}"
    except FileNotFoundError:
        return False, "beep failed: /usr/bin/play not found (install sox package 'sox')"
    except Exception as e:
        return False, f"beep exception: {type(e).__name__}: {e}"


def say(text: str, lead_silence_ms: int = 300, volume: int = 90) -> Tuple[bool, str]:
    """
    Call your existing av/tts_say.sh. This is the same mechanism proven working.
    """
    t = (text or "").strip()
    if not t:
        return False, "say: missing text"

    lead = max(0, int(lead_silence_ms))
    vol = max(0, min(100, int(volume)))

    try:
        cp = subprocess.run(
            ["/usr/bin/bash", TTS_SCRIPT, t, str(lead), str(vol)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=45,   # allow longer sentences
        )
        if cp.returncode == 0:
            return True, f"say ok text_len={len(t)} lead_ms={lead} vol={vol}"
        return False, f"say rc={cp.returncode} stderr={(cp.stderr or '')[:200].strip()}"
    except Exception as e:
        return False, f"say exception: {type(e).__name__}: {e}"


def test_prompt(say_text: str = "Voice test is running.", do_beep: bool = True) -> None:
    """
    Convenience wrapper similar to NMS.CMD.VOICE.TEST.PROMPT behavior.
    """
    if do_beep:
        ok, msg = beep()
        voice_log(f"TEST.PROMPT beep -> {('ok' if ok else 'error')} {msg}")

    ok2, msg2 = say(say_text, lead_silence_ms=600, volume=90)
    voice_log(f"TEST.PROMPT say  -> {('ok' if ok2 else 'error')} {msg2}")
