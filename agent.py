#!/usr/bin/env python3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VOICE_DIR = BASE_DIR / "voice"

# Match the effective import environment used by scanner-agent.service
for p in (str(BASE_DIR), str(VOICE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from robot_agent import main


if __name__ == "__main__":
    main()