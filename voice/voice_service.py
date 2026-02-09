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

def _cue_listen(cfg: Dict[str, Any]) -> None:
    # short audio cue before recording (uses working TTS path)
    t0 = time.time()
    ok, detail = _speak_cfg(cfg, "go", lead_ms=250)
    voice_log(f"CUE: done ok={ok} dt={time.time()-t0:.2f}s detail={detail}")

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
    if m in ("deaf", "name_listen", "conversation", "llm_dummy", "llm_browser"):
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

def _listen_utterance(cfg: Dict[str, Any], stt, *, label: str = "") -> Tuple[bool, str, str]:
    """
    Tier-2: collect multiple STT chunks into one utterance until we hit
    consecutive silence chunks or max_chunks.

    Returns: (ok, raw_joined, norm_joined)
      - ok=False means: we ended with no usable speech (treat as "no utterance")
    """
    max_chunks   = _cfg_int(cfg, "utterance_max_chunks", 4)
    silence_need = _cfg_int(cfg, "utterance_silence_chunks", 1)
    utter_min    = _cfg_int(cfg, "utterance_min_chars", 2)
    joiner = str(cfg.get("utterance_joiner", " "))    # DO NOT strip: a single-space joiner is valid
    if joiner == "":
        joiner = " "

    # Separate threshold: what counts as "speech chunk" vs "silence-ish"
    # Keep small so we don't miss short-but-real chunks like "yes", "no".
    speech_min = _cfg_int(cfg, "utterance_speech_min_chars", 1)

    raw_parts: list[str] = []
    norm_parts: list[str] = []

    silent_run = 0
    stop_reason = "max_chunks"

    for i in range(max_chunks):
        ok, raw, norm = stt_loop_once(cfg, stt)
        if not ok:
            voice_log(f"UTTERANCE[{label}]: chunk_error i={i} err='{raw}'")
            silent_run += 1
            if silent_run >= silence_need:
                stop_reason = "error_as_silence"
                break
            continue

        raw = (raw or "").strip()
        norm = normalize_text(norm).strip()

        # treat short / empty as silence-ish
        if len(norm) < speech_min:
            silent_run += 1
            voice_log(f"UTTERANCE[{label}]: silence i={i} silent_run={silent_run} raw='{raw}' norm='{norm}'")
            if silent_run >= silence_need:
                stop_reason = "silence"
                break
            continue

        # speech chunk
        silent_run = 0
        raw_parts.append(raw)
        norm_parts.append(norm)
        voice_log(f"UTTERANCE[{label}]: speech i={i} raw='{raw}' norm='{norm}'")

        # Optional early stop phrases (keep minimal)
        if norm in ("stop", "stop now", "that is all", "thanks"):
            stop_reason = "stop_phrase"
            break

    raw_joined = joiner.join([p for p in raw_parts if p]).strip()
    norm_joined = joiner.join([p for p in norm_parts if p]).strip()

    # Summary log (this is the money log)
    voice_log(
        f"UTTERANCE[{label}]: done reason={stop_reason} "
        f"chunks={len(norm_parts)} silent_run={silent_run} "
        f"raw_len={len(raw_joined)} norm_len={len(norm_joined)}"
    )

    # IMPORTANT: ok should be False if no usable utterance
    if len(norm_joined) < utter_min:
        return False, raw_joined, norm_joined

    return True, raw_joined, norm_joined

def _should_exit_llm(norm: str) -> bool:
    s = normalize_text(norm)
    return any(p in s for p in (
        "stop chatting", "exit", "quit", "goodbye", "stop", "cancel"
    ))


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

                # Audible cue before recording
                _cue_listen(cfg)
                time.sleep(0.25)                
                
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

                # Timeout based on last real activity (utterance or reply)
                if (time.time() - llm_last_activity_ts) >= llm_to:
                    voice_log("VOICE: llm timeout -> name_listen")
                    mode, mode_enter_ts = _enter_mode(cfg, "name_listen", reason="llm_timeout")
                    continue

                # Keep-alive so thinking time doesn't trigger timeout
                llm_last_activity_ts = time.time()

                # Audible cue before recording
                _cue_listen(cfg)
                time.sleep(0.25)

                # Collect Tier-2 utterance (multi-chunk)
                ok_u, raw, norm = _listen_utterance(cfg, stt, label="llm_dummy")

                if not ok_u:
                    voice_log("RT_STT: no utterance (silence)")
                    time.sleep(IDLE_SLEEP_SEC)
                    continue

                voice_log(f"RT_STT: utterance raw='{raw}' norm='{norm}' (llm_dummy)")
                llm_last_activity_ts = time.time()

                # Exit phrases (user wants out of chat)
                if _should_exit_llm(norm):
                    _speak_cfg(cfg, "OK, exiting chat.", lead_ms=250)
                    mode, mode_enter_ts = _enter_mode(cfg, "name_listen", reason="llm_exit_phrase")
                    llm_last_activity_ts = time.time()
                    continue

                # Guard: minimum length required to justify an LLM call
                llm_min_chars = _cfg_int(cfg, "llm_min_chars", 8)
                if len(norm) < llm_min_chars:
                    voice_log(
                        f"RT_STT: utterance too short for LLM "
                        f"(len={len(norm)} < {llm_min_chars}) -> skip"
                    )
                    time.sleep(IDLE_SLEEP_SEC)
                    continue

                # ---- LLM call (ONE time only) ----
                t0 = time.time()
                voice_log(f"LLM: request start chars={len(norm)} text='{norm[:60]}'")
                ok2, reply_or_err = llm_exchange(norm)
                dt = time.time() - t0

                reply_text = (reply_or_err or "").strip()
                voice_log(f"LLM: request done ok={ok2} dt={dt:.2f}s out_len={len(reply_text)}")

                if ok2:
                    if reply_text:
                        t1 = time.time()
                        ok3, detail3 = _speak_cfg(cfg, reply_text, lead_ms=250)
                        voice_log(
                            f"TTS: done ok={ok3} dt={time.time()-t1:.2f}s detail={detail3}"
                        )
                        llm_last_activity_ts = time.time()
                else:
                    voice_log(f"LLM: error detail='{reply_text[:160]}'")
                    _speak_cfg(cfg, "Sorry, I cannot reach the server right now.", lead_ms=250)
                    llm_last_activity_ts = time.time()

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
