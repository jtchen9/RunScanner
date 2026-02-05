#!/usr/bin/env python3
"""
voice_agent_api.py (Wave-2)

Pi-side executors for NMS voice commands.
Keep all voice code under /home/pi/_RunScanner/voice.
"""

from __future__ import annotations

import subprocess
from typing import Any, Dict, Tuple, List

from voice_common import update_voice_config, validate_script


SERVICE_VOICE = "scanner-voice.service"
SYSTEMCTL = "/bin/systemctl"


def _run_systemctl(args: List[str]) -> Tuple[bool, str]:
    """
    Best-effort systemctl. We assume agent already has sudo-nopasswd,
    but still try non-sudo first.
    """
    # 1) without sudo
    try:
        cp = subprocess.run(
            [SYSTEMCTL] + args,
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=8,
        )
        return True, (cp.stdout or "").strip() or "ok"
    except Exception as e1:
        # 2) sudo -n
        try:
            cp2 = subprocess.run(
                ["/usr/bin/sudo", "-n", SYSTEMCTL] + args,
                check=True,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=8,
            )
            return True, (cp2.stdout or "").strip() or "ok"
        except Exception as e2:
            return False, f"systemctl failed: {type(e2).__name__}: {e2} (first={type(e1).__name__}: {e1})"


def exec_voice_start(args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    NMS.CMD.VOICE.START (Wave-2)
    - Start service
    - Set mode to name_listen (or deaf) ONLY
    - Apply timeouts, STT config, and optional speech strings
    """
    mode = (args.get("mode") or "name_listen").strip()
    if mode not in ("deaf", "name_listen"):
        mode = "name_listen"

    patch: Dict[str, Any] = {"mode": mode}

    # timeouts
    if "conversation_timeout_sec" in args:
        patch["conversation_timeout_sec"] = int(args.get("conversation_timeout_sec") or 20)
    if "llm_timeout_sec" in args:
        patch["llm_timeout_sec"] = int(args.get("llm_timeout_sec") or 30)

    # STT config (optional)
    for k in ("stt_engine", "vosk_model_dir", "mic_dev"):
        if k in args and str(args.get(k) or "").strip():
            patch[k] = str(args.get(k)).strip()
    for k in ("sample_rate", "channels", "chunk_sec"):
        if k in args and args.get(k) is not None:
            patch[k] = int(args.get(k))

    # TTS / spoken prompts (optional)
    # (voice_service.py will read these if present)
    for k in (
        "tts_volume",
        "say_enter_name_listen",
        "say_enter_deaf",
        "say_enter_conversation",
        "say_enter_llm",
    ):
        if k in args and args.get(k) is not None:
            patch[k] = args.get(k)

    update_voice_config(patch)

    ok, detail = _run_systemctl(["start", SERVICE_VOICE])
    return (ok, f"voice.start: mode={mode} {detail}")


def exec_voice_stop() -> Tuple[bool, str]:
    """
    NMS.CMD.VOICE.STOP (Wave-2)
    Stop service (system-level maintenance; not GUI primary control).
    """
    ok, detail = _run_systemctl(["stop", SERVICE_VOICE])
    return (ok, f"voice.stop: {detail}")


def exec_voice_mode_set(args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    NMS.CMD.VOICE.MODE.SET (restricted):
    Only allow switching between deaf <-> name_listen.
    """
    mode = (args.get("mode") or "").strip()
    if mode not in ("deaf", "name_listen"):
        return False, f"voice.mode.set: invalid mode={mode} (allowed: deaf|name_listen)"

    update_voice_config({"mode": mode})
    return True, f"voice.mode.set: mode={mode}"


def exec_voice_script_set(args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    NMS.CMD.VOICE.SCRIPT.SET (agent-only)
    args_json:
      {"commands":[{"phrase":"..","reply":"..","action":".."}, ...]}
    """
    commands = args.get("commands")
    script = validate_script(commands)
    update_voice_config({"script": script})
    return True, f"voice.script.set: script_len={len(script)}"
