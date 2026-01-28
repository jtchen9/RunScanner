import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from datetime import datetime 
import subprocess
import threading
import json
from pathlib import Path
from config import local_ts
from config import get_bundle_version
import shutil
from config import SYSTEMCTL, SUDO, SERVICE_NAME_SCANNER_POLLER

BASE_DIR = Path("/home/pi/_RunScanner")
REGISTER_PY = BASE_DIR / "register.py"
SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
LAST_REGISTER_FILE = BASE_DIR / "last_register.json"

# ---- Startup cached messages (for pre-GUI probes) ----
status_box = None              # will be created later
startup_messages = []          # list[str], printed/logged before GUI exists

def _cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def _run_cmd(cmd_list, timeout=3):
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

def show_status(msg: str, log: bool = True):
    """
    Update status_box if GUI exists; otherwise cache to startup_messages.
    """
    print(msg)
    if log:
        log_records.append(msg)

    global status_box
    if status_box is None:
        # GUI not created yet; cache for later display
        startup_messages.append(msg)
        return

    status_box.config(state="normal")
    status_box.delete("1.0", tk.END)
    status_box.insert(tk.END, msg)
    status_box.config(state="disabled")

def av_runtime_ready_probe() -> str:
    """
    LOCAL.AV.RUNTIME.READY probe.
    Fast, non-invasive checks:
      - device nodes exist
      - tools exist
      - basic device listing via arecord/aplay
    Returns a multi-line report string.
    """
    lines = []
    lines.append("LOCAL.AV.RUNTIME.READY")
    lines.append(f"Time: {local_ts()}")
    lines.append("")

    # 1) Video device
    cam_ok = Path("/dev/video0").exists()
    lines.append(f"Video: /dev/video0 exists = {cam_ok}")

    # 2) Audio devices listing (ALSA)
    if _cmd_exists("arecord"):
        ok, out, err = _run_cmd(["arecord", "-l"], timeout=3)
        lines.append(f"Audio: arecord -l = {'OK' if ok else 'FAIL'}")
        if out:
            # keep it short on the 5" screen
            lines.extend(["  " + s for s in out.splitlines()[:6]])
        elif err:
            lines.append("  " + err.splitlines()[0][:120])
    else:
        lines.append("Audio: arecord not found")

    if _cmd_exists("aplay"):
        ok, out, err = _run_cmd(["aplay", "-l"], timeout=3)
        lines.append(f"Audio: aplay -l = {'OK' if ok else 'FAIL'}")
        if out:
            lines.extend(["  " + s for s in out.splitlines()[:6]])
        elif err:
            lines.append("  " + err.splitlines()[0][:120])
    else:
        lines.append("Audio: aplay not found")

    # 3) Tools availability
    lines.append("")
    lines.append("Tools:")
    lines.append(f"  ffmpeg = {'OK' if _cmd_exists('ffmpeg') else 'MISSING'}")
    lines.append(f"  mpv    = {'OK' if _cmd_exists('mpv') else 'MISSING'}")
    lines.append(f"  ffplay = {'OK' if _cmd_exists('ffplay') else 'MISSING'}")
    lines.append("")

    # 4) Quick hint (your known-good playback path)
    lines.append("Known-good audio test:")
    lines.append("  mpv --ao=alsa --audio-device=alsa/default --no-video ~/music.mp3")
    lines.append("")

    return "\n".join(lines)

def play_test_beep():
    """
    Reliable beep:
    1) generate a 1s WAV sine wave into /tmp
    2) play it through mpv using the known-good ALSA settings
    """
    tmp_wav = "/tmp/beep_880hz_1s.wav"

    # 1) Generate WAV using ffmpeg (no speaker needed on the host, just file gen)
    if not _cmd_exists("ffmpeg"):
        return False, "ffmpeg missing"

    gen_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", "sine=frequency=880:duration=1",
        "-ac", "1", "-ar", "48000",
        "-c:a", "pcm_s16le",
        "-y", tmp_wav
    ]
    ok, out, err = _run_cmd(gen_cmd, timeout=5)
    if not ok:
        return False, f"ffmpeg gen failed: {err or out}"

    # 2) Play WAV using mpv with the same output path you confirmed works
    if not _cmd_exists("mpv"):
        return False, "mpv missing"

    play_cmd = [
        "mpv",
        "--ao=alsa",
        "--audio-device=alsa/default",
        "--no-video",
        tmp_wav
    ]
    ok2, out2, err2 = _run_cmd(play_cmd, timeout=6)
    if not ok2:
        return False, f"mpv play failed: {err2 or out2}"

    return True, "ok"

def run_register_once():
    """Run register.py once; do not crash GUI if it fails."""
    try:
        subprocess.run(
            ["/usr/bin/python3", str(REGISTER_PY)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        pass

def read_scanner_name() -> str:
    try:
        return SCANNER_NAME_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def read_register_status() -> str:
    try:
        j = json.loads(LAST_REGISTER_FILE.read_text(encoding="utf-8"))
        status = j.get("status", "")
        detail = j.get("detail", "")
        http_code = j.get("http_code", "")
        return f"register={status} http={http_code} {detail}".strip()
    except Exception:
        return "register=unknown"
    
def _run_systemctl(args):
    """
    Run systemctl. Try without sudo first; if that fails, retry with sudo -n.
    Returns (ok: bool, stdout: str, stderr: str)
    """
    # 1) try without sudo
    try:
        cp = subprocess.run(
            [SYSTEMCTL] + args,
            check=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return True, cp.stdout.strip(), cp.stderr.strip()
    except subprocess.CalledProcessError as e1:
        # 2) retry with sudo -n
        try:
            cp2 = subprocess.run(
                [SUDO, "-n", SYSTEMCTL] + args,
                check=True,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            )
            return True, cp2.stdout.strip(), cp2.stderr.strip()
        except subprocess.CalledProcessError as e2:
            return False, (e2.stdout or "").strip(), (e2.stderr or e1.stderr or "").strip()
        
def service_is_active():
    ok, out, _ = _run_systemctl(["is-active", SERVICE_NAME_SCANNER_POLLER])
    # systemctl is-active returns nonzero when inactive; treat output text for truth
    return ok and out.strip() == "active"

def service_start():
    return _run_systemctl(["start", SERVICE_NAME_SCANNER_POLLER])

def service_stop():
    return _run_systemctl(["stop", SERVICE_NAME_SCANNER_POLLER])

def set_button_and_status(active: bool):
    """Update button label + status text based on active flag."""
    now_str = local_ts()

    reg_line = f"Scanner: {scanner_name or '(unassigned)'}  Bundle: {get_bundle_version()}\n{register_status}"
    scan_line = f"Scanning Channel since {now_str}" if active else f"Stop Scanning at {now_str}"

    if active:
        button01.config(text="Stop Scan")
    else:
        button01.config(text="Scan Channel")

    show_status(reg_line + "\n" + scan_line)

# ---- Grid functions ----
def function00():
    messagebox.showinfo("Information", "This is a Major Alert!")
    show_status("This is a Major Alert!")

def function01():
    """Toggle scanner-poller service start/stop without freezing the GUI."""
    def worker():
        global scanningChannel
        if not scanningChannel:
            ok, out, err = service_start()
            if ok:
                scanningChannel = True
                root.after(0, lambda: set_button_and_status(True))
            else:
                root.after(0, lambda: show_status(
                    f"Failed to start scanner-poller\n{err or out}", log=True))
        else:
            ok, out, err = service_stop()
            if ok:
                scanningChannel = False
                root.after(0, lambda: set_button_and_status(False))
            else:
                root.after(0, lambda: show_status(
                    f"Failed to stop scanner-poller\n{err or out}", log=True))
    threading.Thread(target=worker, daemon=True).start()

def function02():
    # Show last 10 log messages
    if log_records:
        last10 = log_records[-10:]
        combined = "\n".join(last10)
        show_status(combined, log=False)  # do not log "Show Log"
    else:
        show_status("Log is empty", log=False)

def function03():
    show_status("Quitting application...")
    root.after(500, root.destroy)

def function10():
    # Re-run AV readiness probe and show it
    rep = av_runtime_ready_probe()
    show_status(rep, log=True)

def function13():
    ok, detail = play_test_beep()
    show_status("Test beep: OK" if ok else f"Test beep: FAIL\n{detail}", log=True)

def function20():
    show_status("Hello B20!")

def function23():
    show_status("Hello B23!")

log_records = []   # will store all messages

# ---- LOCAL.AV.RUNTIME.READY (run BEFORE registration) ----
try:
    av_report = av_runtime_ready_probe()
    show_status(av_report, log=True)   # pre-GUI: cached into startup_messages
except Exception as e:
    av_report = f"LOCAL.AV.RUNTIME.READY failed: {e}"
    show_status(av_report, log=True)

# ---- Registration (after AV probe) ----
run_register_once()
scanner_name = read_scanner_name()
register_status = read_register_status()

# root window
root = tk.Tk()
root.geometry("740x410")
root.resizable(False, False)
root.title("Wi-Fi Digital Twins")

# ---- Make grid cells uniform ----
for c in range(4):   # 4 columns
    root.grid_columnconfigure(c, weight=1, uniform="col", minsize=185)   # 740 / 4
for r in range(3):   # 3 rows
    root.grid_rowconfigure(r, weight=1, uniform="row", minsize=135)     # 410 / 3

# ---- Status Text in the center 2x2 block ----
status_box = tk.Text(root, font=("Segoe UI", 14), fg="blue", wrap="word", height=6)
status_box.grid(row=1, column=1, rowspan=2, columnspan=2, sticky="nsew")
status_box.config(state="disabled")  # make it read-only

# Show AV report first, then registration summary
if startup_messages:
    show_status(startup_messages[-1], log=False)  # show last cached report on screen

show_status(f"Scanner: {scanner_name or '(unassigned)'}\n{register_status}", log=True)

# Show registration info at GUI startup
show_status(f"Scanner: {scanner_name or '(unassigned)'}\n{register_status}", log=True)

# ---- Button styles ----
style = ttk.Style()
style.theme_use('alt')
style.configure(
  'TButton', 
  background='red', 
  foreground='black', 
  width=20, 
  borderwidth=1, 
  focusthickness=3, 
  focuscolor='none', 
  font=("Segoe UI", 14))

# ---- Buttons ----
button00 = ttk.Button(root, text="Show Alert!", command=function00)
button00.grid(row=0, column=0, sticky="EWNS")

scanningChannel = False
button01 = ttk.Button(root, text="Scan Channel", command=function01)
button01.grid(row=0, column=1, sticky="EWNS")

# Detect actual service state at startup so UI matches reality
try:
    scanningChannel = service_is_active()
except Exception:
    scanningChannel = False  # fall back if systemctl not accessible

set_button_and_status(scanningChannel)

button02 = ttk.Button(root, text="Show Log", command=function02)
button02.grid(row=0, column=2, sticky="EWNS")

button03 = ttk.Button(root, text="Quit", command=function03)
button03.grid(row=0, column=3, sticky="EWNS")

button10 = ttk.Button(root, text="AV Readiness", command=function10)
button10.grid(row=1, column=0, sticky="EWNS")

button13 = ttk.Button(root, text="Beep Test", command=function13)
button13.grid(row=1, column=3, sticky="EWNS")

button20 = ttk.Button(root, text="B20", command=function20)
button20.grid(row=2, column=0, sticky="EWNS")

button23 = ttk.Button(root, text="B23", command=function23)
button23.grid(row=2, column=3, sticky="EWNS")

root.mainloop()
