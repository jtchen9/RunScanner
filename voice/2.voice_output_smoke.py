#!/usr/bin/env python3
from __future__ import annotations

from voice_common import read_identity, voice_log
from voice_output import test_prompt

def main() -> None:
    ident = read_identity() or "UNKNOWN"
    voice_log(f"OUTPUT_SMOKE: identity='{ident}'")

    # Beep + TTS
    test_prompt(
        say_text=f"Hello. This is {ident}. Voice output smoke test is running.",
        do_beep=True
    )

if __name__ == "__main__":
    main()
