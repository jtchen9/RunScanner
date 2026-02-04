#!/usr/bin/env python3
"""
voice_common.py (Wave-2)

Shared utilities for voice service + agent integration.
Keeps *all* Wave-2 voice files under /home/pi/_RunScanner/voice
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
import re
from difflib import SequenceMatcher

CALLSIGN_MIN_RATIO = 0.82   # stricter (prevents alpha<->bravo)
PREFIX_MIN_RATIO   = 0.70   # looser (twin/scout can be misheard a bit)
ALLOW_CALLSIGN_ONLY = True

# Paths
BASE_DIR = Path("/home/pi/_RunScanner")
VOICE_DIR = BASE_DIR / "voice"
VOICE_CFG_FILE = VOICE_DIR / "voice_config.json"
VOICE_LOG_FILE = VOICE_DIR / "voice_service.log"

# Defaults (Wave-2)
DEFAULT_CFG: Dict[str, Any] = {
    "mode": "deaf",
    "conversation_timeout_sec": 20,
    "llm_timeout_sec": 30,
    "script": [],
}

def local_ts() -> str:
    import time
    return time.strftime("%Y-%m-%d-%H:%M:%S", time.localtime())

def read_identity() -> str:
    """
    Identity is the calling name stored in scanner_name.txt.
    Example: twin-scout-alpha
    """
    p = BASE_DIR / "scanner_name.txt"
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def voice_log(msg: str, *, also_print: bool = True) -> None:
    line = f"[{local_ts()}] {msg}"
    if also_print:
        print(line, flush=True)
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        with VOICE_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def ensure_voice_config() -> None:
    """
    Ensure voice_config.json exists with DEFAULT_CFG.
    """
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if VOICE_CFG_FILE.exists():
        return

    save_voice_config(DEFAULT_CFG)

def load_voice_config() -> Dict[str, Any]:
    """
    Load config from voice_config.json.
    If missing or corrupted, create defaults.
    """
    ensure_voice_config()
    try:
        obj = json.loads(VOICE_CFG_FILE.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            # overlay defaults to ensure keys exist
            out = dict(DEFAULT_CFG)
            out.update(obj)
            # ensure types
            out["script"] = out.get("script") if isinstance(out.get("script"), list) else []
            return out
    except Exception:
        pass

    # fallback: recreate defaults
    save_voice_config(DEFAULT_CFG)
    return dict(DEFAULT_CFG)

def save_voice_config(cfg: Dict[str, Any]) -> None:
    """
    Atomic write config to voice_config.json.
    """
    ensure_voice_config()
    out = dict(DEFAULT_CFG)
    out.update(cfg or {})

    # normalize
    if not isinstance(out.get("script"), list):
        out["script"] = []

    tmp = VOICE_CFG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(VOICE_CFG_FILE)

def update_voice_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read-modify-write update (returns new cfg).
    """
    cur = load_voice_config()
    cur.update(patch or {})
    save_voice_config(cur)
    return cur

def validate_script(commands: Any) -> List[Dict[str, Any]]:
    """
    Script format:
      [{"phrase": "...", "reply": "...", "action": "..."}, ...]
    For Wave-2, we only validate basic shape and store it.
    """
    if not isinstance(commands, list):
        return []

    out: List[Dict[str, Any]] = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        phrase = str(item.get("phrase") or "").strip()
        reply = str(item.get("reply") or "").strip()
        action = str(item.get("action") or "").strip()
        if not phrase:
            continue
        out.append({"phrase": phrase, "reply": reply, "action": action})
    return out

# --- Fuzzy matching helpers (no extra deps) -------------------------------
def normalize_text(s: str) -> str:
    """
    Normalize text for fuzzy matching:
    - lowercase
    - replace '-', '_' with space
    - remove non-alnum (keep spaces)
    - collapse whitespace
    - canonicalize common Vosk confusions (twins->twin, skull->scout, alfa->alpha)
    """
    s = (s or "").lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return ""

    # Canonicalize common recognition variants
    canon = {
        "twins": "twin",
        "skull": "scout",
        "alfa": "alpha",
        "bravo": "bravo",
        "alpha": "alpha",
    }

    toks = []
    for t in s.split():
        toks.append(canon.get(t, t))
    return " ".join(toks)

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def best_token_match(token: str, options: List[str]) -> Tuple[str, float]:
    best = ("", 0.0)
    for o in options:
        r = _ratio(token, o)
        if r > best[1]:
            best = (o, r)
    return best

def match_wake_name(text_norm: str, *, callsign: str) -> Tuple[bool, str]:
    """
    Decide if text contains this robot's wake name:
      twin-scout-<callsign>
    Rules:
      - callsign must match strongly (min CALLSIGN_MIN_RATIO)
      - plus at least one of 'twin'/'scout' present (min PREFIX_MIN_RATIO)
    """
    toks = text_norm.split()
    if not toks:
        return False, "no tokens"

    # 1) callsign strong match somewhere in tokens
    cs_best = ("", 0.0)
    for t in toks:
        o, r = best_token_match(t, [callsign])
        if r > cs_best[1]:
            cs_best = (o, r)
    if cs_best[1] < CALLSIGN_MIN_RATIO:
        return False, f"callsign_no (best={cs_best[1]:.2f})"

    # 2) require at least one prefix token
    prefix_ok = False
    for t in toks:
        # allow either token to satisfy
        if max(_ratio(t, "twin"), _ratio(t, "scout")) >= PREFIX_MIN_RATIO:
            prefix_ok = True
            break
    if prefix_ok:
        return True, "ok(prefix+callsign)"

    if ALLOW_CALLSIGN_ONLY:
        return True, "ok(callsign-only)"

    return False, "prefix_no"

def fuzzy_match(
    text_norm: str,
    target: str,
    *,
    token_cutoff: float = 0.80,
    first_token_cutoff: float = 0.85,
    last_token_cutoff: float = 0.90,
    min_hit: int = 3,
    require_last_token: bool = True,
) -> Tuple[bool, float]:
    """
    Fuzzy match normalized STT text against one target name.

    Example target:
        "kirox scout unit alpha"

    Returns:
        (matched?, score)
    """

    text_tokens = text_norm.split()
    target_tokens = target.split()

    if not text_tokens or not target_tokens:
        return False, 0.0

    hits = 0
    score_sum = 0.0

    for i, tt in enumerate(target_tokens):
        best = 0.0
        for st in text_tokens:
            r = _ratio(tt, st)
            if r > best:
                best = r

        # Position-aware thresholds
        if i == 0:  # brand token: "kirox"
            cutoff = first_token_cutoff
        elif i == len(target_tokens) - 1:  # discriminator: "alpha"
            cutoff = last_token_cutoff
        else:
            cutoff = token_cutoff

        if best >= cutoff:
            hits += 1
            score_sum += best
        elif require_last_token and i == len(target_tokens) - 1:
            # last token is mandatory
            return False, 0.0

    if hits < min_hit:
        return False, 0.0

    avg_score = score_sum / hits
    return True, avg_score
