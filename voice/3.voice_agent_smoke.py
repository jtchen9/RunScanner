#!/usr/bin/env python3
import time
from voice_common import (
    read_identity, ensure_voice_config, load_voice_config, update_voice_config, validate_script
)

import subprocess

SERVICE_VOICE = "scanner-voice.service"

def run(cmd):
    cp = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()

def main():
    ident = read_identity()
    print(f"[SMOKE] identity={ident!r}")

    ensure_voice_config()
    cfg = load_voice_config()
    print(f"[SMOKE] initial cfg: {cfg}")

    # script.set
    script = validate_script([
        {"phrase": "How are you", "reply": "I am fine", "action": "status.report"},
        {"phrase": "Lets talk", "reply": "OK", "action": "enter.llm"},
    ])
    update_voice_config({"script": script})
    print(f"[SMOKE] wrote script_len={len(script)}")

    # mode.set
    update_voice_config({"mode": "name_listen"})
    print("[SMOKE] mode set to name_listen")

    # start service
    rc, out, err = run(["sudo", "systemctl", "start", SERVICE_VOICE])
    print(f"[SMOKE] start rc={rc} err={err}")
    time.sleep(1.0)

    # stop service + deaf
    update_voice_config({"mode": "deaf"})
    rc, out, err = run(["sudo", "systemctl", "stop", SERVICE_VOICE])
    print(f"[SMOKE] stop rc={rc} err={err}")

    cfg2 = load_voice_config()
    print(f"[SMOKE] final cfg: {cfg2}")

if __name__ == "__main__":
    main()
