#!/usr/bin/env python3
"""
Wave-1 TTS Integration Test (standalone)

Flow:
  STEP 0  : enter_test_mode.sh
  STEP 0B : sudo -n warmup (must not prompt)
  STEP 0C : LOCAL sanity: run av/tts_say.sh directly (you should hear speech)
  STEP 1  : Discover NMS + GET /health
  STEP 2  : POST /admin/_reset  (operator-only; keeps whitelist/bundles/autoflush)
  STEP 3  : POST /registry/register (Pi-facing)
  STEP 4  : Start scanner-agent.service (temporarily)
  STEP 5  : Enqueue tts.say (operator-only /cmd/_enqueue/{scanner})
  STEP 6  : Verify agent.log shows RESULT for this cmd_id with status=ok + 'tts.say ok'
  STEP 7  : Stop scanner-agent.service (cleanup)
  STEP 8  : exit_test_mode.sh
"""

import sys
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

# ---------------------------------------------------------------------
# Single source of truth: config.py (ensure import works no matter cwd)
# ---------------------------------------------------------------------
BASE_DIR = Path("/home/pi/_RunScanner")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
import config  # noqa: E402

TEST_DIR = config.BASE_DIR / "TestCodes"
ENTER_SH = TEST_DIR / "enter_test_mode.sh"
EXIT_SH  = TEST_DIR / "exit_test_mode.sh"

HTTP_TIMEOUT_SEC = 12.0
TEST_IP = "192.168.137.2"  # your lab convention

AGENT_LOG = BASE_DIR / "agent.log"
TTS_SCRIPT = BASE_DIR / "av" / "tts_say.sh"


# =============================================================================
# Helpers
# =============================================================================

def die(msg: str, code: int = 2) -> None:
    print(f"\n[FATAL] {msg}\n")
    raise SystemExit(code)

def pause(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    input("Press ENTER to continue (or Ctrl+C to stop) ... ")

def jprint(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))

def ok_or_dump(r: requests.Response) -> None:
    print(f"[HTTP] {r.request.method} {r.url}")
    print(f"[HTTP] status={r.status_code}")
    ctype = r.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            jprint(r.json())
        except Exception:
            print((r.text or "")[:1500])
    else:
        t = (r.text or "")
        print(t[:1500] if t else f"[HTTP] (no text) content-length={len(r.content or b'')}")

def _run_sh(path: Path) -> None:
    if not path.exists():
        die(f"Missing script: {path}")
    subprocess.run(["/usr/bin/bash", str(path)], check=False, text=True)

def _run_cmd(cmd_list, timeout=10) -> Tuple[bool, str, str]:
    try:
        cp = subprocess.run(
            cmd_list,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        return (cp.returncode == 0), out, err
    except Exception as e:
        return False, "", str(e)

def sudo_warmup() -> None:
    ok, out, err = _run_cmd([config.SUDO, "-n", "true"], timeout=5)
    if not ok:
        die("sudo -n is not permitted for this user. Fix sudoers; test will not prompt.")
    print("[OK] sudo -n works (no password required).")

def systemctl_start(service: str) -> None:
    ok, out, err = _run_cmd([config.SUDO, "-n", config.SYSTEMCTL, "start", service], timeout=10)
    if not ok:
        die(f"Failed to start {service}: {err or out}")
    print(f"[OK] started {service}")

def systemctl_stop(service: str) -> None:
    ok, out, err = _run_cmd([config.SUDO, "-n", config.SYSTEMCTL, "stop", service], timeout=10)
    if not ok:
        die(f"Failed to stop {service}: {err or out}")
    print(f"[OK] stopped {service}")

def tail_agent_log(n: int = 120) -> str:
    if not AGENT_LOG.exists():
        return f"[tail] missing: {AGENT_LOG}"
    ok, out, err = _run_cmd(["/usr/bin/tail", "-n", str(n), str(AGENT_LOG)], timeout=6)
    return out if ok else (err or out or "(tail failed)")

def wait_for_cmd_result(cmd_id: str, timeout_sec: int = 35) -> Tuple[bool, str]:
    """
    Wait for agent.log to contain a RESULT line for this cmd_id.
    Success criteria:
      - line contains: 'RESULT cmd_id=<id> status=ok' AND 'tts.say ok'
    """
    t0 = time.time()
    last_tail = ""
    needle_ok = f"RESULT cmd_id={cmd_id} status=ok"
    while time.time() - t0 < timeout_sec:
        last_tail = tail_agent_log(200)
        if needle_ok in last_tail and "tts.say ok" in last_tail:
            return True, last_tail
        if f"RESULT cmd_id={cmd_id} status=error" in last_tail:
            return False, last_tail
        time.sleep(2)
    return False, last_tail


# =============================================================================
# NMS request helpers
# =============================================================================

def req(nms_base: str, method: str, path: str, **kwargs) -> requests.Response:
    url = f"{nms_base}{path}"
    return requests.request(method, url, timeout=HTTP_TIMEOUT_SEC, **kwargs)

def get_health(nms_base: str) -> Dict[str, Any]:
    r = req(nms_base, "GET", "/health")
    ok_or_dump(r)
    if r.status_code != 200:
        die("health check failed")
    j = r.json()
    if not isinstance(j, dict):
        die("health JSON not dict")
    return j

def admin_reset_keep_all(nms_base: str) -> None:
    body = {
        "confirm": "RESET",
        "keep_whitelist": True,
        "keep_bundles": True,
        "keep_autoflush_flag": True,
    }
    r = req(nms_base, "POST", "/admin/_reset", json=body)
    ok_or_dump(r)
    if r.status_code != 200:
        die("/admin/_reset failed (expected 200 OK)")
    print("[OK] admin reset succeeded (whitelist preserved)")

def pi_register(nms_base: str, mac: str, ip: Optional[str], scanner_version: str) -> str:
    body = {
        "mac": mac,
        "ip": ip,
        "scanner_version": scanner_version,
        "capabilities": "scan,av,tts",
    }
    r = req(nms_base, "POST", "/registry/register", json=body)
    ok_or_dump(r)
    if r.status_code != 200:
        die("Pi register failed")
    scanner = (r.text or "").strip()
    if not scanner:
        die("Pi register returned empty scanner name")
    return scanner

def op_cmd_enqueue(nms_base: str, scanner: str, execute_at: str,
                   category: str, action: str, args: Dict[str, Any]) -> str:
    body = {
        "category": category,
        "action": action,
        "execute_at": execute_at,
        "args_json_text": json.dumps(args),
    }
    r = req(nms_base, "POST", f"/cmd/_enqueue/{scanner}", json=body)
    ok_or_dump(r)
    if r.status_code != 200:
        die("cmd enqueue failed")
    j = r.json()
    cmd_id = (j.get("cmd_id") or "").strip()
    if not cmd_id:
        die("enqueue returned no cmd_id")
    return cmd_id


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    nms_base: Optional[str] = None
    assigned: Optional[str] = None

    try:
        pause("STEP 0: Enter test mode (stop/disable services + disable GUI autostart)")
        _run_sh(ENTER_SH)

        pause("STEP 0B: sudo warm-up (sudo -n true)")
        sudo_warmup()

        pause("STEP 0C: LOCAL sanity: run tts_say.sh directly (you SHOULD hear this)")
        if not TTS_SCRIPT.exists():
            die(f"Missing TTS script: {TTS_SCRIPT}")
        text_local = "Front center. Local TTS sanity check. You should hear this clearly."
        print(f"[INFO] running: bash {TTS_SCRIPT} <text> 500 95")
        ok, out, err = _run_cmd(
            ["/usr/bin/bash", str(TTS_SCRIPT), text_local, "500", "95"],
            timeout=45
        )
        if not ok:
            print("[LOCAL TTS] stdout:\n", out)
            print("[LOCAL TTS] stderr:\n", err)
            die("Local TTS sanity failed. Fix local audio/TTS first before NMS command test.")
        print("[OK] Local TTS sanity passed (script returned rc=0).")

        pause("STEP 1: Discover NMS + GET /health")
        nms_base = config.discover_nms_base(force=True)
        if not nms_base:
            die("No reachable NMS found (discover_nms_base returned None)")
        print(f"[INFO] NMS_BASE={nms_base}")
        health = get_health(nms_base)
        server_now = health.get("time")
        if not isinstance(server_now, str) or not server_now:
            die("health missing 'time'")
        print(f"[INFO] server_now={server_now}")

        pause("STEP 2: POST /admin/_reset (operator-only) (keeps whitelist/bundles/autoflush)")
        admin_reset_keep_all(nms_base)

        pause("STEP 3: POST /registry/register (Pi-facing)")
        iface = config.get_reg_iface()
        mac = config.get_mac_address(iface)
        if not mac:
            die(f"Could not read MAC from iface={iface}")
        bundle_ver = config.get_bundle_version()
        assigned = pi_register(nms_base, mac=mac, ip=TEST_IP, scanner_version=bundle_ver)
        print(f"[OK] register assigned scanner={assigned} mac={mac} ip={TEST_IP} bundle_ver={bundle_ver}")

        pause("STEP 4: Start scanner-agent.service (temporarily for this test)")
        systemctl_start("scanner-agent.service")
        time.sleep(1.0)

        pause("STEP 5: Enqueue tts.say (operator-only) -> you SHOULD hear it")
        tts_args = {
            "text": (
                "Front center. This is an NMS TTS test sentence long enough to hear clearly. "
                "If you hear this, TTS is working end to end."
            ),
            "lead_silence_ms": 600,
            "volume": 95,
        }
        cmd_id = op_cmd_enqueue(
            nms_base=nms_base,
            scanner=assigned,
            execute_at=server_now,
            category="av",
            action="tts.say",
            args=tts_args,
        )
        print(f"[OK] enqueued cmd_id={cmd_id} action=tts.say text_len={len(tts_args['text'])}")

        pause("STEP 6: Verify agent.log shows RESULT for this cmd_id with status=ok + 'tts.say ok'")
        ok_res, tail = wait_for_cmd_result(cmd_id, timeout_sec=45)
        print("\n--- tail agent.log ---")
        print(tail)
        print("--- end tail ---\n")
        if not ok_res:
            die("tts.say did not complete with ok. See agent.log tail above.")

        print("[OK] tts.say end-to-end succeeded (command -> agent -> tts_say.sh -> audio).")

        pause("STEP 7: Stop scanner-agent.service (cleanup)")
        systemctl_stop("scanner-agent.service")

        pause("DONE: TTS test complete.")
        return 0

    finally:
        print("\n[FINAL] Exit test mode (restore GUI autostart).")
        try:
            _run_sh(EXIT_SH)
        except Exception as e:
            print(f"[WARN] exit_test_mode.sh failed: {e}")

if __name__ == "__main__":
    raise SystemExit(main())
