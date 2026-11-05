"""Paced, failure-triggered Wi-Fi recovery owned by scanner-agent only.

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
        self.context_at = 0
        self.scan_at = 0
        self.connected_attempt_at = 0
        self.notes = {}

    def note(self, reason):
        now = self.clock()
        if now >= self.notes.get(reason, -1):
            self.log(f'NET_RECOVERY wait: {reason}')
            self.notes[reason] = now + 30

    def restore_context(self):
        # No credentials read. An active profile is authoritative. Otherwise
        # select only one unambiguous, autoconnect-enabled Wi-Fi profile.
        now = self.clock()
        if self.context or now < self.context_at:
            return
        self.context_at = now + 30
        rc, active = run(['/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID',
                          'device', 'show', INTERFACE], 2)
        active = active if not rc and re.fullmatch(UUID, active) else None
        if active:
            uuids = [active]
        else:
            rc, out = run(['/usr/bin/nmcli', '-g', 'UUID', 'connection', 'show'], 2)
            uuids = [u for u in out.splitlines() if re.fullmatch(UUID, u)] if not rc else []
        profiles = []
        for uuid in uuids:
            rc, out = run(['/usr/bin/nmcli', '-g',
                'connection.type,connection.interface-name,connection.autoconnect,802-11-wireless.bssid,802-11-wireless.ssid',
                'connection', 'show', 'uuid', uuid], 2)
            values = out.splitlines()
            if rc or len(values) != 5:
                continue
            kind, iface, automatic, pin, ssid = values
            if (kind == '802-11-wireless' and iface in ('', '--', INTERFACE)
                    and pin in ('', '--') and ssid and (active or automatic == 'yes')):
                profiles.append((uuid, ssid))
        if len(profiles) != 1:
            self.note('profile unavailable, restricted, or ambiguous; no targeted activation')
            return
        uuid, ssid = profiles[0]
        rows = visible()
        current = next((r for r in rows if r[0] == '*' and r[4] == ssid), None)
        # Without an active BSSID the saved profile still enforces authentication.
        self.context = (uuid, current[3] if current else '', ssid)
        self.cached = rows
        self.log('NET_RECOVERY profile context restored without an NMS poll')

    def nms_ok(self):
        now = self.clock()
        if self.started is not None:
            self.log('NET_RECOVERY NMS poll restored')
            self.refresh_at = 0
        self.started = None
        self.tried.clear()
        self.notes.clear()
        self.connected_attempt_at = 0
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
        if os.path.exists(PAUSE):
            self.note('paused by marker')
            return False
        if now < self.next_at:
            return False
        state = wifi_state()
        if state == 'unknown':
            self.note('interface unavailable, intentionally unmanaged, or inspection failed')
            return False
        self.restore_context()
        if state in ('activating', 'no-ipv4'):
            if self.activating_since is None:
                self.activating_since = now
                self.log(f'NET_RECOVERY state={state}: allow 25s for native IP recovery')
            if now - self.activating_since < 25:
                return False
        else:
            if state == 'connected' and self.activating_since is not None:
                # An address just appeared: give the next NMS poll a chance.
                self.connected_attempt_at = max(self.connected_attempt_at, now + 10)
            self.activating_since = None
            if state == 'connected':
                # An address is not proof that the AP/backhaul reaches NMS.
                # Limit disruption if NMS itself is down: at most one such
                # attempt per 30 seconds, rotating candidates across failures.
                if now - self.started < 30 or now < self.connected_attempt_at:
                    self.note('IPv4 present; allowing 30s for NMS/path recovery')
                    return False
            elif state != 'disconnected':
                self.note(f'unhandled state={state}')
                return False
        # Refresh a stale scan during an outage, never on the healthy path.
        # The helper independently guards disabled/unmanaged interfaces.
        if self.context and now >= self.scan_at:
            self.scan_at = now + 60
            rc, _ = run(['/usr/bin/sudo', '-n', '/usr/local/libexec/scanner-wifi-activate', '--rescan'], 6)
            self.log(f'NET_RECOVERY scan request rc={rc}')
        target = None
        args = ACTIVATE
        if self.context:
            uuid, security, ssid = self.context
            # Keep the last healthy scan as fallback when an outage empties cache.
            rows = {r[1].upper(): r for r in self.cached}
            rows.update({r[1].upper(): r for r in visible()})
            candidates = [(self.tried.get(mac.upper(), -1), -int(signal), mac.upper())
                          for _, mac, signal, sec, name in rows.values()
                          if name == ssid and (not security or sec == security)]
            if candidates:
                last, _, target = min(candidates)
                if last >= 0 and now - last < 30:
                    self.next_at = last + 30
                    return False
                args = ['/usr/bin/sudo', '-n',
                        '/usr/local/libexec/scanner-wifi-activate', uuid, target]
        # Without a remembered profile, do not cancel an ongoing activation.
        current = wifi_state()
        if os.path.exists(PAUSE) or current not in ('disconnected', 'activating', 'no-ipv4', 'connected'):
            return False
        if current == 'connected':
            if state != 'connected':
                return False  # native recovery just obtained an address
            if not target:
                self.note('NMS unreachable with IPv4 but no eligible target')
                return False
            args.append('nms-unreachable')
        if current in ('activating', 'no-ipv4') and (state not in ('activating', 'no-ipv4') or not target):
            self.note('waiting for profile/candidate before cancelling activation')
            return False
        self.log(f'NET_RECOVERY state={current} request target={target or "automatic"}')
        rc, output = run(args, 90 if target else 14)
        end = self.clock()
        if current == 'connected':
            self.connected_attempt_at = end + 30
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
