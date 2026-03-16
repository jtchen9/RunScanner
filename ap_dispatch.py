#!/usr/bin/env python3
from typing import Any, Dict, Tuple, Callable

from common_nms import parse_args_json
from bundle_manager import apply_bundle
from ap_handlers_traffic import set_traffic_enabled
from ap_handlers_status import get_ap_status


def dispatch(
    nms_base: str,
    scanner: str,
    cmd_fields: Dict[str, Any],
    http_timeout_sec: int,
    log_func: Callable[[str], None],
) -> Tuple[str, str]:
    """
    AP dispatcher.
    Returns (status, detail).
    """
    category = (cmd_fields.get("category") or "").strip()
    action = (cmd_fields.get("action") or "").strip()
    args = parse_args_json(cmd_fields.get("args_json") or "")

    if category and category != "ap":
        return "error", f"unsupported category={category}"

    if action == "bundle.apply":
        bundle_id = (args.get("bundle_id") or "").strip() or (cmd_fields.get("bundle_id") or "").strip()
        url = (args.get("url") or "").strip() or (cmd_fields.get("url") or "").strip()

        if not bundle_id:
            return "error", "bundle.apply missing bundle_id"

        if not url:
            url = f"{nms_base}/bootstrap/bundle/{bundle_id}"

        ok, detail = apply_bundle(bundle_id, url)
        status = "ok" if ok else "error"
        return status, detail

    if action == "ap.association.get":
        st = get_ap_status()
        count = len(st.get("associations") or [])
        return "ok", f"association table returned count={count}"

    if action == "ap.sta.associate":
        sta_mac = (args.get("sta_mac") or "").strip()
        return "ok", f"dummy associate sta_mac={sta_mac or '(missing)'}"

    if action == "ap.sta.disassociate":
        sta_mac = (args.get("sta_mac") or "").strip()
        return "ok", f"dummy disassociate sta_mac={sta_mac or '(missing)'}"

    if action == "ap.txpower.set":
        sta_mac = (args.get("sta_mac") or "").strip()
        txpower = args.get("txpower")
        mode = "per-sta" if sta_mac else "overall"
        return "ok", f"dummy txpower.set mode={mode} sta_mac={sta_mac or '-'} txpower={txpower}"

    if action == "ap.traffic.enable":
        ok, detail = set_traffic_enabled(True)
        return ("ok" if ok else "error"), detail

    if action == "ap.traffic.disable":
        ok, detail = set_traffic_enabled(False)
        return ("ok" if ok else "error"), detail

    return "error", f"unsupported action={action}"