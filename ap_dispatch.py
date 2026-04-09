#!/usr/bin/env python3
import subprocess
import re
import json
from typing import Any, Dict, Tuple, List

from common_nms import parse_args_json
from ap_handlers_traffic import set_traffic_enabled
from ap_handlers_status import get_ap_status

AP_INTERFACES = ["wlan0.1", "wlan0.2", "wlan0.3", "wlan0.4", "wlan0.5",
                 "wlan1.1", "wlan1.2", "wlan1.3", "wlan1.4", "wlan1.5",
                 "wlan2.1", "wlan2.2", "wlan2.3", "wlan2.4", "wlan2.5"]


def dispatch(cmd_fields: Dict[str, Any]) -> Tuple[str, str]:
    """AP command dispatcher. Returns (status, detail)."""
    category = (cmd_fields.get("category") or "").strip()
    action = (cmd_fields.get("action") or "").strip()
    args = parse_args_json(cmd_fields.get("args_json") or "")

    if category and category != "ap":
        return "error", f"unsupported category={category}"

    if action == "ap.association.get":
        st = get_ap_status()
        associations = st.get("associations") or []
        count = len(associations)
        
        devices_info = []
        for assoc in associations:
            device = {
                "mac": assoc.get("sta_mac", "unknown"),
                "interface": assoc.get("interface", ""),
                "ssid": assoc.get("ssid", ""),
                "band": assoc.get("band", "unknown"),
                "channel": assoc.get("channel", 0),
                "signal_dbm": assoc.get("signal_dbm", 0),
                "mcs": assoc.get("mcs", 0),
                "tx_bitrate": assoc.get("tx_bitrate", "unknown"),
                "rx_bitrate": assoc.get("rx_bitrate", "unknown")
            }
            devices_info.append(device)
        
        result = {
            "count": count,
            "devices": devices_info
        }
        
        return "ok", json.dumps(result, ensure_ascii=False)

    if action == "ap.sta.associate":
        sta_mac = (args.get("sta_mac") or "").strip()
        if not sta_mac:
            return "error", "missing sta_mac"

        return "ok", f"associate request noted for sta_mac={sta_mac} (passive association)"

    if action == "ap.sta.disassociate":
        sta_mac = (args.get("sta_mac") or "").strip()
        if not sta_mac:
            return "error", "missing sta_mac"

        time_period = args.get("time_period", 10)
        try:
            time_period_sec = int(time_period)
            if time_period_sec < 0:
                return "error", "time_period must be non-negative"
        except (ValueError, TypeError):
            return "error", f"invalid time_period value: {time_period}"

        return disassociate_station(sta_mac, time_period_sec)

    if action == "ap.txpower.set":
        sta_mac = (args.get("sta_mac") or "").strip()
        txpower = args.get("txpower")

        if txpower is None:
            return "error", "missing txpower"

        try:
            txpower_dbm = int(txpower)
        except (ValueError, TypeError):
            return "error", f"invalid txpower value: {txpower}"

        if sta_mac:
            return set_station_txpower(sta_mac, txpower_dbm)
        else:
            return set_overall_txpower(txpower_dbm)

    if action == "ap.traffic.enable":
        ok, detail = set_traffic_enabled(True)
        return ("ok" if ok else "error"), detail

    if action == "ap.traffic.disable":
        ok, detail = set_traffic_enabled(False)
        return ("ok" if ok else "error"), detail

    return "error", f"unsupported action={action}"


def disassociate_station(sta_mac: str, time_period_sec: int = 10) -> Tuple[str, str]:
    """
    Force disconnect client and ban on all interfaces.
    Ban first, then deauthenticate to prevent immediate reconnection.
    
    Args:
        sta_mac: MAC address of the station to disassociate
        time_period_sec: Duration in seconds to ban the client (default: 10)
    """
    interface = find_station_interface(sta_mac)

    if not interface:
        return "error", f"station {sta_mac} not found on any interface"

    active_interfaces = get_active_interfaces()
    banned_count = 0
    ban_time_ms = time_period_sec * 1000
    
    for iface in active_interfaces:
        try:
            result = subprocess.run(
                ['ubus', 'call', 'hostapd.' + iface, 'del_client',
                 f'{{"addr":"{sta_mac}","reason":5,"deauth":true,"ban_time":{ban_time_ms}}}'],
                capture_output=True,
                text=True,
                timeout=3,
                check=False
            )
            if result.returncode == 0:
                banned_count += 1
        except Exception:
            pass

    deauth_success = False
    error_msg = ""
    
    try:
        result = subprocess.run(
            ['hostapd_cli', '-i', interface, 'deauthenticate', sta_mac],
            capture_output=True,
            text=True,
            timeout=5,
            check=False
        )

        if result.returncode == 0 or 'OK' in result.stdout:
            deauth_success = True
        else:
            error_msg = (result.stderr or result.stdout or "").strip()

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"

    if not deauth_success and banned_count == 0:
        return "error", f"failed to deauthenticate {sta_mac}: {error_msg}"

    return "ok", f"banned sta_mac={sta_mac} on {banned_count}/{len(active_interfaces)} interfaces for {time_period_sec}s, deauthenticated from {interface}"


def set_overall_txpower(txpower_dbm: int) -> Tuple[str, str]:
    """Set overall TX power (not supported due to regulatory domain lock)."""
    if not (0 <= txpower_dbm <= 30):
        return "error", f"txpower out of range: {txpower_dbm} (valid: 0-30 dBm)"

    return "error", (
        f"txpower modification not supported on this system. "
        f"txpower is locked by regulatory domain. "
        f"Current setting can be viewed with: iw dev wlan0.1 info | grep txpower"
    )


def set_station_txpower(sta_mac: str, txpower_dbm: int) -> Tuple[str, str]:
    """Set per-station TX power (not supported by driver)."""
    if not (0 <= txpower_dbm <= 30):
        return "error", f"txpower out of range: {txpower_dbm} (valid: 0-30 dBm)"

    interface = find_station_interface(sta_mac)

    if not interface:
        return "error", f"station {sta_mac} not found on any interface"

    return "error", f"per-station txpower not supported (sta_mac={sta_mac}, interface={interface})"


def find_station_interface(sta_mac: str) -> str:
    """Find interface where the specified station is associated."""
    sta_mac_lower = sta_mac.lower()

    for interface in AP_INTERFACES:
        try:
            result = subprocess.run(
                ['iw', 'dev', interface, 'station', 'get', sta_mac_lower],
                capture_output=True,
                text=True,
                timeout=3,
                check=False
            )

            if result.returncode == 0 and sta_mac_lower in result.stdout.lower():
                return interface

        except Exception:
            continue

    return ""


def get_active_interfaces() -> List[str]:
    """Get all active wireless interfaces."""
    active = []

    for interface in AP_INTERFACES:
        try:
            result = subprocess.run(
                ['iw', 'dev', interface, 'info'],
                capture_output=True,
                text=True,
                timeout=2,
                check=False
            )

            if result.returncode == 0 and 'type AP' in result.stdout:
                active.append(interface)

        except Exception:
            continue

    return active
