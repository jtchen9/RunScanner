"""Bounded, failure-triggered Wi-Fi recovery owned by scanner-agent only.

No background thread, motion commands, daemon restarts, or command replay.
Budget resets only after a successful NMS poll, not after nmcli succeeds.
"""
import re
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


def wifi_state(timeout=2):
    rc, state = run(['/usr/bin/nmcli', '-g', 'GENERAL.STATE',
                     'device', 'show', INTERFACE], timeout)
    if rc:
        return 'unknown'
    code = state.split(' ', 1)[0]
    if code == '100':
        return 'connected'
    if code in ('30', '120'):
        return 'disconnected'
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

    def nms_ok(self):
        now = self.clock()
        if self.started is not None:
            self.log('NET_RECOVERY NMS poll restored')
            self.refresh_at = 0
        self.started = None
        self.tried.clear()
        if now < self.refresh_at:
            return
        self.refresh_at = now + 30
        rc, uuid = run(['/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID',
                        'device', 'show', INTERFACE], 2)
        active = next((row for row in visible() if row[0] == '*'), None)
        self.context = ((uuid, active[3], active[4]) if active and active[4]
                        and not rc and re.fullmatch(UUID, uuid) else None)

    def failed(self, reason):
        now = self.clock()
        if self.started is None:
            self.started = now
            self.next_at = now + 5
            self.log(f'NET_RECOVERY outage detected: {reason}')
        if now < self.next_at or os.path.exists(PAUSE):
            return False
        if wifi_state() != 'disconnected':
            return False
        target = None
        args = ACTIVATE
        if self.context:
            uuid, security, ssid = self.context
            candidates = [(self.tried.get(mac.upper(), -1), -int(signal), mac.upper())
                          for _, mac, signal, sec, name in visible()
                          if (sec, name) == (security, ssid)]
            if candidates:
                last, _, target = min(candidates)
                if last >= 0 and now - last < 25:
                    return False
                args = ACTIVATE[:5] + ['connection', 'up', 'uuid', uuid,
                                        'ifname', INTERFACE, 'ap', target]
        # Recheck after reading the cache: don't interrupt native activation.
        if os.path.exists(PAUSE) or wifi_state() != 'disconnected':
            return False
        self.log(f'NET_RECOVERY request target={target or "automatic"}')
        rc, _ = run(args, 14)
        end = self.clock()
        if target:
            self.tried[target] = end
        # Keep polling between attempts. Continue slowly after the fast window
        # so a timed-out experiment does not leave the robot unreachable.
        delay = 60 if end - self.started >= 90 else (7 if target else 25)
        self.next_at = end + delay
        self.log(f'NET_RECOVERY result rc={rc}; next eligible in {delay}s')
        return True
