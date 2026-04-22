# Wave-3 Voice LLM Integration

This document defines Wave-3 voice commands and behaviors, enabling the Pi to route recognized speech to an Internet-connected LLM, receive a text response, and optionally speak it back using TTS.
Wave-3 builds on Wave-2 voice state machine and does not modify STT, mic tuning, or wake logic.

## Overview

**Wave-3 adds:**
    Online LLM text interaction
    Minimal chat loop (single-turn or short multi-turn)
    Explicit enable/disable control
    Safe timeout and failure fallback

**Wave-3 does NOT add:**
    New wake words
    Continuous audio streaming
    Audio upload to Internet
    Autonomous behavior without explicit user trigger

===

### LOCAL.LLM.RUNTIME.READY 

**Purpose:**
Verify the Pi can reach the LLM endpoint and has valid config.

**Behavior:**
- Checks `voice_config.json` has `llm` object and required fields:
  - `base_url` (e.g. `https://api.openai.com/v1/responses`)
  - `api_key_file` (path to key file)
  - `model`
- Performs a small test request (e.g., “ping”) and verifies a text reply is returned.

**Entry conditions:**
- GUI button: **LLM status** (top row)
- Optional operator CLI utility (if you keep one)

**Exit conditions:**
Returns a short report string: `OK` / `FAIL: <reason>` (+ optional reply preview)

---

### LOCAL.LLM.LIVE

**Purpose:**
Interactive chat loop: STT → LLM → TTS, until timeout or stop phrase.

**Behavior:**
    On entry: speaks llm_enter_say (e.g., “Do you want to chat with me?”).
    While active:
        listens via STT chunks
        sends each utterance to LLM using Responses API (POST /v1/responses).
        speaks the returned text via TTS
        optionally preserves context using the API conversation field.
    On exit: returns to name_listen.

**Entry conditions:**
    Wave-2 CONVERSATION script action: enter.llm

**Exit conditions:**
    llm_timeout_sec reached
    User says stop phrase (“stop chatting”, “exit”, etc.)
    Error contacting LLM (fallback to name_listen)

---

### NMS.CMD.VOICE.LLM.CONFIG.SET 

**Purpose:**
Update only the `llm` config block inside `voice_config.json`.

**Behavior:**
- Parses provided JSON (must be valid).
- Merges/replaces `voice_config.json["llm"]` with the provided object.
- Does not start/stop services.

**Entry conditions:**
NMS command enqueue

**Exit conditions:**
Returns ok, or error if JSON is invalid / `llm` is not an object.

**args_json template:**
```json
{
  "llm": {
    "provider": "openai",
    "base_url": "https://api.openai.com/v1/responses",
    "model": "gpt-4.1-mini",
    "api_key_file": "",
    "timeout_sec": 30,
    "max_output_tokens": 250,
    "temperature": 0.4,
    "session_id": "twin-scout-alpha"
  }
}
```

---

### GUI.BUTTON.LLM.STATUS

**Purpose:**
Validate end-to-end LLM connectivity from the Pi (no mic required).

**Behavior:**
- Sends a short test prompt to the LLM.
- Displays OK / FAIL + short reason.
- Optionally displays a short reply preview (truncated).
- Does not change voice mode.

**Entry conditions:**
Operator presses **LLM status** button on GUI.

**Exit conditions:**
Shows status result on screen.
