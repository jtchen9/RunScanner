"""Shared helpers; no network changes on import."""
import contextlib
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import tempfile
import time
from vendor.capture_wifi import run, save_json, choose_iface, stop_process


def interrupted(signum, frame):
    raise KeyboardInterrupt(f'signal {signum}')


def setup():
    os.umask(0o077)
    if os.geteuid() != 0:
        raise RuntimeError('Run this robot-side tool with sudo.')
    for name in ('iw', 'ip'):
        if not shutil.which(name):
            raise RuntimeError(f'Missing executable: {name}')
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)


@contextlib.contextmanager
def scanner_lock():
    import fcntl  # Linux-only operation; keep offline imports usable on Windows.
    path = Path('/opt/_RunScanner/.cache/scanner-poller.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('scanner-poller or another capture is running. Stop it first.')
        yield


def session(root, role):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if os.environ.get('SUDO_UID'):
        os.chown(root, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
    prefix = role + '_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ_')
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


class Log:
    def __init__(self, folder):
        self.folder = folder

    def command(self, args, **kwargs):
        result = run(args, **kwargs)
        with (self.folder / 'commands.jsonl').open('a') as f:
            f.write(json.dumps({'epoch': time.time(), **result}) + '\n')
        return result

    def checked(self, args, **kwargs):
        r = self.command(args, **kwargs)
        if r['rc']:
            raise RuntimeError(f"Command failed: {args}: {r['stderr']}")
        return r['stdout']


def clock_state(log):
    r = log.command(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'])
    return {'ntp_synchronized': r['rc'] == 0 and r['stdout'].strip() == 'yes',
            'epoch': time.time(), 'monotonic': time.monotonic()}


def link_state(log, iface):
    info = log.checked(['iw', 'dev', iface, 'info'])
    link = log.checked(['iw', 'dev', iface, 'link'])
    ap = re.search(r'Connected to ([0-9a-f:]{17})', link, re.I)
    ch = re.search(r'channel \d+ \((\d+) MHz\), width: (\d+) MHz, center1: (\d+) MHz', info)
    mac = re.search(r'^\s*addr ([0-9a-f:]{17})', info, re.M | re.I)
    power = re.search(r'txpower (-?[\d.]+) dBm', info)
    if not ap or not ch or not mac or not re.search(r'^\s*type managed\s*$', info, re.M):
        raise RuntimeError('Sender must already be associated in managed mode; channel must be readable.')
    return {'ap_bssid': ap[1].lower(), 'transmitter_mac': mac[1].lower(),
            'primary_mhz': int(ch[1]), 'width_mhz': int(ch[2]), 'center_mhz': int(ch[3]),
            'iw_reported_txpower_dbm': float(power[1]) if power else None,
            'info': info, 'link': link}


def same_link(a, b):
    return all(a[k] == b[k] for k in ('ap_bssid', 'transmitter_mac', 'primary_mhz', 'width_mhz', 'center_mhz'))


def power_args(iface, mode, dbm):
    if mode not in ('auto', 'limit', 'fixed'):
        raise ValueError('Power mode must be auto, limit, or fixed')
    args = ['iw', 'dev', iface, 'set', 'txpower', mode]
    if mode != 'auto':
        if dbm is None or not -10 <= float(dbm) <= 30:
            raise ValueError('This tool accepts requested powers from -10 to 30 dBm; driver limits still apply.')
        args.append(str(round(float(dbm) * 100)))
    return args


def pack(folder):
    # Keep archives beside raw sessions inside TestAX210/results.
    fd, name = tempfile.mkstemp(prefix=f'ax210_power_{socket.gethostname()}_{folder.name[:2]}_', suffix='.zip', dir=folder.parent)
    os.close(fd)
    archive = Path(name)
    try:
        shutil.make_archive(str(archive.with_suffix('')), 'zip', folder)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    os.chmod(archive, 0o600)
    if os.environ.get('SUDO_UID'):
        uid, gid = int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID'])
        os.chown(archive, uid, gid)
        os.chown(folder, uid, gid)
        for entry in folder.rglob('*'):
            if not entry.is_symlink():
                os.chown(entry, uid, gid)
    print(f'Results archive: {archive}', flush=True)
    return archive


def gateway(log, iface, configured):
    if configured:
        target = str(ipaddress.IPv4Address(configured))
    else:
        routes = json.loads(log.checked(['ip', '-j', '-4', 'route', 'show', 'default', 'dev', iface]))
        choices = {r['gateway'] for r in routes if r.get('gateway')}
        if len(choices) != 1:
            raise RuntimeError('Set TARGET_IPV4 to the AP LAN address; no unique Wi-Fi gateway found.')
        target = choices.pop()
    routes = json.loads(log.checked(['ip', '-j', '-4', 'route', 'get', target, 'oif', iface]))
    if not routes or routes[0].get('dev') != iface or routes[0].get('type') in ('local', 'unreachable', 'blackhole'):
        raise RuntimeError('Target is not reachable through the selected Wi-Fi interface.')
    return target
