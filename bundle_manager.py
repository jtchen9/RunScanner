#!/usr/bin/env python3
"""
Bundle manager (Pi-side, production).

Responsibilities:
- Stop running robot services
- Download bundle ZIP from provided URL
- Extract into bundles/{bundle_id}
- Write active_bundle.txt (sole source of truth)
- Run install hook (install.sh)

install.sh is responsible for:
- copying files into /opt/_RunScanner
- installing/updating systemd unit files
- installing/updating sudoers file
- installing/updating GUI autostart file
- daemon-reload
- enable/restart policy

No rollback. No version arbitration. Pi is dumb by design.
"""

import subprocess
import zipfile
from pathlib import Path
from typing import Tuple

import requests

from config import (
    BUNDLES_DIR,
    ACTIVE_BUNDLE_FILE,
    SYSTEMCTL,
    SUDO,
)

HTTP_TIMEOUT = 30


def _run(cmd, timeout=30) -> Tuple[bool, str]:
    try:
        cp = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return True, (cp.stdout or "").strip()
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or "").strip()


def _systemctl(action: str, service: str) -> None:
    _run([SYSTEMCTL, action, service])
    _run([SUDO, "-n", SYSTEMCTL, action, service])


def stop_robot_services() -> None:
    services = [
        "scanner-agent.service",
        "scanner-uploader.service",
        "scanner-poller.service",
        "scanner-voice.service",
        "scanner-avstream.service",
    ]
    for svc in services:
        _systemctl("stop", svc)


def _download_bundle(url: str, dst_zip: Path) -> None:
    r = requests.get(url, stream=True, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    with dst_zip.open("wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def _extract_zip(src_zip: Path, dst_dir: Path) -> None:
    with zipfile.ZipFile(src_zip, "r") as zf:
        zf.extractall(dst_dir)


def _run_install_hook(bundle_dir: Path) -> None:
    hook = bundle_dir / "install.sh"
    if not hook.exists():
        raise RuntimeError("install.sh missing in bundle")

    hook.chmod(0o755)
    ok, out = _run(["/usr/bin/bash", str(hook)], timeout=300)
    if not ok:
        raise RuntimeError(f"install.sh failed: {out}")


def apply_bundle(bundle_id: str, url: str) -> Tuple[bool, str]:
    """
    Apply a bundle specified by (bundle_id, url).

    Returns:
        (ok, detail)
    """
    try:
        BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

        # 1) HARD STOP
        stop_robot_services()

        # 2) Download
        tmp_zip = Path("/tmp") / f"{bundle_id}.zip"
        if tmp_zip.exists():
            tmp_zip.unlink()

        _download_bundle(url, tmp_zip)

        # 3) Extract into bundles/{bundle_id}
        bundle_dir = BUNDLES_DIR / bundle_id
        if bundle_dir.exists():
            subprocess.run(["rm", "-rf", str(bundle_dir)], check=False)

        _extract_zip(tmp_zip, bundle_dir)

        # Support nested layout if zip contains top folder twice
        if not (bundle_dir / "install.sh").exists():
            nested = bundle_dir / bundle_id
            if nested.exists() and (nested / "install.sh").exists():
                bundle_dir = nested

        # 4) Record active bundle id
        ACTIVE_BUNDLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_BUNDLE_FILE.write_text(bundle_id + "\n", encoding="utf-8")

        # 5) Real deployment
        _run_install_hook(bundle_dir)

        return True, f"bundle applied: {bundle_id}"

    except Exception as e:
        return False, f"bundle apply failed: {type(e).__name__}: {e}"
    