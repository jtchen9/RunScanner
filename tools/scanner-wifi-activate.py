#!/usr/bin/python3 -I
"""Root-owned, narrowly scoped recovery helper. Never prints credentials.
Install at /usr/local/libexec/scanner-wifi-activate (root:root, 0755).
Only an in-memory profile override is used; disk profiles are untouched.
"""
import fcntl
import ipaddress
import json
import os
import re
import shutil
import signal
import subprocess
import sys

IFACE = 'wlP1p1s0'
NM = '/usr/bin/nmcli'
UUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
MAC = r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}'

def run(args, timeout=3):
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 124, ''

def ipv4_ready():
    """True: usable IPv4 exists; False: none; None: inspection failed.

    Read kernel addresses, not NetworkManager's high-level connected flag.
    Private LAN addresses are valid. Link-local/loopback addresses are not.
    """
    ip = shutil.which('ip', path='/usr/sbin:/usr/bin:/sbin:/bin')
    if not ip:
        return None
    rc, out = run([ip, '-j', '-4', 'address', 'show', 'dev', IFACE], 2)
    if rc:
        return None
    try:
        devices = json.loads(out)
        if not isinstance(devices, list):
            return None
        for device in devices:
            for addr in device.get('addr_info', []):
                if addr.get('family') != 'inet':
                    continue
                value = ipaddress.IPv4Address(addr['local'])
                if not (value.is_link_local or value.is_loopback or
                        value.is_unspecified or value.is_multicast):
                    return True
        return False
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def field(uuid, name):
    rc, value = run([NM, '-g', name, 'connection', 'show', 'uuid', uuid])
    if rc:
        raise RuntimeError('profile-query-failed')
    return value

def status(wpa):
    rc, out = run([wpa, '-i', IFACE, 'status'])
    return dict(line.split('=', 1) for line in out.splitlines() if '=' in line) if not rc else {}

def interrupted(signum, frame):
    raise RuntimeError('interrupted')

def activate(uuid, target, nms_unreachable=False):
    wpa = shutil.which('wpa_cli', path='/usr/sbin:/usr/bin:/sbin:/bin')
    if not wpa or run([wpa, '-i', IFACE, 'ping'])[1] != 'PONG':
        print('helper-error=wpa-control-unavailable')
        return 2
    # Do not override a deliberately pinned profile or another interface.
    if (field(uuid, 'connection.type') != '802-11-wireless'
            or field(uuid, 'connection.interface-name') not in ('', '--', IFACE)
            or field(uuid, '802-11-wireless.bssid') not in ('', '--')):
        print('helper-error=profile-restriction')
        return 2
    rc, state = run([NM, '-g', 'GENERAL.STATE', 'device', 'show', IFACE])
    code = state.split(' ', 1)[0]
    # Recheck missing IP; an explicit agent mode also permits recovery of a
    # retained-IP link after prolonged NMS transport failure.
    missing_ip = code == '100' and ipv4_ready() is False
    stale_path = code == '100' and nms_unreachable
    if rc or (code not in ('30', '40', '50', '60', '70', '80', '90', '120') and not missing_ip and not stale_path):
        print('helper-skip=state-changed')
        return 3
    # Already disconnected is a valid starting point, not a cancellation error.
    if code not in ('30', '120'):
        cancel_rc, _ = run([NM, '--wait', '3', 'device', 'disconnect', IFACE], 4)
        state_rc, after = run([NM, '-g', 'GENERAL.STATE', 'device', 'show', IFACE])
        after_code = after.split(' ', 1)[0]
        # Judge the resulting state even when nmcli reports an error (a native
        # disconnect may have completed before our cancellation request).
        if state_rc or after_code not in ('30', '120'):
            print(f'helper-error=cancel-failed cancel_rc={cancel_rc} state_rc={state_rc} state={after_code if after_code.isdigit() else "unknown"}')
            return 4
    modified = False
    result = 4
    actual = 'none'
    ip_ready = None
    try:
        # Mark before call so even a timeout triggers restoration.
        modified = True
        if run([NM, 'connection', 'modify', '--temporary', 'uuid', uuid,
                '802-11-wireless.bssid', target])[0]:
            raise RuntimeError('temporary-pin-failed')
        rc, _ = run([NM, '--wait', '10', 'connection', 'up', 'uuid', uuid,
                     'ifname', IFACE], 12)
        s = status(wpa)
        actual = s.get('bssid', 'none').upper()
        ip_ready = ipv4_ready()
        result = 0 if (rc == 0 and s.get('wpa_state') == 'COMPLETED'
                       and actual == target.upper() and ip_ready is True) else 4
        if result:
            # nmcli --wait limits the client, not the activation itself.
            run([NM, '--wait', '3', 'device', 'disconnect', IFACE], 4)
    finally:
        restored = not modified or run([NM, 'connection', 'modify', '--temporary',
                                       'uuid', uuid, '802-11-wireless.bssid', ''])[0] == 0
        # Restoring the NM profile alone does not clear the active supplicant pin.
        if not restored:
            restored = run([NM, 'connection', 'modify', '--temporary',
                            'uuid', uuid, '802-11-wireless.bssid', ''])[0] == 0
        s = status(wpa)
        released = True
        if s.get('wpa_state') == 'COMPLETED':
            network_id = s.get('id', '')
            released = (network_id.isdigit() and run([wpa, '-i', IFACE, 'bssid',
                        network_id, '00:00:00:00:00:00'])[1] == 'OK')
        if not restored or not released:
            print('helper-error=pin-cleanup-failed')
            # Never leave a silently pinned live connection.
            run([NM, '--wait', '3', 'device', 'disconnect', IFACE], 4)
            result = 5
    print(f'helper-result requested={target.upper()} actual={actual} rc={result} ipv4={ip_ready} roaming_released={int(released)}')
    return result

def rescan():
    rc, state = run([NM, '-g', 'GENERAL.STATE', 'device', 'show', IFACE])
    if rc or state.split(' ', 1)[0] not in ('30', '100', '120'):
        return 3
    return run([NM, '--wait', '3', 'device', 'wifi', 'rescan', 'ifname', IFACE], 4)[0]


def main():
    scanning = sys.argv[1:] == ['--rescan']
    activating = (len(sys.argv) in (3, 4) and re.fullmatch(UUID, sys.argv[1])
                  and re.fullmatch(MAC, sys.argv[2])
                  and (len(sys.argv) == 3 or sys.argv[3] == 'nms-unreachable'))
    if os.geteuid() != 0 or not (scanning or activating):
        return 2
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with open('/run/scanner-wifi-activate.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 3
        try:
            if os.path.exists('/run/robot-network-recovery.pause'):
                print('helper-skip=paused')
                return 3
            return rescan() if scanning else activate(sys.argv[1], sys.argv[2], len(sys.argv) == 4)
        except Exception:
            print('helper-error=operation-failed')
            return 4

if __name__ == '__main__':
    sys.exit(main())
