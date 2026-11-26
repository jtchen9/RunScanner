import contextlib
import csv
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_power as a
import transmit_power as tx
from common import power_args
from common import pack
from vendor.capture_wifi import Collector
from vendor.radiotap_decode import decode, LAYOUT

MAC = '02:00:00:00:00:01'
AP = '02:00:00:00:00:02'
LINK = {'transmitter_mac': MAC, 'ap_bssid': AP, 'primary_mhz': 5180,
        'width_mhz': 80, 'center_mhz': 5210, 'iw_reported_txpower_dbm': 20}


def packet(rssi0=-60, rssi1=-65, sender=MAC, retry=False, ref=0, nss=1):
    fields = [(1, b'\x10'), (3, struct.pack('<HH', 5180, 0)), (5, struct.pack('b', max(rssi0, rssi1)))]
    if ref:
        fields.append((20, struct.pack('<IHH', ref, 0, 0)))
    fields.append((21, struct.pack('<HBB', 101, 0, 4) + bytes([32+nss, 0, 0, 0, 0, 0, 0, 0])))
    word0 = sum(1 << k for k, _ in fields) | (1 << 29) | (1 << 31)
    words = [word0, (1 << 5) | (1 << 11) | (1 << 29) | (1 << 31), (1 << 5) | (1 << 11)]
    rt = bytearray(struct.pack('<BBHIII', 0, 0, 0, *words))
    fields += [(5, struct.pack('b', rssi0)), (11, b'\0'), (5, struct.pack('b', rssi1)), (11, b'\1')]
    for index, data in fields:
        align, size = LAYOUT[index]
        rt.extend(b'\0' * ((-len(rt)) % align))
        assert len(data) == size
        rt.extend(data)
    struct.pack_into('<H', rt, 2, len(rt))
    mac = struct.pack('<HH', 0x188 | (0x800 if retry else 0), 0)
    mac += bytes.fromhex(AP.replace(':', '')) + bytes.fromhex(sender.replace(':', ''))
    mac += bytes.fromhex(AP.replace(':', '')) + b'\0\0\0\0' + b'test payload'
    return bytes(rt) + mac + struct.pack('<I', zlib.crc32(mac) & 0xffffffff)


def pcap(items):
    data = struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 262144, 127)
    for timestamp, p in items:
        sec = int(timestamp)
        data += struct.pack('<IIII', sec, round((timestamp-sec)*1e6), len(p), len(p)) + p
    return data


def archives(root, synced=True, offset=0):
    clocks = {'clock_before': {'epoch': 100, 'monotonic': 10, 'ntp_synchronized': synced},
              'clock_after': {'epoch': 130, 'monotonic': 40, 'ntp_synchronized': synced}}
    steps = [{'step': i, 'status': 'completed', 'start_epoch': 100+10*i, 'end_epoch': 110+10*i,
              'requested_dbm': 5+5*i, 'mode': 'limit', 'iw_reported_txpower_dbm': 5+5*i} for i in range(2)]
    sender = {'role': 'tx', 'status': 'completed', 'initial_link': LINK, 'steps': steps, **clocks}
    receiver = {'role': 'rx', 'status': 'completed', 'plan': [LINK], **clocks}
    items = []
    for i in range(2):
        for j in range(25):
            t = 102 + 10*i + j*0.05 + offset
            items.extend([(t, packet(-60+5*i, -65+5*i, ref=100+100*i+j)),
                          (t+0.001, packet(-60+5*i, -65+5*i, ref=100+100*i+j))])
    items += [(103+offset, packet(sender='02:00:00:00:00:03')),
              (103+offset, packet(retry=True)), (110+offset, packet())]
    for filename, manifest, contents in [('tx.zip', sender, {}), ('rx.zip', receiver, {'test.pcap': pcap(items)})]:
        with zipfile.ZipFile(root / filename, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            for name, data in contents.items():
                z.writestr(name, data)
    return {'tx_zip': root / 'tx.zip', 'rx_zip': root / 'rx.zip', 'output_dir': root / 'out',
            'rx_minus_tx_seconds': offset, 'clock_offset_verified': False,
            'guard_seconds': 1, 'minimum_samples': 20}


class ProbeTests(unittest.TestCase):
    def test_export_stays_inside_results(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder) / 'results'
            session = root / 'tx_test'
            session.mkdir(parents=True)
            (session / 'manifest.json').write_text('{}')
            archive = pack(session)
            self.assertEqual(archive.parent, root)
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(z.namelist(), ['manifest.json'])

    def test_normal_package_import_from_outside_is_blocked(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([sys.executable, '-c', 'import TestAX210.receive_power'],
                                cwd=root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Production code must not import TestAX210', result.stderr)

    def test_per_chain_decoder(self):
        row = decode(packet(-60, -65), LINK)
        self.assertEqual((row['chain0_dbm'], row['chain1_dbm']), (-60, -65))
        self.assertTrue(row['fcs_crc_matches'])
        self.assertEqual(row['nss'], 1)
        self.assertEqual(row['bandwidth_mhz'], 80)

    def test_end_to_end_delta_and_ampdu_weighting(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            cfg = archives(Path(folder), offset=2)
            result = a.analyze(cfg)
            self.assertEqual(result['counts']['accepted_samples'], 50)
            self.assertEqual(result['counts']['same_ampdu'], 50)
            self.assertEqual(result['counts']['other_transmitter'], 1)
            self.assertEqual(result['counts']['retry'], 1)
            with (cfg['output_dir'] / 'power_response.csv').open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertAlmostEqual(float(rows[1]['chain0_delta_db']), 5)
            self.assertAlmostEqual(float(rows[1]['chain1_delta_db']), 5)
            self.assertEqual(rows[1]['comparison_status'], 'matched_reported_phy')
            ET.parse(cfg['output_dir'] / 'power_response.svg')

    def test_no_unverified_clock_alignment(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = archives(Path(folder), synced=False)
            with self.assertRaisesRegex(ValueError, 'clocks'):
                a.analyze(cfg)

    def test_no_max_rssi_substitution(self):
        row = decode(packet(), LINK)
        row['chain1_dbm'] = None
        self.assertEqual(a.rejection(row, False, LINK), 'missing_chain_rssi')

    def test_ap_forwarded_frame_rejected(self):
        row = decode(packet(), LINK)
        row.update(transmitter=AP, receiver=MAC, to_ds=False, from_ds=True)
        self.assertEqual(a.rejection(row, False, LINK), 'other_transmitter')

    def test_unknown_is_not_false(self):
        row = decode(packet(), LINK)
        other = dict(row, beamformed=None)
        self.assertNotEqual(a.phy_key(row), a.phy_key(other))
        self.assertTrue(a.complete_phy(row))
        self.assertFalse(a.complete_phy(other))

    def test_nss_separates_comparisons(self):
        self.assertNotEqual(a.phy_key(decode(packet(nss=1), LINK)), a.phy_key(decode(packet(nss=2), LINK)))

    def test_linear_average_not_db_average(self):
        self.assertAlmostEqual(a.mean_power_dbm([-60, -50]), -52.5963731051)

    def test_stable_window_boundaries(self):
        steps = [{'status': 'completed', 'start_epoch': 100, 'end_epoch': 110}]
        self.assertIsNone(a.phase_at(100.5, steps, 1))
        self.assertIsNotNone(a.phase_at(101, steps, 1))
        self.assertIsNone(a.phase_at(109, steps, 1))

    def test_truncated_pcap_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            list(a.records(io.BytesIO(pcap([(100, packet())])[:-3])))

    def test_requested_power_units_and_modes(self):
        self.assertEqual(power_args('w', 'limit', 15.5)[-1], '1550')
        self.assertEqual(power_args('w', 'auto', None)[-1], 'auto')
        with self.assertRaises(ValueError):
            power_args('w', 'fixed', 99)

    def test_restore_after_interruption(self):
        from unittest.mock import Mock
        cfg = {'powers_dbm': [5], 'mode': 'limit', 'settle_seconds': 2,
               'restore_mode': 'auto', 'restore_dbm': None}
        report = {'iface': 'mock', 'initial_link': LINK, 'steps': []}
        log = Mock()
        log.command.return_value = {'rc': 0, 'stdout': '', 'stderr': ''}
        with patch.object(tx, 'link_state', return_value=LINK), patch.object(tx.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                tx.sweep(cfg, log, report, lambda: None)
        self.assertTrue(report['restore_ok'])
        self.assertIn((['iw', 'dev', 'mock', 'set', 'txpower', 'auto'],), [x.args for x in log.command.call_args_list])

    def test_receiver_refuses_connected_adapter_before_changes(self):
        collector = object.__new__(Collector)
        collector.iface = 'mock'
        with patch.object(collector, 'checked', side_effect=['type managed\n', f'Connected to {AP}']), patch.object(collector, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'disconnected dedicated'):
                collector.monitor({'plan': [LINK]})
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
