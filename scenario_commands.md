
---

### NMS.CMD.SCAN.START
**Purpose:**  
Start periodic Wi-Fi scanning.

**Action string:**  
`scan.start`

**Pi behavior:**  
- `systemctl start scanner-poller.service`

**Observable:**
- Service becomes active
- `/tmp/latest_scan.json` updates periodically
- ACK sent

---

### NMS.CMD.SCAN.STOP
**Purpose:**  
Stop periodic Wi-Fi scanning.

**Action string:**  
`scan.stop`

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

**Action string:**  
`scan.once`

**Pi behavior:**  
- Execute `scan_wifi.sh once`

**Observable:**
- `/tmp/latest_scan.json` updated once
- ACK sent

---

### NMS.CMD.BUNDLE.APPLY
**Purpose:**  
Switch Pi into a specific **experiment bundle**.

This is **not** an upgrade/downgrade concept.
Bundles are parallel experiment profiles.

**Action string:**  
`bundle.apply`

**Required args (args_json):**
```json
{
"bundle_id": "robotBundle1.0",
"url": "http://<nms>/bootstrap/bundle/robotBundle1.0"
}
