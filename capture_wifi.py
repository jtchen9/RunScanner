#!/usr/bin/env python3
"""v1.2: Isolated RX-only monitor capture with Radiotap header checks.

No third-party Python dependencies. Settings are in __main__ below.
No packet-field assumptions or transmit-power estimates are made here.
"""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import struct
import subprocess as sp
import sys
import tempfile
import time


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def stop_process(p):
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            p.wait(timeout=3)
        except sp.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()


def run(args, timeout=15, stdin=None, stdout=None):
    """Bound subprocesses, including on cancellation; never invoke a shell."""
    try:
        p = sp.Popen(args, stdin=stdin, stdout=stdout or sp.PIPE,
                     stderr=sp.PIPE, start_new_session=True)
    except OSError as exc:
        return {'command': args, 'rc': 127, 'stdout': '', 'stderr': str(exc)}
    try:
        out, err = p.communicate(timeout=timeout)
        return {'command': args, 'rc': p.returncode,
                'stdout': (out or b'').decode(errors='replace'),
                'stderr': (err or b'').decode(errors='replace')}
    except sp.TimeoutExpired:
        stop_process(p)
        out, err = p.communicate()
        return {'command': args, 'rc': 124,
                'stdout': (out or b'').decode(errors='replace'),
                'stderr': (err or b'').decode(errors='replace') + '\nTIMEOUT'}
    finally:
        stop_process(p)


def choose_iface(explicit):
    names = []
    for p in Path('/sys/class/net').iterdir():
        if p.name in ('lo', 'wlan0'):
            continue
        if (p / 'device/driver').resolve().name == 'iwlwifi':
            info = run(['iw', 'dev', p.name, 'info'])
            if re.search(r'^\s*type managed\s*$', info['stdout'], re.M):
                names.append(p.name)
    if explicit:
        if explicit not in names:
            raise RuntimeError('IFACE must be a managed iwlwifi interface other than wlan0')
        return explicit
    if len(names) != 1:
        raise RuntimeError(f'Expected one dedicated Intel interface; found {names}. Set IFACE explicitly.')
    return names[0]


def enabled_frequencies(phy):
    return {int(m[0]) for m in re.findall(r'^\s*\*\s+(\d+)(?:\.0+)?\s+MHz\s+\[\d+\]([^\n]*)', phy, re.M)
            if 'disabled' not in m[1]}


def discover_plan(scan, phy, explicit):
    """Primary 20 MHz pass first, then 40/80/160 MHz candidate receive settings.

    Wide settings are receiver probes, not claims about an AP's actual width.
    Kernel/regulatory checks decide whether a particular setting is permitted.
    """
    allowed = enabled_frequencies(phy)
    if explicit:
        plan = [dict(primary_mhz=int(x[0]), width_mhz=int(x[1]), center_mhz=int(x[2]))
                for x in explicit]
        for p in plan:
            if p['width_mhz'] not in (20, 40, 80, 160):
                raise ValueError('Channel plan widths must be 20, 40, 80, or 160 MHz')
        return plan, 'explicit'
    found = {int(f) for f in re.findall(r'^\s*freq:\s*(\d+)(?:\.0+)?\s*$', scan, re.M)}
    freqs = sorted(found & allowed)
    source = 'fresh_scan'
    if not freqs:
        # Prefer 6 GHz PSCs on the fallback sweep, but preserve every frequency.
        freqs = sorted(allowed, key=lambda f: (f >= 5955 and (f - 5975) % 80 != 0, f))
        source = 'enabled_phy_channels_fallback'
    bands = [[f for f in freqs if f < 3000],
             [f for f in freqs if 3000 <= f < 5925],
             [f for f in freqs if f >= 5925]]
    ordered = []
    while any(bands):
        for band in bands:
            if band:
                ordered.append(band.pop(0))
    plan = [dict(primary_mhz=f, width_mhz=20, center_mhz=f) for f in ordered]
    for width in (40, 80, 160):
        for f in ordered:
            centers = []
            if 5955 <= f <= 7115:
                center = 5955 + (width // 20 - 1) * 10 + ((f - 5955) // width) * width
                centers = [center]
            elif 5000 <= f < 5925:
                if width == 40:
                    centers = [f - 10, f + 10]
                elif width == 80:
                    centers = [5210, 5290, 5530, 5610, 5690, 5775, 5855]
                else:
                    centers = [5250, 5570, 5815]
            elif f < 2500 and width == 40:
                centers = [f - 10, f + 10]
            for c in centers:
                subs = list(range(c - width // 2 + 10, c + width // 2, 20))
                if f in subs and all(x in allowed for x in subs):
                    plan.append(dict(primary_mhz=f, width_mhz=width, center_mhz=c))
    return plan, source


def parse_tuning(text):
    match = re.search(r'channel\s+\d+\s+\((\d+)(?:\.0+)?\s+MHz\),\s*width:\s+(\d+)\s+MHz', text)
    return (int(match[1]), int(match[2])) if match else None


def radiotap_header_ok(packet):
    """Structural header check only; individual PHY fields still need decoding."""
    if len(packet) < 8 or packet[0] != 0 or packet[1] != 0:
        return False
    length = int.from_bytes(packet[2:4], 'little')
    if not 8 <= length <= len(packet):
        return False
    offset = 4
    while offset + 4 <= length:
        word = int.from_bytes(packet[offset:offset + 4], 'little')
        offset += 4
        if not word & (1 << 31):
            return True
    return False


def probe_plan(phy):
    """Lab channels already seen in Echo's scans. Never force disabled channels."""
    candidates = [(2412, 20, 2412), (5180, 20, 5180), (6115, 20, 6115),
                  (5180, 80, 5210), (6115, 160, 6185)]
    enabled = enabled_frequencies(phy)
    accepted, skipped = [], []
    for f, width, center in candidates:
        profile = dict(primary_mhz=f, width_mhz=width, center_mhz=center)
        subchannels = range(center - width // 2 + 10, center + width // 2, 20)
        if all(x in enabled for x in subchannels):
            accepted.append(profile)
        else:
            skipped.append({**profile, 'reason': 'channel_disabled_or_not_reported_by_driver'})
    return accepted, skipped


def pcap_info(path):
    """Count saved records without interpreting optional Radiotap fields."""
    result = {'file': path.name, 'bytes': path.stat().st_size, 'packets': 0,
              'truncated_packets': 0, 'incomplete_tail': False,
              'radiotap_headers': 0, 'invalid_radiotap_records': 0}
    digest = hashlib.sha256()
    with path.open('rb') as src:
        for block in iter(lambda: src.read(1024 * 1024), b''):
            digest.update(block)
        src.seek(0)
        header = src.read(24)
        magic = {b'\xd4\xc3\xb2\xa1': '<', b'\xa1\xb2\xc3\xd4': '>',
                 b'\x4d\x3c\xb2\xa1': '<', b'\xa1\xb2\x3c\x4d': '>'}
        if len(header) != 24 or header[:4] not in magic:
            result['error'] = 'missing_or_unrecognized_pcap_header'
        else:
            endian = magic[header[:4]]
            result['linktype'] = struct.unpack(endian + 'I', header[20:24])[0] & 0xffff
            while True:
                h = src.read(16)
                if not h:
                    break
                if len(h) != 16:
                    result['incomplete_tail'] = True
                    break
                sec, subsec, captured, original = struct.unpack(endian + 'IIII', h)
                if captured > result['bytes'] - src.tell():
                    result['incomplete_tail'] = True
                    break
                # Radiotap's own length is 16-bit; do not allocate a huge record.
                packet = src.read(min(captured, 65535))
                src.seek(captured - len(packet), 1)
                result['packets'] += 1
                result['truncated_packets'] += captured < original
                if result['linktype'] == 127 and radiotap_header_ok(packet):
                    result['radiotap_headers'] += 1
                else:
                    result['invalid_radiotap_records'] += 1
    result['sha256'] = digest.hexdigest()
    return result


class Collector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.iface = choose_iface(cfg['iface'])
        self.phy = (Path('/sys/class/net') / self.iface / 'phy80211').resolve().name
        self.session = None
        self.events = None

    def command(self, args, **kw):
        r = run(args, **kw)
        with self.events.open('a') as out:
            out.write(json.dumps({'time_utc': utc(), **r}) + '\n')
        return r

    def checked(self, args):
        r = self.command(args)
        if r['rc']:
            raise RuntimeError(f"Command failed: {args}: {r['stderr']}")
        return r['stdout']

    def space_ok(self):
        return shutil.disk_usage(self.cfg['root']).free >= self.cfg['min_free_mb'] * 1024**2

    def diagnostics(self, suffix):
        commands = {
            'iw_dev': ['iw', 'dev'], 'phy': ['iw', 'phy', self.phy, 'info'],
            'reg': ['iw', 'reg', 'get'], 'link': ['iw', 'dev', self.iface, 'link'],
            'survey': ['iw', 'dev', self.iface, 'survey', 'dump'],
            'station': ['iw', 'dev', self.iface, 'station', 'dump'],
            'firmware': ['ethtool', '-i', self.iface], 'kernel': ['uname', '-a'],
            'tcpdump': ['tcpdump', '--version'], 'ip': ['ip', '-details', 'link', 'show', self.iface],
            'kernel_log': ['journalctl', '-k', '-n', '250', '--no-pager'],
        }
        for name, args in commands.items():
            r = self.command(args)
            (self.session / f'{name}_{suffix}.txt').write_text(r['stdout'] + r['stderr'])
        params = {}
        p = Path('/sys/module/iwlwifi/parameters')
        if p.exists():
            for entry in p.iterdir():
                try:
                    params[entry.name] = entry.read_text().strip()
                except OSError as exc:
                    params[entry.name] = str(exc)
        save_json(self.session / f'iwlwifi_parameters_{suffix}.json', params)

    def legacy_scan(self):
        cfg = self.cfg
        for path in (cfg['csv'], cfg['json']):
            path.write_text('')
        start = time.monotonic()
        with (self.session / 'iw_scan.txt').open('wb') as raw:
            r = self.command(['iw', 'dev', self.iface, 'scan'],
                             stdout=raw, timeout=cfg['scan_timeout'])
        Path('/tmp/iw_err.txt').write_text(r['stderr'])
        if r['rc']:
            return {'ok': False, 'stage': 'iw_scan', 'rc': r['rc']}
        remaining = cfg['scan_timeout'] - (time.monotonic() - start)
        if remaining <= 0:
            return {'ok': False, 'stage': 'parser_timeout'}
        with tempfile.TemporaryDirectory(prefix='scanner-poller.', dir='/tmp') as work:
            csv, js = Path(work) / 'scan.csv', Path(work) / 'scan.json'
            with (self.session / 'iw_scan.txt').open('rb') as raw:
                r = self.command(['/usr/bin/python3', str(cfg['parser']), str(csv), str(js)],
                                 stdin=raw, timeout=remaining)
            if r['rc'] or not js.exists() or not js.stat().st_size or not csv.exists():
                return {'ok': False, 'stage': 'parser', 'rc': r['rc']}
            # Same publication order and paths as the supplied scanner.
            shutil.copyfile(csv, self.session / 'scan.csv')
            shutil.copyfile(js, self.session / 'scan.json')
            os.replace(csv, cfg['csv'])
            os.replace(js, cfg['json'])
        return {'ok': True}

    def monitor(self, report):
        if not report['plan']:
            raise RuntimeError('No capture channels parsed; inspect phy_before.txt and iw_scan.txt. Capture was not started.')
        radio = self.iface
        iface = radio
        info = self.checked(['iw', 'dev', iface, 'info'])
        if not re.search(r'^\s*type managed\s*$', info, re.M):
            raise RuntimeError('Selected interface must start in managed mode')
        link = self.checked(['iw', 'dev', iface, 'link'])
        if 'Not connected.' not in link:
            raise RuntimeError('Monitor capture requires a disconnected dedicated Intel adapter')
        # Shared-phy operation may retune or disrupt another interface.
        devs = self.checked(['iw', 'dev'])
        block = next((b for b in re.split(r'(?=phy#\d+)', devs)
                      if b.startswith('phy#' + self.phy.removeprefix('phy') + '\n')), '')
        others = set(re.findall(r'Interface (\S+)', block)) - {iface}
        if others:
            raise RuntimeError(f'Other interfaces share {self.phy}: {sorted(others)}')
        up = bool(int((Path('/sys/class/net') / iface / 'flags').read_text(), 16) & 1)
        nm = self.command(['nmcli', '-g', 'GENERAL.STATE', 'device', 'show', iface])
        nm_restore = nm['rc'] == 0 and not nm['stdout'].strip().startswith('10 ')
        # Record intent before touching the interface, for recovery after power loss.
        report['original_interface'] = {'type': 'managed', 'up': up, 'nm_managed': nm_restore}
        mon = f'axcap{os.getpid():x}'
        if (Path('/sys/class/net') / mon).exists():
            raise RuntimeError(f'Monitor interface name already exists: {mon}')
        report['monitor_interface'] = mon
        created = False
        self.save(report)
        try:
            if nm_restore:
                self.checked(['nmcli', 'device', 'set', iface, 'managed', 'no'])
            # Use a new netdev, with no inherited IPv4 address or old multicast sockets.
            self.checked(['iw', 'phy', self.phy, 'interface', 'add', mon, 'type', 'monitor'])
            created = True
            iface = mon
            # Ask for control/other-BSS and erroneous frames. Log fallbacks explicitly.
            report['monitor_flags'] = None
            for flags in (['control', 'otherbss', 'fcsfail', 'plcpfail'], ['control', 'otherbss'], []):
                r = self.command(['iw', 'dev', iface, 'set', 'monitor', *flags])
                if r['rc'] == 0:
                    report['monitor_flags'] = flags
                    break
            self.checked(['ip', 'link', 'set', 'dev', iface, 'up'])
            # Bring the monitor up first to avoid stopping the radio between roles.
            self.checked(['ip', 'link', 'set', 'dev', radio, 'down'])
            for name, args in [('reg_monitor', ['iw', 'reg', 'get']),
                               ('phy_monitor', ['iw', 'phy', self.phy, 'info']),
                               ('ip_monitor', ['ip', '-details', 'addr', 'show', 'dev', iface])]:
                r = self.command(args)
                (self.session / (name + '.txt')).write_text(r['stdout'] + r['stderr'])
            self.command(['tcpdump', '-i', iface, '-L'])
            deadline = time.monotonic() + self.cfg['monitor_seconds']
            for i, setting in enumerate(report['plan']):
                if time.monotonic() >= deadline or not self.space_ok():
                    report['stop_reason'] = 'time_budget_or_free_space'
                    break
                if sum(p.stat().st_size for p in self.session.glob('*.pcap*')) >= self.cfg['session_mb'] * 1024**2:
                    report['stop_reason'] = 'session_size_limit'
                    break
                report['attempted_profiles'] += 1
                f, width, center = (setting[k] for k in ('primary_mhz', 'width_mhz', 'center_mhz'))
                print(f"Profile {i + 1}/{len(report['plan'])}: primary={f} MHz width={width} MHz center={center} MHz", flush=True)
                args = ['iw', 'dev', iface, 'set', 'freq', str(f)]
                args += ['HT20'] if width == 20 else [str(width), str(center)]
                r = self.command(args)
                item = {**setting, 'tune_rc': r['rc'], 'start_utc': utc()}
                report['captures'].append(item)
                if r['rc']:
                    self.save(report)
                    continue
                item['interface_before'] = self.command(['iw', 'dev', iface, 'info'])['stdout']
                actual = parse_tuning(item['interface_before'])
                item['tuning_verified'] = actual == (f, width)
                if actual and not item['tuning_verified']:
                    item['error'] = 'Observed tuning differs from requested profile; capture skipped'
                    self.save(report)
                    continue
                stem = f'{i:03d}_{f}MHz_{width}MHz'
                filename = self.session / (stem + '.pcap')
                cmd = ['tcpdump', '-i', iface, '-y', 'IEEE802_11_RADIO', '-s', '262144',
                       '-Q', 'in', '--immediate-mode', '-B', '4096', '-U', '-n',
                       '-Z', 'root', '-C', '32', '-w', str(filename)]
                item['command'] = cmd
                until = min(deadline, time.monotonic() + self.cfg['dwell_seconds'])
                with (self.session / (stem + '.tcpdump.log')).open('wb') as log:
                    p = sp.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
                    try:
                        while p.poll() is None and time.monotonic() < until:
                            size = sum(x.stat().st_size for x in self.session.glob('*.pcap*'))
                            if not self.space_ok() or size >= self.cfg['session_mb'] * 1024**2:
                                item['stop_reason'] = 'storage_limit'
                                break
                            time.sleep(0.2)
                    finally:
                        stop_process(p)
                    item['capture_rc'] = p.returncode
                item['end_utc'] = utc()
                item['interface_after'] = self.command(['iw', 'dev', iface, 'info'])['stdout']
                item['files'] = [pcap_info(x) for x in sorted(self.session.glob(stem + '.pcap*'))]
                self.save(report)
        finally:
            # Ignore repeated stop signals while restoring the dedicated adapter.
            old = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)}
            try:
                restore = []
                if up:
                    restore.append(self.command(['ip', 'link', 'set', 'dev', radio, 'up']))
                if created:
                    restore.append(self.command(['iw', 'dev', mon, 'del']))
                if nm_restore:
                    restore.append(self.command(['nmcli', 'device', 'set', radio, 'managed', 'yes']))
                report['restore_ok'] = all(r['rc'] == 0 for r in restore)
                report['restored_interface'] = self.command(['iw', 'dev', radio, 'info'])
                report['restore_ok'] = bool(report['restore_ok'] and
                    report['restored_interface']['rc'] == 0 and
                    re.search(r'^\s*type managed\s*$', report['restored_interface']['stdout'], re.M))
                self.save(report)
            finally:
                for s, handler in old.items():
                    signal.signal(s, handler)

    def save(self, report):
        save_json(self.session / 'manifest.json', report)

    def once(self, capture=True):
        root = self.cfg['root']
        root.mkdir(parents=True, exist_ok=True)
        if not self.space_ok():
            raise RuntimeError('Insufficient free disk space; existing captures were retained')
        self.session = Path(tempfile.mkdtemp(prefix=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ_'), dir=root))
        self.events = self.session / 'commands.jsonl'
        report = {'version': '1.2', 'start_utc': utc(), 'hostname': socket.gethostname(),
                  'iface': self.iface, 'phy': self.phy, 'captures': [], 'attempted_profiles': 0,
                  'status': 'running', 'capture_requested': capture,
                  'settings': {k: str(v) if isinstance(v, Path) else v for k, v in self.cfg.items()}}
        self.save(report)
        try:
            self.diagnostics('before')
            report['legacy_scan'] = self.legacy_scan()
            scan = (self.session / 'iw_scan.txt').read_text(errors='replace')
            # Intel's enabled channels may change during a managed-mode scan.
            r = self.command(['iw', 'phy', self.phy, 'info'])
            phy = r['stdout'] if r['rc'] == 0 else ''
            (self.session / 'phy_after_scan.txt').write_text(r['stdout'] + r['stderr'])
            r = self.command(['iw', 'reg', 'get'])
            (self.session / 'reg_after_scan.txt').write_text(r['stdout'] + r['stderr'])
            report['six_ghz_enabled_after_scan'] = sorted(f for f in enabled_frequencies(phy) if f >= 5925)
            report['plan'], report['plan_source'] = discover_plan(
                scan if report['legacy_scan'].get('stage') != 'iw_scan' else '', phy, self.cfg['plan'])
            if self.cfg.get('probe'):
                report['plan'], report['skipped_probe_profiles'] = probe_plan(phy)
                report['plan_source'] = 'lab_probe_after_scan'
            print(f"6 GHz enabled channels after scan: {len(report['six_ghz_enabled_after_scan'])}", flush=True)
            self.save(report)
            if capture:
                print(f"Capture plan: {len(report['plan'])} receive profiles; monitor budget {self.cfg['monitor_seconds']} seconds", flush=True)
                self.monitor(report)
            report['status'] = 'completed'
        except BaseException as exc:
            report['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            report['error'] = str(exc) or type(exc).__name__
            raise
        finally:
            # Keep post-run evidence of firmware errors and final interface state.
            for name, args in [('iw_dev_after', ['iw', 'dev']),
                               ('kernel_log_after', ['journalctl', '-k', '-n', '250', '--no-pager'])]:
                r = self.command(args)
                (self.session / (name + '.txt')).write_text(r['stdout'] + r['stderr'])
            report['end_utc'] = utc()
            report['unvisited_profiles'] = len(report.get('plan', [])) - report['attempted_profiles']
            # Include a capture interrupted before its per-profile record was completed.
            report['pcap_files'] = [pcap_info(p) for p in sorted(self.session.glob('*.pcap*'))]
            report['saved_packets'] = sum(f['packets'] for f in report['pcap_files'])
            report['radiotap_headers'] = sum(f['radiotap_headers'] for f in report['pcap_files'])
            report['invalid_radiotap_records'] = sum(f['invalid_radiotap_records'] for f in report['pcap_files'])
            report['sweep_complete'] = bool(report.get('plan')) and report['unvisited_profiles'] == 0
            if report['status'] == 'completed':
                if not report.get('legacy_scan', {}).get('ok'):
                    report['status'] = 'scan_failed'
                if capture and report['saved_packets'] == 0:
                    report['status'] = 'no_packets'
                elif capture and report['invalid_radiotap_records']:
                    report['status'] = 'invalid_capture_records'
                elif capture and not report['sweep_complete']:
                    report['status'] = 'partial'
                if capture and report.get('restore_ok') is False:
                    report['status'] = 'restore_failed'
            self.save(report)
            (root / 'LATEST.txt').write_text(str(self.session) + '\n')
            print(f"Capture folder: {self.session}\nStatus: {report['status']}; saved records: {report['saved_packets']}; Radiotap headers: {report['radiotap_headers']}; invalid records: {report['invalid_radiotap_records']}", flush=True)
        return bool(report['legacy_scan']['ok'] and
                    (not capture or (report['radiotap_headers'] > 0 and not report['invalid_radiotap_records'] and report.get('restore_ok', False))))


def main(cfg, mode):
    os.umask(0o077)
    if mode not in ('once', 'loop', 'scan-only', 'pack', 'probe'):
        raise ValueError('Usage: scan_wifi.sh [once|loop|scan-only|pack|probe]')
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo/root, as required for scan and monitor operations')
    if mode == 'pack':
        root = cfg['root'].resolve()
        folder = Path((root / 'LATEST.txt').read_text().strip()).resolve()
        if folder.parent != root or not (folder / 'manifest.json').is_file():
            raise RuntimeError('LATEST.txt does not identify a capture session')
        if json.loads((folder / 'manifest.json').read_text())['status'] == 'running':
            raise RuntimeError('Capture is still running; wait for completion before packing')
        with tempfile.TemporaryDirectory(prefix='ax210-pack.', dir='/tmp') as temp:
            archive = Path(shutil.make_archive(str(Path(temp) / 'capture'), 'zip', folder))
            # Unique output; never overwrite a previous evidence archive.
            fd, name = tempfile.mkstemp(prefix=f'ax210_{socket.gethostname()}_', suffix='.zip', dir='/tmp')
            os.close(fd)
            try:
                shutil.move(archive, name)
                os.chmod(name, 0o600)
                if os.environ.get('SUDO_UID'):
                    os.chown(name, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
            except BaseException:
                Path(name).unlink(missing_ok=True)
                raise
        print(f'Results archive: {name}', flush=True)
        return 0
    for name in ('iw', 'ip', 'tcpdump'):
        if not shutil.which(name):
            raise RuntimeError(f'Missing {name}; on Ubuntu install iw iproute2 tcpdump')
    lockpath = Path('/opt/_RunScanner/.cache/scanner-poller.lock')
    lockpath.parent.mkdir(parents=True, exist_ok=True)
    with lockpath.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('scanner-poller is already running; stop it before this diagnostic run')
        if mode == 'probe':
            cfg = {**cfg, 'probe': True, 'dwell_seconds': 20, 'monitor_seconds': 120}
        collector = Collector(cfg)
        while True:
            ok = collector.once(capture=mode != 'scan-only')
            if mode != 'loop':
                return 0 if ok else 1
            # Retain the old post-scan sleep; diagnostic capture extends each cycle.
            time.sleep(60)


if __name__ == '__main__':
    CONFIG = {
        'iface': os.environ.get('IFACE', ''),
        'root': Path(os.environ.get('CAPTURE_ROOT', '/opt/_RunScanner/captures/ax210')),
        'parser': Path('/opt/_RunScanner/parse_iw.py'),
        'csv': Path('/tmp/latest_scan.csv'), 'json': Path('/tmp/latest_scan.json'),
        'scan_timeout': int(os.environ.get('SCAN_TIMEOUT_SEC', '30')),
        'dwell_seconds': 5, 'monitor_seconds': 180,
        'min_free_mb': 1024, 'session_mb': 256,
        # Optional exact receive profiles: (primary MHz, width MHz, center MHz).
        # Empty means discover from scan results, then try wider candidate settings.
        'plan': [],
    }
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if CONFIG['scan_timeout'] <= 0:
            raise ValueError('SCAN_TIMEOUT_SEC must be positive')
        sys.exit(main(CONFIG, sys.argv[1] if len(sys.argv) > 1 else 'once'))
    except KeyboardInterrupt:
        print('Capture interrupted; see manifest.json for restoration status.', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
