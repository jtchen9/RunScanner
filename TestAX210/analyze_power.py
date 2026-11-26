#!/usr/bin/env python3
"""Offline TX/RX archive comparison. Standard library only; no radio operations."""
import collections
import csv
import datetime as dt
import html
import json
import math
from pathlib import Path
import statistics
import struct
import zipfile
from vendor.radiotap_decode import decode

PHY_KEYS = ('phy', 'legacy_rate_mbps', 'mcs', 'nss', 'bandwidth_mhz', 'ru_tones',
            'he_format', 'gi_us', 'beamformed', 'stbc', 'dcm', 'coding_user0', 'subtype')


def records(stream):
    header = stream.read(24)
    fmt = {b'\xd4\xc3\xb2\xa1': ('<', 1e6), b'\xa1\xb2\xc3\xd4': ('>', 1e6),
           b'\x4d\x3c\xb2\xa1': ('<', 1e9), b'\xa1\xb2\x3c\x4d': ('>', 1e9)}.get(header[:4])
    if len(header) != 24 or not fmt:
        raise ValueError('Invalid PCAP header')
    endian, scale = fmt
    if struct.unpack_from(endian + 'I', header, 20)[0] & 65535 != 127:
        raise ValueError('Requires Radiotap PCAP, linktype 127')
    while h := stream.read(16):
        if len(h) != 16:
            raise ValueError('Incomplete PCAP record header')
        sec, frac, n, original = struct.unpack(endian + 'IIII', h)
        if n > 262144:
            raise ValueError('Oversized PCAP record')
        packet = stream.read(n)
        if len(packet) != n:
            raise ValueError('Incomplete PCAP record data')
        yield sec + frac / scale, packet, n < original


def phase_at(epoch, steps, guard):
    matches = [s for s in steps if s['status'] == 'completed'
               and s['start_epoch'] + guard <= epoch < s['end_epoch'] - guard]
    if len(matches) > 1:
        raise ValueError('Overlapping TX phase timestamps')
    return matches[0] if matches else None


def rejection(row, truncated, tx):
    if row.get('transmitter') != tx['transmitter_mac']:
        return 'other_transmitter'
    if row.get('phy_only') or row.get('mac_kind') != 'data':
        return 'not_data'
    if row.get('receiver') != tx['ap_bssid'] or not row.get('to_ds') or row.get('from_ds'):
        return 'not_uplink_to_selected_ap'
    if truncated or row.get('bad_fcs_flag') or row.get('fcs_crc_matches') is not True:
        return 'unverified_or_bad_fcs'
    if row.get('reported_frequency_mhz') != tx['primary_mhz']:
        return 'wrong_or_missing_frequency'
    if row.get('retry'):
        return 'retry'
    if row.get('chain0_dbm') is None or row.get('chain1_dbm') is None:
        return 'missing_chain_rssi'
    if row.get('subtype') not in (0, 8):
        return 'non_payload_data_subtype'
    return ''


def phy_key(row):
    return tuple(row.get(k) for k in PHY_KEYS)


def complete_phy(row):
    if row.get('phy') == 'legacy':
        return False  # Legacy rate alone does not establish occupied bandwidth or TX mapping.
    required = ('mcs', 'nss', 'bandwidth_mhz', 'gi_us', 'beamformed', 'stbc')
    return all(row.get(k) is not None for k in required)


def mean_power_dbm(values):
    return 10 * math.log10(statistics.mean(10 ** (v / 10) for v in values))


def write_csv(path, rows, columns=None):
    columns = columns or sorted({k for r in rows for k in r})
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def response_svg(path, summaries):
    # At most eight profiles, selected by total sample count; full tables retain every profile.
    counts = collections.Counter()
    for r in summaries:
        counts[r['profile_id']] += r['samples']
    selected = [k for k, _ in counts.most_common(8)]
    height = max(1, len(selected)) * 280 + 70
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="sans-serif" font-size="14" fill="#172435">',
           '<text x="30" y="28" font-size="20">AX210 power response — measured RSSI, grouped by reported PHY</text>',
           '<text x="30" y="50">Blue: RX chain 0 • Orange: RX chain 1 • x: requested dBm • y: mean received dBm (linear-power mean)</text>']
    if not selected:
        svg.append('<text x="30" y="100">No usable, time-aligned sender frames. See analysis.json for exclusions.</text>')
    for panel, key in enumerate(selected):
        rows = sorted([r for r in summaries if r['profile_id'] == key], key=lambda r: r['step'])
        top = 90 + panel * 280
        label = f"{key}: " + ', '.join(f'{k}={rows[0].get(k)}' for k in ('phy', 'mcs', 'nss', 'bandwidth_mhz', 'beamformed'))
        svg.append(f'<text x="30" y="{top}">{html.escape(label)}</text>')
        xs = [r['requested_dbm'] for r in rows]
        ys = [r[f'chain{i}_mean_dbm'] for r in rows for i in (0, 1)]
        xmin, xmax = min(xs) - 1, max(xs) + 1
        ymin, ymax = math.floor(min(ys) - 2), math.ceil(max(ys) + 2)
        def x(v): return 90 + 820 * (v - xmin) / (xmax - xmin)
        def y(v): return top + 210 - 170 * (v - ymin) / (ymax - ymin)
        for v in range(ymin, ymax + 1, max(1, math.ceil((ymax - ymin) / 5))):
            svg.append(f'<path d="M90 {y(v)} H910" stroke="#dde3ea"/><text x="45" y="{y(v)+5}">{v}</text>')
        for v in sorted(set(xs)):
            svg.append(f'<text x="{x(v)-8}" y="{top+235}">{v:g}</text>')
        for chain, color in ((0, '#1766bd'), (1, '#cc6500')):
            for r in rows:
                xx, yy = x(r['requested_dbm']), y(r[f'chain{chain}_mean_dbm'])
                title = f"Step {r['step']}: n={r['samples']}, chain {chain}; {r['comparison_status']}"
                svg.append(f'<circle cx="{xx}" cy="{yy}" r="4" fill="{color}"><title>{html.escape(title)}</title></circle>')
                svg.append(f'<text x="{xx+6}" y="{yy-4}" fill="{color}" font-size="11">{r["step"]}</text>')
    svg.append('</g></svg>')
    path.write_text('\n'.join(svg), encoding='utf-8')


def analyze(cfg):
    out = Path(cfg['output_dir'])
    out.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(cfg['tx_zip']) as z:
        tx = json.loads(z.read('manifest.json'))
    if tx.get('role') != 'tx':
        raise ValueError('TX_ZIP must be a transmit_power archive')
    with zipfile.ZipFile(cfg['rx_zip']) as z:
        rx = json.loads(z.read('manifest.json'))
        if rx.get('role') != 'rx':
            raise ValueError('RX_ZIP must be a receive_power archive')
        expected = tx['initial_link']
        if any(rx['plan'][0][k] != expected[k] for k in ('primary_mhz', 'width_mhz', 'center_mhz')):
            raise ValueError('Receiver tuning does not match sender channel/width/center')
        synced = all(m.get(k, {}).get('ntp_synchronized') for m in (tx, rx)
                     for k in ('clock_before', 'clock_after'))
        for manifest in (tx, rx):
            a, b = manifest['clock_before'], manifest['clock_after']
            drift = (b['epoch'] - a['epoch']) - (b['monotonic'] - a['monotonic'])
            if abs(drift) > 0.25:
                raise ValueError('Clock changed during run; repeat with stable synchronized clocks.')
        if not synced and not cfg['clock_offset_verified']:
            raise ValueError('Both clocks must report synchronized, or supply and verify RX_MINUS_TX_SECONDS.')
        guard = cfg['guard_seconds']
        if guard < 1 or abs(cfg['rx_minus_tx_seconds']) > 3600:
            raise ValueError('Use at least one second boundary guard and a clock offset within one hour.')
        counts = collections.Counter()
        frames, samples = [], collections.defaultdict(list)
        ampdu_seen = {}
        files = sorted(n for n in z.namelist() if '.pcap' in n and '/' not in n)
        for name in files:
            with z.open(name) as stream:
                for index, (timestamp, packet, truncated) in enumerate(records(stream), 1):
                    counts['all_records'] += 1
                    try:
                        row = decode(packet, rx['plan'][0])
                    except (ValueError, IndexError, struct.error):
                        counts['decode_error'] += 1
                        continue
                    reason = rejection(row, truncated, expected)
                    if reason == 'other_transmitter':
                        counts[reason] += 1
                        continue
                    phase = phase_at(timestamp - cfg['rx_minus_tx_seconds'], tx['steps'], guard)
                    reason = reason or ('' if phase else 'outside_stable_phase')
                    row.update(file=name, record=index, timestamp_epoch=timestamp,
                               step=phase['step'] if phase else None, exclusion=reason)
                    frames.append(row)
                    if reason:
                        counts[reason] += 1
                        continue
                    key = (phase['step'], phy_key(row))
                    ref = row.get('ampdu_reference')
                    if ref:
                        akey = (key, ref)
                        previous = ampdu_seen.get(akey)
                        ampdu_seen[akey] = timestamp
                        if previous is not None and 0 <= timestamp - previous < 0.01:
                            counts['same_ampdu'] += 1
                            row['exclusion'] = 'same_ampdu'
                            continue
                    samples[key].append(row)
                    counts['accepted_samples'] += 1
    summaries, baseline, profile_ids = [], {}, {}
    steps = {s['step']: s for s in tx['steps']}
    for (step, key), group in sorted(samples.items(), key=lambda item: (item[0][0], repr(item[0][1]))):
        profile_ids.setdefault(key, f'P{len(profile_ids)+1}')
        r = dict(zip(PHY_KEYS, key))
        r.update(step=step, profile_id=profile_ids[key], requested_dbm=steps[step]['requested_dbm'],
                 mode=steps[step]['mode'], iw_reported_txpower_dbm=steps[step].get('iw_reported_txpower_dbm'),
                 samples=len(group), metadata_complete=complete_phy(r))
        for chain in (0, 1):
            values = [v[f'chain{chain}_dbm'] for v in group]
            r.update({f'chain{chain}_mean_dbm': mean_power_dbm(values),
                      f'chain{chain}_median_dbm': statistics.median(values),
                      f'chain{chain}_stdev_db': statistics.stdev(values) if len(values) > 1 else None})
        if len(group) < cfg['minimum_samples']:
            r['comparison_status'] = 'insufficient_samples'
        elif key not in baseline:
            baseline[key] = r
            r['comparison_status'] = 'reference'
        else:
            base = baseline[key]
            r.update(reference_step=base['step'], requested_delta_db=r['requested_dbm'] - base['requested_dbm'],
                     comparison_status='matched_reported_phy' if r['metadata_complete'] else 'incomplete_phy_exploratory')
            for chain in (0, 1):
                r[f'chain{chain}_delta_db'] = r[f'chain{chain}_mean_dbm'] - base[f'chain{chain}_mean_dbm']
        summaries.append(r)
    write_csv(out / 'sender_frames.csv', frames)
    write_csv(out / 'power_response.csv', summaries)
    response_svg(out / 'power_response.svg', summaries)
    step_counts = {str(s['step']): sum(r['samples'] for r in summaries if r['step'] == s['step']) for s in tx['steps']}
    result = {'schema': 'ax210-power-analysis-1', 'transmitter': expected,
              'counts': dict(counts), 'samples_per_step': step_counts,
              'tx_status': tx['status'], 'rx_status': rx['status'],
              'tx_restore_ok': tx.get('restore_ok'), 'rx_restore_ok': rx.get('restore_ok'),
              'clock_alignment': {'ntp_reported_synced': synced, 'manual_offset_verified': cfg['clock_offset_verified'],
                                  'rx_minus_tx_seconds': cfg['rx_minus_tx_seconds'], 'guard_seconds': guard},
              'assumptions': ['Robot receive branches modeled independent for future capacity estimates.',
                              'This tool measures power response; it does not estimate eigenvalues or capacity.',
                              'iw reported/requested power is not calibrated per-frame or per-chain output.',
                              'NTP synchronization is not a measured clock-offset bound.',
                              'Matched reported PHY does not prove identical precoding or power allocation.',
                              'Capture includes all sender uplink data; encrypted payload is not identified as ping.',
                              'Samples exclude retries/bad FCS and may be biased near receiver sensitivity.',
                              'One sample per recognized AMPDU; samples need not be statistically independent.']}
    (out / 'analysis.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    print(f'Results: {out.resolve()}')
    return result


if __name__ == '__main__':
    BASE = Path(__file__).resolve().parent
    TX_ZIP = BASE / 'input' / 'tx_results.zip'
    RX_ZIP = BASE / 'input' / 'rx_results.zip'
    OUTPUT_DIR = BASE / 'results' / dt.datetime.now().strftime('analysis_%Y%m%d_%H%M%S_%f')
    RX_MINUS_TX_SECONDS = 0.0  # RX clock minus TX clock. Subtracted from RX timestamps.
    CLOCK_OFFSET_VERIFIED = False  # True only if offset was independently checked.
    analyze({'tx_zip': TX_ZIP, 'rx_zip': RX_ZIP, 'output_dir': OUTPUT_DIR,
             'rx_minus_tx_seconds': RX_MINUS_TX_SECONDS, 'clock_offset_verified': CLOCK_OFFSET_VERIFIED,
             'guard_seconds': 1.0, 'minimum_samples': 20})
