"""Bounded, failure-triggered Wi-Fi recovery owned by scanner-agent only.

No background thread, motion commands, daemon restarts, or command replay.
Recovery resets only after a successful NMS poll, not after nmcli succeeds.
"""
import re
import ipaddress
import json
import shutil
import os
import subprocess
import time


# Select an existing suitable profile via NetworkManager, without SSID/BSSID lock.
INTERFACE = 'wlP1p1s0'
ACTIVATE = ['/usr/bin/sudo', '-n', '/usr/bin/nmcli', '--wait', '12',
            'connection', 'up', 'ifname', INTERFACE]


def run(argv, timeout):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout,
                           env={**os.environ, 'LC_ALL': 'C'})
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
    rc, out = run([ip, '-j', '-4', 'address', 'show', 'dev', INTERFACE], 2)
    if rc:
        return None
    try:
        devices = json.loads(out)
        if not isinstance(devices, list) or not devices:
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


def wifi_state(timeout=2):
    rc, state = run(['/usr/bin/nmcli', '-g', 'GENERAL.STATE',
                     'device', 'show', INTERFACE], timeout)
    if rc:
        return 'unknown'
    code = state.split(' ', 1)[0]
    if code == '100':
        ready = ipv4_ready()
        return ('connected' if ready else 'no-ipv4') if ready is not None else 'unknown'
    if code in ('30', '120'):
        return 'disconnected'
    if code in ('40', '50', '60', '70', '80', '90'):
        return 'activating'
    # Missing, unmanaged, unavailable, or deactivating: do not override intent.
    return 'unknown'


MAC = r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}'
UUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
ROW = re.compile(r'([^:]*):(' + MAC + r'):(\d+):([^:]*):(.*)')
PAUSE = '/run/robot-network-recovery.pause'


def visible():
    # Read NetworkManager's scan cache; never initiate a scan during a roam.
    rc, out = run(['/usr/bin/nmcli', '-t', '-e', 'no', '-f',
                   'IN-USE,BSSID,SIGNAL,SECURITY,SSID', 'device', 'wifi', 'list',
                   'ifname', INTERFACE, '--rescan', 'no'], 2)
    return [m.groups() for row in out.splitlines()
            if (m := ROW.fullmatch(row))] if rc == 0 else []


class NetworkRecovery:
    def __init__(self, log, clock=time.monotonic):
        self.log = log
        self.clock = clock
        self.context = None
        self.refresh_at = 0
        self.started = None
        self.next_at = 0
        self.tried = {}
        self.cached = []
        self.activating_since = None

    def nms_ok(self):
        now = self.clock()
        if self.started is not None:
            self.log('NET_RECOVERY NMS poll restored')
            self.refresh_at = 0
        self.started = None
        self.tried.clear()
        self.activating_since = None
        if now < self.refresh_at:
            return
        self.refresh_at = now + 30
        rc, uuid = run(['/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID',
                        'device', 'show', INTERFACE], 2)
        rows = visible()
        active = next((row for row in rows if row[0] == '*'), None)
        if active and active[4] and not rc and re.fullmatch(UUID, uuid):
            self.context = (uuid, active[3], active[4])
            self.cached = rows

    def failed(self, reason):
        now = self.clock()
        if self.started is None:
            self.started = now
            self.next_at = now + 5
            self.log(f'NET_RECOVERY outage detected: {reason}')
        if now < self.next_at or os.path.exists(PAUSE):
            return False
        state = wifi_state()
        if state in ('activating', 'no-ipv4'):
            if self.activating_since is None:
                self.activating_since = now
                self.log(f'NET_RECOVERY state={state}: allow 25s for native IP recovery')
            if now - self.activating_since < 25:
                return False
        else:
            self.activating_since = None
            if state != 'disconnected':
                return False
        target = None
        args = ACTIVATE
        if self.context:
            uuid, security, ssid = self.context
            # Keep the last healthy scan as fallback when an outage empties cache.
            rows = {r[1].upper(): r for r in self.cached}
            rows.update({r[1].upper(): r for r in visible()})
            candidates = [(self.tried.get(mac.upper(), -1), -int(signal), mac.upper())
                          for _, mac, signal, sec, name in rows.values()
                          if (sec, name) == (security, ssid)]
            if candidates:
                last, _, target = min(candidates)
                if last >= 0 and now - last < 30:
                    self.next_at = last + 30
                    return False
                args = ['/usr/bin/sudo', '-n',
                        '/usr/local/libexec/scanner-wifi-activate', uuid, target]
        # Without a remembered profile, do not cancel an ongoing activation.
        current = wifi_state()
        if os.path.exists(PAUSE) or current not in ('disconnected', 'activating', 'no-ipv4'):
            return False
        if current in ('activating', 'no-ipv4') and (state not in ('activating', 'no-ipv4') or not target):
            return False
        self.log(f'NET_RECOVERY state={current} request target={target or "automatic"}')
        rc, output = run(args, 90 if target else 14)
        end = self.clock()
        if target:
            self.tried[target] = end
        self.activating_since = None
        # Exhaust alternatives before revisiting an AP. No age-based 60s delay.
        delay = 5 if target else 25
        self.next_at = end + delay
        # Helper output is deliberately restricted to identifiers and outcomes.
        if target:
            for line in output.splitlines():
                if line.startswith(('helper-result ', 'helper-error=', 'helper-skip=')):
                    self.log('NET_RECOVERY ' + line)
        self.log(f'NET_RECOVERY result rc={rc}; next eligible in {delay}s; no command replay')
        return True
