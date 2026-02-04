#!/usr/bin/env python3
"""
4.VoiceWave2Test.py
End-to-end Wave-2 Voice test (operator-side script running on Pi).

Covers (in one go):
- Start scanner-agent.service temporarily
- Enqueue 4 voice commands to NMS (with execute_at)
- Wait for agent execution
- Verify via agent.log + systemctl/journalctl
- Cleanup (stop agent + stop voice service)

Assumptions:
- Identity is stored in /home/pi/_RunScanner/scanner_name.txt, e.g. "twin-scout-alpha"
- NMS endpoint: POST {NMS_BASE}/cmd/_enqueue/{identity}
- NMS requires execute_at in body (TIME_FMT "%Y-%m-%d-%H:%M:%S")
- agent.py dispatch supports category "voice" and actions:
    voice.start, voice.stop, voice.mode.set, voice.script.set
- scanner-voice.service exists and runs /home/pi/_RunScanner/voice/voice_service.py
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

import requests

BASE_DIR = Path("/home/pi/_RunScanner")
VOICE_DIR = BASE_DIR / "voice"
AGENT_LOG = BASE_DIR / "agent.log"
SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
NMS_BASE_FILE = BASE_DIR / "nms_base.txt"

TIME_FMT = "%Y-%m-%d-%H:%M:%S"

HTTP_TIMEOUT_SEC = 10
WAIT_AGENT_SEC = 45  # total wait for agent to execute all 4 commands
POLL_INTERVAL = 2

SERVICE_AGENT = "scanner-agent.service"
SERVICE_VOICE = "scanner-voice.service"


def now_ts() -> str:
    return datetime.now().strftime(TIME_FMT)


def ts_plus(seconds: int) -> str:
    return (datetime.now() + timedelta(seconds=seconds)).strftime(TIME_FMT)


def sh(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def read_identity() -> str:
    try:
        s = SCANNER_NAME_FILE.read_text(encoding="utf-8").strip()
        return s
    except Exception:
        return ""


def get_nms_base() -> str:
    # Priority: env > file > fallback
    env = (os.getenv("NMS_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    try:
        s = NMS_BASE_FILE.read_text(encoding="utf-8").strip()
        if s:
            return s.rstrip("/")
    except Exception:
        pass
    # common lab default
    return "http://192.168.137.1:8000"


def tail_file(path: Path, n: int = 80) -> str:
    try:
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


def print_step(title: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n{title}\n{bar}", flush=True)


def pause() -> None:
    input("Press ENTER to continue (or Ctrl+C to stop) ... ")


def systemctl(*args: str) -> tuple[int, str, str]:
    cp = sh(["systemctl", *args], check=False)
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def sudo_systemctl(*args: str) -> tuple[int, str, str]:
    cp = sh(["sudo", "systemctl", *args], check=False)
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def ensure_services_clean() -> None:
    # Best-effort: stop voice, reset failed state (avoids "start request repeated too quickly")
    sudo_systemctl("stop", SERVICE_VOICE)
    sudo_systemctl("reset-failed", SERVICE_VOICE)


def start_agent_temporarily() -> None:
    # Start agent (do not enable permanently here)
    rc, out, err = sudo_systemctl("start", SERVICE_AGENT)
    if rc != 0:
        raise RuntimeError(f"failed to start {SERVICE_AGENT}: {err or out}")


def stop_agent_cleanup() -> None:
    sudo_systemctl("stop", SERVICE_AGENT)


def enqueue(nms_base: str, identity: str, action: str, args_obj: dict) -> str:
    url = f"{nms_base}/cmd/_enqueue/{identity}"
    body = {
        "category": "voice",
        "action": action,
        "execute_at": ts_plus(5),  # execute soon, but not immediately
        "args": args_obj,
    }
    r = requests.post(url, json=body, timeout=HTTP_TIMEOUT_SEC)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:200]}
    if r.status_code != 200:
        raise RuntimeError(f"enqueue failed http={r.status_code} body={json.dumps(data, indent=2)}")

    cmd_id = (data.get("cmd_id") or "").strip()
    if not cmd_id:
        raise RuntimeError(f"enqueue ok but missing cmd_id: {json.dumps(data, indent=2)}")
    print(f"[OK] enqueued cmd_id={cmd_id} action={action}")
    return cmd_id


def wait_for_agent_results(cmd_ids: list[str]) -> None:
    deadline = time.time() + WAIT_AGENT_SEC
    remaining = set(cmd_ids)

    while time.time() < deadline and remaining:
        txt = tail_file(AGENT_LOG, n=250)
        # Consider a cmd done if we see "RESULT cmd_id=... status="
        done = set()
        for cid in list(remaining):
            if f"RESULT cmd_id={cid} " in txt:
                done.add(cid)
        remaining -= done
        if remaining:
            time.sleep(POLL_INTERVAL)

    if remaining:
        print("\n--- tail agent.log ---")
        print(tail_file(AGENT_LOG, n=120))
        raise RuntimeError(f"did not observe RESULT lines for cmd_id(s): {sorted(remaining)}")


def show_voice_service_status() -> None:
    print("\n--- systemctl status scanner-voice.service ---")
    cp = sh(["systemctl", "status", SERVICE_VOICE, "--no-pager", "-l"], check=False)
    print((cp.stdout or "").rstrip())
    if cp.stderr:
        print((cp.stderr or "").rstrip())

    print("\n--- journalctl -u scanner-voice.service (last 80) ---")
    cp2 = sh(["sudo", "journalctl", "-u", SERVICE_VOICE, "-n", "80", "--no-pager"], check=False)
    print((cp2.stdout or "").rstrip())
    if cp2.stderr:
        print((cp2.stderr or "").rstrip())


def main() -> int:
    identity = read_identity()
    if not identity:
        print("[FATAL] scanner_name.txt missing/empty. Identity required.")
        return 2

    nms_base = get_nms_base()
    print(f"[INFO] now={now_ts()} identity='{identity}' nms_base={nms_base}")

    # Wave-2 script payload
    commands = [
        {"phrase": "How are you", "reply": "I am fine", "action": "status.report"},
        {"phrase": "Lets talk", "reply": "OK", "action": "enter.llm"},
    ]

    try:
        print_step("STEP 0: Clean slate (stop voice service + reset-failed)")
        ensure_services_clean()
        show_voice_service_status()
        pause()

        print_step("STEP 1: Start scanner-agent.service (temporarily for this test)")
        start_agent_temporarily()
        print("[OK] started scanner-agent.service")
        pause()

        print_step("STEP 2: Enqueue voice.script.set (agent should write script into voice_config.json)")
        cid_script = enqueue(
            nms_base, identity, "voice.script.set",
            {"commands": commands}
        )
        pause()

        print_step("STEP 3: Enqueue voice.start (agent should start scanner-voice.service)")
        cid_start = enqueue(
            nms_base, identity, "voice.start",
            {
                "mode": "name_listen",
                "conversation_timeout_sec": 20,
                "llm_timeout_sec": 30,
            }
        )
        pause()

        print_step("STEP 4: Enqueue voice.mode.set (agent should set mode=conversation without restart)")
        cid_mode = enqueue(
            nms_base, identity, "voice.mode.set",
            {"mode": "conversation"}
        )
        pause()

        print_step("STEP 5: Enqueue voice.stop (agent should stop scanner-voice.service)")
        cid_stop = enqueue(
            nms_base, identity, "voice.stop",
            {}
        )
        pause()

        print_step("STEP 6: Wait for agent RESULTS for all 4 cmd_id(s)")
        wait_for_agent_results([cid_script, cid_start, cid_mode, cid_stop])
        print("[OK] observed RESULT lines for all 4 commands in agent.log")
        pause()

        print_step("STEP 7: Inspect scanner-voice.service + journalctl (verify mode/script effects)")
        show_voice_service_status()
        print("\n--- tail agent.log ---")
        print(tail_file(AGENT_LOG, n=120))
        pause()

        print_step("STEP 8: Cleanup (stop scanner-agent.service + ensure voice service stopped)")
        stop_agent_cleanup()
        ensure_services_clean()
        print("[OK] cleanup done")

        print("\nDONE: Wave-2 voice (script/start/mode/stop) end-to-end test complete.\n")
        return 0

    except KeyboardInterrupt:
        print("\n[ABORT] interrupted by user. Cleaning up best-effort...")
        stop_agent_cleanup()
        ensure_services_clean()
        return 130
    except Exception as e:
        print(f"\n[FATAL] {type(e).__name__}: {e}")
        print("\n--- tail agent.log ---")
        print(tail_file(AGENT_LOG, n=140))
        print("\n--- scanner-voice.service status/journal ---")
        show_voice_service_status()
        print("\nCleaning up best-effort...")
        stop_agent_cleanup()
        ensure_services_clean()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
