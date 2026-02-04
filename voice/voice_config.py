#!/usr/bin/env python3
"""
Wave-2 Voice Configuration

Stores config as JSON at:
  /home/pi/_RunScanner/voice/voice_config.json

Atomic write to avoid partial files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from voice_common import VOICE_DIR, voice_log


VOICE_CFG_FILE = VOICE_DIR / "voice_config.json"


def default_config() -> Dict[str, Any]:
    # Keep it minimal for Step-1; we will extend in later steps.
    return {
        "mode": "deaf",  # deaf | name_listen | conversation | llm(dummy)
        "conversation_timeout_sec": 20,
        "llm_timeout_sec": 30,
        "script": [],  # list of {"phrase": "...", "reply": "...", "action": "..."}  (Wave-2 scripted)
    }


def load_config() -> Dict[str, Any]:
    """
    Best-effort load. If file missing/corrupt, returns defaults.
    """
    if not VOICE_CFG_FILE.exists():
        return default_config()

    try:
        obj = json.loads(VOICE_CFG_FILE.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return default_config()

        # Merge with defaults so new keys appear automatically.
        cfg = default_config()
        cfg.update(obj)
        # Basic sanity
        if not isinstance(cfg.get("script"), list):
            cfg["script"] = []
        return cfg
    except Exception as e:
        voice_log(f"voice_config load failed: {type(e).__name__}: {e}", also_print=False)
        return default_config()


def save_config(cfg: Dict[str, Any]) -> None:
    """
    Atomic save. Best-effort (never raises).
    """
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        tmp = VOICE_CFG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(VOICE_CFG_FILE)
    except Exception as e:
        voice_log(f"voice_config save failed: {type(e).__name__}: {e}", also_print=False)
