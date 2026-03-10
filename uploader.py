#!/usr/bin/env python3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
for p in (str(BASE_DIR),):
    if p not in sys.path:
        sys.path.insert(0, p)

from robot_uploader import main


if __name__ == "__main__":
    main()
    