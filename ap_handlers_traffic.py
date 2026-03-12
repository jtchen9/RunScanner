#!/usr/bin/env python3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any

from config import BASE_DIR, TIME_FMT

AP_TRAFFIC_CFG_FILE = BASE_DIR / "ap_traffic_config.json"


def _load_cfg() -> Dict[str, Any]:
    try:
        return json.loads(AP_TRAFFIC_CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False}


def _save_cfg(cfg: Dict[str, Any]) -> bool:
    try:
        tmp = AP_TRAFFIC_CFG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(AP_TRAFFIC_CFG_FILE)
        return True
    except Exception:
        return False


def set_traffic_enabled(enabled: bool) -> tuple[bool, str]:
    cfg = _load_cfg()
    cfg["enabled"] = bool(enabled)
    ok = _save_cfg(cfg)
    if ok:
        return True, f"ap traffic {'enabled' if enabled else 'disabled'}"
    return False, "failed to update ap traffic config"


def is_traffic_enabled() -> bool:
    cfg = _load_cfg()
    return bool(cfg.get("enabled", False))


def get_ap_traffic_report() -> Dict[str, Any]:
    """
    Dummy per-STA per-AC one-minute traffic report.
    Your colleague can later replace the fixed values with real AP stats.
    """
    t_end = datetime.now()
    t_start = t_end - timedelta(minutes=1)

    return {
        "time_start": t_start.strftime(TIME_FMT),
        "time_end": t_end.strftime(TIME_FMT),
        "records": [
            {
                "sta_mac": "11:22:33:44:55:66",
                "ac": "BE",
                "avg_frame_duration_us": 820.5,
                "frame_count": 1200,
                "mcs_distribution": {
                    "0": 15,
                    "1": 30,
                    "2": 100,
                    "3": 180,
                    "4": 250,
                    "5": 300,
                    "6": 220,
                    "7": 105,
                },
            },
            {
                "sta_mac": "11:22:33:44:55:66",
                "ac": "VO",
                "avg_frame_duration_us": 410.2,
                "frame_count": 120,
                "mcs_distribution": {
                    "5": 20,
                    "6": 60,
                    "7": 40,
                },
            },
            {
                "sta_mac": "22:33:44:55:66:77",
                "ac": "BK",
                "avg_frame_duration_us": 1300.0,
                "frame_count": 300,
                "mcs_distribution": {
                    "2": 50,
                    "3": 120,
                    "4": 130,
                },
            },
        ],
    }
