#!/usr/bin/env python3
import requests

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
        