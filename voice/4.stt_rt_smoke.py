#!/usr/bin/env python3
"""
Step-1 smoke test:
- Runs RT STT loop
- Detects any robot name (or just this robot)
"""

from pathlib import Path
from voice_common import read_identity, voice_log
from voice_rt_stt import run_rt_match_loop


def main():
    ident = read_identity()
    voice_log(f"SMOKE_RT: identity='{ident}'")

    # Minimal cfg: adjust to your known-good mic setup if needed
    cfg = {
        "vosk_model_dir": "/home/pi/_RunScanner/voice/models/vosk-model-small-en-us-0.15",
        "mic_dev": "plughw:1,0",
        "sample_rate": 16000,
        "channels": 1,
        "chunk_sec": 2,
    }

    # Option A: match only this robot’s own name (recommended for NAME_LISTEN)
    robot_names = [ident]

    # Option B: match any robot name (useful for debugging)
    # robot_names = build_robot_names()

    ok, ev, detail = run_rt_match_loop(
        cfg,
        kind="name",
        robot_names=robot_names,
        max_sec=30,
    )

    voice_log(f"SMOKE_RT: ok={ok} detail={detail}")
    if ev:
        voice_log(f"SMOKE_RT: MATCH kind={ev.kind} matched='{ev.matched}' raw='{ev.text_raw}'")

if __name__ == "__main__":
    main()
