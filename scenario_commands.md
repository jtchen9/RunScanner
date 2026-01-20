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
**NMS APIs:**
- `POST /registry/register`  (body: mac, ip?, scanner_version?, capabilities?)

---

### LOCAL.GUI.START
**Purpose:** Bring up 5" on-site GUI; show current registration identity and status.  
**Trigger:** Desktop autostart at login.  
**Pi implementation:** Tkinter `main.py` (runs LOCAL.REGISTER first, then displays status).  
**Observable:** GUI shows:
- `Scanner: scannerXX`
- `register=ok|blocked|offline ...`

### LOCAL.UPLOADER.START
**Purpose:** Start periodic upload of scan payloads to NMS using the assigned scanner name.  
**Trigger:** systemd service `scanner-uploader.service` (boot or manual restart).  
**Pi implementation:** `uploader.py` reads `/home/pi/_RunScanner/scanner_name.txt` and POSTs to `/ingest/{scanner}`.  
**Observable:**  
- `/home/pi/_RunScanner/uploader.log` shows `UPLOAD ok scanner=scannerXX ...`  
- NMS queue length increases for that scanner (e.g., `/debug/queue/{scannerXX}`)

---

## NMS (remote) commands
(Empty for now — will be filled starting Step 4 when we add NMS command polling.)

---

## Data reporting
(Reserved. Step 5+ will add scan upload and reporting-related actions.)
