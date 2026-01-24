#!/usr/bin/env python3
"""
Bundle manager (Pi-side, production).

Responsibilities:
- Stop running services (highest priority)
- Download bundle ZIP from NMS: GET /bootstrap/bundle/{bundle_id}
- Extract into bundles/{bundle_id}
- Switch active symlink
- Write active_bundle.txt (sole source of truth)
- Run install hook (optional)
- Restart uploader service

No rollback. No version arbitration. Pi is dumb by design.
"""

import subprocess
import zipfile
from pathlib import Path
from typing import Tuple

import requests

BASE_DIR = Path("/home/pi/_RunScanner")
BUNDLES_DIR = BASE_DIR / "bundles"
ACTIVE_LINK = BUNDLES_DIR / "active"
ACTIVE_BUNDLE_FILE = BUNDLES_DIR / "active_bundle.txt"

SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"

SERVICE_SCAN = "scanner-poller.service"
SERVICE_UPLOADER = "scanner-uploader.service"

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
    # best-effort; try normal then sudo -n
    _run([SYSTEMCTL, action, service])
    _run([SUDO, "-n", SYSTEMCTL, action, service])


def stop_all_services() -> None:
    _systemctl("stop", SERVICE_SCAN)
    _systemctl("stop", SERVICE_UPLOADER)


def restart_uploader() -> None:
    _systemctl("restart", SERVICE_UPLOADER)


def _download_bundle(nms_base: str, bundle_id: str, dst_zip: Path) -> None:
    url = f"{nms_base}/bootstrap/bundle/{bundle_id}"
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
        return

    hook.chmod(0o755)
    ok, out = _run(["/usr/bin/bash", str(hook)], timeout=180)
    if not ok:
        raise RuntimeError(f"install.sh failed: {out}")


def _read_prev_bundle_id() -> str:
    try:
        s = ACTIVE_BUNDLE_FILE.read_text(encoding="utf-8").strip()
        return s if s else "0"
    except Exception:
        return "0"


def apply_bundle(nms_base: str, bundle_id: str) -> Tuple[bool, str, str]:
    """
    Main entry.
    Returns (ok, detail, prev_bundle_id)
    """
    prev = _read_prev_bundle_id()

    try:
        BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

        # 1) HARD STOP (highest priority)
        stop_all_services()

        # 2) Download
        tmp_zip = Path("/tmp") / f"{bundle_id}.zip"
        if tmp_zip.exists():
            tmp_zip.unlink()

        _download_bundle(nms_base, bundle_id, tmp_zip)

        # 3) Extract
        bundle_dir = BUNDLES_DIR / bundle_id
        if bundle_dir.exists():
            return False, f"bundle already exists: {bundle_id}", prev

        _extract_zip(tmp_zip, bundle_dir)

        # 4) Activate (atomic)
        if ACTIVE_LINK.exists() or ACTIVE_LINK.is_symlink():
            ACTIVE_LINK.unlink()
        ACTIVE_LINK.symlink_to(bundle_dir)

        ACTIVE_BUNDLE_FILE.write_text(bundle_id + "\n", encoding="utf-8")

        # 5) Install hook
        _run_install_hook(bundle_dir)

        # 6) Restart uploader only
        restart_uploader()

        return True, f"bundle applied: {bundle_id}", prev

    except Exception as e:
        return False, f"bundle apply failed: {type(e).__name__}: {e}", prev
