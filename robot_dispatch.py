#!/usr/bin/env python3
from typing import Any, Dict, Tuple

from common_nms import parse_args_json
from robot_agent_handlers import (
    exec_scan_start,
    exec_scan_stop,
    exec_scan_once,
    exec_av_stream_start,
    exec_av_stream_stop,
    exec_audio_play,
    exec_audio_stop,
    exec_tts_say,
    exec_voice_start_local,
    exec_voice_stop_local,
    exec_voice_mode_set_local,
    exec_voice_script_set_local,
    report_installed_bundle,
    exec_mobility_move_forward,
    exec_mobility_move_backward,
    exec_mobility_turn_left,
    exec_mobility_turn_right,
    exec_mobility_report_location,
)
from bundle_manager import apply_bundle
from voice.voice_agent_api import exec_voice_llm_config_set


def dispatch(
    nms_base: str,
    scanner: str,
    cmd_fields: Dict[str, Any],
    http_timeout_sec: int,
    log_func,
) -> Tuple[str, str]:
    """
    Execute one command.
    Returns (status, detail) where status in {'ok','error'}.
    """
    category = (cmd_fields.get("category") or "").strip()
    action = (cmd_fields.get("action") or "").strip()
    args = parse_args_json(cmd_fields.get("args_json") or "")

    if category and category not in ("scan", "av", "voice", "mobility"):
        return "error", f"unsupported category={category}"

    # =====================
    # scan
    # =====================
    if action == "scan.start":
        ok, detail = exec_scan_start()
        return ("ok" if ok else "error"), detail

    if action == "scan.stop":
        ok, detail = exec_scan_stop()
        return ("ok" if ok else "error"), detail

    if action == "scan.once":
        ok, detail = exec_scan_once()
        return ("ok" if ok else "error"), detail

    # ======================
    # bundle
    # ======================
    if action == "bundle.apply":
        bundle_id = (args.get("bundle_id") or "").strip() or (cmd_fields.get("bundle_id") or "").strip()
        url = (args.get("url") or "").strip() or (cmd_fields.get("url") or "").strip()

        if not bundle_id:
            return "error", "bundle.apply missing bundle_id"

        if not url:
            url = f"{nms_base}/bootstrap/bundle/{bundle_id}"

        ok, detail = apply_bundle(bundle_id, url)
        status = "ok" if ok else "error"

        if ok:
            report_installed_bundle(
                nms_base=nms_base,
                scanner=scanner,
                installed_version=bundle_id,
                http_timeout_sec=http_timeout_sec,
                log_func=log_func,
            )

        return status, detail

    # ===================
    # av
    # ===================
    if action == "av.stream.start":
        ok, detail = exec_av_stream_start(scanner, args)
        return ("ok" if ok else "error"), detail

    if action == "av.stream.stop":
        ok, detail = exec_av_stream_stop()
        return ("ok" if ok else "error"), detail

    # ===================
    # audio / tts
    # ===================
    if action == "audio.play":
        ok, detail = exec_audio_play(scanner, args)
        return ("ok" if ok else "error"), detail

    if action == "audio.stop":
        ok, detail = exec_audio_stop(scanner, args)
        return ("ok" if ok else "error"), detail

    if action == "tts.say":
        ok, detail = exec_tts_say(scanner, args)
        return ("ok" if ok else "error"), detail

    # ===================
    # mobility
    # ===================
    if category == "mobility":
        if action == "mobility.move.forward":
            ok, detail = exec_mobility_move_forward(args)
            return ("ok" if ok else "error"), detail

        if action == "mobility.move.backward":
            ok, detail = exec_mobility_move_backward(args)
            return ("ok" if ok else "error"), detail

        if action == "mobility.turn.left":
            ok, detail = exec_mobility_turn_left(args)
            return ("ok" if ok else "error"), detail

        if action == "mobility.turn.right":
            ok, detail = exec_mobility_turn_right(args)
            return ("ok" if ok else "error"), detail

        if action == "mobility.report.location":
            ok, detail = exec_mobility_report_location(args)
            return ("ok" if ok else "error"), detail

        return "error", f"unknown mobility action: {action}"

    # ====================
    # voice
    # ====================
    if category == "voice":
        if action == "voice.start":
            ok, detail = exec_voice_start_local(args)
            return ("ok" if ok else "error"), detail

        if action == "voice.stop":
            ok, detail = exec_voice_stop_local()
            return ("ok" if ok else "error"), detail

        if action == "voice.mode.set":
            ok, detail = exec_voice_mode_set_local(args)
            return ("ok" if ok else "error"), detail

        if action == "voice.script.set":
            ok, detail = exec_voice_script_set_local(args)
            return ("ok" if ok else "error"), detail

        if action == "voice.llm.config.set":
            ok, detail = exec_voice_llm_config_set(args)
            return ("ok" if ok else "error"), detail

        return "error", f"unknown voice action: {action}"

    return "error", f"unknown action={action}"
