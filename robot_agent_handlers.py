#!/usr/bin/env python3
import requests
import json
from pathlib import Path

from robot_handlers_scan import (
    exec_scan_start,
    exec_scan_stop,
    exec_scan_once,
)

from robot_handlers_av import (
    get_av_streaming_flag,
    exec_av_stream_start,
    exec_av_stream_stop,
)

from robot_handlers_audio import (
    exec_audio_play,
    exec_audio_stop,
    exec_tts_say,
)

from robot_handlers_voice import (
    exec_voice_start_local,
    exec_voice_stop_local,
    exec_voice_mode_set_local,
    exec_voice_script_set_local,
)

from robot_handlers_mobility import (
    exec_mobility_turn,
    exec_mobility_turn_move_turn_forward,
    exec_mobility_turn_move_turn_backward,
    exec_mobility_report_location,
)


MOBILITY_STATE_PATH = Path("/tmp/mobility_state.json")


def get_mobility_report_payload():
    """
    Return one-shot mobility report payload for command poller.
    Option B: if pending_report is false or file missing, return {}.
    """
    try:
        if not MOBILITY_STATE_PATH.exists():
            return {}

        state = json.loads(MOBILITY_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return {}

        if not state.get("pending_report", False):
            return {}

        return {
            "last_command": state.get("last_command", ""),
            "last_command_args": state.get("last_command_args", {}),
            "last_command_received_ts": state.get("last_command_received_ts", 0.0),
            "last_command_finished_ts": state.get("last_command_finished_ts", 0.0),
            "last_exec_status": state.get("last_exec_status", ""),
            "last_error_code": state.get("last_error_code", ""),
            "last_error_detail": state.get("last_error_detail", ""),
            "last_location_result": state.get("last_location_result", None),
        }
    except Exception:
        return {}


def mark_mobility_report_sent():
    """
    Option B: clear pending_report after successful fetch_commands().
    """
    try:
        if not MOBILITY_STATE_PATH.exists():
            return

        state = json.loads(MOBILITY_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return

        state["pending_report"] = False
        MOBILITY_STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    
def report_installed_bundle(
    nms_base: str,
    scanner: str,
    installed_version: str,
    http_timeout_sec: int,
    log_func,
) -> None:
    url = f"{nms_base}/bootstrap/report/{scanner}"
    body = {"installed_version": installed_version}
    try:
        r = requests.post(url, json=body, timeout=http_timeout_sec)
        if r.status_code != 200:
            log_func(f"BOOTSTRAP report fail http={r.status_code} body={r.text[:200]}")
    except Exception as e:
        log_func(f"BOOTSTRAP report exception: {type(e).__name__}: {e}")
        