#!/usr/bin/env python3
import json
import subprocess
import re
from typing import Dict, Any, List, Optional
from pathlib import Path

from config import get_mac_address
from common_register import get_ip_best_effort

AP_INTERFACES = ["wlan0.1", "wlan0.2", "wlan0.3", "wlan0.4", "wlan0.5",
                 "wlan1.1", "wlan1.2", "wlan1.3", "wlan1.4", "wlan1.5",
                 "wlan2.1", "wlan2.2", "wlan2.3", "wlan2.4", "wlan2.5"]


def get_ap_status() -> Dict[str, Any]:
    """Get AP status including MAC, IP, SSIDs, band, channel, antennas, and associations."""
    mac = get_mac_address() or "00:00:00:00:00:00"
    ip = get_ip_best_effort() or ""

    interface_info = _collect_interface_info()
    associations = _collect_all_associations(interface_info)
    ssids = list(set(info.get("ssid", "") for info in interface_info.values() if info.get("ssid")))
    
    primary_band = "unknown"
    primary_channel = 0
    antenna_count = 0
    for iface, info in interface_info.items():
        if info.get("has_clients", False):
            primary_band = info.get("band", "unknown")
            primary_channel = info.get("channel", 0)
            antenna_count = info.get("antenna_count", 0)
            break
    
    if primary_band == "unknown" and interface_info:
        first_info = next(iter(interface_info.values()))
        primary_band = first_info.get("band", "unknown")
        primary_channel = first_info.get("channel", 0)
        antenna_count = first_info.get("antenna_count", 0)

    interfaces_set = set()
    for iface, info in interface_info.items():
        if info.get("ssid"):
            band = info.get("band", "unknown")
            channel = info.get("channel", 0)
            if band != "unknown":
                interfaces_set.add((band, channel))
    
    interfaces = [{"band": b, "channel": c} for b, c in sorted(interfaces_set)]

    return {
        "mac": mac,
        "ip": ip,
        "ssids": ssids,
        "band": primary_band,
        "channel": primary_channel,
        "antenna_count": antenna_count,
        "interfaces": interfaces,
        "associations": associations,
    }


def _collect_interface_info() -> Dict[str, Dict[str, Any]]:
    """Collect info from all AP interfaces."""
    interface_info = {}
    
    for interface in AP_INTERFACES:
        info = _get_interface_info(interface)
        if info:
            interface_info[interface] = info
    
    return interface_info


def _get_interface_info(interface: str) -> Optional[Dict[str, Any]]:
    """Get interface info via iw dev {interface} info."""
    try:
        result = subprocess.run(
            ['iw', 'dev', interface, 'info'],
            capture_output=True,
            text=True,
            timeout=3,
            check=False
        )
        
        if result.returncode != 0:
            return None
        
        return _parse_iw_info(result.stdout, interface)
    
    except (subprocess.TimeoutExpired, Exception):
        return None


def _parse_iw_info(output: str, interface: str) -> Dict[str, Any]:
    """Parse iw dev info output."""
    info: Dict[str, Any] = {
        "ssid": "",
        "band": "unknown",
        "channel": 0,
        "antenna_count": 0,
        "has_clients": False,
    }
    
    for line in output.splitlines():
        line_stripped = line.strip()
        
        if line_stripped.startswith('ssid '):
            info["ssid"] = line_stripped[5:].strip()
        
        elif line_stripped.startswith('channel '):
            match = re.search(r'channel\s+(\d+)\s+\((\d+)\s+MHz\)', line_stripped)
            if match:
                channel = int(match.group(1))
                freq = int(match.group(2))
                info["channel"] = channel
                
                if 2400 <= freq < 2500:
                    info["band"] = "2.4g"
                elif 5000 <= freq < 6000:
                    info["band"] = "5g"
                elif freq >= 6000:
                    info["band"] = "6g"
    
    wiphy_num = _extract_wiphy_number(output)
    if wiphy_num is not None:
        info["antenna_count"] = _get_antenna_count(wiphy_num)
    
    info["has_clients"] = _interface_has_clients(interface)
    
    return info


def _extract_wiphy_number(iw_info_output: str) -> Optional[int]:
    """Extract wiphy number from iw info output."""
    match = re.search(r'wiphy\s+(\d+)', iw_info_output)
    return int(match.group(1)) if match else None


def _get_antenna_count(wiphy_num: int) -> int:
    """Get antenna count from iw phy info."""
    for phy_name in [f'phy{wiphy_num}', f'phy{wiphy_num:02d}']:
        try:
            result = subprocess.run(
                ['iw', 'phy', phy_name, 'info'],
                capture_output=True,
                text=True,
                timeout=3,
                check=False
            )
            
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if 'Available Antennas:' in line:
                        match = re.search(r'TX\s+0x([0-9a-fA-F]+)', line)
                        if match:
                            mask = int(match.group(1), 16)
                            count = bin(mask).count('1')
                            if count > 0:
                                return count
                        break
        except Exception:
            pass
    
    return 2


def _interface_has_clients(interface: str) -> bool:
    """Check if interface has associated clients."""
    try:
        result = subprocess.run(
            ['iw', 'dev', interface, 'station', 'dump'],
            capture_output=True,
            text=True,
            timeout=2,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return "Station " in result.stdout
    except Exception:
        pass
    
    return False


def _collect_all_associations(interface_info: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collect associated clients from all interfaces."""
    associations = []
    
    for interface, info in interface_info.items():
        if not info.get("has_clients", False):
            continue
        
        stations = _get_stations_from_interface(interface)
        ssid = info.get("ssid", "")
        band = info.get("band", "unknown")
        channel = info.get("channel", 0)
        
        for sta in stations:
            mcs = sta.get("tx_mcs") or sta.get("mcs", 0)
            
            association = {
                "sta_mac": sta.get("mac", ""),
                "interface": sta.get("interface", ""),
                "ssid": ssid,
                "band": band,
                "channel": channel,
                "signal_dbm": sta.get("signal", 0),
                "mcs": mcs,
                "tx_bitrate": sta.get("tx_bitrate", "unknown"),
                "rx_bitrate": sta.get("rx_bitrate", "unknown"),
            }
            associations.append(association)
    
    return associations


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
    
    except (subprocess.TimeoutExpired, Exception):
        return []


def _parse_ubus_clients(json_output: str, interface: str) -> List[Dict[str, Any]]:
    """Parse ubus hostapd get_clients JSON output and enrich with iw station dump data."""
    stations = []
    
    try:
        data = json.loads(json_output)
        clients = data.get('clients', {})
        
        for sta_mac, client_info in clients.items():
            rate_info = client_info.get('rate', {})
            
            tx_rate_kbps = rate_info.get('tx', 0)
            rx_rate_kbps = rate_info.get('rx', 0)
            
            tx_bitrate = f"{tx_rate_kbps / 1000:.1f} MBit/s" if tx_rate_kbps > 0 else "unknown"
            rx_bitrate = f"{rx_rate_kbps / 1000:.1f} MBit/s" if rx_rate_kbps > 0 else "unknown"
            
            station = {
                'mac': sta_mac.lower(),
                'interface': interface,
                'signal': client_info.get('signal', 0),
                'mcs': 0,
                'tx_bitrate': tx_bitrate,
                'rx_bitrate': rx_bitrate,
            }
            
            mcs_info = _get_station_mcs_info(interface, sta_mac.lower())
            station.update(mcs_info)
            
            stations.append(station)
    
    except (json.JSONDecodeError, Exception):
        pass
    
    return stations


def _get_station_mcs_info(interface: str, sta_mac: str) -> Dict[str, Any]:
    """Get MCS information for a specific station from iw station dump."""
    mcs_info = {
        'tx_mcs': None,
        'rx_mcs': None,
        'tx_nss': None,
        'rx_nss': None,
        'tx_bandwidth': None,
        'rx_bandwidth': None,
        'mcs_distribution': None,
    }
    
    try:
        result = subprocess.run(
            ['iw', 'dev', interface, 'station', 'get', sta_mac],
            capture_output=True,
            text=True,
            timeout=2,
            check=False
        )
        
        if result.returncode == 0:
            output = result.stdout
            
            tx_match = re.search(
                r'tx bitrate:.*?(\d+)\s*MHz.*?(?:EHT-MCS|VHT-MCS|HE-MCS|MCS)\s+(\d+)(?:.*?(?:EHT-NSS|VHT-NSS|HE-NSS|NSS)\s+(\d+))?',
                output
            )
            if tx_match:
                mcs_info['tx_bandwidth'] = int(tx_match.group(1))
                mcs_info['tx_mcs'] = int(tx_match.group(2))
                if tx_match.group(3):
                    mcs_info['tx_nss'] = int(tx_match.group(3))
            
            rx_match = re.search(
                r'rx bitrate:.*?(\d+)\s*MHz.*?(?:EHT-MCS|VHT-MCS|HE-MCS|MCS)\s+(\d+)(?:.*?(?:EHT-NSS|VHT-NSS|HE-NSS|NSS)\s+(\d+))?',
                output
            )
            if rx_match:
                mcs_info['rx_bandwidth'] = int(rx_match.group(1))
                mcs_info['rx_mcs'] = int(rx_match.group(2))
                if rx_match.group(3):
                    mcs_info['rx_nss'] = int(rx_match.group(3))
            
            if mcs_info['tx_mcs'] is not None:
                mcs_info['mcs'] = mcs_info['tx_mcs']
            elif mcs_info['rx_mcs'] is not None:
                mcs_info['mcs'] = mcs_info['rx_mcs']
        
        mcs_dist = _get_mcs_distribution_from_debugfs(interface, sta_mac)
        if mcs_dist:
            mcs_info['mcs_distribution'] = mcs_dist
    
    except Exception:
        pass
    
    return mcs_info


def _get_mcs_distribution_from_debugfs(interface: str, sta_mac: str) -> Optional[Dict[str, int]]:
    """Read MCS distribution from debugfs rate control statistics."""
    try:
        # Try common debugfs paths
        for phy_num in range(10):
            for phy_fmt in [f'phy{phy_num}', f'phy{phy_num:02d}']:
                debugfs_path = Path(f"/sys/kernel/debug/ieee80211/{phy_fmt}/netdev:{interface}/stations/{sta_mac}")
                
                if debugfs_path.exists():
                    search_paths = [
                        debugfs_path,
                        debugfs_path / "link-0",
                    ]
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
                                        result = _parse_rc_stats(content)
                                        if result:
                                            return result
                                except (PermissionError, IOError):
                                    continue
    
    except Exception:
        pass
    
    return None


def _parse_rc_stats(content: str) -> Dict[str, int]:
    """Parse rate control statistics file to extract MCS distribution."""
    mcs_dist = {}
    
    try:
        for line in content.splitlines():
            match = re.search(r'MCS[\s_]+(\d+).*?(\d+)\s+(?:attempts|packets|success)', line, re.IGNORECASE)
            if match:
                mcs = match.group(1)
                count = int(match.group(2))
                mcs_dist[mcs] = mcs_dist.get(mcs, 0) + count
        
    except Exception:
        pass
    
    return mcs_dist if mcs_dist else None
