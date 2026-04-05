#!/usr/bin/env python3
import json
import subprocess
import re
import glob
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import BASE_DIR, TIME_FMT

AP_TRAFFIC_CFG_FILE = BASE_DIR / "ap_traffic_config.json"
AP_TRAFFIC_HISTORY_FILE = BASE_DIR / "ap_traffic_history.json"
AP_TRAFFIC_DEBUG_FILE = BASE_DIR / "ap_traffic_debug.json"
MCS_SAMPLER_DATA_FILE = Path("/tmp/mcs_distributions.json")

AP_INTERFACES = ["wlan0.1", "wlan0.2", "wlan0.3", "wlan0.4", "wlan0.5",
                 "wlan1.1", "wlan1.2", "wlan1.3", "wlan1.4", "wlan1.5",
                 "wlan2.1", "wlan2.2", "wlan2.3", "wlan2.4", "wlan2.5"]

WNC_LOGS_DIR = Path("/mnt/wnc/logs")

# WiFi overhead constants (microseconds)
WIFI_OVERHEAD_US = 40

INTERFACE_TO_HW_RADIO = {
    "wlan0": "ath12k_hw2",
    "wlan1": "ath12k_hw1",
    "wlan2": "ath12k_hw0",
}

EHT_DATA_RATES = {
    (0, 160, 2): 163.2, (1, 160, 2): 326.4, (2, 160, 2): 489.6,
    (3, 160, 2): 652.8, (4, 160, 2): 979.2, (5, 160, 2): 1305.6,
    (6, 160, 2): 1468.8, (7, 160, 2): 1632.0, (8, 160, 2): 1958.4,
    (9, 160, 2): 2176.0, (10, 160, 2): 2448.0, (11, 160, 2): 2720.0,
    (12, 160, 2): 2938.8, (13, 160, 2): 3265.2,
    (0, 80, 2): 81.6, (1, 80, 2): 163.2, (2, 80, 2): 244.8,
    (3, 80, 2): 326.4, (4, 80, 2): 489.6, (5, 80, 2): 652.8,
    (6, 80, 2): 734.4, (7, 80, 2): 816.0, (8, 80, 2): 979.2,
    (9, 80, 2): 1088.0, (10, 80, 2): 1224.0, (11, 80, 2): 1360.0,
}

CATEGORY_TO_AC = {
    "GAME": "VO",
    "CONFERENCE_WFH": "VI",
    "CONFERENCE": "VI",
    "WFH": "VI",
    "MEDIA_STREAM": "BE",
    "WEB": "BE",
    "DOWNLOAD": "BK",
    "OTHER": "BK",
    "UNKNOWN": "BK",
}

_last_stats_cache = {
    "timestamp": None,
    "stations": {}
}


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
        try:
            if enabled:
                subprocess.run(['/etc/init.d/ap-mcs-sampler', 'start'], 
                             capture_output=True, timeout=5, check=False)
            else:
                subprocess.run(['/etc/init.d/ap-mcs-sampler', 'stop'], 
                             capture_output=True, timeout=5, check=False)
        except Exception:
            pass
        
        return True, f"ap traffic {'enabled' if enabled else 'disabled'}"
    return False, "failed to update ap traffic config"


def is_traffic_enabled() -> bool:
    cfg = _load_cfg()
    return bool(cfg.get("enabled", False))


def get_ap_traffic_report() -> Dict[str, Any]:
    """Get traffic statistics from WNC device_data with per-AC support (delta since last call)."""
    t_end = datetime.now()
    t_start = t_end - timedelta(minutes=1)

    last_stats = _load_history()
    current_data = _collect_wnc_device_data()
    current_mac_stats = _get_mac_statistics()
    ac_packet_sizes = _get_ac_packet_sizes_from_iptables()
    ap_mcs_distributions = _get_ap_mcs_distributions()
    
    records = _calculate_delta_records_from_wnc(current_data, current_mac_stats, last_stats, ac_packet_sizes, ap_mcs_distributions)
    
    station_statistics = _collect_station_statistics()
    
    last_ap_mcs = last_stats.get("ap_mcs_distributions", {})
    delta_ap_mcs_distributions = {}
    for hw_radio, current_dist in ap_mcs_distributions.items():
        last_dist = last_ap_mcs.get(hw_radio, {})
        delta_dist = {}
        for mcs_idx, count in current_dist.items():
            last_count = last_dist.get(mcs_idx, 0)
            delta_count = max(0, count - last_count)
            if delta_count > 0:
                delta_dist[mcs_idx] = delta_count
        if delta_dist:
            delta_ap_mcs_distributions[hw_radio] = delta_dist
    
    debug_info = {
        "timestamp": t_end.isoformat(),
        "time_start": t_start.strftime(TIME_FMT),
        "time_end": t_end.strftime(TIME_FMT),
        "current_data_count": len(current_data),
        "last_data_count": len(last_stats.get("data", {})),
        "records_count": len(records),
        "current_data": current_data,
        "last_data": last_stats.get("data", {}),
        "current_mac_stats": current_mac_stats,
        "last_mac_stats": last_stats.get("mac_stats", {}),
        "current_ap_mcs_distributions": ap_mcs_distributions,
        "last_ap_mcs_distributions": last_ap_mcs,
        "delta_ap_mcs_distributions": delta_ap_mcs_distributions,
        "records": records,
    }
    _save_debug_info(debug_info)
    
    _save_history({
        "timestamp": t_end.isoformat(),
        "data": current_data,
        "mac_stats": current_mac_stats,
        "ap_mcs_distributions": ap_mcs_distributions
    })

    return {
        "time_start": t_start.strftime(TIME_FMT),
        "time_end": t_end.strftime(TIME_FMT),
        "records": records,
    }


def _collect_wnc_device_data() -> Dict[str, Dict[str, Any]]:
    """Collect traffic data from WNC device_data files.
    
    Uses UTC time to determine which category file to read, ensuring consistency
    regardless of system timezone changes.
    """
    current_hour = datetime.utcnow().hour
    
    before_file = WNC_LOGS_DIR / f"device_data_before_category_{current_hour}"
    now_file = WNC_LOGS_DIR / f"device_data_now_category_{current_hour}"
    
    data = {}
    
    before_stats = _parse_device_data_file(before_file)
    now_stats = _parse_device_data_file(now_file)
    
    all_keys = set(before_stats.keys()) | set(now_stats.keys())
    
    for key in all_keys:
        before = before_stats.get(key, {"download_bytes": 0, "upload_bytes": 0})
        now = now_stats.get(key, {"download_bytes": 0, "upload_bytes": 0})
        
        parts = key.split(":")
        ip = parts[0] if len(parts) > 0 else ""
        category = parts[1] if len(parts) > 1 else "UNKNOWN"
        
        data[key] = {
            "ip": ip,
            "category": category,
            "download_bytes": before["download_bytes"] + now["download_bytes"],
            "upload_bytes": before["upload_bytes"] + now["upload_bytes"],
        }
    
    return data


def _parse_device_data_file(file_path: Path) -> Dict[str, Dict[str, int]]:
    """Parse device_data file (format: IP,CATEGORY,DOWNLOAD_BITS,UPLOAD_BITS).
    Converts bits to bytes.
    """
    stats = {}
    
    try:
        if not file_path.exists():
            return stats
        
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('ELSE,'):
                    continue
                
                parts = line.split(',')
                if len(parts) < 4:
                    continue
                
                ip = parts[0].strip()
                category = parts[1].strip()
                download_bits = int(parts[2].strip())
                upload_bits = int(parts[3].strip())
                
                download_bytes = download_bits // 8
                upload_bytes = upload_bits // 8
                
                key = f"{ip}:{category}"
                stats[key] = {
                    "download_bytes": download_bytes,
                    "upload_bytes": upload_bytes,
                }
    
    except Exception:
        pass
    
    return stats


def _calculate_delta_records_from_wnc(
    current_data: Dict[str, Dict[str, Any]],
    current_mac_stats: Dict[str, Dict[str, Any]],
    last_history: Dict[str, Any],
    ac_packet_sizes: Dict[str, float],
    ap_mcs_distributions: Dict[str, Dict[str, int]]
) -> List[Dict[str, Any]]:
    """Calculate traffic deltas from WNC data and generate records."""
    if not last_history.get("timestamp"):
        return []
    
    last_ap_mcs = last_history.get("ap_mcs_distributions", {})
    delta_ap_mcs_distributions = {}
    
    for hw_radio, current_dist in ap_mcs_distributions.items():
        last_dist = last_ap_mcs.get(hw_radio, {})
        delta_dist = {}
        
        for mcs_idx, count in current_dist.items():
            last_count = last_dist.get(mcs_idx, 0)
            delta_count = max(0, count - last_count)
            if delta_count > 0:
                delta_dist[mcs_idx] = delta_count
        
        if delta_dist:
            delta_ap_mcs_distributions[hw_radio] = delta_dist
    
    records_map: Dict[tuple, Dict[str, Any]] = {}
    last_data = last_history.get("data", {})
    last_mac_stats = last_history.get("mac_stats", {})
    
    ip_to_mac = _build_ip_to_mac_mapping()
    
    ip_stats = {}
    for key, stats in current_data.items():
        ip = stats["ip"]
        category = stats["category"]
        
        if ip not in ip_stats:
            ip_stats[ip] = {}
        
        if category not in ip_stats[ip]:
            ip_stats[ip][category] = {
                "download_bytes": 0,
                "upload_bytes": 0,
            }
        
        ip_stats[ip][category]["download_bytes"] += stats["download_bytes"]
        ip_stats[ip][category]["upload_bytes"] += stats["upload_bytes"]
    
    interface_total_delta_bytes = {}
    interface_user_info = {}
    
    for ip, categories in ip_stats.items():
        mac = ip_to_mac.get(ip)
        if not mac or mac == "00:00:00:00:00:00":
            continue
        
        current_mac_info = current_mac_stats.get(mac, {})
        interface = current_mac_info.get("interface", "")
        
        if not interface:
            continue
        
        if interface not in interface_total_delta_bytes:
            interface_total_delta_bytes[interface] = 0
        if interface not in interface_user_info:
            interface_user_info[interface] = []
        
        for category, current_stats in categories.items():
            key = f"{ip}:{category}"
            
            if key in last_data:
                last_stats = last_data[key]
                delta_download = max(0, current_stats["download_bytes"] - last_stats["download_bytes"])
                delta_upload = max(0, current_stats["upload_bytes"] - last_stats["upload_bytes"])
            else:
                delta_download = current_stats["download_bytes"]
                delta_upload = current_stats["upload_bytes"]
            
            interface_total_delta_bytes[interface] += delta_download + delta_upload
            
            tx_mcs = current_mac_info.get('tx_mcs')
            if tx_mcs is not None:
                interface_user_info[interface].append({
                    'mac': mac,
                    'category': category,
                    'tx_mcs': tx_mcs,
                    'traffic_bytes': delta_download + delta_upload,
                })
    
    for ip, categories in ip_stats.items():
        mac = ip_to_mac.get(ip)
        
        if not mac or mac == "00:00:00:00:00:00":
            continue
        
        if mac not in current_mac_stats:
            continue
        
        current_mac_info = current_mac_stats.get(mac, {})
        current_total_packets = current_mac_info.get("total_packets", 0)
        current_total_duration = current_mac_info.get("total_duration_us", 0)
        
        last_mac_info = last_mac_stats.get(mac, {})
        last_total_packets = last_mac_info.get("total_packets", 0)
        last_total_duration = last_mac_info.get("total_duration_us", 0)
        
        delta_packets = max(0, current_total_packets - last_total_packets)
        delta_duration_us = max(0, current_total_duration - last_total_duration)
        
        total_delta_bytes = 0
        category_deltas = {}
        
        for category, current_stats in categories.items():
            key = f"{ip}:{category}"
            
            if key in last_data:
                last_stats = last_data[key]
                delta_download = max(0, current_stats["download_bytes"] - last_stats["download_bytes"])
                delta_upload = max(0, current_stats["upload_bytes"] - last_stats["upload_bytes"])
            else:
                delta_download = current_stats["download_bytes"]
                delta_upload = current_stats["upload_bytes"]
            
            delta_bytes = delta_download + delta_upload
            category_deltas[category] = {
                "download_bytes": delta_download,
                "upload_bytes": delta_upload,
                "total_bytes": delta_bytes,
            }
            total_delta_bytes += delta_bytes
        
        for category, deltas in category_deltas.items():
            if deltas["total_bytes"] == 0:
                continue
            
            ratio = deltas["total_bytes"] / total_delta_bytes if total_delta_bytes > 0 else 0
            
            estimated_frames = int(delta_packets * ratio)
            
            ac = CATEGORY_TO_AC.get(category, "BE")
            
            avg_packet_size = ac_packet_sizes.get(ac, 1200.0)
            tx_mcs = current_mac_info.get('tx_mcs')
            tx_bandwidth = current_mac_info.get('tx_bandwidth')
            tx_nss = current_mac_info.get('tx_nss')
            
            avg_duration = _calculate_frame_duration(
                avg_packet_size, tx_mcs, tx_bandwidth, tx_nss
            )
            
            current_mcs_dist = current_mac_info.get("mcs_distribution")
            tx_mcs = current_mac_info.get("tx_mcs")
            is_real_mcs = current_mac_info.get("is_real_mcs_distribution", False)
            mcs_data_source = current_mac_info.get("mcs_source")
            interface = current_mac_info.get("interface", "")
            
            final_mcs_dist = None
            mcs_source = None
            
            if current_mcs_dist and is_real_mcs:
                if mcs_data_source == "background_sampler":
                    if current_mcs_dist:
                        total_mcs_frames = sum(current_mcs_dist.values())
                        if total_mcs_frames > 0 and estimated_frames > 0:
                            scale_factor = estimated_frames / total_mcs_frames
                            final_mcs_dist = {
                                mcs_idx: max(1, int(count * scale_factor))
                                for mcs_idx, count in current_mcs_dist.items()
                            }
                        else:
                            final_mcs_dist = current_mcs_dist
                        mcs_source = "background_sampler"
                else:
                    last_mcs_dist = last_mac_info.get("mcs_distribution") or {}
                    delta_mcs_dist = {}
                    for mcs_idx, count in current_mcs_dist.items():
                        last_count = last_mcs_dist.get(mcs_idx, 0)
                        delta_count = max(0, count - last_count)
                        if delta_count > 0:
                            delta_mcs_dist[mcs_idx] = delta_count
                    
                    if delta_mcs_dist:
                        total_mcs_frames = sum(delta_mcs_dist.values())
                        if total_mcs_frames > 0 and estimated_frames > 0:
                            scale_factor = estimated_frames / total_mcs_frames
                            final_mcs_dist = {
                                mcs_idx: max(1, int(count * scale_factor))
                                for mcs_idx, count in delta_mcs_dist.items()
                            }
                        else:
                            final_mcs_dist = delta_mcs_dist
                        mcs_source = "per_station_debugfs"
            
            if not final_mcs_dist and estimated_frames > 0:
                ap_mcs_dist = _get_interface_ap_mcs_distribution(interface, delta_ap_mcs_distributions)
                if ap_mcs_dist and tx_mcs is not None:
                    user_key = (mac, category)
                    users_on_interface = interface_user_info.get(interface, [])
                    interface_total = interface_total_delta_bytes.get(interface, 0)
                    
                    final_mcs_dist = _allocate_ap_mcs_weighted(
                        ap_mcs_dist=ap_mcs_dist,
                        user_key=user_key,
                        user_tx_mcs=tx_mcs,
                        user_traffic=deltas["total_bytes"],
                        all_users=users_on_interface,
                        interface_total=interface_total
                    )
                    mcs_source = "ap_level_htt_stats"
            
            if not final_mcs_dist and estimated_frames > 0:
                default_mcs = tx_mcs if tx_mcs is not None else 9
                final_mcs_dist = _estimate_mcs_distribution_from_current(default_mcs, estimated_frames)
                mcs_source = "estimated"
            
            key = (mac, ac)
            
            if key in records_map:
                existing = records_map[key]
                total_frames = existing["frame_count"] + estimated_frames
                total_duration = (existing["avg_frame_duration_us"] * existing["frame_count"] + 
                                avg_duration * estimated_frames)
                existing["avg_frame_duration_us"] = round(total_duration / total_frames, 1) if total_frames > 0 else 0.0
                existing["frame_count"] = total_frames
                existing["download_bytes"] += deltas["download_bytes"]
                existing["upload_bytes"] += deltas["upload_bytes"]
                
                if "mcs_distribution" in existing:
                    if final_mcs_dist and estimated_frames > 0:
                        for mcs_idx, count in final_mcs_dist.items():
                            existing["mcs_distribution"][mcs_idx] = existing["mcs_distribution"].get(mcs_idx, 0) + count
                    
                    if total_frames > 0:
                        mcs_total = sum(existing["mcs_distribution"].values())
                        if mcs_total > 0 and abs(mcs_total - total_frames) > total_frames * 0.1:
                            norm_factor = total_frames / mcs_total
                            existing["mcs_distribution"] = {
                                mcs_idx: max(1, int(count * norm_factor))
                                for mcs_idx, count in existing["mcs_distribution"].items()
                            }
                            adjusted_total = sum(existing["mcs_distribution"].values())
                            if adjusted_total != total_frames and existing["mcs_distribution"]:
                                max_mcs = max(existing["mcs_distribution"].keys(), key=lambda k: existing["mcs_distribution"][k])
                                existing["mcs_distribution"][max_mcs] += (total_frames - adjusted_total)
                                if existing["mcs_distribution"][max_mcs] < 1:
                                    existing["mcs_distribution"][max_mcs] = 1
                    else:
                        existing.pop("mcs_distribution", None)
                        existing.pop("mcs_distribution_source", None)
                elif final_mcs_dist and estimated_frames > 0:
                    existing["mcs_distribution"] = final_mcs_dist
                    if mcs_source:
                        existing["mcs_distribution_source"] = mcs_source
                
                if mcs_source and existing.get("mcs_distribution"):
                    if not existing.get("mcs_distribution_source") or \
                       _get_mcs_source_priority(mcs_source) < _get_mcs_source_priority(existing.get("mcs_distribution_source")):
                        existing["mcs_distribution_source"] = mcs_source
            else:
                record = {
                    "sta_mac": mac,
                    "ac": ac,
                    "avg_frame_duration_us": round(avg_duration, 1),
                    "frame_count": estimated_frames,
                    "download_bytes": deltas["download_bytes"],
                    "upload_bytes": deltas["upload_bytes"],
                }
                
                if final_mcs_dist and estimated_frames > 0:
                    mcs_total = sum(final_mcs_dist.values())
                    if mcs_total > 0 and abs(mcs_total - estimated_frames) > estimated_frames * 0.1:
                        norm_factor = estimated_frames / mcs_total
                        final_mcs_dist = {
                            mcs_idx: max(1, int(count * norm_factor))
                            for mcs_idx, count in final_mcs_dist.items()
                        }
                        adjusted_total = sum(final_mcs_dist.values())
                        if adjusted_total != estimated_frames and final_mcs_dist:
                            max_mcs = max(final_mcs_dist.keys(), key=lambda k: final_mcs_dist[k])
                            final_mcs_dist[max_mcs] += (estimated_frames - adjusted_total)
                            if final_mcs_dist[max_mcs] < 1:
                                final_mcs_dist[max_mcs] = 1
                    
                    record["mcs_distribution"] = final_mcs_dist
                    if mcs_source:
                        record["mcs_distribution_source"] = mcs_source
                
                records_map[key] = record
    
    records = list(records_map.values())
    
    if delta_ap_mcs_distributions:
        ap_merged_mcs = {}
        for hw_radio, mcs_dist in delta_ap_mcs_distributions.items():
            for mcs_idx, count in mcs_dist.items():
                ap_merged_mcs[mcs_idx] = ap_merged_mcs.get(mcs_idx, 0) + count
        
        if ap_merged_mcs:
            total_ap_frames = sum(ap_merged_mcs.values())
            
            ap_record = {
                "sta_mac": "AP",
                "ac": "All",
                "avg_frame_duration_us": 0.0,
                "frame_count": total_ap_frames,
                "download_bytes": 0,
                "upload_bytes": 0,
                "mcs_distribution": ap_merged_mcs,
                "mcs_distribution_source": "ap_level_htt_stats"
            }
            
            records.insert(0, ap_record)
    
    return records


def _get_mcs_weight(user_mcs: int, target_mcs: int) -> float:
    """Calculate MCS similarity weight for AP-level allocation.
    Nearby MCS values get higher weights.
    """
    distance = abs(target_mcs - user_mcs)
    
    if distance == 0:
        return 1.0
    elif distance == 1:
        return 0.6
    elif distance == 2:
        return 0.3
    elif distance == 3:
        return 0.15
    elif distance == 4:
        return 0.08
    else:
        return 0.02


def _allocate_ap_mcs_weighted(
    ap_mcs_dist: Dict[str, int],
    user_key: tuple,
    user_tx_mcs: int,
    user_traffic: int,
    all_users: List[Dict[str, Any]],
    interface_total: int
) -> Dict[str, int]:
    """Allocate AP-level MCS to user via weighted distribution.
    Combines MCS similarity and traffic ratio.
    """
    if interface_total == 0 or not all_users:
        return {}
    
    user_allocated = {}
    
    for mcs_str, ap_count in ap_mcs_dist.items():
        try:
            target_mcs = int(mcs_str)
        except ValueError:
            continue
        
        total_weight = 0.0
        user_weight = 0.0
        
        for user_info in all_users:
            u_mac = user_info['mac']
            u_category = user_info['category']
            u_mcs = user_info['tx_mcs']
            u_traffic = user_info['traffic_bytes']
            
            mcs_similarity = _get_mcs_weight(u_mcs, target_mcs)
            traffic_ratio = u_traffic / interface_total if interface_total > 0 else 0
            weight = mcs_similarity * traffic_ratio
            
            total_weight += weight
            
            if (u_mac, u_category) == user_key:
                user_weight = weight
        
        if total_weight > 0 and user_weight > 0:
            allocated_count = int(ap_count * (user_weight / total_weight))
            if allocated_count > 0:
                user_allocated[mcs_str] = allocated_count
    
    return user_allocated


def _load_sampler_mcs_distributions() -> Dict[str, Dict[str, int]]:
    """Load MCS distributions from background sampler.
    Returns 60-second delta from /tmp/mcs_distributions.json.
    """
    try:
        if not MCS_SAMPLER_DATA_FILE.exists():
            return {}
        
        file_age = datetime.now().timestamp() - MCS_SAMPLER_DATA_FILE.stat().st_mtime
        if file_age > 70:
            return {}
        
        with open(MCS_SAMPLER_DATA_FILE, 'r') as f:
            sampler_data = json.load(f)
        
        result = {}
        for mac, device_data in sampler_data.items():
            mac = mac.lower()
            
            tx_mcs_dist = device_data.get('tx_mcs_distribution', {})
            
            if tx_mcs_dist:
                result[mac] = tx_mcs_dist
        
        return result
    
    except Exception:
        return {}


def _get_mac_statistics() -> Dict[str, Dict[str, Any]]:
    """Get overall statistics for each MAC from iw station dump."""
    mac_stats = {}
    
    sampler_mcs_data = _load_sampler_mcs_distributions()
    
    for interface in AP_INTERFACES:
        try:
            stations = _get_stations_from_interface(interface)
            for sta_info in stations:
                mac = sta_info['mac']
                
                if mac in sampler_mcs_data:
                    mcs_dist = sampler_mcs_data[mac]
                    is_real_mcs = True
                    mcs_source = "background_sampler"
                else:
                    mcs_dist = sta_info.get('mcs_distribution')
                    is_real_mcs = sta_info.get('is_real_mcs_distribution', False)
                    mcs_source = "per_station_debugfs" if is_real_mcs else None
                
                mac_stats[mac] = {
                    "total_packets": sta_info.get('tx_packets', 0),
                    "total_duration_us": sta_info.get('tx_duration_us', 0),
                    "tx_rate_kbps": sta_info.get('tx_rate', 0),
                    "rx_rate_kbps": sta_info.get('rx_rate', 0),
                    "signal": sta_info.get('signal', 0),
                    "mcs_distribution": mcs_dist,
                    "is_real_mcs_distribution": is_real_mcs,
                    "mcs_source": mcs_source,
                    "tx_mcs": sta_info.get('tx_mcs'),
                    "rx_mcs": sta_info.get('rx_mcs'),
                    "interface": interface,
                }
        except Exception:
            pass
    
    return mac_stats


def _build_ip_to_mac_mapping() -> Dict[str, str]:
    """Build IP to MAC mapping from /proc/net/arp."""
    ip_to_mac = {}
    
    try:
        with open('/proc/net/arp', 'r') as f:
            lines = f.readlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[0]
                    mac = parts[3]
                    if mac != "00:00:00:00:00:00":
                        ip_to_mac[ip] = mac
    except Exception:
        pass
    
    return ip_to_mac


def _collect_station_statistics() -> List[Dict[str, Any]]:
    """Collect raw station statistics directly from ubus hostapd."""
    station_stats = []
    
    for interface in AP_INTERFACES:
        try:
            stations = _get_stations_from_interface(interface)
            for sta_info in stations:
                tx_packets = sta_info.get('tx_packets', 0)
                tx_duration_us = sta_info.get('tx_duration_us', 0)
                
                avg_frame_duration = 0.0
                if tx_packets > 0 and tx_duration_us > 0:
                    avg_frame_duration = float(tx_duration_us) / tx_packets
                
                stat = {
                    "sta_mac": sta_info['mac'],
                    "tx_packets": tx_packets,
                    "rx_packets": sta_info.get('rx_packets', 0),
                    "tx_bytes": sta_info.get('tx_bytes', 0),
                    "rx_bytes": sta_info.get('rx_bytes', 0),
                    "tx_duration_us": tx_duration_us,
                    "rx_duration_us": sta_info.get('rx_duration_us', 0),
                    "avg_frame_duration_us": round(avg_frame_duration, 1),
                    "tx_rate_kbps": sta_info.get('tx_rate', 0),
                    "rx_rate_kbps": sta_info.get('rx_rate', 0),
                    "signal": sta_info.get('signal', 0),
                    "interface": interface,
                }
                
                # Add MCS information if available
                if sta_info.get('tx_mcs') is not None:
                    stat['tx_mcs'] = sta_info['tx_mcs']
                if sta_info.get('rx_mcs') is not None:
                    stat['rx_mcs'] = sta_info['rx_mcs']
                if sta_info.get('tx_nss') is not None:
                    stat['tx_nss'] = sta_info['tx_nss']
                if sta_info.get('rx_nss') is not None:
                    stat['rx_nss'] = sta_info['rx_nss']
                if sta_info.get('tx_bandwidth') is not None:
                    stat['tx_bandwidth'] = sta_info['tx_bandwidth']
                if sta_info.get('rx_bandwidth') is not None:
                    stat['rx_bandwidth'] = sta_info['rx_bandwidth']
                if sta_info.get('mcs_distribution'):
                    stat['mcs_distribution'] = sta_info['mcs_distribution']
                
                station_stats.append(stat)
        except Exception:
            pass
    
    return station_stats


def _collect_all_stations_traffic() -> List[Dict[str, Any]]:
    """Collect station traffic stats from all AP interfaces (deprecated)."""
    all_records = []
    
    for interface in AP_INTERFACES:
        try:
            stations = _get_stations_from_interface(interface)
            for sta_info in stations:
                records = _convert_sta_to_records(sta_info)
                all_records.extend(records)
        except Exception as e:
            pass
    
    return all_records


def _collect_all_stations_raw() -> Dict[str, Dict[str, Any]]:
    """Collect raw station statistics from all AP interfaces."""
    all_stations = {}
    
    for interface in AP_INTERFACES:
        try:
            stations = _get_stations_from_interface(interface)
            for sta_info in stations:
                sta_mac = sta_info['mac']
                all_stations[sta_mac] = sta_info
        except Exception:
            pass
    
    return all_stations


def _load_history() -> Dict[str, Any]:
    """Load previous statistics history."""
    try:
        if AP_TRAFFIC_HISTORY_FILE.exists():
            content = AP_TRAFFIC_HISTORY_FILE.read_text(encoding='utf-8')
            return json.loads(content)
        return {"timestamp": None, "data": {}, "mac_stats": {}, "ap_mcs_distributions": {}}
    except Exception:
        return {"timestamp": None, "data": {}, "mac_stats": {}, "ap_mcs_distributions": {}}


def _save_history(history: Dict[str, Any]) -> None:
    """Save current statistics as history."""
    try:
        tmp = AP_TRAFFIC_HISTORY_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(AP_TRAFFIC_HISTORY_FILE)
    except Exception:
        pass


def _save_debug_info(debug_info: Dict[str, Any]) -> None:
    """Save debug information for troubleshooting."""
    try:
        tmp = AP_TRAFFIC_DEBUG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(debug_info, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(AP_TRAFFIC_DEBUG_FILE)
    except Exception:
        pass


def _calculate_delta_records(
    current_stations: Dict[str, Dict[str, Any]], 
    last_history: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Calculate traffic deltas and generate records."""
    records = []
    last_stations = last_history.get("stations", {})
    
    for sta_mac, current_stats in current_stations.items():
        if sta_mac not in last_stations:
            delta_stats = {
                'mac': sta_mac,
                'interface': current_stats.get('interface', ''),
                'tx_packets': current_stats.get('tx_packets', 0),
                'rx_packets': current_stats.get('rx_packets', 0),
                'tx_bytes': current_stats.get('tx_bytes', 0),
                'rx_bytes': current_stats.get('rx_bytes', 0),
                'tx_duration_us': current_stats.get('tx_duration_us', 0),
                'signal': current_stats.get('signal', 0),
                'mcs': current_stats.get('mcs'),
            }
        else:
            last_stats = last_stations[sta_mac]
            delta_stats = {
                'mac': sta_mac,
                'interface': current_stats.get('interface', ''),
                'tx_packets': max(0, current_stats.get('tx_packets', 0) - last_stats.get('tx_packets', 0)),
                'rx_packets': max(0, current_stats.get('rx_packets', 0) - last_stats.get('rx_packets', 0)),
                'tx_bytes': max(0, current_stats.get('tx_bytes', 0) - last_stats.get('tx_bytes', 0)),
                'rx_bytes': max(0, current_stats.get('rx_bytes', 0) - last_stats.get('rx_bytes', 0)),
                'tx_duration_us': max(0, current_stats.get('tx_duration_us', 0) - last_stats.get('tx_duration_us', 0)),
                'signal': current_stats.get('signal', 0),
                'mcs': current_stats.get('mcs'),
            }
        
        records_for_sta = _convert_sta_to_records(delta_stats)
        records.extend(records_for_sta)
    
    return records


def _get_stations_from_interface(interface: str) -> List[Dict[str, Any]]:
    """Get all connected stations via ubus hostapd interface."""
    try:
        result = subprocess.run(
            ['ubus', 'call', f'hostapd.{interface}', 'get_clients'],
            capture_output=True,
            text=True,
            timeout=3,
            check=False
        )
        
        if result.returncode != 0:
            return []
        
        return _parse_ubus_clients(result.stdout, interface)
    
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []


def _parse_ubus_clients(json_output: str, interface: str) -> List[Dict[str, Any]]:
    """Parse ubus hostapd get_clients JSON output and enrich with MCS info."""
    stations = []
    
    try:
        data = json.loads(json_output)
        clients = data.get('clients', {})
        
        for sta_mac, client_info in clients.items():
            bytes_info = client_info.get('bytes', {})
            packets_info = client_info.get('packets', {})
            airtime_info = client_info.get('airtime', {})
            rate_info = client_info.get('rate', {})
            
            station = {
                'mac': sta_mac.lower(),
                'interface': interface,
                'tx_packets': packets_info.get('tx', 0),
                'rx_packets': packets_info.get('rx', 0),
                'tx_bytes': bytes_info.get('tx', 0),
                'rx_bytes': bytes_info.get('rx', 0),
                'tx_duration_us': airtime_info.get('tx', 0),
                'rx_duration_us': airtime_info.get('rx', 0),
                'signal': client_info.get('signal', 0),
                'tx_rate': rate_info.get('tx', 0),
                'rx_rate': rate_info.get('rx', 0),
            }
            
            # Enrich with MCS info from iw station dump
            mcs_info = _get_station_mcs_info_traffic(interface, sta_mac.lower())
            station.update(mcs_info)
            
            stations.append(station)
    
    except (json.JSONDecodeError, Exception):
        pass
    
    return stations


def _get_station_mcs_info_traffic(interface: str, sta_mac: str) -> Dict[str, Any]:
    """Get MCS information for a specific station from iw station dump."""
    mcs_info = {
        'tx_mcs': None,
        'rx_mcs': None,
        'tx_nss': None,
        'rx_nss': None,
        'tx_bandwidth': None,
        'rx_bandwidth': None,
        'mcs_distribution': None,
        'is_real_mcs_distribution': False,
    }
    
    try:
        # Get detailed station info from iw
        result = subprocess.run(
            ['iw', 'dev', interface, 'station', 'get', sta_mac],
            capture_output=True,
            text=True,
            timeout=2,
            check=False
        )
        
        if result.returncode == 0:
            output = result.stdout
            
            # Parse tx bitrate (e.g., "tx bitrate: 2401.9 MBit/s 160MHz EHT-MCS 11 EHT-NSS 2 EHT-GI 0")
            tx_match = re.search(
                r'tx bitrate:.*?(\d+)\s*MHz.*?(?:EHT-MCS|VHT-MCS|HE-MCS|MCS)\s+(\d+)(?:.*?(?:EHT-NSS|VHT-NSS|HE-NSS|NSS)\s+(\d+))?',
                output
            )
            if tx_match:
                mcs_info['tx_mcs'] = int(tx_match.group(2))
                mcs_info['tx_bandwidth'] = int(tx_match.group(1))
                if tx_match.group(3):
                    mcs_info['tx_nss'] = int(tx_match.group(3))
            
            # Parse rx bitrate
            rx_match = re.search(
                r'rx bitrate:.*?(\d+)\s*MHz.*?(?:EHT-MCS|VHT-MCS|HE-MCS|MCS)\s+(\d+)(?:.*?(?:EHT-NSS|VHT-NSS|HE-NSS|NSS)\s+(\d+))?',
                output
            )
            if rx_match:
                mcs_info['rx_mcs'] = int(rx_match.group(2))
                mcs_info['rx_bandwidth'] = int(rx_match.group(1))
                if rx_match.group(3):
                    mcs_info['rx_nss'] = int(rx_match.group(3))
        
        # Try to get MCS distribution from debugfs
        mcs_dist = _get_mcs_distribution_from_debugfs_traffic(interface, sta_mac)
        if mcs_dist:
            mcs_info['mcs_distribution'] = mcs_dist
            mcs_info['is_real_mcs_distribution'] = True  # Mark as real data from debugfs
    
    except Exception:
        pass
    
    return mcs_info


def _get_mcs_distribution_from_debugfs_traffic(interface: str, sta_mac: str) -> Optional[Dict[str, int]]:
    """Read MCS distribution from debugfs rate control statistics."""
    try:
        # Try common debugfs paths
        for phy_num in range(10):
            for phy_fmt in [f'phy{phy_num}', f'phy{phy_num:02d}']:
                debugfs_path = Path(f"/sys/kernel/debug/ieee80211/{phy_fmt}/netdev:{interface}/stations/{sta_mac}")
                
                if debugfs_path.exists():
                    # Try reading rc_stats or similar files in multiple locations
                    search_paths = [
                        debugfs_path,  # Main station directory
                        debugfs_path / "link-0",  # Wi-Fi 7 MLO link directory
                    ]
                    # Different drivers use different file names:
                    # - Intel/mt76: rc_stats, rate_stats
                    # - Qualcomm ath12k/ath11k: htt_peer_stats
                    rc_stats_files = ['rc_stats', 'rate_stats', 'rate_scale_table', 'htt_peer_stats']
                    
                    for search_path in search_paths:
                        if not search_path.exists():
                            continue
                        
                        for stats_file in rc_stats_files:
                            stats_path = search_path / stats_file
                            if stats_path.exists():
                                try:
                                    with open(stats_path, 'r') as f:
                                        content = f.read()
                                        result = _parse_rc_stats(content, stats_file)
                                        if result:  # Only return if we got valid data
                                            return result
                                except (PermissionError, IOError, OSError):
                                    # Permission denied or operation not permitted - skip this file
                                    continue
    
    except Exception:
        pass
    
    return None

def _parse_iw_station_dump(output: str, interface: str) -> List[Dict[str, Any]]:
    """Parse iw station dump output."""
    stations = []
    current_sta = None
    
    for line in output.splitlines():
        line_stripped = line.strip()
        
        if line.startswith('Station '):
            if current_sta:
                stations.append(current_sta)
            
            parts = line.split()
            sta_mac = parts[1]
            current_sta = {
                'mac': sta_mac,
                'interface': interface,
                'tx_packets': 0,
                'rx_packets': 0,
                'tx_bytes': 0,
                'rx_bytes': 0,
                'tx_duration_us': 0,
                'signal': 0,
            }
        
        elif current_sta and ':' in line_stripped:
            if line_stripped.startswith('tx packets:'):
                current_sta['tx_packets'] = int(line_stripped.split(':')[1].strip())
            
            elif line_stripped.startswith('rx packets:'):
                current_sta['rx_packets'] = int(line_stripped.split(':')[1].strip())
            
            elif line_stripped.startswith('tx bytes:'):
                current_sta['tx_bytes'] = int(line_stripped.split(':')[1].strip())
            
            elif line_stripped.startswith('rx bytes:'):
                current_sta['rx_bytes'] = int(line_stripped.split(':')[1].strip())
            
            elif line_stripped.startswith('tx duration:'):
                match = re.search(r'(\d+)\s*us', line_stripped)
                if match:
                    current_sta['tx_duration_us'] = int(match.group(1))
            
            elif line_stripped.startswith('signal avg:'):
                match = re.search(r'(-?\d+)\s*dBm', line_stripped)
                if match:
                    current_sta['signal'] = int(match.group(1))
            
            elif line_stripped.startswith('tx bitrate:'):
                current_sta['tx_bitrate'] = line_stripped.split(':', 1)[1].strip()
            
            elif line_stripped.startswith('rx bitrate:'):
                bitrate_info = line_stripped.split(':', 1)[1].strip()
                current_sta['rx_bitrate'] = bitrate_info
                
                mcs_match = re.search(r'MCS\s+(\d+)', bitrate_info)
                if mcs_match:
                    current_sta['mcs'] = int(mcs_match.group(1))
    
    if current_sta:
        stations.append(current_sta)
    
    return stations


def _convert_sta_to_records(sta_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert station statistics to traffic record format. Note: iw station dump doesn't provide per-AC stats."""
    records = []
    
    sta_mac = sta_info['mac']
    tx_packets = sta_info.get('tx_packets', 0)
    tx_duration_us = sta_info.get('tx_duration_us', 0)
    
    if tx_packets > 0 and tx_duration_us > 0:
        avg_frame_duration = float(tx_duration_us) / tx_packets
    else:
        avg_frame_duration = 0.0
    
    tx_rate_kbps = sta_info.get('tx_rate', 0)
    rx_rate_kbps = sta_info.get('rx_rate', 0)
    
    record = {
        "sta_mac": sta_mac,
        "ac": "BE",
        "avg_frame_duration_us": round(avg_frame_duration, 1),
        "frame_count": tx_packets,
        "current_tx_rate_mbps": round(tx_rate_kbps / 1000.0, 1),
        "current_rx_rate_mbps": round(rx_rate_kbps / 1000.0, 1),
    }
    records.append(record)
    
    return records


def _estimate_mcs_distribution(sta_info: Dict[str, Any]) -> Dict[str, int]:
    """Estimate MCS distribution from current MCS value (simplified model)."""
    mcs_dist = {}
    
    if 'mcs' in sta_info:
        current_mcs = sta_info['mcs']
        frame_count = sta_info.get('tx_packets', 0)
        
        if frame_count > 0:
            mcs_dist[str(current_mcs)] = int(frame_count * 0.6)
            
            if current_mcs > 0:
                mcs_dist[str(current_mcs - 1)] = int(frame_count * 0.25)
            
            if current_mcs < 11:
                mcs_dist[str(current_mcs + 1)] = int(frame_count * 0.15)
    
    else:
        pass
    
    return mcs_dist


def _estimate_mcs_distribution_from_current(current_mcs: int, frame_count: int) -> Dict[str, int]:
    """Estimate MCS distribution centered around current MCS.
    50% current, 20% MCS-1, 15% MCS-2, 10% MCS+1, 5% MCS-3.
    """
    if frame_count <= 0 or current_mcs < 0:
        return {}
    
    mcs_dist = {}
    
    mcs_dist[str(current_mcs)] = int(frame_count * 0.50)
    
    if current_mcs >= 1:
        mcs_dist[str(current_mcs - 1)] = int(frame_count * 0.20)
    if current_mcs >= 2:
        mcs_dist[str(current_mcs - 2)] = int(frame_count * 0.15)
    if current_mcs >= 3:
        mcs_dist[str(current_mcs - 3)] = int(frame_count * 0.05)
    
    if current_mcs <= 12:
        mcs_dist[str(current_mcs + 1)] = int(frame_count * 0.10)
    
    total_allocated = sum(mcs_dist.values())
    if total_allocated < frame_count:
        mcs_dist[str(current_mcs)] += (frame_count - total_allocated)
    
    return mcs_dist


def _read_mcs_from_debugfs(interface: str, sta_mac: str) -> Optional[Dict[str, int]]:
    """Read MCS statistics from debugfs (requires root, driver-specific)."""
    debugfs_base = f"/sys/kernel/debug/ieee80211/phy00/netdev:{interface}/stations/{sta_mac}"
    
    try:
        rc_stats_file = Path(debugfs_base) / "rc_stats"
        
        if rc_stats_file.exists():
            with open(rc_stats_file, 'r') as f:
                content = f.read()
                return _parse_rc_stats(content)
    
    except (PermissionError, FileNotFoundError, Exception):
        pass
    
    return None


def _get_ac_packet_sizes_from_iptables() -> Dict[str, float]:
    """Get average packet sizes for each AC from iptables statistics."""
    ac_packet_sizes = {
        "VO": 1200.0,
        "VI": 1200.0,
        "BE": 1200.0,
        "BK": 1200.0,
    }
    
    try:
        result = subprocess.run(
            ['iptables', '-t', 'mangle', '-nvL', 'ndpi_rule_egress'],
            capture_output=True,
            text=True,
            timeout=3,
            check=False
        )
        
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            
            for line in lines:
                parts = line.split()
                if len(parts) < 3:
                    continue
                
                try:
                    pkts_str = parts[0].strip()
                    bytes_str = parts[1].strip()
                    
                    pkts = _parse_iptables_size(pkts_str)
                    bytes_val = _parse_iptables_size(bytes_str)
                    
                    if pkts > 0 and bytes_val > 0:
                        avg_size = bytes_val / pkts
                        
                        if 'ai_real' in line or 'manual_real' in line:
                            ac_packet_sizes['VO'] = avg_size
                        elif 'ai_vi' in line or 'manual_vi' in line:
                            ac_packet_sizes['VI'] = avg_size
                        elif 'ai_be' in line or 'manual_be' in line:
                            ac_packet_sizes['BE'] = avg_size
                        elif 'ai_bk' in line or 'manual_bk' in line:
                            ac_packet_sizes['BK'] = avg_size
                except (ValueError, IndexError):
                    continue
    
    except (subprocess.TimeoutExpired, Exception):
        pass
    
    return ac_packet_sizes


def _parse_iptables_size(size_str: str) -> float:
    """Parse iptables size string with K/M/G suffix."""
    size_str = size_str.strip().upper()
    
    if size_str == '0' or not size_str:
        return 0.0
    
    multipliers = {'K': 1000, 'M': 1000000, 'G': 1000000000}
    
    for suffix, multiplier in multipliers.items():
        if size_str.endswith(suffix):
            try:
                return float(size_str[:-1]) * multiplier
            except ValueError:
                return 0.0
    
    try:
        return float(size_str)
    except ValueError:
        return 0.0


def _calculate_frame_duration(
    packet_size: float,
    mcs: Optional[int],
    bandwidth: Optional[int],
    nss: Optional[int]
) -> float:
    """Calculate frame transmission duration based on PHY parameters.
    Returns duration in microseconds.
    """
    # If PHY parameters unavailable, use simplified estimate
    if mcs is None or bandwidth is None or nss is None:
        # Assume reasonable defaults: MCS 9, 80MHz, NSS 2 → ~1088 Mbps
        data_rate_mbps = 1088.0
    else:
        key = (mcs, bandwidth, nss)
        data_rate_mbps = EHT_DATA_RATES.get(key)
        
        if data_rate_mbps is None:
            if bandwidth == 160 and nss == 2:
                data_rate_mbps = 163.2 + mcs * 204.8
            else:
                data_rate_mbps = 1088.0
    
    packet_bits = packet_size * 8
    data_time_us = (packet_bits / (data_rate_mbps * 1000000)) * 1000000
    
    total_duration_us = WIFI_OVERHEAD_US + data_time_us
    
    return round(total_duration_us, 1)


def _get_ap_mcs_distributions() -> Dict[str, Dict[str, int]]:
    """Get AP-level MCS distributions from htt_stats for each radio."""
    distributions = {}
    
    for hw_radio in ["ath12k_hw0", "ath12k_hw1", "ath12k_hw2"]:
        try:
            stats_type_path = Path(f"/sys/kernel/debug/ieee80211/phy00/{hw_radio}/htt_stats_type")
            htt_stats_path = Path(f"/sys/kernel/debug/ieee80211/phy00/{hw_radio}/htt_stats")
            
            if not stats_type_path.exists() or not htt_stats_path.exists():
                continue
            
            with open(stats_type_path, 'w') as f:
                f.write('9')
            
            with open(htt_stats_path, 'r') as f:
                content = f.read()
            
            mcs_dist = {}
            for line in content.splitlines():
                if line.startswith('tx_mcs ='):
                    pairs_str = line.split('=', 1)[1].strip()
                    pairs = pairs_str.split(',')
                    
                    for pair in pairs:
                        pair = pair.strip()
                        if ':' in pair:
                            parts = pair.split(':')
                            mcs_str = parts[0].strip()
                            count_str = parts[1].strip()
                            
                            try:
                                mcs_idx = int(mcs_str)
                                count = int(count_str)
                                
                                # Only include valid MCS indices (0-13 for EHT)
                                if 0 <= mcs_idx <= 13 and count > 0:
                                    mcs_dist[str(mcs_idx)] = count
                            except ValueError:
                                continue
                    break
            
            if mcs_dist:
                distributions[hw_radio] = mcs_dist
        
        except Exception:
            pass
    
    return distributions


def _get_mcs_source_priority(source: Optional[str]) -> int:
    """Get priority value for MCS source (lower = higher priority)."""
    if not source:
        return 4
    
    priority_map = {
        "background_sampler": 0,
        "per_station_debugfs": 1,
        "ap_level_htt_stats": 2,
        "estimated": 3,
    }
    
    return priority_map.get(source, 4)


def _get_interface_ap_mcs_distribution(interface: str, ap_distributions: Dict[str, Dict[str, int]]) -> Optional[Dict[str, int]]:
    """Get AP MCS distribution for a specific interface."""
    if not interface or not ap_distributions:
        return None
    
    base_interface = interface.split('.')[0] if '.' in interface else interface
    
    hw_radio = INTERFACE_TO_HW_RADIO.get(base_interface)
    if not hw_radio:
        return None
    
    return ap_distributions.get(hw_radio)


def _parse_rc_stats(content: str, filename: str = 'rc_stats') -> Dict[str, int]:
    """Parse rate control statistics file (format varies by driver)."""
    mcs_dist = {}
    
    if filename == 'htt_peer_stats':
        in_tx_section = False
        for line in content.splitlines():
            line = line.strip()
            if 'TX' in line.upper() and 'MCS' in line.upper():
                in_tx_section = True
                continue
            if in_tx_section:
                match = re.match(r'MCS\s+(\d+)\s*:\s*(\d+)', line, re.IGNORECASE)
                if match:
                    mcs = match.group(1)
                    count = int(match.group(2))
                    mcs_dist[mcs] = count
                elif line and not line.startswith('MCS'):
                    break
    else:
        for line in content.splitlines():
            match = re.match(r'.*MCS\s+(\d+).*?(\d+)\s+(?:packets|attempts)', line, re.IGNORECASE)
            if match:
                mcs = match.group(1)
                count = int(match.group(2))
                mcs_dist[mcs] = count
    
    return mcs_dist
