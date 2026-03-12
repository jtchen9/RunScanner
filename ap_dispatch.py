#!/usr/bin/env python3
from typing import Any, Dict, Tuple

from common_nms import parse_args_json
from ap_handlers_traffic import set_traffic_enabled
from ap_handlers_status import get_ap_status


def dispatch(cmd_fields: Dict[str, Any]) -> Tuple[str, str]:
    """
    Dummy AP dispatcher.
    Returns (status, detail).
    """
    category = (cmd_fields.get("category") or "").strip()
    action = (cmd_fields.get("action") or "").strip()
    args = parse_args_json(cmd_fields.get("args_json") or "")

    if category and category != "ap":
        return "error", f"unsupported category={category}"

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
