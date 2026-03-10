#!/usr/bin/env python3
import subprocess
from typing import Any, Dict, Tuple

from config import (
    SYSTEMCTL,
    SUDO,
)

from voice.voice_common import (
    ensure_voice_config,
    load_voice_config,
    update_voice_config,
    validate_script,
)

SERVICE_VOICE = "scanner-voice.service"


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


def exec_voice_start_local(args: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        ensure_voice_config()
    except Exception:
        pass

    mode = (args.get("mode") or "").strip() or "name_listen"
    conv_to = int(args.get("conversation_timeout_sec") or 20)
    llm_to = int(args.get("llm_timeout_sec") or 30)

    script = validate_script(args.get("commands") or args.get("script"))

    new_cfg = update_voice_config({
        "mode": mode,
        "conversation_timeout_sec": conv_to,
        "llm_timeout_sec": llm_to,
        "script": script if script else load_voice_config().get("script", []),
    })

    ok, out, err = _run_systemctl(["start", SERVICE_VOICE])
    if ok:
        return True, f"started {SERVICE_VOICE} mode={new_cfg.get('mode')} script_len={len(new_cfg.get('script') or [])}"
    return False, f"start failed: {err or out}"


def exec_voice_stop_local() -> Tuple[bool, str]:
    try:
        ensure_voice_config()
        update_voice_config({"mode": "deaf"})
    except Exception:
        pass

    ok, out, err = _run_systemctl(["stop", SERVICE_VOICE])
    if ok:
        return True, f"stopped {SERVICE_VOICE}"
    return False, f"stop failed: {err or out}"


def exec_voice_mode_set_local(args: Dict[str, Any]) -> Tuple[bool, str]:
    mode = (args.get("mode") or "").strip()
    if mode not in ("deaf", "name_listen", "conversation", "llm"):
        return False, f"voice.mode.set invalid mode={mode}"

    ensure_voice_config()
    update_voice_config({"mode": mode})
    return True, f"voice mode set to {mode}"


def exec_voice_script_set_local(args: Dict[str, Any]) -> Tuple[bool, str]:
    commands = validate_script(args.get("commands"))
    ensure_voice_config()
    update_voice_config({"script": commands})
    return True, f"voice script updated script_len={len(commands)}"
