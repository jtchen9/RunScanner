Voice Control & Interaction — Wave-2 (Revised)

This document defines voice-related capabilities on the Pi side, including
long-running listening behavior, local interaction logic, and how NMS and GUI
are allowed to influence voice state.

> ⚠️ IMPORTANT (Debian / Raspberry Pi OS – Python packages)
>
> This project uses Python packages that are **NOT available via apt**.
> On Debian Bookworm / Raspberry Pi OS, pip is externally managed (PEP 668).
>
> You MUST install the following with pip using these exact commands:
>
> ```bash
> pip3 install --user --break-system-packages webrtcvad
> pip3 install --user --break-system-packages vosk
> ```
>
> Notes:
>
> - `webrtcvad` is used for speech / silence detection
> - `vosk` is used for offline speech-to-text (STT)
> - Both are required for Wave-2 voice features
> - The voice service must run as **User=pi** to see these packages
>
> If `import vosk` fails, check:
>
> ```bash
> python3 -c "import vosk; print(vosk.__file__)"
> systemctl cat scanner-voice.service
> ```

===

# 1. Control Surfaces

## 1.1 Agent (NMS-driven)

**Used for:**
Long-running scenarios (minutes → hours)
Scheduled or unattended experiments
Scripted human–robot interaction over time

**Implemented in:**
agent.py
scanner-voice.service

**Restrictions:**
Agent cannot directly force CONVERSATION or LLM
Agent may only toggle DEAF ⇄ NAME_LISTEN
Agent may update scripted phrases

## 1.2 Local GUI (5" touchscreen)

**Used for:**
On-site debugging
Recovery when something goes wrong
Audio / voice sanity checks

**Constraints:**
Button-based UI only
No CLI interaction

**GUI Voice Controls:**
One shared toggle button: DEAF ⇄ NAME_LISTEN
One test button: VOICE.TEST.PROMPT

===

# 2. Voice Listening Modes

Mode Meaning
deaf No microphone processing
name_listen Lightweight listening for own calling name
conversation Short-lived scripted interaction
llm Placeholder (Wave-3 only)

Each Pi has one identity name (e.g. twin-scout-alpha), obtained during
registration and used as the wake name.
There are no internal scannerXX IDs.

===

# 3. Voice State Machine (Authoritative)

## 3.1 Allowed Transitions

DEAF ⇄ NAME_LISTEN
NAME_LISTEN → CONVERSATION
CONVERSATION → NAME_LISTEN
CONVERSATION → LLM (Wave-3 only)
LLM → NAME_LISTEN (Wave-3 only)
ANY → NAME_LISTEN (safety fallback on error)

## 3.2 Transition Triggers

**External (Agent / GUI)**
DEAF ⇄ NAME_LISTEN
GUI toggle button
NMS.CMD.VOICE.MODE.SET
External control is strictly limited to these two modes.

**Internal (voice service only)**
NAME_LISTEN → CONVERSATION
Wake-name detected
CONVERSATION → NAME_LISTEN
Timeout
CONVERSATION → LLM
Script action enter.llm matched (Wave-3)
LLM → NAME_LISTEN
Timeout (Wave-3)
ANY → NAME_LISTEN
Internal error / safety fallback

## 3.3 Disallowed Transitions (by design)

    DEAF → CONVERSATION ❌
    DEAF → LLM ❌
    NAME_LISTEN → LLM ❌
    LLM → CONVERSATION ❌
    Any NMS / GUI command forcing CONVERSATION or LLM ❌

**Rationale:**
This ensures that:
Heavy processing is entered only via internal triggers
CONVERSATION and LLM are self-terminating
CPU usage is bounded and predictable

===

# 4. Scenario Commands Log (Pi ↔ NMS)

===

## LOCAL Voice Behaviors (Internal)

These are not NMS commands.
They define what the voice service actually does in each mode.

===

### LOCAL.DEAF

**Purpose:**
Disable all microphone processing.

**Behavior:**
No audio capture
No STT
Minimal CPU usage

**Entry conditions:**
GUI toggle
NMS.CMD.VOICE.MODE.SET
Startup default (optional)

**Exit conditions:**
GUI toggle
NMS.CMD.VOICE.MODE.SET

---

### LOCAL.NAME_LISTEN

**Purpose:**
Lightweight listening for the Pi’s own calling name.

**Behavior:**
Minimal STT / wake-name detection
No full transcription
Optimized for low CPU usage

**Entry conditions:**
GUI toggle
NMS.CMD.VOICE.MODE.SET
Fallback from any error
Timeout from CONVERSATION / LLM

**Exit conditions:**
Wake-name detected → CONVERSATION
GUI / NMS toggle → DEAF

---

### LOCAL.CONVERSATION

**Purpose:**
Short-lived scripted human interaction.

**Behavior:**
Full STT enabled
Matches phrases against scripted table
Speaks reply via TTS
Optional local actions

**Entry conditions:**
Wake-name detected in NAME_LISTEN

**Exit conditions:**
Timeout → NAME_LISTEN
Safety error → NAME_LISTEN

**Notes:**
No explicit “conversation done” logic required
Timeout is sufficient and simpler

---

### LOCAL.LLM (Wave-3)

**Purpose:**
Temporary free-form interaction with an LLM.

**Entry conditions:**
Scripted action enter.llm during CONVERSATION

**Exit conditions:**
Timeout → NAME_LISTEN
Error → NAME_LISTENv

===

## NMS Commands (Wave-2)

Command Summary
Command Category Agent GUI Notes
VOICE.MODE.SET voice ✅ ❌ DEAF / NAME_LISTEN only
VOICE.SCRIPT.SET voice ✅ ❌ Scenario control
VOICE.TEST.PROMPT voice ❌ ✅ Debug only

VOICE.START / VOICE.STOP are no longer scenario tools
Voice service lifecycle is handled by systemd

===

### NMS.CMD.VOICE.START

**Purpose:**
Start the long-running voice service and enter an initial listening mode.

**Category:** voice
**Action:** voice.start

**Required args (args_json):**

```json
{
  "mode": "name_listen",
  "conversation_timeout_sec": 20,
  "llm_timeout_sec": 30
}
```

**Pi behavior:**
Starts scanner-voice.service
Loads configuration
Enters requested mode
Uses its own assigned name as wake-name

**Observable:**
systemctl status scanner-voice.service
/home/pi/\_RunScanner/voice/voice.log

---

### NMS.CMD.VOICE.STOP

**Purpose:**
Stop the voice service and disable microphone processing.

**Category:** voice
**Action:** voice.stop

**Required args (args_json):**

```json
{}
```

**Pi behavior:**
Stops scanner-voice.service
Enters deaf state

**Observable:**
Service inactive
ACK sent

---

### NMS.CMD.VOICE.MODE.SET

**Purpose:**
Enable or disable voice listening without restarting the service.

**Category:** voice
**Action:** voice.mode.set

**Required args (args_json):**

```JSON
{
  "mode": "name_listen"
}
```

**Allowed values:**
deaf
name_listen

**Pi behavior:**
Updates voice mode immediately
Rejects attempts to set conversation or llm
Does not restart service

**Notes:**
Semantically identical to GUI toggle
Intended for scenario orchestration

---

### NMS.CMD.VOICE.SCRIPT.SET (agent.py only)

**Purpose:**
Replace the scripted phrase–response table used in CONVERSATION mode.

**Category:** voice
**Action:** voice.script.set

**Required args (args_json):**

```JSON
{
  "commands": [
    {
      "phrase": "How are you",
      "reply": "System is operating normally.",
      "action": "status.report"
    },
    {
      "phrase": "Let's talk",
      "reply": "OK.",
      "action": "enter.llm"
    }
  ]
}
```

**Pi behavior:**
Replaces all existing scripted commands
Does not change current mode
Used only after CONVERSATION is entered internally

**Notes:**
enter.llm is a placeholder in Wave-2
No cloud calls in Wave-2

---

### NMS.CMD.VOICE.TEST.PROMPT (GUI only)

**Purpose:**
On-site test hook for voice output without microphone input.

**Category:** voice
**Action:** voice.test.prompt

**Required args (args_json):**

```JSON
{
  "say": "Voice test is running.",
  "beep": true
}
```

**Pi behavior:**
Optional beep
Optional TTS playback
Does not affect voice mode
Does not require voice service running

---
