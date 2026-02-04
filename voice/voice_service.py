#!/usr/bin/env python3
"""
Wave-2 Voice Service (skeleton)

- Long-running process controlled by systemd (scanner-voice.service)
- Maintains a current mode from voice_config.json:
    deaf | name_listen | conversation | llm_dummy
- For Wave-2: no microphone input; we only prove state machine + scripted test output
- Provides a periodic "heartbeat" log so we know it's alive.

Control plane:
- agent.py will update voice_config.json and (re)start/stop this service.
"""

from __future__ import annotations

import time
from typing import Dict, Any

from voice_common import (
    read_identity,
    load_voice_config,
    save_voice_config,
    voice_log,
)

HEARTBEAT_SEC = 10


def _sanitize_mode(mode: str) -> str:
    m = (mode or "").strip()
    if m in ("deaf", "name_listen", "conversation", "llm_dummy"):
        return m
    return "deaf"


def main() -> None:
    ident = read_identity() or "UNKNOWN"
    voice_log(f"VOICE: service start identity='{ident}'")

    last_hb = 0.0
    last_mode = None

    # Ensure config exists / has defaults
    cfg: Dict[str, Any] = load_voice_config()
    cfg["mode"] = _sanitize_mode(cfg.get("mode", "deaf"))
    save_voice_config(cfg)

    while True:
        cfg = load_voice_config()
        mode = _sanitize_mode(cfg.get("mode", "deaf"))

        # Log mode transition
        if mode != last_mode:
            voice_log(f"VOICE: mode -> {mode}")
            last_mode = mode

        # Heartbeat
        now = time.time()
        if now - last_hb >= HEARTBEAT_SEC:
            voice_log(
                f"VOICE: heartbeat mode={mode} script_len={len(cfg.get('script') or [])} "
                f"conv_to={cfg.get('conversation_timeout_sec')} llm_to={cfg.get('llm_timeout_sec')}"
            )
            last_hb = now

        # Wave-2: no mic loop here; just sleep
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
