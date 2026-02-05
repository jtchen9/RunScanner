#!/usr/bin/env python3
"""
scanner agent (headless): polls NMS for commands, executes, and ACKs.

Scope:
- Identity: read scanner_name.txt; if missing, run register.py and retry.
- NMS discovery: via config.get_nms_base() (failover + caching).
- Poll: GET /cmd/poll/{scanner}
- Execute: scan.start / scan.stop / scan.once / bundle.apply
- Ack: POST /cmd/ack/{scanner}
- Bundle telemetry: POST /bootstrap/report/{scanner}  (installed_version only)

Notes:
- Time format MUST match NMS TIME_FMT (local time)
- args_json is JSON text stored in Redis; parse as dict for actions
"""

import os
import time
import json
import subprocess
import signal
from typing import Any, Dict, Tuple, List
from pathlib import Path
from config import (
    BASE_DIR,
    get_nms_base,
    SCANNER_NAME_FILE,
    local_ts,

    # AV single source of truth
    AV_DIR,
    AV_CFG_FILE,
    SERVICE_AVSTREAM,
    AV_DEFAULT_SERVER,
    AV_DEFAULT_RTSP_PORT,
    AV_DEFAULT_TRANSPORT,
    AV_DEFAULT_VIDEO_DEV,
    AV_DEFAULT_AUDIO_DEV,
    AV_DEFAULT_SIZE,
    AV_DEFAULT_FPS,
    BASE_DIR,
    get_nms_base,
    SCANNER_NAME_FILE,
    local_ts,
)
from config import SYSTEMCTL, SUDO, SERVICE_SCANNER_POLLER, SERVICE_AVSTREAM
from config import (
    MPV_BIN, AUDIO_AO_DEFAULT, AUDIO_DEVICE_DEFAULT, AUDIO_VOLUME_DEFAULT,
)
import requests
from bundle_manager import apply_bundle
# Voice (Wave-2)
from voice.voice_agent_api import (
    exec_voice_start,
    exec_voice_stop,
    exec_voice_mode_set,
    exec_voice_script_set,
)

REGISTER_PY = BASE_DIR / "register.py"
LOG_PATH = BASE_DIR / "agent.log"
SCAN_SCRIPT = str(BASE_DIR / "scan_wifi.sh")

# Runtime tuning
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "5"))
POLL_LIMIT = int(os.getenv("POLL_LIMIT", "10"))
HTTP_TIMEOUT_SEC = int(os.getenv("HTTP_TIMEOUT_SEC", "10"))
REGISTER_RETRY_SEC = int(os.getenv("REGISTER_RETRY_SEC", "10"))
OFFLINE_RETRY_SEC = int(os.getenv("OFFLINE_RETRY_SEC", "5"))

# Audio/TTS
AUDIO_PID_FILE = "/tmp/scanner_audio_play.pid"
TTS_SCRIPT = str(BASE_DIR / "av" / "tts_say.sh")

# Voice (Wave-2)
from pathlib import Path
VOICE_DIR = BASE_DIR / "voice"
VOICE_CFG_FILE = VOICE_DIR / "voice_config.json"
SERVICE_VOICE = "scanner-voice.service"
from voice.voice_common import (
    ensure_voice_config,
    load_voice_config,
    update_voice_config,
    validate_script,
)


def log(msg: str) -> None:
    line = f"[{local_ts()}] {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def read_scanner_name() -> str:
    try:
        return SCANNER_NAME_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def run_register_once() -> None:
    """Best-effort registration attempt. Never raise."""
    try:
        subprocess.run(
            ["/usr/bin/python3", str(REGISTER_PY)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=12,
        )
    except Exception:
        pass

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

def fetch_commands(nms_base: str, scanner: str) -> Tuple[bool, Dict[str, Any]]:
    """Returns (ok, payload). ok=False means network/parse error."""
    url = f"{nms_base}/cmd/poll/{scanner}"
    try:
        r = requests.get(url, params={"limit": POLL_LIMIT}, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            return False, {"error": f"http {r.status_code}", "text": r.text[:200]}
        return True, r.json()
    except Exception as e:
        return False, {"error": f"exception {type(e).__name__}", "detail": str(e)[:200]}

def ack_command(nms_base: str, scanner: str, cmd_id: str, status: str, detail: str) -> None:
    """Best-effort ACK. Never raise."""
    url = f"{nms_base}/cmd/ack/{scanner}"
    body = {
        "cmd_id": cmd_id,
        "status": status,
        "detail": detail,
        "finished_at": local_ts(),  # MUST match TIME_FMT
    }
    try:
        r = requests.post(url, json=body, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            log(f"ACK fail cmd_id={cmd_id} http={r.status_code} body={r.text[:200]}")
    except Exception as e:
        log(f"ACK exception cmd_id={cmd_id} {type(e).__name__}: {e}")

def parse_args_json(s: str) -> Dict[str, Any]:
    """NMS stores args_json as JSON text. Pi parses it into dict."""
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def report_installed_bundle(nms_base: str, scanner: str, installed_version: str) -> None:
    """
    Best-effort bundle telemetry to NMS.

    NMS contract:
      POST /bootstrap/report/{scanner}
      body: {"installed_version": "<bundle_id>"}
    """
    url = f"{nms_base}/bootstrap/report/{scanner}"
    body = {"installed_version": installed_version}
    try:
        r = requests.post(url, json=body, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            log(f"BOOTSTRAP report fail http={r.status_code} body={r.text[:200]}")
    except Exception as e:
        log(f"BOOTSTRAP report exception: {type(e).__name__}: {e}")

def _ensure_dir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def _write_json(p: Path, obj: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True, f"wrote {p}"
    except Exception as e:
        return False, f"write_json failed {p}: {type(e).__name__}: {e}"

def exec_av_stream_start(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Write av_stream_config.json then start scanner-avstream.service.
    Args are passed through to the runner script.
    """
    _ensure_dir(AV_DIR)

    cfg = {
        "server": (args.get("server") or "").strip() or AV_DEFAULT_SERVER,
        "port": int(args.get("port") or AV_DEFAULT_RTSP_PORT),
        "path": (args.get("path") or "").strip() or scanner,  # usually twin-scout-alpha
        "transport": (args.get("transport") or "").strip() or AV_DEFAULT_TRANSPORT,
        "video_dev": (args.get("video_dev") or "").strip() or AV_DEFAULT_VIDEO_DEV,
        "audio_dev": (args.get("audio_dev") or "").strip() or AV_DEFAULT_AUDIO_DEV,
        "size": (args.get("size") or "").strip() or AV_DEFAULT_SIZE,
        "fps": int(args.get("fps") or AV_DEFAULT_FPS),
    }

    ok, msg = _write_json(AV_CFG_FILE, cfg)
    if not ok:
        return False, msg

    ok2, out, err = _run_systemctl(["start", SERVICE_AVSTREAM])
    return (True, f"started {SERVICE_AVSTREAM}") if ok2 else (False, f"start failed: {err or out}")

def exec_av_stream_stop() -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["stop", SERVICE_AVSTREAM])
    return (True, f"stopped {SERVICE_AVSTREAM}") if ok else (False, f"stop failed: {err or out}")

def exec_audio_play(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    audio_file = (args.get("file") or "").strip()
    if not audio_file:
        return False, "audio.play missing args.file"

    stop_existing = bool(args.get("stop_existing", True))
    if stop_existing:
        _ = exec_audio_stop(scanner, {})  # best-effort stop before playing

    ao = (args.get("ao") or AUDIO_AO_DEFAULT).strip()
    audio_dev = (args.get("audio_device") or AUDIO_DEVICE_DEFAULT).strip()
    vol = int(args.get("volume") or AUDIO_VOLUME_DEFAULT)

    cmd = [
        MPV_BIN,
        f"--ao={ao}",
        f"--audio-device={audio_dev}",
        "--no-video",
        f"--volume={vol}",
        audio_file,
    ]

    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(AUDIO_PID_FILE, "w") as f:
            f.write(str(p.pid))
        return True, f"audio.play started pid={p.pid} file={audio_file}"
    except Exception as e:
        return False, f"audio.play exception: {type(e).__name__}: {e}"

def _read_pidfile(pidfile: str) -> int:
    try:
        with open(pidfile, "r") as f:
            return int((f.read() or "").strip())
    except Exception:
        return 0

def _pid_exists(pid: int) -> bool:
    """
    True if PID exists.
    - ProcessLookupError => does NOT exist
    - PermissionError    => exists (but no permission)
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        # be conservative: if unsure, assume it exists
        return True

def _remove_pidfile(pidfile: str) -> None:
    try:
        os.remove(pidfile)
    except Exception:
        pass

def exec_audio_stop(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Stop any running audio playback started by audio.play.
    Success cases:
      - pidfile missing
      - pidfile unreadable/empty
      - pid in pidfile does not exist anymore
      - process is terminated successfully
    """
    pid = _read_pidfile(AUDIO_PID_FILE)

    # No pidfile / unreadable pid => consider already stopped
    if pid <= 0:
        _remove_pidfile(AUDIO_PID_FILE)
        return True, "audio.stop ok (no pidfile / no pid)"

    # PID already gone => success
    if not _pid_exists(pid):
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} already exited)"

    # Try graceful stop first
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} already exited)"
    except Exception as e:
        # Continue to SIGKILL attempt below, but record detail
        pass

    # Give it a brief moment
    time.sleep(0.3)
    if not _pid_exists(pid):
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} terminated by SIGTERM)"

    # Escalate to SIGKILL
    try:
        os.kill(pid, 9)  # SIGKILL
    except ProcessLookupError:
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} already exited)"
    except Exception as e:
        # If we can't kill, report error
        return False, f"audio.stop failed: SIGKILL exception {type(e).__name__}: {e}"

    time.sleep(0.3)
    if not _pid_exists(pid):
        _remove_pidfile(AUDIO_PID_FILE)
        return True, f"audio.stop ok (pid {pid} killed by SIGKILL)"

    # Still exists => real failure
    return False, f"audio.stop failed pid={pid} (still exists after SIGKILL)"

def exec_tts_say(scanner: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    text = (args.get("text") or "").strip()
    if not text:
        return False, "tts.say missing args.text"

    lead_ms = int(args.get("lead_silence_ms") or 300)
    vol = int(args.get("volume") or AUDIO_VOLUME_DEFAULT)

    # Keep it simple + robust: call the shell script that does:
    # espeak-ng -> wav, prepend silence, mpv playback
    try:
        cp = subprocess.run(
            ["/usr/bin/bash", TTS_SCRIPT, text, str(lead_ms), str(vol)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,   # avoid hanging forever
        )
        if cp.returncode == 0:
            return True, f"tts.say ok text_len={len(text)} lead_ms={lead_ms}"
        return False, f"tts.say rc={cp.returncode} stderr={(cp.stderr or '')[:200].strip()}"
    except Exception as e:
        return False, f"tts.say exception: {type(e).__name__}: {e}"

def exec_voice_start(args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Wave-2: start scanner-voice.service + write initial config.
    """
    try:
        ensure_voice_config()
    except Exception:
        pass

    mode = (args.get("mode") or "").strip() or "name_listen"
    conv_to = int(args.get("conversation_timeout_sec") or 20)
    llm_to = int(args.get("llm_timeout_sec") or 30)

    # For Wave-2, script may be provided here or via voice.script.set
    script = validate_script(args.get("commands") or args.get("script"))

    new_cfg = update_voice_config({
        "mode": mode,
        "conversation_timeout_sec": conv_to,
        "llm_timeout_sec": llm_to,
        "script": script if script else load_voice_config().get("script", []),
    })

    ok, out, err = _run_systemctl(["start", SERVICE_VOICE])
    if ok:
        return True, f"started {SERVICE_VOICE} mode={new_cfg.get('mode')} script_len={len(new_cfg.get('script') or [])}"
    return False, f"start failed: {err or out}"

def exec_voice_stop() -> Tuple[bool, str]:
    """
    Wave-2: stop voice service and set mode=deaf in config.
    """
    try:
        ensure_voice_config()
        update_voice_config({"mode": "deaf"})
    except Exception:
        pass

    ok, out, err = _run_systemctl(["stop", SERVICE_VOICE])
    if ok:
        return True, f"stopped {SERVICE_VOICE}"
    return False, f"stop failed: {err or out}"

def exec_voice_mode_set(args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Wave-2: update mode in config only (service may be running or stopped).
    """
    mode = (args.get("mode") or "").strip()
    if mode not in ("deaf", "name_listen", "conversation", "llm"):
        return False, f"voice.mode.set invalid mode={mode}"

    ensure_voice_config()
    update_voice_config({"mode": mode})
    return True, f"voice mode set to {mode}"

def exec_voice_script_set(args: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Wave-2: replace script list in config.
    """
    commands = validate_script(args.get("commands"))
    ensure_voice_config()
    update_voice_config({"script": commands})
    return True, f"voice script updated script_len={len(commands)}"

def dispatch(nms_base: str, scanner: str, cmd_fields: Dict[str, Any]) -> Tuple[str, str]:
    """
    Execute one command.
    Returns (status, detail) where status in {'ok','error'}.
    """
    category = (cmd_fields.get("category") or "").strip()
    action = (cmd_fields.get("action") or "").strip()
    args = parse_args_json(cmd_fields.get("args_json") or "")

    # Policy: allow only known categories.
    # (scan already exists; av added for streaming)
    if category and category not in ("scan", "av", "voice"):
        return "error", f"unsupported category={category}"

    if action == "scan.start":
        ok, detail = exec_scan_start()
        return ("ok" if ok else "error"), detail

    if action == "scan.stop":
        ok, detail = exec_scan_stop()
        return ("ok" if ok else "error"), detail

    if action == "scan.once":
        ok, detail = exec_scan_once()
        return ("ok" if ok else "error"), detail

    if action == "bundle.apply":
        bundle_id = (args.get("bundle_id") or "").strip() or (cmd_fields.get("bundle_id") or "").strip()
        url = (args.get("url") or "").strip() or (cmd_fields.get("url") or "").strip()

        if not bundle_id or not url:
            return "error", "bundle.apply missing bundle_id or url"

        ok, detail = apply_bundle(bundle_id, url)  # ensure bundle_manager.py matches this signature
        status = "ok" if ok else "error"

        if ok:
            report_installed_bundle(nms_base, scanner, bundle_id)

        return status, detail

    if action == "av.stream.start":
        ok, detail = exec_av_stream_start(scanner, args)
        return ("ok" if ok else "error"), detail

    if action == "av.stream.stop":
        ok, detail = exec_av_stream_stop()
        return ("ok" if ok else "error"), detail
    
    if action == "audio.play":
        ok, detail = exec_audio_play(scanner, args)
        return ("ok" if ok else "error"), detail
    
    if action == "audio.stop":
        ok, detail = exec_audio_stop(scanner, args)
        return ("ok" if ok else "error"), detail
    
    if action == "tts.say":
        ok, detail = exec_tts_say(scanner, args)
        return ("ok" if ok else "error"), detail

    if action == "voice.start":
        ok, detail = exec_voice_start(args)
        return ("ok" if ok else "error"), detail

    if action == "voice.stop":
        ok, detail = exec_voice_stop()
        return ("ok" if ok else "error"), detail

    if action == "voice.mode.set":
        ok, detail = exec_voice_mode_set(args)
        return ("ok" if ok else "error"), detail

    if action == "voice.script.set":
        ok, detail = exec_voice_script_set(args)
        return ("ok" if ok else "error"), detail

    return "error", f"unknown action={action}"


def main() -> None:
    log(f"agent started poll={POLL_INTERVAL_SEC}s limit={POLL_LIMIT}")

    while True:
        # 1) Ensure identity
        scanner = read_scanner_name()
        if not scanner:
            log("scanner_name.txt missing/empty; attempt registration")
            run_register_once()
            scanner = read_scanner_name()
            if not scanner:
                log(f"still unassigned; retry in {REGISTER_RETRY_SEC}s")
                time.sleep(REGISTER_RETRY_SEC)
                continue

        # 2) Ensure NMS is reachable
        nms_base = get_nms_base()
        if not nms_base:
            log(f"offline: no NMS reachable; retry in {OFFLINE_RETRY_SEC}s")
            time.sleep(OFFLINE_RETRY_SEC)
            continue

        # 3) Poll
        ok, payload = fetch_commands(nms_base, scanner)
        if not ok:
            log(f"poll fail scanner={scanner} via={nms_base} {payload}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        cmds = payload.get("commands") or []
        if not cmds:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        for item in cmds:
            try:
                xid, fields = item
            except Exception:
                log(f"bad command item: {item}")
                continue

            fields = fields or {}
            cmd_id = (fields.get("cmd_id") or "").strip()
            action = (fields.get("action") or "").strip()
            execute_at = (fields.get("execute_at") or "").strip()

            if not cmd_id:
                log(f"skip command without cmd_id xid={xid} action={action}")
                continue

            log(f"EXEC cmd_id={cmd_id} action={action} execute_at={execute_at} xid={xid}")

            status, detail = dispatch(nms_base, scanner, fields)
            log(f"RESULT cmd_id={cmd_id} status={status} detail={detail}")

            ack_command(nms_base, scanner, cmd_id, status, detail)

        time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
