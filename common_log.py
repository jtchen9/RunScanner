#!/usr/bin/env python3
from pathlib import Path
from typing import Callable


def append_log_line(
    log_path: Path,
    msg: str,
    ts_func: Callable[[], str],
    ensure_parent: bool = False,
    also_print: bool = True,
) -> str:
    """
    Shared timestamped logger.

    Behavior is intentionally simple and close to the old code:
    - format line as: [<timestamp>] <msg>
    - optionally print to stdout
    - optionally mkdir parent before append
    - never raise on file write failure

    Returns the final formatted line.
    """
    line = f"[{ts_func()}] {msg}"

    if also_print:
        print(line, flush=True)

    try:
        if ensure_parent:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    return line