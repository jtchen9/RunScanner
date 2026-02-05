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

**Purpose:****
Verify the Pi can reach the LLM endpoint and has valid config.

**Behavior:**
    Checks voice_config.json has llm key and required fields (api_base, api_key, model).
    Performs a small test request (e.g., “ping”) and verifies a text reply is returned via POST /v1/responses.

**Entry conditions:**
    Manual operator run (CLI / GUI utility button later)
    Integration test script (recommended)

**Exit conditions:**
    Returns a short report string (OK / FAIL + reason)

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
Update the llm config block (single key) inside voice_config.json.

**Behavior:**
Merge/replace voice_config.json["llm"] with provided object.
Does not start/stop services by itself.

**Entry conditions:**
NMS command enqueue

**Exit conditions:**
Returns ok or error (validation failure)

**args_json template:**
```json
{
  "llm": {
    "provider": "openai",
    "api_base": "https://api.openai.com/v1",
    "api_key": "sk-REDACTED",
    "model": "gpt-5",
    "timeout_sec": 30,
    "max_output_tokens": 200,
    "temperature": 0.2,
    "system_prompt": "Keep replies short."
  }
}
```

---

### NMS.CMD.VOICE.LLM.TEST

**Purpose:**
Validate end-to-end LLM connectivity from the Pi (no mic required).

**Behavior:**
Sends a provided test prompt to the LLM and returns the text reply (truncated).

**Entry conditions:**
Operator / integration test

**Exit conditions:**
Returns ok + reply_preview, or error

**args_json template:**
```json
{
  "prompt": "Say 'LLM OK' in two words."
}
```
