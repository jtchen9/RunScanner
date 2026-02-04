#!/usr/bin/env python3
"""
Real-time-ish STT loop (Wave-2)

- Records audio in short chunks (arecord)
- Runs Vosk STT on each chunk
- Provides generic matching: wake-name or scripted phrase

This is Step-1 only: building a reusable STT+matching engine.
"""

import json
import time
import wave
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from voice_common import fuzzy_match, normalize_text, match_wake_name

# Reuse logging helpers if you have them
try:
    from voice_common import voice_log, local_ts
except Exception:
    def local_ts() -> str:
        return time.strftime("%Y-%m-%d-%H:%M:%S", time.localtime())
    def voice_log(msg: str, *, also_print: bool = True) -> None:
        line = f"[{local_ts()}] {msg}"
        if also_print:
            print(line, flush=True)
            
# --- Optional Vosk import guarded ---
try:
    from vosk import Model, KaldiRecognizer  # type: ignore
except Exception:
    Model = None
    KaldiRecognizer = None


CHUNK_WAV = Path("/tmp/voice_chunk.wav")


@dataclass
class MatchEvent:
    kind: str              # "name" or "phrase"
    text_raw: str          # raw stt output
    text_norm: str         # normalized output
    matched: str           # matched name or phrase
    score: float = 1.0     # placeholder (Wave-2 simple)


# -------------------------
# Normalization utilities
# -------------------------
def robot_wake_tokens(name: str) -> List[str]:
    """
    "twin-scout-charli" -> ["kirox", "scout", "unit", "charli"]
    """
    n = normalize_text(name)
    return n.split()

def build_robot_names() -> List[str]:
    return [
        "twin-scout-alpha",
        "twin-scout-bravo",
        "twin-scout-charlie",
        "twin-scout-delta",
        "twin-scout-echo",
        "twin-scout-foxtrot",
        "twin-scout-golf",
        "twin-scout-hotel",
        "twin-scout-india",
        "twin-scout-julia",
    ]

# -------------------------
# Audio recording
# -------------------------

def record_wav(out_wav: Path, mic_dev: str, rate: int, ch: int, dur_sec: int) -> Tuple[bool, str]:
    """
    Use arecord to capture a short chunk.
    """
    out_wav = Path(out_wav)
    try:
        if out_wav.exists():
            out_wav.unlink()
    except Exception:
        pass

    cmd = [
        "/usr/bin/arecord",
        "-D", mic_dev,
        "-f", "S16_LE",
        "-r", str(rate),
        "-c", str(ch),
        "-d", str(dur_sec),
        str(out_wav),
    ]
    try:
        cp = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=dur_sec + 3,
        )
        if cp.returncode != 0:
            return False, f"arecord rc={cp.returncode} err={(cp.stderr or cp.stdout or '')[:200].strip()}"
        if not out_wav.exists() or out_wav.stat().st_size < 2000:
            return False, "arecord produced empty/too-small wav"
        return True, "ok"
    except Exception as e:
        return False, f"arecord exception: {type(e).__name__}: {e}"


# -------------------------
# Vosk STT engine
# -------------------------

class VoskSTT:
    def __init__(self, model_dir: Path, sample_rate: int):
        if Model is None or KaldiRecognizer is None:
            raise RuntimeError("vosk not installed/importable")
        self.model = Model(str(model_dir))
        self.sample_rate = sample_rate

    def transcribe_wav(self, wav_path: Path) -> Tuple[bool, str]:
        try:
            wf = wave.open(str(wav_path), "rb")
            if wf.getnchannels() != 1:
                return False, "vosk expects mono wav"
            rec = KaldiRecognizer(self.model, self.sample_rate)

            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                rec.AcceptWaveform(data)

            j = json.loads(rec.FinalResult() or "{}")
            text = (j.get("text") or "").strip()
            return True, text
        except Exception as e:
            return False, f"vosk transcribe exception: {type(e).__name__}: {e}"


def init_vosk(cfg: Dict[str, Any]) -> Tuple[Optional[VoskSTT], str]:
    model_dir = (cfg.get("vosk_model_dir") or "").strip()
    if not model_dir:
        return None, "vosk_model_dir missing"
    p = Path(model_dir)
    if not p.exists():
        return None, f"vosk model not found: {model_dir}"
    try:
        stt = VoskSTT(p, int(cfg.get("sample_rate") or 16000))
        return stt, "vosk ready"
    except Exception as e:
        return None, f"vosk init failed: {type(e).__name__}: {e}"

# -------------------------
# Matching
# -------------------------
# voice_rt_stt.py

def match_robot_name(text_norm: str, robot_names: List[str]) -> Optional[str]:
    """
    robot_names here should be [ident] for NAME_LISTEN.
    ident format: "twin-scout-alpha" etc.
    """
    for rn in robot_names:
        rn_norm = normalize_text(rn)
        parts = rn_norm.split()
        # Expect: ["twin","scout","alpha"] but be defensive
        if not parts:
            continue
        callsign = parts[-1]  # last word: alpha/bravo/...
        ok, _why = match_wake_name(text_norm, callsign=callsign)
        if ok:
            return rn
    return None

def match_phrase(text_norm: str, phrases: List[str]) -> Optional[str]:
    """
    Fuzzy phrase match.
    For phrases, we usually want a bit stricter ratio than wake-name.
    """
    if not text_norm:
        return None

    # 1) Fast path: strict substring
    for ph in phrases:
        ph_norm = normalize_text(ph)
        if ph_norm and ph_norm in text_norm:
            return ph

    # 2) Fuzzy path
    best_phrase: Optional[str] = None
    best_score: float = 0.0

    for ph in phrases:
        ok, dbg = fuzzy_match(
            ph,
            text_norm,
            min_token_overlap=0.50,
            min_ratio=0.65,   # a bit stricter for longer phrases
        )
        score = float(dbg.get("ratio", 0.0)) if isinstance(dbg, dict) else 0.0
        if ok and score > best_score:
            best_score = score
            best_phrase = ph

    return best_phrase

# -------------------------
# Real-time-ish loop
# -------------------------

def stt_loop_once(cfg: Dict[str, Any], stt: VoskSTT) -> Tuple[bool, str, str]:
    """
    Record one chunk and transcribe.
    Returns (ok, raw_text, norm_text)
    """
    mic = (cfg.get("mic_dev") or "plughw:1,0")
    rate = int(cfg.get("sample_rate") or 16000)
    ch = int(cfg.get("channels") or 1)
    dur = int(cfg.get("chunk_sec") or 2)

    ok, detail = record_wav(CHUNK_WAV, mic, rate, ch, dur)
    if not ok:
        return False, detail, ""

    ok2, raw = stt.transcribe_wav(CHUNK_WAV)
    if not ok2:
        return False, raw, ""

    norm = normalize_text(raw)
    return True, raw, norm


def run_rt_match_loop(
    cfg: Dict[str, Any],
    *,
    kind: str,
    robot_names: Optional[List[str]] = None,
    phrases: Optional[List[str]] = None,
    max_sec: int = 30,
    min_chars: int = 3,
    idle_sleep_ms: int = 50,
) -> Tuple[bool, Optional[MatchEvent], str]:
    """
    Generic loop for both:
      - name matching (kind="name")
      - phrase matching (kind="phrase")

    Stops when:
      - matched
      - max_sec elapsed
      - fatal error (e.g. vosk not available)
    """
    stt, detail = init_vosk(cfg)
    if stt is None:
        return False, None, detail

    t0 = time.time()
    robot_names = robot_names or []
    phrases = phrases or []

    voice_log(f"RT_STT: start kind={kind} max_sec={max_sec}")

    while True:
        if time.time() - t0 > max_sec:
            return True, None, "timeout"

        ok, raw, norm = stt_loop_once(cfg, stt)
        if not ok:
            # Non-fatal: in lab, mic glitches happen. Fall back to continue.
            voice_log(f"RT_STT: chunk error: {raw}")
            time.sleep(0.2)
            continue

        if len(norm) < min_chars:
            time.sleep(idle_sleep_ms / 1000.0)
            continue

        voice_log(f"RT_STT: heard raw='{raw}' norm='{norm}'")

        if kind == "name":
            m = match_robot_name(norm, robot_names)
            voice_log(f"RT_STT: NAME matched='{m}' from norm='{norm}'")
            if m:
                ev = MatchEvent(kind="name", text_raw=raw, text_norm=norm, matched=m)
                return True, ev, "matched"

        elif kind == "phrase":
            m = match_phrase(norm, phrases)
            if m:
                ev = MatchEvent(kind="phrase", text_raw=raw, text_norm=norm, matched=m)
                return True, ev, "matched"

        else:
            return False, None, f"unknown kind={kind}"

        time.sleep(idle_sleep_ms / 1000.0)
