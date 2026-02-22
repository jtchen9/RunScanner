#!/usr/bin/env python3
"""
voice_common.py (Wave-2)

Shared utilities for voice service + agent integration.
Keeps *all* Wave-2 voice files under /home/pi/_RunScanner/voice
"""

from __future__ import annotations

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
VOICE_LOG_FILE = VOICE_DIR / "voice_service.log"
DEFAULT_CALLSIGNS = [
    "alpha","bravo","charlie","delta","echo","foxtrot","golf","hotel","india","julia"
]


# def _prefix_score(toks: list[str]) -> float:
#     """
#     Score how strongly prefixes are present.
#     We keep it tolerant, but measurable for ranking.
#     """
#     best = 0.0
#     for t in toks:
#         best = max(best, _ratio(t, "twin"), _ratio(t, "scout"))
#     return best
def _prefix_score(toks: list[str]) -> float:
    best_twin = 0.0
    best_scout = 0.0
    for t in toks:
        best_twin = max(best_twin, _ratio(t, "twin"))
        best_scout = max(best_scout, _ratio(t, "scout"))
    return min(best_twin, best_scout)   # requires both

def _callsign_score(toks: list[str], callsign: str) -> float:
    best = 0.0
    for t in toks:
        _, r = best_token_match(t, [callsign])
        best = max(best, r)
    return best

def match_wake_name_ranked(
    text_norm: str,
    *,
    callsign: str,
    all_callsigns: list[str] | None = None,
    min_callsign_ratio: float = CALLSIGN_MIN_RATIO,
    min_prefix_ratio: float = PREFIX_MIN_RATIO,
    min_margin: float = 0.15,
) -> tuple[bool, str]:
    """
    Ranked wake decision:
      - compute score for each callsign in all_callsigns
      - accept only if THIS callsign is the best and wins by min_margin
      - keep the original prefix rule, but use it consistently

    Why this helps:
      - prevents alpha<->bravo when both are "kind of similar" in STT output
      - tunable with min_margin
    """
    toks = (text_norm or "").split()
    if not toks:
        return False, "no tokens"

    all_callsigns = all_callsigns or DEFAULT_CALLSIGNS

    # score components
    pref = _prefix_score(toks)  # 0..1
    prefix_ok = (pref >= min_prefix_ratio)

    # compute per-callsign best token ratio
    cs_scores: dict[str, float] = {}
    for cs in all_callsigns:
        cs_scores[cs] = _callsign_score(toks, cs)

    # rank
    ranked = sorted(cs_scores.items(), key=lambda kv: kv[1], reverse=True)
    best_cs, best_r = ranked[0]
    second_r = ranked[1][1] if len(ranked) > 1 else 0.0

    # enforce "my callsign must be the best"
    if best_cs != callsign:
        return False, f"rank_no(best={best_cs}:{best_r:.2f} mine={callsign}:{cs_scores.get(callsign,0.0):.2f} second={second_r:.2f} prefix={pref:.2f})"

    # enforce callsign minimum
    if best_r < min_callsign_ratio:
        return False, f"callsign_no(best={best_r:.2f} min={min_callsign_ratio:.2f} prefix={pref:.2f})"

    # enforce prefix rule
    if not prefix_ok and not ALLOW_CALLSIGN_ONLY:
        return False, f"prefix_no(prefix={pref:.2f} min={min_prefix_ratio:.2f} best={best_r:.2f})"

    # enforce separation margin (this is the key for alpha/bravo separation)
    if (best_r - second_r) < min_margin:
        return False, f"margin_no(best={best_r:.2f} second={second_r:.2f} margin={(best_r-second_r):.2f} need={min_margin:.2f} prefix={pref:.2f})"

    # accepted
    if prefix_ok:
        return True, f"ok(rank+prefix best={best_r:.2f} second={second_r:.2f} prefix={pref:.2f})"
    return True, f"ok(rank+callsign-only best={best_r:.2f} second={second_r:.2f} prefix={pref:.2f})"

# Config IO lives in voice_config.py (single source of truth)
from voice_config import load_voice_config, save_voice_config, update_voice_config

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
        # common prefix confusions
        "twins": "twin",
        "twinss": "twin",
        "skull": "scout",
        "scowt": "scout",
        "scoutt": "scout",
        "call": "scout",
        "calls": "scout",

        # callsign variants (10 robots)
        "alfa": "alpha",
        "alphas": "alpha",

        "bravo": "bravo",
        "bravos": "bravo",

        "charley": "charlie",
        "chalie": "charlie",
        "charli": "charlie",

        "deltha": "delta",
        "deltah": "delta",
        "delta": "delta",

        "eco": "echo",
        "ecko": "echo",
        "echo": "echo",

        "fox": "foxtrot",
        "foxtrots": "foxtrot",
        "foxtrot": "foxtrot",

        "gulf": "golf",
        "golff": "golf",
        "golf": "golf",

        "hotel": "hotel",
        "hotell": "hotel",

        "india": "india",
        "indya": "india",

        "julia": "julia",
        "juliet": "julia",
        "jullia": "julia",
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
