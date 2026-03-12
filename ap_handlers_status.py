#!/usr/bin/env python3
from typing import Dict, Any, List

from config import get_mac_address
from common_register import get_ip_best_effort


def get_ap_status() -> Dict[str, Any]:
    """
    Dummy AP status provider.
    Your colleague can later replace the fixed values with real AP data.
    """
    mac = get_mac_address() or "00:00:00:00:00:00"
    ip = get_ip_best_effort() or ""

    associations: List[Dict[str, Any]] = [
        {
            "sta_mac": "11:22:33:44:55:66",
            "ssid": "Dummy-5G",
            "mcs": 7,
        },
        {
            "sta_mac": "22:33:44:55:66:77",
            "ssid": "Dummy-5G",
            "mcs": 5,
        },
    ]

    return {
        "mac": mac,
        "ip": ip,
        "ssids": ["Dummy-5G", "Dummy-2G"],
        "band": "5g",
        "channel": 44,
        "antenna_count": 2,
        "associations": associations,
    }
