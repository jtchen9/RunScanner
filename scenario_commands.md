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

====================================================================
Audio/Video/LLM documentation:

### LOCAL.AV.RUNTIME.READY

Purpose:
    Initialize and validate the Audio/Video runtime environment on the Pi at GUI startup, without requiring any NMS command.
    This ensures basic A/V utilities are present and that the Pi can actually output sound (a common failure mode).

Trigger:
    Desktop autostart at login (myscript.desktop), invoked by main.py.

Pi implementation:
    main.py (Tkinter GUI) triggers this action before registration (right after function blocks, before run_register_once()).
    Uses show_status() to report results to the 5" GUI.

Behavior (what it does):
    Performs a lightweight A/V readiness check (no tuning, no streaming).
    Provides a test-beep function that:
        uses ffmpeg to generate a short WAV beep into /tmp (e.g. /tmp/beep_880hz_1s.wav)
        plays it using mpv with a known-good ALSA output path:
            mpv --ao=alsa --audio-device=alsa/default --no-video /tmp/beep_880hz_1s.wav

Commands used (reference):
    # generate a 1s 880Hz beep wav
    ffmpeg -f lavfi -i "sine=frequency=880:duration=1" -c:a pcm_s16le -ar 48000 -ac 1 /tmp/beep_880hz_1s.wav -y
    # play through ALSA default
    mpv --ao=alsa --audio-device=alsa/default --no-video /tmp/beep_880hz_1s.wav

Observable outputs:
    GUI status text shows:
        A/V readiness summary (tools detected / missing)
        Beep test result (OK/FAIL + error text if failed)
    Optional files:
        /tmp/beep_880hz_1s.wav (ephemeral test artifact)

Notes:
    This is intentionally self-triggered and not an NMS command.
    This avoids mpv lavfi://... since some builds lack the lavfi protocol handler.
    This action is a prerequisite sanity check for later capabilities:
        Play mp3/wav files
        TTS playback
        Conversation/LLM voice output
        (Video streaming is orthogonal but can be checked later)

---

### NMS.CMD.AV.STREAM.START

Purpose:
    Start live A/V streaming from the Pi to the MediaMTX server (RTSP publish).
    This is the standard “go live now” trigger.

Category: av
    
Action: av.stream.start

Required args (args_json):
    {
    "server": "6g-private.com",
    "port": 8554,
    "path": "scanner02",
    "transport": "tcp",
    "video_dev": "/dev/video0",
    "audio_dev": "plughw:1,0",
    "size": "640x480",
    "fps": 30
    }

Pi behavior:
    Write the args into a local config file:
        /home/pi/_RunScanner/av/av_stream_config.json
    Start streaming by turning on a systemd service:
        systemctl start scanner-avstream.service

Observable:
    systemctl status scanner-avstream.service shows Active (running)
    Log file grows:
        /home/pi/_RunScanner/av/av_stream.log
    MediaMTX shows the RTSP publisher on the given path (e.g. scanner02)
    Viewer can watch:
        RTSP: rtsp://<server>:8554/<path> (VLC)
        HLS/WebRTC: provided by MediaMTX server side

ACK rule:
    ACK ok if config written + systemctl start ... succeeds.
    ACK error with detail if config write fails or service fails to start.

---

### NMS.CMD.AV.STREAM.STOP

Purpose:
    Stop live A/V streaming from the Pi.

Category: av

Action: av.stream.stop

Optional args (args_json):
    {}

Pi behavior:
    Stop the streaming service:
        systemctl stop scanner-avstream.service

Observable:
    systemctl status scanner-avstream.service shows Inactive (dead)
    MediaMTX publisher on that path disappears shortly after stop
    Viewer disconnects or freezes

ACK rule:
    ACK ok if systemctl stop ... succeeds (even if it was already stopped).
    ACK error only if systemctl returns a failure.

---

### NMS.CMD.AUDIO.PLAY

Purpose:
    Play an audio file (mp3/wav) locally on the Pi speaker/headphone, for demo/attention/notification.

Category: av

Action: audio.play

Required args (args_json):
    {
    "file": "/home/pi/_RunScanner/av/demo.mp3"
    }

Optional args (args_json):

Pi behavior:
    Optionally stops an existing audio playback process (if stop_existing=true).
    Starts playback using mpv (non-blocking):
        mpv --ao=alsa --audio-device=alsa/default --no-video --volume=<volume> <file>

Observable:
    /home/pi/_RunScanner/agent.log shows START + PID
    Optional pid file: /tmp/scanner_audio_play.pid

Notes:
    This is a one-shot trigger (not a systemd service).
    Intended for short clips, alerts, “robot moving” music, etc.

---

### NMS.CMD.TTS.SAY

Purpose:
Make the Pi speak a short text string (TTS) through its audio output.

Category: av

Action: tts.say

Required args (args_json):


Optional args (args_json):
    {
    "lead_silence_ms": 300,
    "voice": "en-us",
    "speed_wpm": 175,
    "volume": 90
    }

Pi behavior:
    Generates a temporary wav via espeak-ng (or your chosen TTS backend).
    Prepends a short silence (default 300ms) to prevent clipping of the first few words.
    Plays the resulting wav using mpv (blocking until finished, but with a short safety timeout).

Observable:
    /home/pi/_RunScanner/agent.log contains TTS generation + playback result
    Temporary files:
        /tmp/tts_raw.wav
        /tmp/tts_padded.wav

Notes:
    This is a one-shot trigger (not a systemd service).
    Long text is allowed but not recommended; keep it short for reliable demo timing.

---

