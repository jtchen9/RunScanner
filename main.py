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

SERVICE_NAME = "scanner-poller.service"
SYSTEMCTL = "/usr/bin/systemctl"   
SUDO      = "/usr/bin/sudo"       
BASE_DIR = Path("/home/pi/_RunScanner")
REGISTER_PY = BASE_DIR / "register.py"
SCANNER_NAME_FILE = BASE_DIR / "scanner_name.txt"
LAST_REGISTER_FILE = BASE_DIR / "last_register.json"

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
    ok, out, _ = _run_systemctl(["is-active", SERVICE_NAME])
    # systemctl is-active returns nonzero when inactive; treat output text for truth
    return ok and out.strip() == "active"

def service_start():
    return _run_systemctl(["start", SERVICE_NAME])

def service_stop():
    return _run_systemctl(["stop", SERVICE_NAME])

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

# ---- Log buffer ----
log_records = []   # will store all messages

def show_status(msg: str, log: bool = True):
    """Update status_box with the same message we print, optionally logging."""
    print(msg)
    if log:
        log_records.append(msg)

    # update the text box
    status_box.config(state="normal")
    status_box.delete("1.0", tk.END)
    status_box.insert(tk.END, msg)
    status_box.config(state="disabled")

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
    show_status("Hello B10!")

def function13():
    show_status("Hello B13!")

def function20():
    show_status("Hello B20!")

def function23():
    show_status("Hello B23!")

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

button10 = ttk.Button(root, text="B10", command=function10)
button10.grid(row=1, column=0, sticky="EWNS")

button13 = ttk.Button(root, text="B13", command=function13)
button13.grid(row=1, column=3, sticky="EWNS")

button20 = ttk.Button(root, text="B20", command=function20)
button20.grid(row=2, column=0, sticky="EWNS")

button23 = ttk.Button(root, text="B23", command=function23)
button23.grid(row=2, column=3, sticky="EWNS")

root.mainloop()
