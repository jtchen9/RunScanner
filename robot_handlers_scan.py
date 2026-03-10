#!/usr/bin/env python3
import subprocess
from typing import Tuple, List

from config import (
    BASE_DIR,
    SYSTEMCTL,
    SUDO,
    SERVICE_SCANNER_POLLER,
)

SCAN_SCRIPT = str(BASE_DIR / "scan_wifi.sh")


def _run_systemctl(args: List[str]) -> Tuple[bool, str, str]:
    """Run systemctl. Try without sudo first; if that fails, retry with sudo -n."""
    try:
        cp = subprocess.run(
            [SYSTEMCTL] + args,
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return True, (cp.stdout or "").strip(), (cp.stderr or "").strip()
    except subprocess.CalledProcessError as e1:
        try:
            cp2 = subprocess.run(
                [SUDO, "-n", SYSTEMCTL] + args,
                check=True,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            )
            return True, (cp2.stdout or "").strip(), (cp2.stderr or "").strip()
        except subprocess.CalledProcessError as e2:
            return False, (e2.stdout or "").strip(), (e2.stderr or e1.stderr or "").strip()


def exec_scan_start() -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["start", SERVICE_SCANNER_POLLER])
    return (True, "started scanner-poller.service") if ok else (False, f"start failed: {err or out}")


def exec_scan_stop() -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["stop", SERVICE_SCANNER_POLLER])
    return (True, "stopped scanner-poller.service") if ok else (False, f"stop failed: {err or out}")


def exec_scan_once() -> Tuple[bool, str]:
    """Run one scan immediately (does not rely on systemd service)."""
    try:
        cp = subprocess.run(
            ["/usr/bin/bash", SCAN_SCRIPT, "once"],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=40,
        )
        if cp.returncode == 0:
            return True, "scan_once ok"
        return False, f"scan_once rc={cp.returncode} stderr={((cp.stderr or '')[:200]).strip()}"
    except Exception as e:
        return False, f"scan_once exception: {type(e).__name__}: {e}"
    