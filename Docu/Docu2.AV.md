# Scenario Commands Log (Pi ↔ NMS)

This document defines Audio / Video related capabilities on the Pi side.
It is intentionally separate from Documentation1.Integration.md.

===

## Local (Pi-side) actions

(Triggered locally; NOT commands from NMS)

---

### LOCAL.AV.RUNTIME.READY

**Purpose:**
Initialize and validate the Audio / Video / Voice runtime environment on the Pi at GUI startup, without requiring any NMS command.
This ensures basic A/V and voice utilities are present and that the Pi can actually output sound (a common failure mode).

**Trigger:**
Desktop autostart at login (myscript.desktop), invoked by main.py.

**Pi implementation:**
main.py (Tkinter GUI) triggers this action before registration
(right after function blocks, before run_register_once()).
Status and results are reported via:
show_status() on the 5" GUI

**Behavior (what it does):**
Performs a lightweight runtime readiness check (no tuning, no streaming)
Verifies presence of:
audio output path
basic playback utilities
voice pipeline prerequisites (mic + output only, no STT yet)
Performs a test beep to confirm audible output

**Commands used (reference):**
Generate a 1s 880 Hz beep WAV:
`ffmpeg -f lavfi -i "sine=frequency=880:duration=1" -c:a pcm_s16le -ar 48000 -ac 1 /tmp/beep_880hz_1s.wav -y`
Play through ALSA default:
`mpv --ao=alsa --audio-device=alsa/default --no-video /tmp/beep_880hz_1s.wav`

**Observable outputs:**
GUI status text shows:
A/V runtime readiness summary (tools detected / missing)
Beep test result (OK / FAIL + error detail if failed)
Optional temporary files:
/tmp/beep_880hz_1s.wav (ephemeral test artifact)

**Notes:**
This is intentionally self-triggered, not an NMS command
Avoids mpv lavfi://... since some builds lack the lavfi protocol handler
This action is a prerequisite sanity check for:
Audio playback
TTS
Scripted conversation
LLM voice output (Wave-3)
Video streaming is orthogonal and controlled separately

===

## NMS → Pi Commands (A/V Streaming)

(Executed by agent.py)

Category convention:
av — real-time audio/video streaming and playback
(this avoids confusion with Wi-Fi RCPI channel scanning)

---

### NMS.CMD.AV.STREAM.START

**Purpose:**
Start live A/V streaming from the Pi to the MediaMTX server (RTSP publish).
This is the standard “go live now” trigger.

**Category:** av
**Action:** av.stream.start

**Required args (args_json):**

```json
{
  "server": "6g-private.com",
  "port": 8554,
  "path": "twin-scout-bravo",
  "transport": "tcp",
  "video_dev": "/dev/video0",
  "audio_dev": "plughw:1,0",
  "size": "640x480",
  "fps": 30
}
```

**Pi behavior:**
Writes args into:
/home/pi/\_RunScanner/av/av_stream_config.json
Starts streaming by enabling:
scanner-avstream.service

**Observable:**
systemctl status scanner-avstream.service → Active (running)
Log file grows:
/home/pi/\_RunScanner/av/av_stream.log
MediaMTX shows RTSP publisher at path (e.g. twin-scout-alpha)
Viewer access:
RTSP: rtsp://<server>:8554/<path> (VLC)
HLS / WebRTC via MediaMTX server

**ACK rule:**
ACK ok if config write + service start succeed
ACK error if config write or service start fails

---

### NMS.CMD.AV.STREAM.STOP

**Purpose:**
Stop live A/V streaming from the Pi.

**Category:** av
**Action:** av.stream.stop

**Optional args (args_json):**

```json
{}
```

**Pi behavior:**
Stops streaming service:
systemctl stop scanner-avstream.service

**Observable:**
Service becomes inactive
MediaMTX publisher disappears
Viewer disconnects or freezes

**ACK rule:**
ACK ok if stop succeeds (even if already stopped)
ACK error only if systemctl returns failure

---

### NMS.CMD.AUDIO.PLAY

**Purpose:**
Play a local audio file (mp3 / wav) on the Pi speaker or headphone.

**Category:** av
**Action:** audio.play

**Required args (args_json):**

```json
{
  "file": "/home/pi/_RunScanner/av/demo.mp3"
}
```

**Pi behavior:**
Plays audio using mpv (non-blocking)
Intended for alerts, demos, notifications

**Observable:**
/home/pi/\_RunScanner/agent.log shows playback start / PID
Optional PID file:
/tmp/scanner_audio_play.pid

**Notes:**
One-shot trigger (not a systemd service)
Short clips recommended

---

### NMS.CMD.AUDIO.STOP

**Purpose:**
Stop any currently-playing audio started by audio.play (mpv playback on the Pi).

**Category:** av
**Action:** audio.stop

**Required args (args_json):**
{}

**Optional args (args_json):**
{
"signal": "TERM",
"grace_ms": 800,
"force_kill": true
}

**Pi behavior:**
If /tmp/scanner_audio_play.pid exists, read PID and send a signal to stop playback (default: SIGTERM).
Wait briefly (grace_ms) for the process to exit.
If still alive and force_kill=true, send SIGKILL.
Remove the PID file (best-effort) after stopping.

**Observable:**
/home/pi/\_RunScanner/agent.log shows stop attempt and result, e.g.
RESULT ... detail=audio.stop ok pid=XXXX
or detail=audio.stop: no pidfile
PID file:
/tmp/scanner_audio_play.pid should be removed when stop succeeds.

**Notes:**
This command is primarily for testing, demos, and “long file” interruption.
It is safe to call even when nothing is playing (should return ok with a “nothing to stop” detail, or ok with no pidfile).
This command only manages playback started via your audio.play PID tracking. If mpv was launched manually outside the agent, it may not be affected unless you choose to broaden scope later (not required for Wave-1).

---

### NMS.CMD.TTS.SAY

**Purpose:**
Make the Pi speak a short text string (TTS).

**Category:** av
**Action:** tts.say

**Required args (args_json):**

```json
{
  "text": "Hello, this is scanner zero two."
}
```

**Optional args (args_json):**

```json
{
  "lead_silence_ms": 300,
  "voice": "en-us",
  "speed_wpm": 175,
  "volume": 90
}
```

**Pi behavior:**
Generates TTS wav (e.g. via espeak-ng)
Prepends short silence to avoid clipping
Plays wav using mpv (synchronous execution with timeout; agent continues after playback completes)
Prepends configurable lead silence (lead_silence_ms) to avoid first-phoneme clipping on ALSA startup

**Observable:**
Agent log shows TTS generation + playback
Temporary files:
/tmp/tts_raw.wav
/tmp/tts_pad.wav
/tmp/tts_padded.wav

**Notes:**
One-shot trigger
Keep text reasonably short (≲10–15 s speech) for reliable demos and responsiveness
