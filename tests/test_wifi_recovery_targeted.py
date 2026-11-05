"""Run from repository root: python3 -m unittest discover -s tests -p test_wifi_recovery_targeted.py"""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import robot_network_recovery as r
spec = importlib.util.spec_from_file_location('wifi_helper', Path(__file__).resolve().parents[1] / 'tools/scanner-wifi-activate.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
UID = '12345678-1234-1234-1234-123456789abc'
ROWS = [('', 'AA:BB:CC:DD:EE:%02X' % i, str(90-i*10), 'WPA3', 'test') for i in range(4)]

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.t = 0
        self.messages = []
        self.r = r.NetworkRecovery(self.messages.append, clock=lambda: self.t)
        self.r.context = UID, 'WPA3', 'test'
        self.r.cached = ROWS
        self.pause = patch.object(r.os.path, 'exists', return_value=False); self.pause.start()
        self.scan = patch.object(r, 'visible', return_value=[]); self.scan.start()
    def tearDown(self):
        patch.stopall()
    def test_four_alternatives_even_after_long_outage(self):
        with patch.object(r, 'wifi_state', return_value='disconnected'), patch.object(r, 'run', return_value=(4, '')) as run:
            self.r.failed('test')
            targets = []
            for self.t in (100, 106, 112, 118):
                self.assertTrue(self.r.failed('test'))
                targets.append(run.call_args.args[0][-1])
            self.assertEqual(targets, [row[1] for row in ROWS])
    def test_stale_activation_bounded(self):
        with patch.object(r, 'wifi_state', return_value='activating'), patch.object(r, 'run', return_value=(4, '')) as run:
            self.r.failed('test'); self.t = 5
            self.assertFalse(self.r.failed('test'))
            self.t = 29; self.assertFalse(self.r.failed('test'))
            self.t = 30; self.assertTrue(self.r.failed('test'))
            run.assert_called_once()
    def test_healthy_wifi_not_reset_for_nms_outage(self):
        with patch.object(r, 'wifi_state', return_value='connected'), patch.object(r, 'run') as run:
            self.r.failed('test'); self.t = 300
            self.assertFalse(self.r.failed('test')); run.assert_not_called()
    def test_pause_and_connection_race(self):
        self.r.failed('test'); self.t = 10
        with patch.object(r, 'wifi_state', side_effect=['disconnected', 'connected']), patch.object(r, 'run') as run:
            self.assertFalse(self.r.failed('test')); run.assert_not_called()
        with patch.object(r.os.path, 'exists', return_value=True), patch.object(r, 'run') as run:
            self.assertFalse(self.r.failed('test')); run.assert_not_called()
    def test_different_ssid_not_selected(self):
        self.r.cached = [('', ROWS[0][1], '99', 'WPA3', 'other')] + [ROWS[3]]
        with patch.object(r, 'wifi_state', return_value='disconnected'), patch.object(r, 'run', return_value=(4, '')) as run:
            self.r.failed('test'); self.t = 5; self.r.failed('test')
            self.assertEqual(run.call_args.args[0][-1], ROWS[3][1])

class HelperTests(unittest.TestCase):
    def execute(self, actual, activation_rc=0, pin_rc=0):
        calls = []
        def command(args, timeout=3):
            calls.append(args)
            if args[-1] == 'ping': return 0, 'PONG'
            if 'GENERAL.STATE' in args: return 0, '30 (disconnected)'
            if 'up' in args: return activation_rc, ''
            if 'modify' in args and args[-1]: return pin_rc, ''
            if 'bssid' in args: return 0, 'OK'
            return 0, ''
        fields = {'connection.type':'802-11-wireless', 'connection.interface-name':'wlP1p1s0', '802-11-wireless.bssid':''}
        with patch.object(h.shutil, 'which', return_value='/usr/sbin/wpa_cli'), patch.object(h, 'field', side_effect=lambda u,k: fields[k]), patch.object(h, 'run', side_effect=command), patch.object(h, 'status', return_value={'wpa_state':'COMPLETED', 'bssid':actual, 'id':'0'}):
            try:
                result = h.activate(UID, ROWS[0][1])
            except RuntimeError:
                result = 4
        return result, calls
    def test_success_releases_both_pins(self):
        rc, calls = self.execute(ROWS[0][1])
        self.assertEqual(rc, 0)
        modifications = [c for c in calls if 'modify' in c]
        self.assertEqual([c[-1] for c in modifications], [ROWS[0][1], ''])
        self.assertTrue(all('--temporary' in c for c in modifications))
        self.assertTrue(any(c[-1] == '00:00:00:00:00:00' for c in calls))
    def test_wrong_ap_is_failure(self):
        rc, calls = self.execute(ROWS[1][1])
        self.assertEqual(rc, 4)
        self.assertEqual(len([c for c in calls if 'disconnect' in c]), 2)
    def test_activation_timeout_cancels_and_restores(self):
        rc, calls = self.execute(ROWS[0][1], activation_rc=124)
        self.assertEqual(rc, 4)
        self.assertTrue(any('modify' in c and c[-1] == '' for c in calls))
        self.assertEqual(len([c for c in calls if 'disconnect' in c]), 2)
    def test_pin_timeout_still_restores(self):
        rc, calls = self.execute(ROWS[0][1], pin_rc=124)
        self.assertEqual(rc, 4)
        self.assertTrue(any('modify' in c and c[-1] == '' for c in calls))
        self.assertFalse(any('up' in c for c in calls))

if __name__ == '__main__':
    unittest.main()
