#!/usr/bin/env python3
from __future__ import annotations

import json
import time
import requests
from pathlib import Path
from typing import Any, Dict, Tuple, Optional
from voice_common import voice_log, load_voice_config
import re

_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
LLM_STATE_PATH = Path("/home/pi/_RunScanner/voice/llm_state.json")

def _now_ts() -> str:
    return time.strftime("%Y-%m-%d-%H:%M:%S", time.localtime())

def _read_text_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def _load_state() -> Dict[str, Any]:
    try:
        if LLM_STATE_PATH.exists():
            return json.loads(LLM_STATE_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        pass
    return {}

def _save_state(state: Dict[str, Any]) -> None:
    try:
        tmp = LLM_STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(LLM_STATE_PATH)
    except Exception:
        pass

def _extract_output_text(resp_json: Dict[str, Any]) -> str:
    """
    Responses API returns 'output' array with content blocks.
    We'll collect any text chunks.
    """
    out = []
    for item in (resp_json.get("output") or []):
        for c in (item.get("content") or []):
            if c.get("type") == "output_text":
                t = (c.get("text") or "").strip()
                if t:
                    out.append(t)
    return "\n".join(out).strip()

def _id_is_safe(s: str) -> bool:
    return bool(s) and bool(_ID_SAFE_RE.match(s))

def _pick_session_id(llm_cfg: Dict[str, Any]) -> str:
    # Backward/forward compatible with your config naming
    sid = str(llm_cfg.get("session_id") or "").strip()
    if not sid:
        sid = str(llm_cfg.get("conversation_id") or "").strip()
    return sid

def llm_exchange(user_text: str) -> Tuple[bool, str]:
    """
    Returns (ok, assistant_text_or_error)
    """
    user_text = (user_text or "").strip()
    if not user_text:
        return True, ""

    cfg = load_voice_config()
    llm = cfg.get("llm") or {}

    base_url = str(llm.get("base_url") or "https://api.openai.com/v1/responses").strip()
    model = str(llm.get("model") or "").strip()
    api_key_file = str(llm.get("api_key_file") or "").strip()
    timeout_sec = int(llm.get("timeout_sec") or 30)
    max_out = int(llm.get("max_output_tokens") or 300)
    temperature = float(llm.get("temperature") or 0.4)

    session_id = _pick_session_id(llm)

    if not model:
        return False, "LLM config missing: llm.model"
    if not api_key_file:
        return False, "LLM config missing: llm.api_key_file"

    key = _read_text_file(Path(api_key_file))
    if not key:
        return False, f"LLM key file empty: {api_key_file}"

    # Load state
    state = _load_state()
    prev_id = str(state.get("previous_response_id") or "").strip()
    if prev_id and not _id_is_safe(prev_id):
        # sanitize bad stored value
        prev_id = ""
        state.pop("previous_response_id", None)
        state["updated_at"] = _now_ts()
        _save_state(state)

    system_text = (
        "You are a small helpful voice assistant running on a Raspberry Pi robot. "
        "Be brief, clear, and practical. No long explanations unless asked."
    )

    def _do_request(prev: str) -> Tuple[bool, str, Optional[Dict[str, Any]], int, str]:
        payload: Dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            "max_output_tokens": max_out,
            "temperature": temperature,
        }
        if prev:
            payload["previous_response_id"] = prev
        if session_id:
            payload["metadata"] = {"session_id": session_id}

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        try:
            r = requests.post(base_url, headers=headers, json=payload, timeout=timeout_sec)
        except Exception as e:
            return False, f"LLM request failed: {type(e).__name__}: {e}", None, 0, ""

        if r.status_code >= 300:
            body = (r.text or "")[:400].replace("\n", " ")
            return False, f"LLM http={r.status_code} body={body}", None, r.status_code, body

        try:
            j = r.json()
        except Exception:
            return False, "LLM returned non-JSON response", None, r.status_code, ""

        return True, "ok", j, r.status_code, ""

    # First attempt (with prev_id if present)
    ok, detail, j, http_code, body = _do_request(prev_id)

    # Auto-retry once if prev_id is rejected (common case)
    if (not ok) and http_code == 400 and prev_id:
        # Clear and retry once
        state.pop("previous_response_id", None)
        state["updated_at"] = _now_ts()
        _save_state(state)
        ok, detail, j, http_code, body = _do_request("")

    if not ok or j is None:
        return False, detail

    # Update state
    new_resp_id = str(j.get("id") or "").strip()
    if new_resp_id and _id_is_safe(new_resp_id):
        state["previous_response_id"] = new_resp_id
    if session_id:
        state["session_id"] = session_id
    state["updated_at"] = _now_ts()
    _save_state(state)

    txt = _extract_output_text(j)
    if not txt:
        txt = "I do not have an answer yet."
    return True, txt


if __name__ == "__main__":
    ok, out = llm_exchange("Hello. Say one short sentence.")
    print(("OK: " if ok else "ERR: ") + out)
