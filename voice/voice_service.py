#!/usr/bin/env python3
"""
Wave-2 Voice Service (state machine)

- Long-running process controlled by systemd (scanner-voice.service)
- Maintains a current mode stored in voice_config.json:
    deaf | name_listen | conversation | llm_dummy
- External control (agent/GUI) is restricted to: deaf <-> name_listen
- Internal transitions:
    name_listen -> conversation  (wake-name match)
    conversation -> llm_dummy    (script action enter.llm)
    conversation -> name_listen  (timeout)
    llm_dummy -> name_listen     (timeout)
- Optional safety:
    ANY -> name_listen on error

Wave-2:
- Uses Vosk chunk STT loop in name_listen / conversation (Step-1 engine)
- Simple phrase matching in conversation
- Uses tts_say.sh for spoken replies (no mic tuning here)
"""

from __future__ import annotations

import time
import subprocess
from typing import Dict, Any, Tuple
from voice_common import (
    read_identity,
    load_voice_config,
    save_voice_config,
    voice_log,
    normalize_text,
    match_wake_name,
)
from voice_rt_stt import init_vosk, stt_loop_once
from voice_llm import llm_exchange

HEARTBEAT_SEC = 10
IDLE_SLEEP_SEC = 0.05

TTS_SCRIPT = "/home/pi/_RunScanner/av/tts_say.sh"

def _cfg_int(cfg: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except Exception:
        return default

def _cfg_bool(cfg: Dict[str, Any], key: str, default: bool = False) -> bool:
    v = cfg.get(key, default)
    return bool(v)

def _cfg_str(cfg: Dict[str, Any], key: str, default: str = "") -> str:
    s = cfg.get(key, default)
    return str(s).strip()

def _speak_cfg(cfg: Dict[str, Any], text: str, *, lead_ms: int = 600) -> Tuple[bool, str]:
    vol  = _cfg_int(cfg, "tts_volume", 120)   # mpv gain
    rate = _cfg_int(cfg, "tts_rate", 135)     # espeak speed
    amp  = _cfg_int(cfg, "tts_amp", 200)      # espeak amplitude
    return _speak(text, lead_ms=lead_ms, vol=vol, rate=rate, amp=amp)

def _mode_enter_prompt(cfg: Dict[str, Any], mode: str) -> str:
    if mode == "deaf":
        return _cfg_str(cfg, "deaf_enter_say", "I will stay quiet.")
    if mode == "name_listen":
        return _cfg_str(cfg, "name_listen_enter_say", "I am listening.")
    if mode == "conversation":
        return _cfg_str(cfg, "conversation_enter_say", "How can I help you?")
    if mode == "llm_dummy":
        return _cfg_str(cfg, "llm_enter_say", "Do you want to chat with me?")
    return ""

def _enter_mode(cfg: Dict[str, Any], new_mode: str, *, reason: str = "") -> Tuple[str, float]:
    """
    Single entry point for mode transitions:
      - persist mode into voice_config.json
      - speak enter prompt (always)
    Returns: (mode, enter_ts)
    """
    new_mode = _sanitize_mode(new_mode)

    # Persist mode
    save_voice_config({**cfg, "mode": new_mode})

    # Speak enter prompt (always)
    prompt = _mode_enter_prompt(cfg, new_mode)
    if prompt:
        ok, detail = _speak_cfg(cfg, prompt, lead_ms=300 if new_mode != "deaf" else 600)
        voice_log(f"VOICE: enter_say mode={new_mode} ok={ok} detail={detail} reason={reason}")

    return new_mode, time.time()

def _sanitize_mode(mode: str) -> str:
    m = (mode or "").strip()
    if m in ("deaf", "name_listen", "conversation", "llm_dummy"):
        return m
    return "deaf"

def _callsign_from_identity(ident: str) -> str:
    """
    identity example:
      twin-scout-alpha
    callsign:
      alpha
    """
    toks = normalize_text(ident).split()
    if not toks:
        return ""
    return toks[-1]

def _speak(
    text: str,
    *,
    lead_ms: int = 600,
    vol: int = 120,
    rate: int = 135,
    amp: int = 200,
) -> Tuple[bool, str]:
    text = (text or "").strip()
    if not text:
        return True, "skip empty"

    try:
        cp = subprocess.run(
            [
                "/usr/bin/bash",
                TTS_SCRIPT,
                text,
                str(int(lead_ms)),
                str(int(vol)),
                str(int(rate)),
                str(int(amp)),
            ],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        if cp.returncode == 0:
            return True, f"ok text_len={len(text)}"
        return False, f"rc={cp.returncode} err={(cp.stderr or cp.stdout or '')[:200].strip()}"
    except Exception as e:
        return False, f"exception {type(e).__name__}: {e}"

def _run_status_summary() -> str:
    """
    Very lightweight health summary for Wave-2.
    Keep it short so TTS is clear.
    """
    def _is_active(unit: str) -> str:
        try:
            cp = subprocess.run(
                ["/bin/systemctl", "is-active", unit],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=3,
            )
            s = (cp.stdout or "").strip()
            return s if s else "unknown"
        except Exception:
            return "unknown"

    agent = _is_active("scanner-agent.service")
    voice = _is_active("scanner-voice.service")

    # Keep it short:
    if agent == "active" and voice == "active":
        return "All systems look normal."
    return f"Agent is {agent}. Voice is {voice}."

def _phrase_match(cfg: Dict[str, Any], norm_text: str, phrase: str) -> bool:
    """
    Wave-2 phrase match.
    Test mode: if test_phrase_always is true, any non-trivial speech counts as match.
    """
    test_easy = _cfg_bool(cfg, "test_easy_match", False)
    min_chars = _cfg_int(cfg, "test_min_chars", 1) if test_easy else 3
    if test_easy and _cfg_bool(cfg, "test_phrase_always", False):
        return len(norm_text) >= min_chars

    p = normalize_text(phrase)
    return bool(p) and p in norm_text

def main() -> None:
    ident = read_identity() or "UNKNOWN"
    callsign = _callsign_from_identity(ident)
    voice_log(f"VOICE: service start identity='{ident}' callsign='{callsign}'")
    cfg0 = load_voice_config()
    voice_log(
        "VOICE: test_knobs "
        f"easy={bool(cfg0.get('test_easy_match'))} "
        f"min_chars={cfg0.get('test_min_chars')} "
        f"wake_always={bool(cfg0.get('test_wake_always'))} "
        f"phrase_always={bool(cfg0.get('test_phrase_always'))}"
    )

    # Ensure config exists / has defaults
    cfg: Dict[str, Any] = load_voice_config()
    cfg["mode"] = _sanitize_mode(cfg.get("mode", "deaf"))
    save_voice_config(cfg)

    # Vosk init (lazy but cached)
    stt = None
    stt_detail = "not initialized"

    # State tracking
    mode = cfg["mode"]
    last_mode = None
    mode_enter_ts = time.time()
    last_hb = 0.0
    conv_last_activity_ts = mode_enter_ts
    llm_last_activity_ts = mode_enter_ts

    while True:
        try:
            cfg = load_voice_config()

            # --- External requested mode (restricted to deaf <-> name_listen) ---
            requested = _sanitize_mode(cfg.get("mode", "deaf"))

            if requested in ("deaf", "name_listen") and requested != mode:
                mode, mode_enter_ts = _enter_mode(cfg, requested, reason="external_request")
            elif requested not in ("deaf", "name_listen") and requested != mode:
                # external tries to force conversation/llm -> ignore
                voice_log(f"VOICE: ignore external mode request '{requested}' (restricted)")

            # --- Log mode transitions (once) ---
            if mode != last_mode:
                voice_log(f"VOICE: mode -> {mode}")
                last_mode = mode
                if mode == "conversation":
                    conv_last_activity_ts = time.time()
                elif mode == "llm_dummy":
                    llm_last_activity_ts = time.time()

            # --- Heartbeat ---
            now = time.time()
            if now - last_hb >= HEARTBEAT_SEC:
                voice_log(
                    f"VOICE: heartbeat mode={mode} script_len={len(cfg.get('script') or [])} "
                    f"conv_to={cfg.get('conversation_timeout_sec')} llm_to={cfg.get('llm_timeout_sec')} "
                    f"stt={stt_detail}"
                )
                last_hb = now

            # --- Mode behavior ---
            if mode == "deaf":
                time.sleep(0.5)
                continue

            # init vosk once when needed
            if stt is None:
                stt, stt_detail = init_vosk(cfg)
                if stt is None:
                    # Can't do STT; safety fallback to name_listen but keep running
                    stt_detail = f"vosk unavailable ({stt_detail})"
                    voice_log(f"VOICE: STT not ready -> stay name_listen (detail={stt_detail})")
                    mode, mode_enter_ts = _enter_mode(cfg, "name_listen", reason=f"stt_unavailable:{stt_detail}")
                    time.sleep(1.0)
                    continue
                stt_detail = "vosk ready"

            # NAME_LISTEN: listen for wake name -> enter conversation
            if mode == "name_listen":
                ok, raw, norm = stt_loop_once(cfg, stt)
                if ok:
                    norm = normalize_text(norm)

                    test_easy = _cfg_bool(cfg, "test_easy_match", False)
                    min_chars = _cfg_int(cfg, "test_min_chars", 1) if test_easy else 3

                    if test_easy and _cfg_bool(cfg, "test_wake_always", False):
                        matched = (len(norm) >= min_chars)
                        why = f"test_wake_always(min_chars={min_chars})"
                    else:
                        matched, why = match_wake_name(norm, callsign=callsign)

                    voice_log(f"RT_STT: heard raw='{raw}' norm='{norm}' wake={matched} why={why}")

                    if matched:
                        # Enter conversation (this will also speak conversation_enter_say)
                        mode, mode_enter_ts = _enter_mode(cfg, "conversation", reason="wake_match")
                else:
                    voice_log(f"RT_STT: chunk error: {raw}")
                    time.sleep(0.2)

                time.sleep(IDLE_SLEEP_SEC)
                continue

            # CONVERSATION: match scripted phrases; timeout -> name_listen
            if mode == "conversation":
                conv_to = int(cfg.get("conversation_timeout_sec") or 20)
                if (time.time() - conv_last_activity_ts) >= conv_to:
                    voice_log("VOICE: conversation timeout -> name_listen")
                    mode, mode_enter_ts = _enter_mode(cfg, "name_listen", reason="conversation_timeout")
                    continue                    

                ok, raw, norm = stt_loop_once(cfg, stt)
                if ok:
                    norm = normalize_text(norm)
                    voice_log(f"RT_STT: heard raw='{raw}' norm='{norm}'")
                    # any usable speech keeps conversation alive
                    test_easy = _cfg_bool(cfg, "test_easy_match", False)
                    min_chars = _cfg_int(cfg, "test_min_chars", 1) if test_easy else 3
                    if len(norm) >= min_chars:
                        conv_last_activity_ts = time.time()

                    script = cfg.get("script") or []

                    # Test helper: if phrase_always is enabled and script has entries,
                    # treat ONLY the first script entry as matched when we have any speech.
                    test_easy = _cfg_bool(cfg, "test_easy_match", False)
                    phrase_always = _cfg_bool(cfg, "test_phrase_always", False)
                    min_chars = _cfg_int(cfg, "test_min_chars", 1) if test_easy else 3

                    if test_easy and phrase_always and script:
                        script = [script[0]]

                    for item in script:
                        phrase = str(item.get("phrase") or "").strip()
                        reply  = str(item.get("reply") or "").strip()
                        action = str(item.get("action") or "").strip()

                        if not phrase:
                            continue

                        # Decide hit
                        if len(norm) < min_chars:
                            continue

                        if test_easy and phrase_always:
                            hit = True
                        else:
                            hit = _phrase_match(cfg, norm, phrase)   # <-- correct signature

                        if not hit:
                            continue

                        voice_log(f"VOICE: phrase matched phrase='{phrase}' action='{action}'")

                        # 1) Speak reply (if provided)
                        if reply:
                            _speak_cfg(cfg, reply, lead_ms=300)

                        # 2) Optional action: status.report (Wave-2 useful)
                        if action == "status.report":
                            _speak_cfg(cfg, "Let me check the operation condition.", lead_ms=300)
                            summary = _run_status_summary()
                            _speak_cfg(cfg, summary, lead_ms=300)

                        # 3) Only way to enter llm_dummy
                        if action == "enter.llm":
                            mode, mode_enter_ts = _enter_mode(cfg, "llm_dummy", reason="enter_llm_action")
                        break
                else:
                    voice_log(f"RT_STT: chunk error: {raw}")
                    time.sleep(0.2)

                time.sleep(IDLE_SLEEP_SEC)
                continue

            # LLM_DUMMY (Wave-3): STT -> LLM -> TTS; timeout -> name_listen
            if mode == "llm_dummy":
                llm_to = int(cfg.get("llm_timeout_sec") or 30)

                # timeout based on last activity (NOT entry time)
                if (time.time() - llm_last_activity_ts) >= llm_to:
                    voice_log("VOICE: llm timeout -> name_listen")
                    mode, mode_enter_ts = _enter_mode(cfg, "name_listen", reason="llm_timeout")
                    continue

                ok, raw, norm = stt_loop_once(cfg, stt)
                if ok:
                    norm = normalize_text(norm)
                    voice_log(f"RT_STT: heard raw='{raw}' norm='{norm}' (llm_dummy)")

                    # Consider "activity" only when norm has enough chars
                    test_easy = _cfg_bool(cfg, "test_easy_match", False)
                    min_chars = _cfg_int(cfg, "test_min_chars", 1) if test_easy else 3

                    if len(norm) >= min_chars:
                        # user activity keeps LLM session alive
                        llm_last_activity_ts = time.time()

                        # Send to LLM
                        ok2, reply_or_err = llm_exchange(norm)
                        if ok2:
                            if reply_or_err.strip():
                                _speak_cfg(cfg, reply_or_err.strip(), lead_ms=250)
                                # assistant activity also keeps LLM session alive
                                llm_last_activity_ts = time.time()
                        else:
                            voice_log(f"LLM: error {reply_or_err}")
                            _speak_cfg(cfg, "Sorry, I cannot reach the server right now.", lead_ms=250)
                            llm_last_activity_ts = time.time()

                else:
                    voice_log(f"RT_STT: chunk error: {raw}")
                    time.sleep(0.2)

                time.sleep(IDLE_SLEEP_SEC)
                continue

            # Unknown -> safety
            voice_log(f"VOICE: unknown mode '{mode}' -> name_listen")
            mode, mode_enter_ts = _enter_mode(cfg, "name_listen", reason="unknown_mode")

        except Exception as e:
            # Optional safety: ANY -> name_listen on error
            voice_log(f"VOICE: ERROR {type(e).__name__}: {e} -> name_listen")
            try:
                cfg = load_voice_config()
                mode, mode_enter_ts = _enter_mode(cfg, "name_listen", reason="exception_fallback")
            except Exception:
                pass
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
