"""Bounded, failure-triggered Wi-Fi recovery owned by scanner-agent only.

No background thread, motion commands, daemon restarts, or command replay.
Budget resets only after a successful NMS poll, not after nmcli succeeds.
"""
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
    if code in ('30', '40', '50', '60', '70', '80', '90', '120'):
        return 'disconnected'
    # Missing, unmanaged, unavailable, or deactivating: do not override intent.
    return 'unknown'


class NetworkRecovery:
    def __init__(self, log, clock=time.monotonic, sleep=time.sleep):
        self.log = log
        self.clock = clock
        self.sleep = sleep
        self.outage_started = None
        self.attempts = 0
        self.exhausted_logged = False

    def nms_ok(self):
        if self.outage_started is not None:
            self.log(f'NET_RECOVERY NMS poll restored; attempts={self.attempts}')
        self.outage_started = None
        self.attempts = 0
        self.exhausted_logged = False

    def failed(self, reason):
        """Return True if activation was attempted; caller may poll promptly.

        A 40s window starts on the first observed NMS failure, after the failed
        request/discovery. Each nmcli operation waits <=12s, subprocess <=14s.
        No new attempt starts after this window or after two attempts.
        """
        if self.outage_started is None:
            self.outage_started = self.clock()
            self.log(f'NET_RECOVERY outage detected: {reason}')
        deadline = self.outage_started + 40
        tried = False
        while True:
            if self.attempts >= 2 or self.clock() >= deadline:
                if not self.exhausted_logged:
                    self.log('NET_RECOVERY quick recovery exhausted; normal polling continues')
                    self.exhausted_logged = True
                return tried
            if os.path.exists('/run/robot-network-recovery.pause'):
                return tried
            remaining = deadline - self.clock()
            state = wifi_state(timeout=min(2, remaining))
            if state != 'disconnected':
                # A reachable HTTP service, healthy Wi-Fi or query error does not
                # justify disconnecting or resetting the radio.
                return tried
            if deadline - self.clock() < 14:
                self.exhausted_logged = True
                self.attempts = 2
                self.log('NET_RECOVERY insufficient time for another activation; stopped')
                return tried
            self.attempts += 1
            tried = True
            self.log(f'NET_RECOVERY activation {self.attempts}/2 interface={INTERFACE}')
            rc, _ = run(ACTIVATE, timeout=14)
            self.log(f'NET_RECOVERY activation result rc={rc}; no command replay')
            if rc == 0:
                return True
            # Do not log nmcli output or secrets. Existing NetworkManager journal
            # records detailed failure reasons, including permissions failures.
            if self.attempts < 2:
                self.sleep(min(2, max(0, deadline - self.clock())))
