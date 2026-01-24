# Scenario Commands Log (Pi ↔ NMS)

This file records command-like actions that a script writer can use to build multi-Pi lab scenarios.
We will append/update this file as new abilities are added.

---

## Local (Pi-side) actions

### LOCAL.REGISTER
**Purpose:** Pi registers to NMS using MAC and obtains assigned scanner name.  
**Trigger:** Boot/login (invoked before GUI starts).  
**Pi implementation:** `/home/pi/_RunScanner/register.py`  
**Outputs:**
- `/home/pi/_RunScanner/scanner_name.txt` (assigned name, e.g. scanner01)
- `/home/pi/_RunScanner/last_register.json` (status record)
- `/home/pi/_RunScanner/nms_base.txt` (selected NMS base URL)
**NMS APIs:**
- `POST /registry/register` (body: mac, ip?, scanner_version?, capabilities?)

scanner_version reports bundle version from bundles/active_bundle.txt
Pi does not decide upgrades

---

### LOCAL.GUI.START
**Purpose:** Bring up 5" on-site GUI; show current registration identity and status.  
**Trigger:** Desktop autostart at login.  
**Pi implementation:** Tkinter `main.py` (runs LOCAL.REGISTER first, then displays status).  
**Observable:** GUI shows:
- `Scanner: scannerXX`
- `NMS: online|offline (ip:port)`
- `register=ok|blocked|offline ...`

---

### LOCAL.AGENT.START
**Purpose:** Start headless command agent that polls NMS and executes commands.  
**Trigger:** systemd service `scanner-agent.service` (boot or manual restart).  
**Pi implementation:** `agent.py`  
**Observable:**
- `/home/pi/_RunScanner/agent.log` shows `agent started ...`
- Regular `poll ok` / `poll fail` messages
**NMS APIs:**
- `GET /cmd/poll/{scanner}`
- `POST /cmd/ack/{scanner}`

---

### LOCAL.UPLOADER.START
**Purpose:** Start periodic upload of scan payloads to NMS using the assigned scanner name.  
**Trigger:** systemd service `scanner-uploader.service` (boot or manual restart).  
**Pi implementation:** `uploader.py` reads `/home/pi/_RunScanner/scanner_name.txt` and POSTs to `/ingest/{scanner}`.  
**Observable:**  
- `/home/pi/_RunScanner/uploader.log` shows `UPLOAD ok scanner=scannerXX ...`  
- NMS queue length increases for that scanner (e.g., `/debug/queue/{scannerXX}`)

---

## NMS → Pi commands (executed by agent)

### NMS.CMD.SCAN.START
**Purpose:** Start periodic Wi-Fi scanning on Pi.  
**Trigger:** NMS issues command with execute_at <= now.  
**Pi implementation:** `systemctl start scanner-poller.service`  
**Observable:**  
- `scanner-poller.service` active  
- `/tmp/latest_scan.json` updates periodically  
- ACK sent with status `ok|error`

---

### NMS.CMD.SCAN.STOP
**Purpose:** Stop periodic Wi-Fi scanning.  
**Trigger:** NMS command.  
**Pi implementation:** `systemctl stop scanner-poller.service`  
**Observable:**  
- service inactive  
- no new scan files  
- ACK sent

---

### NMS.CMD.SCAN.ONCE
**Purpose:** Perform a single Wi-Fi scan immediately.  
**Trigger:** NMS command.  
**Pi implementation:** run `scan_wifi.sh once`  
**Observable:**  
- `/tmp/latest_scan.json` updated once  
- ACK sent

---

### NMS.CMD.BUNDLE.APPLY
**Purpose:** Install or upgrade an experiment bundle on Pi.  
**Trigger:** NMS command with highest priority.  
**Pi behavior (authoritative):**
1. Stop all running services (scan + uploader).
2. Download bundle ZIP.
3. Extract to `/home/pi/_RunScanner/bundles/{bundle_id}`.
4. Switch `bundles/active` symlink.
5. Run `install.sh` if present.
6. Restart uploader service only.

**Pi implementation:** `bundle_manager.apply_bundle()`  
**Args (args_json):**
```json
{"bundle_id": "robotBundle1.1"}
bundle_id must match Windows-safe format robotBundleX.Y
Note: current Pi agent expects bundle_id and url as top-level command fields (not nested in args_json)
Pi reports via: POST /bootstrap/report/{scanner}

