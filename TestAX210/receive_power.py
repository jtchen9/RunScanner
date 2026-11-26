#!/usr/bin/env python3
"""Dedicated passive listener. Edit the single channel profile under __main__."""
import json
from pathlib import Path
import re
import shutil
import socket
from common import Log, setup, scanner_lock, session, clock_state, pack
from vendor.capture_wifi import Collector, enabled_frequencies, pcap_info


class PassiveCollector(Collector):
    def command(self, args, **kwargs):
        result = super().command(args, **kwargs)
        text = result['stdout']
        if args[-1:] == ['info'] and 'dev' in args and re.search(r'type monitor', text):
            profile = self.cfg['profile']
            expected = f"center1: {profile['center_mhz']} MHz"
            if expected not in text:
                raise RuntimeError('Monitor center frequency does not match requested channel.')
        return result


def main(cfg):
    setup()
    if not shutil.which('tcpdump'):
        raise RuntimeError('Install tcpdump first.')
    profile = cfg['profile']
    if profile['width_mhz'] not in (20, 40, 80, 160) or not 10 <= cfg['monitor_seconds'] <= 600:
        raise ValueError('Width must be 20/40/80/160 MHz; duration 10–600 seconds.')
    if not 32 <= cfg['session_mb'] <= 1024 or cfg['min_free_mb'] < 256:
        raise ValueError('Use session size 32–1024 MiB and at least 256 MiB free-space reserve.')
    cfg = {**cfg, 'dwell_seconds': cfg['monitor_seconds']}
    with scanner_lock():
        collector = PassiveCollector(cfg)
        collector.session = session(cfg['root'], 'rx')
        collector.events = collector.session / 'commands.jsonl'
        log = Log(collector.session)
        report = {'schema': 'ax210-power-probe-1', 'role': 'rx', 'hostname': socket.gethostname(),
                  'iface': collector.iface, 'phy': collector.phy, 'status': 'running',
                  'captures': [], 'attempted_profiles': 0, 'plan': [profile],
                  'settings': {k: str(v) if isinstance(v, Path) else v for k, v in cfg.items()}}
        collector.save(report)
        try:
            report['clock_before'] = clock_state(log)
            if not collector.space_ok():
                raise RuntimeError('Insufficient free space for capture.')
            phy = collector.checked(['iw', 'phy', collector.phy, 'info'])
            if profile['primary_mhz'] not in enabled_frequencies(phy):
                raise RuntimeError('Requested channel is disabled. Resolve channel availability before passive capture; no scan or regulatory changes made.')
            collector.diagnostics('before')
            print(f"Listening for {cfg['monitor_seconds']} seconds on {profile}. Start transmitter after capture begins.", flush=True)
            collector.monitor(report)
            captures = report['captures']
            complete = (len(captures) == 1 and captures[0].get('tuning_verified') is True
                        and captures[0].get('capture_rc') == 0 and not captures[0].get('stop_reason'))
            report['status'] = 'completed' if complete else 'partial'
        except BaseException as exc:
            report.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', error=str(exc))
            raise
        finally:
            report['clock_after'] = clock_state(log)
            report['pcap_files'] = [pcap_info(p) for p in sorted(collector.session.glob('*.pcap*'))]
            report['saved_packets'] = sum(p['packets'] for p in report['pcap_files'])
            if report.get('restore_ok') is False:
                report['status'] = 'restore_failed'
            elif report['status'] == 'completed' and report['saved_packets'] == 0:
                report['status'] = 'no_packets'
            collector.save(report)
            print(f"Status: {report['status']}; saved records: {report['saved_packets']}", flush=True)
            pack(collector.session)
        return report['status'] == 'completed'


if __name__ == '__main__':
    CONFIG = {
        'iface': '',
        'root': Path(__file__).resolve().parent / 'results',
        # Match the SENDER's iw dev output. This is the lab's known 6 GHz profile.
        'profile': {'primary_mhz': 5180, 'width_mhz': 80, 'center_mhz': 5210},
        'monitor_seconds': 180,
        'session_mb': 256,
        'min_free_mb': 1024,
    }
    try:
        raise SystemExit(0 if main(CONFIG) else 1)
    except KeyboardInterrupt:
        print('Interrupted. Inspect restore_ok in the archive.'); raise SystemExit(130)
    except Exception as exc:
        print(f'ERROR: {exc}'); raise SystemExit(1)
