#!/usr/bin/env python3
"""Step AX210 power requests while sending ordinary ICMP traffic to an AP.

Edit settings under __main__. Does not associate, disconnect, retune, or stop services.
"""
import json
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
from common import (Log, setup, scanner_lock, session, clock_state, link_state,
                    same_link, power_args, pack, gateway, choose_iface,
                    save_json, stop_process)


def sweep(cfg, log, report, save):
    iface = report['iface']
    initial = report['initial_link']
    attempted = False
    try:
        for number, dbm in enumerate(cfg['powers_dbm']):
            before = link_state(log, iface)
            if not same_link(initial, before):
                raise RuntimeError('Sender association or channel changed; sweep stopped.')
            phase = {'step': number, 'mode': cfg['mode'], 'requested_dbm': dbm,
                     'command_epoch': time.time(), 'status': 'setting'}
            report['steps'].append(phase)
            save()
            attempted = True  # Also restore after a rejected/partly applied command.
            result = log.command(power_args(iface, cfg['mode'], dbm))
            phase['set_result'] = result
            if result['rc']:
                phase['status'] = 'request_rejected'
                save()
                continue
            time.sleep(cfg['settle_seconds'])
            phase['link_before'] = link_state(log, iface)
            if not same_link(initial, phase['link_before']):
                raise RuntimeError('Sender association or channel changed after power request.')
            phase['iw_reported_txpower_dbm'] = phase['link_before']['iw_reported_txpower_dbm']
            args = ['ping', '-4', '-n', '-I', iface, '-i', str(cfg['ping_interval_seconds']),
                    '-s', str(cfg['ping_payload_bytes']), report['target_ipv4']]
            phase['traffic_command'] = args
            print(f"Step {number + 1}/{len(cfg['powers_dbm'])}: {cfg['mode']} {dbm} dBm; "
                  f"iw reports {phase['iw_reported_txpower_dbm']} dBm", flush=True)
            with (log.folder / f'step_{number:02d}_ping.txt').open('wb') as output:
                p = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    start_mono = time.monotonic()
                    phase.update(start_epoch=time.time(), status='traffic_running')
                    save()
                    while time.monotonic() - start_mono < cfg['hold_seconds']:
                        if p.poll() is not None:
                            raise RuntimeError('Traffic generator stopped early; inspect ping log.')
                        time.sleep(0.2)
                    phase['end_epoch'] = time.time()
                    phase['elapsed_monotonic_seconds'] = time.monotonic() - start_mono
                    phase['clock_drift_seconds'] = (phase['end_epoch'] - phase['start_epoch']
                                                    - phase['elapsed_monotonic_seconds'])
                finally:
                    stop_process(p)
                    phase['traffic_exit_code'] = p.returncode
            phase['link_after'] = link_state(log, iface)
            phase['status'] = 'completed'
            if not same_link(initial, phase['link_after']):
                phase['status'] = 'link_changed'
            elif abs(phase['clock_drift_seconds']) > 0.25:
                phase['status'] = 'clock_changed'
            save()
            if phase['status'] != 'completed':
                raise RuntimeError(f"Invalid phase: {phase['status']}")
    finally:
        if attempted:
            handlers = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)}
            try:
                r = log.command(power_args(iface, cfg['restore_mode'], cfg['restore_dbm']))
                report['restore_result'] = r
                report['restore_ok'] = r['rc'] == 0
                report['iw_info_after_restore'] = log.command(['iw', 'dev', iface, 'info'])
                save()
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)


def main(cfg):
    setup()
    if cfg['mode'] not in ('limit', 'fixed') or not cfg['powers_dbm']:
        raise ValueError('Select limit or fixed, and at least one requested power.')
    for value in cfg['powers_dbm']:
        power_args('unused', cfg['mode'], value)
    power_args('unused', cfg['restore_mode'], cfg['restore_dbm'])
    if not 6 <= cfg['hold_seconds'] <= 120 or not 1 <= cfg['settle_seconds'] <= 30:
        raise ValueError('Use hold 6–120 s and settling 1–30 s.')
    if not 0.02 <= cfg['ping_interval_seconds'] <= 1 or not 64 <= cfg['ping_payload_bytes'] <= 1400:
        raise ValueError('Use ping interval 0.02–1 s, payload 64–1400 bytes.')
    if not shutil.which('ping'):
        raise RuntimeError('Install iputils-ping first.')
    with scanner_lock():
        iface = choose_iface(cfg['iface'])
        folder = session(cfg['root'], 'tx')
        log = Log(folder)
        report = {'schema': 'ax210-power-probe-1', 'role': 'tx', 'hostname': socket.gethostname(),
                  'iface': iface, 'settings': {k: str(v) if isinstance(v, Path) else v for k, v in cfg.items()},
                  'status': 'running', 'steps': [], 'restore_ok': None}
        def save():
            save_json(folder / 'manifest.json', report)
        save()
        try:
            report['clock_before'] = clock_state(log)
            report['initial_link'] = link_state(log, iface)
            report['target_ipv4'] = gateway(log, iface, cfg['target_ipv4'])
            print('Sender/channel: ' + json.dumps({k: v for k, v in report['initial_link'].items()
                                                if k not in ('info', 'link')}), flush=True)
            print(f"Restore policy: {cfg['restore_mode']} {cfg['restore_dbm']}; this is explicit, not auto-detected.", flush=True)
            log.checked(['ping', '-4', '-n', '-I', iface, '-c', '2', '-W', '2', report['target_ipv4']], timeout=8)
            sweep(cfg, log, report, save)
            report['status'] = 'completed' if all(s['status'] == 'completed' for s in report['steps']) else 'partial'
        except BaseException as exc:
            report.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', error=str(exc))
            raise
        finally:
            report['clock_after'] = clock_state(log)
            if report['restore_ok'] is False:
                report['status'] = 'restore_failed'
            save()
            pack(folder)
        return report['status'] == 'completed'


if __name__ == '__main__':
    CONFIG = {
        'iface': '',  # Auto-select the sole managed Intel interface, excluding wlan0.
        'root': Path(__file__).resolve().parent / 'results',
        'target_ipv4': '',  # Empty: use this Wi-Fi interface's IPv4 gateway.
        'mode': 'fixed',  # Repeat later with 'limit'/'fixed'; rejection is recorded, never silently substituted.
        'powers_dbm': [5, 10, 15, 20, 15, 10, 5],
        'settle_seconds': 2,
        'hold_seconds': 10,
        'ping_interval_seconds': 0.02,
        'ping_payload_bytes': 1000,
        # iw does not expose the previous auto/limit/fixed policy. Choose restoration explicitly.
        'restore_mode': 'auto',
        'restore_dbm': None,
    }
    try:
        raise SystemExit(0 if main(CONFIG) else 1)
    except KeyboardInterrupt:
        print('Interrupted. Inspect restore_ok in the archive.'); raise SystemExit(130)
    except Exception as exc:
        print(f'ERROR: {exc}'); raise SystemExit(1)
