"""Bounded, failure-triggered Wi-Fi recovery owned by scanner-agent only.

No background thread, motion commands, daemon restarts, or command replay.
Budget resets only after a successful NMS poll, not after nmcli succeeds.
"""
from pathlib import Path
from robot_ap_roster import radios_for, RADIOS, MIN_RSSI_DBM, MAX_AGE_SEC
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


MAC = r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}'
UUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'


def working_profile():
    rc, rows = run(['/usr/bin/nmcli', '-t', '-f', 'UUID,DEVICE',
                    'connection', 'show', '--active'], 2)
    for row in rows.splitlines() if rc == 0 else []:
        uid, _, dev = row.partition(':')
        if dev == INTERFACE and re.fullmatch(UUID, uid):
            def field(key):
                return run(['/usr/bin/nmcli', '-e', 'no', '-g', key,
                            'connection', 'show', 'uuid', uid], 2)
            ok, pin = field('802-11-wireless.bssid')
            if ok or pin not in ('', '--'):
                return None  # Respect deliberately BSSID-locked profiles.
            ok, ssid = field('802-11-wireless.ssid')
            if not ok and ssid and ssid != '--':
                return uid, ssid
    return None


IW = ['/usr/bin/sudo','-n','/usr/sbin/iw','dev',INTERFACE,'scan']


def learn(table, ssid, now, allowed):
    rc, data = run(IW + ['dump'], 4)
    if rc:
        return
    for block in re.split(r'(?m)^BSS ', data)[1:]:
        addr = block[:17].upper()
        if addr not in allowed:
            continue
        def field(pattern):
            return re.search(pattern, block, re.MULTILINE)
        name = field(r'^\s*SSID: ([^\r\n]*)$')
        signal = field(r'^\s*signal: (-?[0-9.]+) dBm')
        age = field(r'^\s*last seen: (\d+) ms ago')
        freq = field(r'^\s*freq: (\d+)')
        if name and name[1] == ssid and signal and age and freq:
            table[addr] = (float(signal[1]), int(freq[1]), now - int(age[1])/1000)


class NetworkRecovery:
    def __init__(self, log, clock=time.monotonic, sleep=time.sleep):
        self.log, self.clock, self.sleep = log, clock, sleep
        self.allowed = frozenset()
        self.table = {}
        self.profile = None
        self.refresh_profile_at = 0
        self.outage_started = None
        self.next_try = self.next_scan = 0
        self.attempts = 0
        self.last_try = {}

    def nms_ok(self):
        if self.clock() >= self.refresh_profile_at or self.outage_started is not None:
            try:
                scanner = Path(__file__).with_name('scanner_name.txt').read_text().strip()
            except OSError:
                scanner = ''
            allowed = radios_for(scanner)
            if allowed != self.allowed:
                self.table.clear()
                self.allowed = allowed
            profile = working_profile()
            if profile != self.profile:
                self.table.clear()
            self.profile = profile
            if profile:
                learn(self.table, profile[1], self.clock(), self.allowed)
            self.refresh_profile_at = self.clock() + 60
        if self.outage_started is not None:
            self.log(f'NET_RECOVERY NMS poll restored; attempts={self.attempts}')
        self.outage_started = None
        self.attempts = self.next_try = self.next_scan = 0
        self.last_try.clear()

    def failed(self, reason):
        if self.outage_started is None:
            self.outage_started = self.clock()
            self.full_scan_at = self.clock() + 20
            self.log(f'NET_RECOVERY outage detected: {reason}')
        attempted = False
        # Return to NMS discovery between batches, immediately on successful activation.
        for _ in range(4):
            now = self.clock()
            if (now < self.next_try or os.path.exists('/run/robot-network-recovery.pause')
                    or wifi_state() != 'disconnected'):
                break
            slow = now - self.outage_started >= 120
            cooldown = 60 if slow else 15
            if not self.profile or not self.allowed:
                self.log('NET_RECOVERY no assigned radios or usable profile; leaving selection to NetworkManager')
                self.next_try = now + 30
                break
            uid, ssid = self.profile
            def ranked():
                return sorted((b for b,(dbm,freq,at) in self.table.items()
                               if b in self.allowed and now-at <= MAX_AGE_SEC and dbm >= MIN_RSSI_DBM
                               and (b not in self.last_try or now-self.last_try[b] >= cooldown)),
                              key=lambda b:(self.last_try.get(b,float('-inf')), -self.table[b][0],b))
            eligible = ranked()
            if not eligible and now >= self.next_scan:
                learn(self.table, ssid, now, self.allowed)
                eligible = ranked()
                if not eligible:
                    freqs = sorted({str(v[1]) for v in self.table.values()})
                    # Try known channels first. Periodically broaden discovery for channel changes.
                    args = ['trigger','freq',*freqs] if freqs and now < self.full_scan_at else ['trigger']
                    run(IW + args, 4)
                    if args == ['trigger']:
                        self.full_scan_at = now + 60
                self.next_scan = now + 5
            if not eligible:
                self.next_try = now + 5
                break
            target = eligible[0]
            argv = ACTIVATE[:6] + ['up','uuid',uid,'ifname',INTERFACE,'ap',target]
            if wifi_state() != 'disconnected':
                break
            self.attempts += 1
            attempted = True
            self.log(f'NET_RECOVERY activation {self.attempts} target={target} radio={RADIOS[target]} rssi={self.table[target][0]}')
            rc, _ = run(argv, timeout=14)
            self.last_try[target] = self.clock()
            self.log(f'NET_RECOVERY activation result rc={rc} target={target}; no command replay')
            if rc == 0 or target == 'automatic' or slow:
                self.next_try = self.clock() + (60 if slow else 15)
                break
        return attempted
