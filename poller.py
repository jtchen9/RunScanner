import os, time, signal, subprocess, requests, json, sys
from pathlib import Path

# ====== CONFIG ======
NMS_BASE = "http://<NMS_HOST>:8000"            # <- set your NMS host/IP
DEVICE_ID = os.uname().nodename                # default to hostname
DEVICE_TOKEN = "<YOUR_DEVICE_TOKEN>"           # <- set per device
POLL_INTERVAL_SEC = 10                         # command polling frequency
HEARTBEAT_EVERY = 30                           # status report frequency
SCAN_SCRIPT = "/home/pi/_RunScanner/scan_wifi.sh"
PIDFILE = "/tmp/wifi_scan.pid"
# =====================

HEADERS = {
    "X-Device-Id": DEVICE_ID,
    "X-Device-Token": DEVICE_TOKEN,
}

def is_running(pid: int) -> bool:
    return pid > 0 and Path(f"/proc/{pid}").exists()

def get_scan_pid() -> int:
    try:
        return int(Path(PIDFILE).read_text().strip())
    except Exception:
        return -1

def start_scan():
    if is_running(get_scan_pid()):
        return "already_running"
    # run scan script every minute via loop: we spawn a tiny wrapper
    # Using bash -c with a loop so we keep one PID
    cmd = [
        "bash", "-c",
        f'while true; do "{SCAN_SCRIPT}"; sleep 60; done'
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid  # own process group
    )
    Path(PIDFILE).write_text(str(proc.pid))
    return "started"

def stop_scan():
    pid = get_scan_pid()
    if not is_running(pid):
        # cleanup stale pidfile
        if Path(PIDFILE).exists():
            Path(PIDFILE).unlink(missing_ok=True)
        return "not_running"
    try:
        # kill the whole process group
        os.killpg(pid, signal.SIGTERM)
        # wait a bit
        for _ in range(20):
            if not is_running(pid): break
            time.sleep(0.1)
        if is_running(pid):
            os.killpg(pid, signal.SIGKILL)
        Path(PIDFILE).unlink(missing_ok=True)
        return "stopped"
    except ProcessLookupError:
        Path(PIDFILE).unlink(missing_ok=True)
        return "not_running"

def status():
    running = is_running(get_scan_pid())
    return {"device_id": DEVICE_ID, "scanning": running}

def send_status():
    try:
        requests.post(
            f"{NMS_BASE}/api/report-status",
            headers=HEADERS,
            json=status(),
            timeout=5,
        )
    except requests.RequestException:
        pass

def fetch_commands():
    try:
        r = requests.get(
            f"{NMS_BASE}/api/pending-commands",
            headers=HEADERS,
            params={"device_id": DEVICE_ID},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()  # e.g. ["start_scan", "stop_scan"]
    except requests.RequestException:
        return []
    return []

def handle_command(cmd: str):
    if cmd == "start_scan":
        return start_scan()
    if cmd == "stop_scan":
        return stop_scan()
    return f"unknown:{cmd}"

def main():
    last_heartbeat = 0
    while True:
        # heartbeat
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_EVERY:
            send_status()
            last_heartbeat = now

        # polling
        cmds = fetch_commands()
        for c in cmds:
            handle_command(c)

        time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
