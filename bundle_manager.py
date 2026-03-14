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
    BASE_DIR,
    BUNDLES_DIR,
    ACTIVE_BUNDLE_FILE,
    SYSTEMCTL,
    SUDO,
)

HTTP_TIMEOUT = 30
BUNDLE_LOG_PATH = BASE_DIR / "bundle_apply.log"

def _blog(msg: str) -> None:
    line = f"[bundle] {msg}"
    print(line, flush=True)
    try:
        with BUNDLE_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

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
    _blog(f"run install hook: {hook}")

    try:
        with BUNDLE_LOG_PATH.open("a", encoding="utf-8") as logf:
            logf.write(f"[bundle] install.sh stdout/stderr begin\n")
            cp = subprocess.run(
                ["/usr/bin/bash", str(hook)],
                check=True,
                stdout=logf,
                stderr=logf,
                text=True,
                timeout=300,
            )
            logf.write(f"[bundle] install.sh stdout/stderr end rc={cp.returncode}\n")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"install.sh failed rc={e.returncode}")


def apply_bundle(bundle_id: str, url: str) -> Tuple[bool, str]:
    """
    Apply a bundle specified by (bundle_id, url).

    Returns:
        (ok, detail)
    """
    try:
        BUNDLES_DIR.mkdir(parents=True, exist_ok=True)
        _blog(f"START bundle_id={bundle_id} url={url}")

        # 1) Download first (safe phase)
        tmp_zip = Path("/tmp") / f"{bundle_id}.zip"
        if tmp_zip.exists():
            _blog(f"remove old tmp zip: {tmp_zip}")
            tmp_zip.unlink()

        _blog(f"download begin -> {tmp_zip}")
        _download_bundle(url, tmp_zip)
        _blog(f"download ok size={tmp_zip.stat().st_size} path={tmp_zip}")

        # 2) Only after successful download, enter disruptive phase
        _blog("stop_robot_services begin")
        stop_robot_services()
        _blog("stop_robot_services done")

        # 3) Extract into bundles/{bundle_id}
        bundle_dir = BUNDLES_DIR / bundle_id
        if bundle_dir.exists():
            _blog(f"remove old bundle dir: {bundle_dir}")
            subprocess.run(["rm", "-rf", str(bundle_dir)], check=False)

        _blog(f"extract begin -> {bundle_dir}")
        _extract_zip(tmp_zip, bundle_dir)
        _blog(f"extract done -> {bundle_dir}")

        # Support nested layout if zip contains top folder twice
        if not (bundle_dir / "install.sh").exists():
            nested = bundle_dir / bundle_id
            if nested.exists() and (nested / "install.sh").exists():
                _blog(f"nested bundle layout detected -> {nested}")
                bundle_dir = nested

        _blog(f"effective bundle_dir={bundle_dir}")

        # 4) Record active bundle id
        ACTIVE_BUNDLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_BUNDLE_FILE.write_text(bundle_id + "\n", encoding="utf-8")
        _blog(f"active bundle written -> {ACTIVE_BUNDLE_FILE}")

        # 5) Real deployment
        _blog("install hook begin")
        _run_install_hook(bundle_dir)
        _blog("install hook returned normally")

        return True, f"bundle applied: {bundle_id}"

    except Exception as e:
        _blog(f"ERROR {type(e).__name__}: {e}")
        return False, f"bundle apply failed: {type(e).__name__}: {e}"
