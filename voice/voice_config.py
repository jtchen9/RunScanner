#!/usr/bin/env python3
"""
voice_config.py

Owns voice_config.json persistence:
- paths
- defaults
- atomic load/save
- read-modify-write update

No dependency on voice_common paths (avoids entanglement).
Logging is best-effort.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

# --- Paths owned here ---
BASE_DIR = Path("/opt/_RunScanner")
VOICE_DIR = BASE_DIR / "voice"
VOICE_CFG_FILE = VOICE_DIR / "voice_config.json"

# --- Defaults owned here ---
DEFAULT_CFG: Dict[str, Any] = {
    "mode": "deaf",
    "conversation_timeout_sec": 20,
    "llm_timeout_sec": 30,
    "script": [],
}

def _log(msg: str) -> None:
    # best-effort: avoid hard dependency / circular import
    try:
        from voice_common import voice_log  # keep your current absolute-import style
        voice_log(msg, also_print=False)
    except Exception:
        pass

def ensure_voice_config() -> None:
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if not VOICE_CFG_FILE.exists():
        save_voice_config(dict(DEFAULT_CFG))

def load_voice_config() -> Dict[str, Any]:
    """
    Best-effort load. If missing/corrupt, recreate defaults.
    """
    ensure_voice_config()
    try:
        obj = json.loads(VOICE_CFG_FILE.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("voice_config.json not dict")

        cfg = dict(DEFAULT_CFG)
        cfg.update(obj)

        if not isinstance(cfg.get("script"), list):
            cfg["script"] = []

        return cfg
    except Exception as e:
        _log(f"voice_config load failed: {type(e).__name__}: {e}")
        save_voice_config(dict(DEFAULT_CFG))
        return dict(DEFAULT_CFG)

def save_voice_config(cfg: Dict[str, Any]) -> None:
    """
    Atomic save. Best-effort.
    """
    ensure_voice_config()

    out = dict(DEFAULT_CFG)
    out.update(cfg or {})

    if not isinstance(out.get("script"), list):
        out["script"] = []

    try:
        tmp = VOICE_CFG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(VOICE_CFG_FILE)
    except Exception as e:
        _log(f"voice_config save failed: {type(e).__name__}: {e}")

def update_voice_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read-modify-write update (returns new cfg).
    """
    cur = load_voice_config()
    cur.update(patch or {})
    save_voice_config(cur)
    return cur

# --- Compatibility aliases (so you don't have to edit all callers today) ---
def default_config() -> Dict[str, Any]:
    return dict(DEFAULT_CFG)

def load_config() -> Dict[str, Any]:
    return load_voice_config()

def save_config(cfg: Dict[str, Any]) -> None:
    save_voice_config(cfg)
