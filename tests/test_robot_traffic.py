import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
config = types.ModuleType('config')
config.TRAFFIC_STATE_DIR = Path('/unused')
config.TRAFFIC_IPERF3_BIN = '/usr/bin/iperf3'
config.get_ax210_iface = lambda: 'ax210'
config.local_ts = lambda: '2026-10-22-16:00:00'
status = types.ModuleType('robot_status_report')
status.get_wifi_status = lambda iface: {'iface': iface, 'connected': True}
sys.modules['config'] = config
sys.modules['robot_status_report'] = status
spec = importlib.util.spec_from_file_location('robot_handlers_traffic', ROOT / 'robot_handlers_traffic.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ARGS = dict(session_id='S1', server_host='example.test', server_port=5201,
            duration_sec=180, report_interval_sec=60, reverse=True,
            protocol='udp', ac='bk', bitrate='5M', packet_size=1200)

class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        m.TRAFFIC_STATE_DIR = Path(self.tmp.name)
    def tearDown(self):
        self.assertFalse(m._RUNNING)
        self.tmp.cleanup()
    def wait_result(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            records = m.pending_records()
            results = [r for r in records if r['kind'] == 'result']
            if results and not m._RUNNING:
                return results[0]
            time.sleep(.01)
        self.fail('no completed result')
    def fake_prepare(self, body):
        return ([sys.executable, '-c', body], {'duration_sec': 1, 'wifi_start': {'iface': 'ax210'}, 'reverse': True})
    def test_directions_and_ax210_binding(self):
        addresses = json.dumps([{'addr_info': [{'family': 'inet', 'scope': 'global', 'local': '192.0.2.3'}]}])
        with patch.object(m.subprocess, 'check_output', return_value=addresses):
            upload, meta = m._prepare(ARGS)
            download, _ = m._prepare({**ARGS, 'reverse': False})
        self.assertNotIn('-R', upload)
        self.assertIn('-R', download)
        self.assertEqual(upload[upload.index('-B') + 1], '192.0.2.3')
        self.assertFalse(meta['iperf_reverse'])
    def test_nonblocking_duplicate_and_durable_result(self):
        command = self.fake_prepare('import time; time.sleep(.2); print(\'{"end": {}}\')')
        with patch.object(m, '_prepare', return_value=command):
            before = time.monotonic()
            self.assertTrue(m.start_session('alpha', ARGS, 'cmd1')[0])
            self.assertLess(time.monotonic() - before, .15)
            self.assertTrue(m.start_session('alpha', ARGS, 'cmd1')[0])
            self.assertFalse(m.start_session('alpha', ARGS, 'cmd2')[0])
            result = self.wait_result()
            self.assertTrue(m.start_session('alpha', ARGS, 'cmd1')[0])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len([r for r in m.pending_records() if r['kind'] == 'result']), 1)
        self.assertEqual(m.pending_records(), m.pending_records())
        m.acknowledge_records([result['record_id'], '../bad'])
        self.assertFalse(any(r['kind'] == 'result' for r in m.pending_records()))
    def test_json_error_is_failure(self):
        with patch.object(m, '_prepare', return_value=self.fake_prepare('print(\'{"error":"busy"}\')')):
            self.assertTrue(m.start_session('alpha', ARGS, 'cmd3')[0])
            self.assertEqual(self.wait_result()['status'], 'error')
    def test_exit_failure_even_with_valid_json(self):
        with patch.object(m, '_prepare', return_value=self.fake_prepare('import sys; print(\'{"end":{}}\'); sys.exit(1)')):
            m.start_session('alpha', ARGS, 'cmd4')
            self.assertEqual(self.wait_result()['status'], 'error')
    def test_stop_and_other_session_untouched(self):
        with patch.object(m, '_prepare', return_value=self.fake_prepare('import time; time.sleep(20)')):
            m.start_session('alpha', ARGS, 'cmd5')
            m.start_session('bravo', ARGS, 'cmd6')
            self.assertTrue(m.stop_session('alpha', ARGS)[0])
            self.assertIsNone(m._RUNNING[('bravo', 'S1')]['proc'].poll())
            self.assertTrue(m.stop_session('bravo', ARGS)[0])
            self.assertEqual(self.wait_result()['status'], 'stopped')
    def test_launch_failure_recorded(self):
        command, metadata = self.fake_prepare('')
        with patch.object(m, '_prepare', return_value=(['/no/such/iperf3'], metadata)):
            self.assertFalse(m.start_session('alpha', ARGS, 'cmd7')[0])
        self.assertEqual(self.wait_result()['status'], 'error')
    def test_invalid_arguments_fail_before_process(self):
        with patch.object(m.subprocess, 'Popen') as popen:
            self.assertFalse(m.start_session('alpha', {**ARGS, 'reverse': 'false'}, 'cmd8')[0])
            popen.assert_not_called()
        self.assertEqual(self.wait_result()['status'], 'error')

if __name__ == '__main__':
    unittest.main()
