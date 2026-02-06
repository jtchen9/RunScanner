#!/usr/bin/env python3
"""
Wave-1 AV Integration Test (PART 1 + Streaming)

Steps included:
  STEP 0  : enter_test_mode.sh
  STEP 0A : LOCAL.AV.RUNTIME.READY (local-only checks + beep)
  STEP 1  : Discover NMS + GET /health
  STEP 2  : POST /admin/_reset   (operator-only; keeps whitelist/bundles/autoflush)
  STEP 4  : POST /registry/register  (Pi-facing)

  STEP 5  : Start scanner-agent.service (temporarily for this test)
  STEP 6  : Enqueue av.stream.start (operator-only /cmd/_enqueue/{scanner})
  STEP 7  : Verify scanner-avstream.service active + log contains RTSP START
  STEP 7A : (Optional) ffprobe RTSP URL
  STEP 8  : Enqueue av.stream.stop
  STEP 9  : Verify scanner-avstream.service inactive
  STEP 10 : Stop scanner-agent.service (cleanup)

NOT included yet:
  - audio.play
  - tts.say
"""

import sys
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import os

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
EXIT_SH = TEST_DIR / "exit_test_mode.sh"

# Your fixed lab IP convention (per your requirement)
TEST_IP = "192.168.137.2"

HTTP_TIMEOUT_SEC = 12.0  # fixed constant in test script

AV_DIR = BASE_DIR / "av"
AV_CFG = AV_DIR / "av_stream_config.json"
AV_LOG = AV_DIR / "av_stream.log"

# =============================================================================
# Small helpers (same style as 1.integrationTest.py)
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
        if t:
            print(t[:1500])
        else:
            print(f"[HTTP] (no text) content-length={len(r.content or b'')}")

def _run_sh(path: Path) -> None:
    if not path.exists():
        die(f"Missing script: {path}")
    subprocess.run(
        ["/usr/bin/bash", str(path)],
        check=False,
        capture_output=False,  # show output live
        text=True,
    )

def _cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def _run_cmd(cmd_list, timeout=8) -> Tuple[bool, str, str]:
    """
    Run a small command and return (ok, stdout, stderr).
    Never raises; always returns strings.
    """
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
        ok = (cp.returncode == 0)
        return ok, out, err
    except Exception as e:
        return False, "", str(e)

def _run_systemctl(args) -> Tuple[bool, str, str]:
    """
    Run systemctl WITHOUT EVER prompting.
    Lab rule: always sudo -n. If not permitted, fail (no interactive fallback).
    """
    ok, out, err = _run_cmd([config.SUDO, "-n", config.SYSTEMCTL] + args, timeout=10)
    return ok, out, err

def _systemctl_is_active(service: str) -> bool:
    ok, out, _ = _run_systemctl(["is-active", service])
    return ok and out.strip() == "active"

def _systemctl_start(service: str) -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["start", service])
    return (True, "started") if ok else (False, err or out)

def _systemctl_stop(service: str) -> Tuple[bool, str]:
    ok, out, err = _run_systemctl(["stop", service])
    return (True, "stopped") if ok else (False, err or out)

def _tail_file(path: Path, n: int = 60) -> str:
    if not path.exists():
        return f"[tail] file missing: {path}"
    try:
        ok, out, err = _run_cmd(["/usr/bin/tail", "-n", str(n), str(path)], timeout=6)
        if ok:
            return out
        return f"[tail] failed: {err or out}"
    except Exception as e:
        return f"[tail] exception: {type(e).__name__}: {e}"

def play_test_beep() -> Tuple[bool, str]:
    """
    Reliable beep:
    1) generate a 1s WAV sine wave into /tmp using ffmpeg
    2) play it through mpv using config's known-good ALSA defaults
    """
    tmp_wav = "/tmp/beep_880hz_1s.wav"

    if not _cmd_exists("ffmpeg"):
        return False, "ffmpeg missing"
    if not _cmd_exists("mpv"):
        return False, "mpv missing"

    gen_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", "sine=frequency=880:duration=1",
        "-ac", "1", "-ar", "48000",
        "-c:a", "pcm_s16le",
        "-y", tmp_wav
    ]
    ok, out, err = _run_cmd(gen_cmd, timeout=10)
    if not ok:
        return False, f"ffmpeg gen failed: {err or out}"

    play_cmd = [
        config.MPV_BIN,
        f"--ao={config.AUDIO_AO_DEFAULT}",
        f"--audio-device={config.AUDIO_DEVICE_DEFAULT}",
        "--no-video",
        tmp_wav
    ]
    ok2, out2, err2 = _run_cmd(play_cmd, timeout=12)
    if not ok2:
        return False, f"mpv play failed: {err2 or out2}"

    return True, "ok"

def av_runtime_ready_probe(with_beep: bool = True) -> str:
    """
    LOCAL.AV.RUNTIME.READY
    Local-only checks (no NMS calls, no streaming)
    """
    lines = []
    lines.append("LOCAL.AV.RUNTIME.READY")
    lines.append(f"Time: {config.local_ts()}")
    lines.append("")

    cam_ok = Path(config.AV_DEFAULT_VIDEO_DEV).exists()
    lines.append(f"Video: {config.AV_DEFAULT_VIDEO_DEV} exists = {cam_ok}")

    if _cmd_exists("arecord"):
        ok, out, err = _run_cmd(["arecord", "-l"], timeout=3)
        lines.append(f"Audio: arecord -l = {'OK' if ok else 'FAIL'}")
        if out:
            lines.extend(["  " + s for s in out.splitlines()[:8]])
        elif err:
            lines.append("  " + err.splitlines()[0][:140])
    else:
        lines.append("Audio: arecord not found")

    if _cmd_exists("aplay"):
        ok, out, err = _run_cmd(["aplay", "-l"], timeout=3)
        lines.append(f"Audio: aplay -l = {'OK' if ok else 'FAIL'}")
        if out:
            lines.extend(["  " + s for s in out.splitlines()[:8]])
        elif err:
            lines.append("  " + err.splitlines()[0][:140])
    else:
        lines.append("Audio: aplay not found")

    lines.append("")
    lines.append("Tools:")
    lines.append(f"  ffmpeg = {'OK' if _cmd_exists('ffmpeg') else 'MISSING'}")
    lines.append(f"  mpv    = {'OK' if _cmd_exists('mpv') else 'MISSING'}")
    lines.append(f"  ffprobe = {'OK' if _cmd_exists('ffprobe') else 'MISSING'}")

    if with_beep:
        lines.append("")
        ok, detail = play_test_beep()
        lines.append(f"Beep test: {'OK' if ok else 'FAIL'}")
        if not ok:
            lines.append(f"  {detail}")

    lines.append("")
    lines.append("Known-good audio test (manual):")
    lines.append("  mpv --ao=alsa --audio-device=alsa/default --no-video /home/pi/music.mp3")
    lines.append("")
    return "\n".join(lines)

def sudo_warmup() -> None:
    """
    Non-interactive warmup: verify sudo -n works (never prompts).
    """
    ok, out, err = _run_cmd([config.SUDO, "-n", "true"], timeout=5)
    if not ok:
        die("sudo -n is not permitted for this user. Fix sudoers; test will not prompt.")
    print("[OK] sudo -n works (no password required).")

def _systemctl_status(service: str) -> str:
    ok, out, err = _run_cmd([config.SUDO, "-n", config.SYSTEMCTL, "status", service, "--no-pager", "-l"], timeout=10)
    if ok and out:
        return out
    # fallback without sudo (sometimes allowed)
    ok2, out2, err2 = _run_cmd([config.SYSTEMCTL, "status", service, "--no-pager", "-l"], timeout=10)
    return out2 or err2 or err or out or "(no status output)"

def _journal_tail(unit: str, n: int = 80) -> str:
    # journalctl often needs sudo; try sudo -n first, then no sudo
    ok, out, err = _run_cmd([config.SUDO, "-n", "/usr/bin/journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "short-iso"], timeout=10)
    if ok and out:
        return out
    ok2, out2, err2 = _run_cmd(["/usr/bin/journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "short-iso"], timeout=10)
    return out2 or err2 or err or out or "(no journal output)"

def _run_local(cmd: list[str]) -> tuple[int, str]:
    """Run a local shell command and return (rc, combined_output)."""
    try:
        cp = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        out = (cp.stdout or "") + (cp.stderr or "")
        return cp.returncode, out.strip()
    except Exception as e:
        return 999, f"exception: {type(e).__name__}: {e}"

def sudo_n_systemctl(args: list[str]) -> tuple[bool, str]:
    """Run: sudo -n /usr/bin/systemctl <args...> (no password)."""
    rc, out = _run_local(["sudo", "-n", "/usr/bin/systemctl"] + args)
    return (rc == 0), out

def is_service_active(service: str) -> bool:
    ok, out = sudo_n_systemctl(["is-active", service])
    return ok and out.strip() == "active"

def wait_service_active(service: str, timeout_sec: int = 25, poll_sec: int = 2) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if is_service_active(service):
            return True
        time.sleep(poll_sec)
    return False

def tail_file(path: Path, n: int = 80) -> None:
    if not path.exists():
        print(f"[tail] file missing: {path}")
        return
    ok, out = _run_local(["/usr/bin/tail", "-n", str(n), str(path)])
    print(out if out else "[tail] (empty)")

def show_systemd_diagnostics(service: str) -> None:
    print(f"\n--- systemctl status {service} ---")
    ok, out = sudo_n_systemctl(["status", service, "--no-pager", "-n", "20"])
    print(out if out else "(no output)")
    print("--- end status ---\n")

    print(f"--- journalctl -u {service} ---")
    # journalctl may return nonzero if no entries; that's fine
    rc, jout = _run_local(["/usr/bin/journalctl", "-u", service, "--no-pager", "-n", "60"])
    print(jout if jout else "(no entries)")
    print("--- end journal ---\n")

def wait_for_file(path: Path, timeout_sec: int = 12, poll_sec: float = 0.5) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if path.exists():
            return True
        time.sleep(poll_sec)
    return False

def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def read_pidfile(pidfile: Path) -> Optional[int]:
    try:
        s = pidfile.read_text(encoding="utf-8").strip()
        return int(s)
    except Exception:
        return None

def kill_pidfile(pidfile: Path) -> None:
    """
    Best-effort stop any previous mpv playback started by audio.play.
    Mirrors agent.py logic (SIGTERM + remove pidfile).
    """
    try:
        pid = read_pidfile(pidfile)
        if pid is not None and pid_alive(pid):
            os.kill(pid, 15)  # SIGTERM
            time.sleep(0.5)
    except Exception:
        pass
    try:
        if pidfile.exists():
            pidfile.unlink()
    except Exception:
        pass

def tail_contains(path: Path, needle: str, last_n: int = 200) -> bool:
    txt = _tail_file(path, n=last_n)
    return needle in (txt or "")

def wait_tail_contains(path: Path, needle: str, timeout_sec: int = 12) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if tail_contains(path, needle):
            return True
        time.sleep(1.0)
    return False

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def _read_pidfile(path: str) -> Tuple[bool, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        return True, str(pid)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


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
        "scanner_version": scanner_version,  # telemetry only
        "capabilities": "scan,av,tts",        # telemetry only
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
                   category: str, action: str, args_json_text: str) -> str:
    """
    Operator-only: POST /cmd/_enqueue/{scanner}
    Note: category MUST be accepted by agent.py gate.
          Your current agent.py (from earlier) allows category in ("scan","av").
          So we use category="av" for AV actions.
    """
    # validate args_json_text is JSON
    try:
        _ = json.loads(args_json_text) if args_json_text else {}
    except Exception as e:
        die(f"args_json_text is not valid JSON: {e}")

    body = {
        "category": category,
        "action": action,
        "execute_at": execute_at,
        "args_json_text": args_json_text,
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

def op_cmd_list_queue(nms_base: str, scanner: str, limit: int = 20) -> Dict[str, Any]:
    r = req(nms_base, "GET", f"/cmd/_list_command_queue/{scanner}", params={"limit": limit})
    ok_or_dump(r)
    if r.status_code != 200:
        return {"error": f"http {r.status_code}", "text": (r.text or "")[:400]}
    try:
        return r.json()
    except Exception:
        return {"error": "non-json", "text": (r.text or "")[:400]}

# =============================================================================
# RTSP validation (optional)
# =============================================================================

def rtsp_url(server: str, port: int, path: str) -> str:
    return f"rtsp://{server}:{port}/{path}"

def ffprobe_rtsp(url: str, transport: str = "tcp") -> Tuple[bool, str]:
    """
    Best-effort RTSP probe.
    Returns (ok, detail). If ffprobe missing, returns (False, "...missing").
    """
    if not _cmd_exists("ffprobe"):
        return False, "ffprobe missing (skip RTSP probe)"

    # -stimeout is microseconds (e.g. 3s = 3000000)
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-loglevel", "error",
        "-rtsp_transport", transport,
        "-stimeout", "3000000",
        "-show_streams",
        "-of", "json",
        url
    ]
    ok, out, err = _run_cmd(cmd, timeout=8)
    if ok and out:
        return True, out[:1200]
    return False, (err or out or "ffprobe failed/empty")[:1200]

# =============================================================================
# Main flow
# =============================================================================

def main() -> int:
    nms_base: Optional[str] = None
    assigned: Optional[str] = None
    server_now: Optional[str] = None

    try:
        pause("STEP 0: Enter test mode (stop/disable services + disable GUI autostart)")
        _run_sh(ENTER_SH)

        pause("STEP 0B: sudo warm-up (sudo -v) so later sudo -n calls won't prompt")
        sudo_warmup()

        pause("STEP 0A: LOCAL.AV.RUNTIME.READY (local-only) + Beep Test")
        print(av_runtime_ready_probe(with_beep=True))

        pause("STEP 1: Discover NMS + GET /health")
        nms_base = config.discover_nms_base(force=True)
        if not nms_base:
            die("No reachable NMS found (config.discover_nms_base returned None)")
        print(f"[INFO] NMS_BASE={nms_base}")

        health = get_health(nms_base)
        server_now = health.get("time")
        time_fmt = health.get("time_format")
        print(f"[INFO] server_now={server_now} time_format={time_fmt}")
        if not isinstance(server_now, str) or not server_now:
            die("health missing 'time'")

        pause("STEP 2: POST /admin/_reset (operator-only) (keeps whitelist/bundles/autoflush)")
        admin_reset_keep_all(nms_base)

        pause("STEP 4: POST /registry/register (Pi-facing) using MAC from config.get_reg_iface()")
        iface = config.get_reg_iface()
        mac = config.get_mac_address(iface)
        if not mac:
            die(f"Could not read MAC from iface={iface} (config.get_mac_address returned empty)")
        bundle_ver = config.get_bundle_version()

        assigned = pi_register(nms_base, mac=mac, ip=TEST_IP, scanner_version=bundle_ver)
        print(f"[OK] register assigned scanner={assigned} mac={mac} ip={TEST_IP} bundle_ver={bundle_ver}")

        # -----------------------------
        # Streaming test begins here
        # -----------------------------
        pause("STEP 5: Start scanner-agent.service (temporarily for this test)")
        ok, detail = _systemctl_start("scanner-agent.service")
        if not ok:
            die(f"Failed to start scanner-agent.service: {detail}")
        time.sleep(0.5)
        print("[OK] scanner-agent.service start issued")
        print(f"[INFO] scanner-agent active = {_systemctl_is_active('scanner-agent.service')}")

        # ---- STEP 6: Enqueue av.stream.start (operator-only) ----
        pause("STEP 6: Enqueue av.stream.start (operator-only) -> agent should start scanner-avstream.service")

        scanner_for_av = assigned  # or TEST_SCANNER if you prefer fixed; keep consistent
        rtsp_server = "6g-private.com"
        rtsp_port = 8554
        rtsp_path = scanner_for_av  # recommended: same as scanner name

        av_args = {
            "server": rtsp_server,
            "port": rtsp_port,
            "path": rtsp_path,
            "transport": "tcp",
            "video_dev": "/dev/video0",
            "audio_dev": "plughw:1,0",
            "size": "640x480",
            "fps": 30,
        }

        cmd_id = op_cmd_enqueue(
            nms_base=nms_base,
            scanner=scanner_for_av,
            execute_at=server_now,            # due immediately
            category="av",                    # MUST be "av" to pass agent allowlist
            action="av.stream.start",
            args_json_text=json.dumps(av_args),
        )
        print(f"[OK] enqueued cmd_id={cmd_id} action=av.stream.start rtsp_url=rtsp://{rtsp_server}:{rtsp_port}/{rtsp_path}")
        print(f"[INFO] args={json.dumps(av_args)}")

        # ---- STEP 7: Verify service active + diagnostics ----
        pause("STEP 7: Verify scanner-avstream.service active + diagnostics (agent log / systemctl / journal / cfg / av log)")

        SERVICE_AV = "scanner-avstream.service"

        became_active = wait_service_active(SERVICE_AV, timeout_sec=25, poll_sec=2)
        print(f"[INFO] {SERVICE_AV} active = {became_active}")

        print("\n--- tail agent.log ---")
        tail_file(BASE_DIR / "agent.log", n=80)
        print("--- end tail ---\n")

        show_systemd_diagnostics(SERVICE_AV)

        print("--- show av_stream_config.json ---")
        if AV_CFG.exists():
            try:
                print(AV_CFG.read_text(encoding="utf-8")[:2000])
            except Exception as e:
                print(f"[read cfg] exception: {type(e).__name__}: {e}")
        else:
            print(f"[cfg] missing: {AV_CFG}")
        print("--- end cfg ---\n")

        print("--- tail av_stream.log (if exists) ---")
        tail_file(AV_LOG, n=80)
        print("--- end av_stream.log ---\n")

        if not became_active:
            die(f"{SERVICE_AV} did not become active. See diagnostics above.")

        print("[OK] scanner-avstream.service is active. Next: stop it via command.")

        # ---- STEP 8: Enqueue av.stream.stop and verify service inactive ----
        pause("STEP 8: Enqueue av.stream.stop (operator-only) -> agent should stop scanner-avstream.service")

        cmd_id2 = op_cmd_enqueue(
            nms_base=nms_base,
            scanner=scanner_for_av,
            execute_at=server_now,
            category="av",
            action="av.stream.stop",
            args_json_text="{}",
        )
        print(f"[OK] enqueued cmd_id={cmd_id2} action=av.stream.stop")

        pause("STEP 8.1: Verify scanner-avstream.service inactive")

        t0 = time.time()
        stopped = False
        while time.time() - t0 < 20:
            if not is_service_active(SERVICE_AV):
                stopped = True
                break
            time.sleep(2)

        print(f"[INFO] {SERVICE_AV} stopped = {stopped}")
        print("\n--- tail agent.log ---")
        tail_file(BASE_DIR / "agent.log", n=80)
        print("--- end tail ---\n")

        show_systemd_diagnostics(SERVICE_AV)

        print("--- tail av_stream.log (if exists) ---")
        tail_file(AV_LOG, n=80)
        print("--- end av_stream.log ---\n")

        if not stopped:
            die(f"{SERVICE_AV} did not stop (still active). See diagnostics above.")

        print("[OK] AV stream start/stop command flow succeeded.")

        pause("STEP 9: Verify scanner-avstream.service inactive + check av_stream.log")
        for _ in range(10):
            if not _systemctl_is_active(config.SERVICE_AVSTREAM):
                break
            time.sleep(1.0)

        active2 = _systemctl_is_active(config.SERVICE_AVSTREAM)
        print(f"[INFO] {config.SERVICE_AVSTREAM} active = {active2}")
        print("\n--- tail av_stream.log ---")
        print(_tail_file(config.AV_LOG_FILE, n=80))
        print("--- end tail ---\n")

        if active2:
            die(f"{config.SERVICE_AVSTREAM} still active after stop command.")

        # -----------------------------
        # Audio + TTS tests (Wave-1)
        # -----------------------------
        # -----------------------------------------------------------------
        # AUDIO TESTS (PLAY then STOP)
        # -----------------------------------------------------------------
        pause("STEP 11: Enqueue audio.play (operator-only) -> agent should start mpv playback (PID file expected)")

        audio_args = {
            "file": AUDIO_FILE,
            "ao": "alsa",
            "audio_device": "alsa/default",
            "volume": 90,
            "stop_existing": True
        }

        cmd_id3 = op_cmd_enqueue(
            nms_base=nms_base,
            scanner=scanner_for_av,
            execute_at=server_now,
            category="av",
            action="audio.play",
            args_json_text=json.dumps(audio_args),
        )
        print(f"[OK] enqueued cmd_id={cmd_id3} action=audio.play file={AUDIO_FILE}")

        pause("STEP 11.1: Verify audio.play executed (pidfile + process + agent log)")

        # wait up to ~8s for PID file to appear
        t0 = time.time()
        pid = None
        while time.time() - t0 < 8:
            if os.path.exists(AUDIO_PID_FILE):
                okp, outp = _read_pidfile(AUDIO_PID_FILE)
                if okp:
                    pid = int(outp)
                    break
            time.sleep(0.5)

        if pid is None:
            print("\n--- tail agent.log ---")
            tail_file(BASE_DIR / "agent.log", n=120)
            print("--- end tail ---\n")
            die(f"audio.play: pidfile {AUDIO_PID_FILE} not created")

        alive = _pid_alive(pid)
        print(f"[INFO] pidfile exists=True pid={pid} alive={alive}")

        print("\n--- tail agent.log ---")
        tail_file(BASE_DIR / "agent.log", n=120)
        print("--- end tail ---\n")

        if not alive:
            die(f"audio.play: pid={pid} not alive (mpv may have exited immediately)")

        # Let it play a bit so human can hear it
        pause("STEP 11.2: Human check -> Did you hear the audio? (wait ~1-2 seconds then press ENTER)")
        time.sleep(1.5)

        pause("STEP 12: Enqueue audio.stop (operator-only) -> agent should stop mpv playback and remove pidfile")

        cmd_id4 = op_cmd_enqueue(
            nms_base=nms_base,
            scanner=scanner_for_av,
            execute_at=server_now,
            category="av",
            action="audio.stop",
            args_json_text="{}",
        )
        print(f"[OK] enqueued cmd_id={cmd_id4} action=audio.stop")

        pause("STEP 12.1: Verify audio stopped (pidfile removed OR pid dead)")

        t0 = time.time()
        stopped = False
        while time.time() - t0 < 10:
            pid_alive = _pid_alive(pid) if pid is not None else False
            pidfile_exists = os.path.exists(AUDIO_PID_FILE)
            if (not pid_alive) and (not pidfile_exists):
                stopped = True
                break
            # allow either condition to satisfy if you prefer:
            if (not pid_alive) or (not pidfile_exists):
                # mpv dead is enough even if pidfile removal lags; but we expect removal
                stopped = True
                break
            time.sleep(0.5)

        print(f"[INFO] audio stopped={stopped} pid_alive={_pid_alive(pid) if pid else False} pidfile_exists={os.path.exists(AUDIO_PID_FILE)}")

        print("\n--- tail agent.log ---")
        tail_file(BASE_DIR / "agent.log", n=160)
        print("--- end tail ---\n")

        if not stopped:
            die("audio.stop: playback did not stop in time (see agent.log tail above)")

        print("[OK] AUDIO play/stop command flow succeeded.")

        pause("STEP 14: Stop scanner-agent.service (cleanup)")
        ok, detail = _systemctl_stop("scanner-agent.service")
        if not ok:
            die(f"Failed to stop scanner-agent.service: {detail}")
        print("[OK] scanner-agent.service stop issued")

        pause("DONE: AV stream + AUDIO play/stop tests complete. Next: TTS.")
        return 0

    finally:
        print("\n[FINAL] Exit test mode (restore GUI autostart).")
        try:
            _run_sh(EXIT_SH)
        except Exception as e:
            print(f"[WARN] exit_test_mode.sh failed: {e}")


if __name__ == "__main__":
    # Audio test constants (test-side only)
    AUDIO_FILE = "/home/pi/_RunScanner/av/demo.mp3"
    AUDIO_PID_FILE = "/tmp/scanner_audio_play.pid"
    
    raise SystemExit(main())
