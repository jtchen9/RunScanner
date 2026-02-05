#!/usr/bin/env python3
"""
5.VoiceWave3LLMTest.py
End-to-end Wave-3 Voice+LLM test (operator-side script running on Pi).

Goal:
Cover what 4.VoiceWave2Test.py does NOT cover yet:
- Verify OpenAI/Responses connectivity via voice_llm.llm_exchange() (preflight)
- Verify llm config is present in voice/voice_config.json (single key "llm")
- Verify api_key_file exists + is non-empty
- Verify llm_state.json creation + updates (previous_response_id / updated_at)
- Verify Wave-3 pipeline in scanner-voice.service:
    STT -> LLM -> TTS inside llm_dummy mode
- Verify llm_dummy timeout uses last-activity (manual speaking) rather than entry time
- Verify exit path from llm_dummy -> name_listen on timeout
- Keep "Pi is dumb": use existing NMS enqueue (optional) to set script + start voice.
  (We do not require a new NMS command. We reuse voice.script.set + voice.start.)

Assumptions (same as Wave-2 test):
- Identity in /home/pi/_RunScanner/scanner_name.txt, e.g. "twin-scout-alpha"
- NMS endpoint: POST {NMS_BASE}/cmd/_enqueue/{identity}
- agent.py dispatch supports category "voice" actions:
    voice.start, voice.stop, voice.mode.set, voice.script.set
- scanner-voice.service exists and runs /home/pi/_RunScanner/voice_service.py
- voice_llm.py exists and llm_exchange() uses voice_config.json -> cfg["llm"]

NOTE (manual parts):
- Actual STT->LLM->TTS needs you to speak, so this test includes HUMAN CHECK steps.
- We validate LLM API connectivity programmatically BEFORE starting the voice service.

"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import requests

BASE_DIR = Path("/home/pi/_RunScanner")
VOICE_DIR = BASE_DIR / "voice"
AGENT_LOG = BASE_DIR / "agent.log"
SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
NMS_BASE_FILE = BASE_DIR / "nms_base.txt"

VOICE_CFG_PATH = VOICE_DIR / "voice_config.json"
LLM_STATE_PATH = VOICE_DIR / "llm_state.json"

TIME_FMT = "%Y-%m-%d-%H:%M:%S"

HTTP_TIMEOUT_SEC = 10
WAIT_AGENT_SEC = 60
POLL_INTERVAL = 2

SERVICE_AGENT = "scanner-agent.service"
SERVICE_VOICE = "scanner-voice.service"

# ----- helpers -----

def now_ts() -> str:
    return datetime.now().strftime(TIME_FMT)

def ts_plus(seconds: int) -> str:
    return (datetime.now() + timedelta(seconds=seconds)).strftime(TIME_FMT)

def sh(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)

def tail_file(path: Path, n: int = 120) -> str:
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

def pause(msg: str = "Press ENTER to continue (or Ctrl+C to stop) ... ") -> None:
    input(msg)

def read_identity() -> str:
    try:
        return SCANNER_NAME_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def get_nms_base() -> str:
    env = (os.getenv("NMS_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    try:
        s = NMS_BASE_FILE.read_text(encoding="utf-8").strip()
        if s:
            return s.rstrip("/")
    except Exception:
        pass
    return "http://192.168.137.1:8000"

def systemctl(*args: str) -> Tuple[int, str, str]:
    cp = sh(["systemctl", *args], check=False)
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()

def sudo_systemctl(*args: str) -> Tuple[int, str, str]:
    cp = sh(["sudo", "systemctl", *args], check=False)
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()

def ensure_services_clean() -> None:
    sudo_systemctl("stop", SERVICE_VOICE)
    sudo_systemctl("reset-failed", SERVICE_VOICE)

def start_agent_temporarily() -> None:
    rc, out, err = sudo_systemctl("start", SERVICE_AGENT)
    if rc != 0:
        raise RuntimeError(f"failed to start {SERVICE_AGENT}: {err or out}")

def stop_agent_cleanup() -> None:
    sudo_systemctl("stop", SERVICE_AGENT)

def show_voice_service_status(n_journal: int = 120) -> None:
    print("\n--- systemctl status scanner-voice.service ---")
    cp = sh(["systemctl", "status", SERVICE_VOICE, "--no-pager", "-l"], check=False)
    print((cp.stdout or "").rstrip())
    if cp.stderr:
        print((cp.stderr or "").rstrip())

    print(f"\n--- journalctl -u scanner-voice.service (last {n_journal}) ---")
    cp2 = sh(["sudo", "journalctl", "-u", SERVICE_VOICE, "-n", str(n_journal), "--no-pager"], check=False)
    print((cp2.stdout or "").rstrip())
    if cp2.stderr:
        print((cp2.stderr or "").rstrip())

def enqueue(nms_base: str, identity: str, action: str, args_obj: dict) -> str:
    url = f"{nms_base}/cmd/_enqueue/{identity}"
    body = {
        "category": "voice",
        "action": action,
        "execute_at": ts_plus(5),
        "args": args_obj,
    }
    r = requests.post(url, json=body, timeout=HTTP_TIMEOUT_SEC)
    try:
        data = r.json()
    except Exception:
        data = {"raw": (r.text or "")[:200]}
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
        txt = tail_file(AGENT_LOG, n=260)
        done = set()
        for cid in list(remaining):
            if f"RESULT cmd_id={cid} " in txt:
                done.add(cid)
        remaining -= done
        if remaining:
            time.sleep(POLL_INTERVAL)
    if remaining:
        print("\n--- tail agent.log ---")
        print(tail_file(AGENT_LOG, n=160))
        raise RuntimeError(f"did not observe RESULT lines for cmd_id(s): {sorted(remaining)}")

def load_voice_cfg() -> Dict[str, Any]:
    if not VOICE_CFG_PATH.exists():
        raise RuntimeError(f"missing {VOICE_CFG_PATH}")
    try:
        return json.loads(VOICE_CFG_PATH.read_text(encoding="utf-8") or "{}")
    except Exception as e:
        raise RuntimeError(f"cannot parse {VOICE_CFG_PATH}: {e}")

def read_text_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def preflight_llm_exchange() -> None:
    # Import here so the test can still print useful info if import fails.
    try:
        from voice_llm import llm_exchange  # type: ignore
    except Exception as e:
        raise RuntimeError(f"cannot import voice_llm.llm_exchange: {type(e).__name__}: {e}")

    ok, out = llm_exchange("Hello. Reply with exactly one short sentence.")
    if not ok:
        raise RuntimeError(f"llm_exchange failed: {out}")
    if not (out or "").strip():
        raise RuntimeError("llm_exchange returned empty response")
    print(f"[OK] llm_exchange preflight: {out.strip()}")

def llm_state_summary() -> str:
    if not LLM_STATE_PATH.exists():
        return "(llm_state.json does not exist)"
    try:
        j = json.loads(LLM_STATE_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        return "(llm_state.json exists but cannot parse)"
    sid = str(j.get("session_id") or j.get("conversation_id") or "").strip()
    pid = str(j.get("previous_response_id") or "").strip()
    upd = str(j.get("updated_at") or "").strip()
    return f"session_id='{sid}' previous_response_id='{pid}' updated_at='{upd}'"

def remove_llm_state_best_effort() -> None:
    try:
        if LLM_STATE_PATH.exists():
            LLM_STATE_PATH.unlink()
    except Exception:
        pass

# ----- main -----

def main() -> int:
    identity = read_identity()
    if not identity:
        print("[FATAL] scanner_name.txt missing/empty. Identity required.")
        return 2

    nms_base = get_nms_base()
    print(f"[INFO] now={now_ts()} identity='{identity}' nms_base={nms_base}")
    print(f"[INFO] voice_cfg={VOICE_CFG_PATH} llm_state={LLM_STATE_PATH}")

    try:
        print_step("STEP 0: Clean slate (stop voice service + reset-failed)")
        ensure_services_clean()
        show_voice_service_status(n_journal=40)
        pause()

        print_step("STEP 1: Validate voice_config.json has a single 'llm' key + key file is present")
        cfg = load_voice_cfg()
        llm = cfg.get("llm") or {}
        if not isinstance(llm, dict):
            raise RuntimeError("voice_config.json: 'llm' must be an object/dict")

        api_key_file = str(llm.get("api_key_file") or "").strip()
        model = str(llm.get("model") or "").strip()
        base_url = str(llm.get("base_url") or "").strip()

        if not model:
            raise RuntimeError("voice_config.json: llm.model missing/empty")
        if not api_key_file:
            raise RuntimeError("voice_config.json: llm.api_key_file missing/empty")
        if not base_url:
            print("[WARN] voice_config.json: llm.base_url empty; voice_llm.py will default")

        key_text = read_text_file(Path(api_key_file))
        if not key_text:
            raise RuntimeError(f"LLM key file empty/unreadable: {api_key_file}")

        print(f"[OK] llm.model={model}")
        print(f"[OK] llm.api_key_file={api_key_file} (len={len(key_text)})")
        pause()

        print_step("STEP 2: Reset llm_state.json (so we can confirm it gets created/updated)")
        print(f"Before: {llm_state_summary()}")
        remove_llm_state_best_effort()
        print(f"After : {llm_state_summary()}")
        pause()

        print_step("STEP 3: LLM preflight (direct Internet/API test via voice_llm.llm_exchange)")
        preflight_llm_exchange()
        print(f"[INFO] llm_state after preflight: {llm_state_summary()}")
        pause()

        print_step("STEP 4: Start scanner-agent.service (temporarily for this test)")
        start_agent_temporarily()
        print("[OK] started scanner-agent.service")
        pause()

        print_step("STEP 5: Enqueue voice.script.set with Wave-3 trigger phrases")
        # Keep it minimal: one phrase enters llm mode, one phrase is status report
        commands = [
            {"phrase": "how are you", "reply": "Let me check the operating status.", "action": "status.report"},
            {"phrase": "lets talk",   "reply": "", "action": "enter.llm"},
        ]
        cid_script = enqueue(nms_base, identity, "voice.script.set", {"commands": commands})
        pause()

        print_step("STEP 6: Enqueue voice.start (start voice service in name_listen)")
        cid_start = enqueue(
            nms_base, identity, "voice.start",
            {"mode": "name_listen", "conversation_timeout_sec": 20, "llm_timeout_sec": 30},
        )
        pause()

        print_step("STEP 7: Wait for agent RESULTS (script.set + start)")
        wait_for_agent_results([cid_script, cid_start])
        print("[OK] observed RESULT lines for script.set + start in agent.log")
        pause()

        print_step("STEP 8 (HUMAN): Trigger CONVERSATION, then say 'lets talk' to enter LLM mode")
        print(
            "Instructions:\n"
            "  A) Ensure speakers are audible.\n"
            "  B) Speak anything to trigger wake (your test_wake_always may auto-trigger).\n"
            "  C) In CONVERSATION mode, say: 'lets talk'\n"
            "Expected:\n"
            "  - voice service transitions to llm_dummy\n"
            "  - it may speak the llm_enter_say prompt\n"
            "  - journal shows '(llm_dummy)' lines and LLM exchanges\n"
        )
        pause("Press ENTER when you have spoken 'lets talk' and heard it respond ... ")
        show_voice_service_status(n_journal=120)
        print(f"\n[INFO] llm_state now: {llm_state_summary()}")
        pause()

        print_step("STEP 9 (HUMAN): Test LLM activity timeout reset (no interruption while you keep talking)")
        print(
            "Instructions:\n"
            "  - Stay in llm_dummy.\n"
            "  - Ask 2~3 short questions with < 15s gap between questions.\n"
            "Expected:\n"
            "  - It should not time out back to name_listen while you keep talking.\n"
            "  - journal shows multiple '(llm_dummy)' STT lines and LLM calls.\n"
            "  - llm_state.json updated_at changes.\n"
        )
        pause("Press ENTER after you asked multiple questions and heard multiple replies ... ")
        show_voice_service_status(n_journal=160)
        print(f"\n[INFO] llm_state now: {llm_state_summary()}")
        pause()

        print_step("STEP 10 (HUMAN): Verify LLM timeout -> name_listen when you STOP talking")
        llm_to = int((load_voice_cfg().get("llm_timeout_sec") or 30))
        print(
            f"Instructions:\n"
            f"  - Do NOT speak for ~{llm_to + 5} seconds.\n"
            "Expected:\n"
            "  - voice service logs: 'llm timeout -> name_listen'\n"
            "  - mode becomes name_listen\n"
        )
        pause(f"Press ENTER AFTER you waited ~{llm_to + 5}s in silence ... ")
        show_voice_service_status(n_journal=120)
        pause()

        print_step("STEP 11: Cleanup (stop voice service + stop agent)")
        ensure_services_clean()
        stop_agent_cleanup()
        print("[OK] cleanup done")

        print("\nDONE: Wave-3 voice+LLM end-to-end test complete.\n")
        return 0

    except KeyboardInterrupt:
        print("\n[ABORT] interrupted by user. Cleaning up best-effort...")
        stop_agent_cleanup()
        ensure_services_clean()
        return 130
    except Exception as e:
        print(f"\n[FATAL] {type(e).__name__}: {e}")
        print("\n--- tail agent.log ---")
        print(tail_file(AGENT_LOG, n=160))
        print("\n--- scanner-voice.service status/journal ---")
        show_voice_service_status(n_journal=160)
        print(f"\n[INFO] llm_state summary: {llm_state_summary()}")
        print("\nCleaning up best-effort...")
        stop_agent_cleanup()
        ensure_services_clean()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
