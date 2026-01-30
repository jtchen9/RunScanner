# Scenario Commands Log (Pi ↔ NMS)

This document defines all **currently supported actions** between Pi scanners and NMS.
It is the authoritative reference for **what NMS is allowed to send to Pi today**.

Future capabilities (audio / video / mobility / LLM) are intentionally NOT included.

---

## Local (Pi-side) actions
(Triggered locally; NOT commands from NMS)

---

### LOCAL.REGISTER
**Purpose:**  
Pi registers to NMS using its MAC address and obtains an assigned scanner name.

**Trigger:**  
Boot or login (invoked before GUI and before agent polling).

**Pi implementation:**  
`/home/pi/_RunScanner/register.py`

**Outputs:**
- `/home/pi/_RunScanner/scanner_name.txt` — assigned scanner name (e.g. `scanner01`)
- `/home/pi/_RunScanner/last_register.json` — telemetry/debug record
- `/home/pi/_RunScanner/nms_base.txt` — selected NMS base URL

**NMS API (Pi-facing):**
- `POST /registry/register`

**Notes:**
- `scanner_version` (bundle version) is telemetry-only
- Pi never decides upgrades or bundle selection

---

### LOCAL.GUI.START
**Purpose:**  
Start on-site 5" GUI for local monitoring and manual scan control.

**Trigger:**  
Desktop autostart at login (`myscript.desktop`).

**Pi implementation:**  
Tkinter GUI in `main.py`

**Observable UI information:**
- Scanner name
- Registration status
- Bundle version
- Scan start/stop state

---

### LOCAL.AGENT.START
**Purpose:**  
Start headless agent that polls NMS and executes commands.

**Trigger:**  
systemd service `scanner-agent.service`

**Pi implementation:**  
`agent.py`

**Observable:**
- `/home/pi/_RunScanner/agent.log`
- Regular poll / execute / ACK activity

**NMS APIs used:**
- `GET  /cmd/poll/{scanner}`
- `POST /cmd/ack/{scanner}`

---

### LOCAL.UPLOADER.START
**Purpose:**  
Start periodic upload of scan results to NMS.

**Trigger:**  
systemd service `scanner-uploader.service`

**Pi implementation:**  
`uploader.py`

**Observable:**
- `/home/pi/_RunScanner/uploader.log`
- `/ingest/{scanner}` queue grows on NMS

---

## NMS → Pi Commands
(Executed by `agent.py`)

**Important rules:**
- Only the commands listed below are supported
- Any unknown action is rejected and ACKed as error
- All timestamps use `TIME_FMT` (no UTC / no timezone)

---

### NMS.CMD.SCAN.START
**Purpose:**  
Start periodic Wi-Fi scanning.

**Category:** `scan`  
**Action:** `scan.start`

**Pi behavior:**
- `systemctl start scanner-poller.service`

**Observable:**
- Service active
- `/tmp/latest_scan.json` updates periodically
- ACK sent

---

### NMS.CMD.SCAN.STOP
**Purpose:**  
Stop periodic Wi-Fi scanning.

**Category:** `scan`  
**Action:** `scan.stop`

**Pi behavior:**
- `systemctl stop scanner-poller.service`

**Observable:**
- Service inactive
- No new scan data
- ACK sent

---

### NMS.CMD.SCAN.ONCE
**Purpose:**  
Perform a single Wi-Fi scan immediately.

**Category:** `scan`  
**Action:** `scan.once`

**Pi behavior:**
- Execute `scan_wifi.sh once`

**Observable:**
- `/tmp/latest_scan.json` updated once
- ACK sent

---

### NMS.CMD.BUNDLE.APPLY
**Purpose:**  
Switch Pi into a specific **experiment bundle**.

Bundles are **experiment profiles**, not firmware upgrades.
Pi performs no arbitration — NMS is authoritative.

**Category:** `scan`  
**Action:** `bundle.apply`

**Required args (`args_json`):**
```json
{
  "bundle_id": "robotBundle1.0",
  "url": "http://<nms>/bootstrap/bundle/robotBundle1.0"
}
```
