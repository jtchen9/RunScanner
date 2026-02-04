#!/usr/bin/env python3
"""
Step-1 smoke test:
- reads identity from /home/pi/_RunScanner/scanner_name.txt
- writes to /home/pi/_RunScanner/voice/voice.log
- loads voice_config.json (or defaults)
"""

from __future__ import annotations

from voice_common import read_identity, voice_log
from voice_config import load_config, save_config


def main() -> None:
    ident = read_identity()
    voice_log(f"SMOKE: identity='{ident or 'UNKNOWN'}'")

    cfg = load_config()
    voice_log(f"SMOKE: loaded config mode={cfg.get('mode')} conv_to={cfg.get('conversation_timeout_sec')} llm_to={cfg.get('llm_timeout_sec')} script_len={len(cfg.get('script') or [])}")

    # Ensure a config file exists (write defaults on first run)
    if ident and cfg:
        save_config(cfg)
        voice_log("SMOKE: ensured voice_config.json exists")

    print("\n--- SUMMARY ---")
    print(f"identity: {ident!r}")
    print(f"config: {cfg}")


if __name__ == "__main__":
    main()
