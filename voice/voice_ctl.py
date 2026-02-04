#!/usr/bin/env python3
"""
Local control helper (no NMS needed).

Usage:
  python3 voice/voice_ctl.py mode deaf|name_listen|conversation|llm_dummy
  python3 voice/voice_ctl.py script clear
  python3 voice/voice_ctl.py script demo
  python3 voice/voice_ctl.py show
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from voice_common import load_voice_config, save_voice_config, voice_log

VALID_MODES = {"deaf", "name_listen", "conversation", "llm_dummy"}


def _die(msg: str) -> None:
    print(msg)
    sys.exit(2)


def cmd_show() -> None:
    cfg = load_voice_config()
    print(json.dumps(cfg, ensure_ascii=False, indent=2))


def cmd_mode(mode: str) -> None:
    m = (mode or "").strip()
    if m not in VALID_MODES:
        _die(f"bad mode: {m} (valid: {sorted(VALID_MODES)})")
    cfg = load_voice_config()
    cfg["mode"] = m
    save_voice_config(cfg)
    voice_log(f"VOICE_CTL: set mode={m}")
    print("ok")


def cmd_script_clear() -> None:
    cfg = load_voice_config()
    cfg["script"] = []
    save_voice_config(cfg)
    voice_log("VOICE_CTL: script cleared")
    print("ok")


def cmd_script_demo() -> None:
    demo: List[Dict[str, Any]] = [
        {"phrase": "How are you", "reply": "Let me check.", "action": "status.report"},
        {"phrase": "Let's talk", "reply": "Nice to talk to you.", "action": "enter.llm"},
    ]
    cfg = load_voice_config()
    cfg["script"] = demo
    save_voice_config(cfg)
    voice_log(f"VOICE_CTL: script set demo_len={len(demo)}")
    print("ok")


def main() -> None:
    if len(sys.argv) < 2:
        _die(__doc__.strip())

    if sys.argv[1] == "show":
        cmd_show()
        return

    if sys.argv[1] == "mode" and len(sys.argv) >= 3:
        cmd_mode(sys.argv[2])
        return

    if sys.argv[1] == "script" and len(sys.argv) >= 3:
        sub = sys.argv[2]
        if sub == "clear":
            cmd_script_clear()
            return
        if sub == "demo":
            cmd_script_demo()
            return

    _die(__doc__.strip())


if __name__ == "__main__":
    main()
