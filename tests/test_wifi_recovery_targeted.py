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
        with patch.object(h.shutil, 'which', return_value='/usr/sbin/wpa_cli'), patch.object(h, 'field', side_effect=lambda u,k: fields[k]), patch.object(h, 'run', side_effect=command), patch.object(h, 'status', return_value={'wpa_state':'COMPLETED', 'bssid':actual, 'id':'0'}), patch.object(h, 'ipv4_ready', return_value=True):
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
        self.assertEqual(len([c for c in calls if 'disconnect' in c]), 1)
    def test_activation_timeout_cancels_and_restores(self):
        rc, calls = self.execute(ROWS[0][1], activation_rc=124)
        self.assertEqual(rc, 4)
        self.assertTrue(any('modify' in c and c[-1] == '' for c in calls))
        self.assertEqual(len([c for c in calls if 'disconnect' in c]), 1)
    def test_pin_timeout_still_restores(self):
        rc, calls = self.execute(ROWS[0][1], pin_rc=124)
        self.assertEqual(rc, 4)
        self.assertTrue(any('modify' in c and c[-1] == '' for c in calls))
        self.assertFalse(any('up' in c for c in calls))

class MissingIPTests(unittest.TestCase):
    def test_kernel_address_results_agent_and_helper(self):
        # The root helper independently verifies the same condition.
        samples = [
            ('[{"addr_info": []}]', False),
            ('[{"addr_info": [{"family":"inet", "local":"192.168.11.125"}]}]', True),
            ('[{"addr_info": [{"family":"inet", "local":"169.254.1.2"}]}]', False),
            ('malformed', None),
            ('[]', None),
        ]
        for module in (r, h):
            for raw, expected in samples:
                with self.subTest(module=module.__name__, raw=raw), patch.object(module.shutil, 'which', return_value='/usr/sbin/ip'), patch.object(module, 'run', return_value=(0, raw)):
                    self.assertIs(module.ipv4_ready(), expected)
            with patch.object(module.shutil, 'which', return_value='/usr/sbin/ip'), patch.object(module, 'run', return_value=(124, '')):
                self.assertIsNone(module.ipv4_ready())

    def test_connected_state_is_not_enough(self):
        with patch.object(r, 'run', return_value=(0, '100 (connected)')):
            for ready, state in ((True, 'connected'), (False, 'no-ipv4'), (None, 'unknown')):
                with patch.object(r, 'ipv4_ready', return_value=ready):
                    self.assertEqual(r.wifi_state(), state)

    def test_missing_ip_wait_survives_roaming(self):
        now = [0]
        recovery = r.NetworkRecovery(lambda _: None, clock=lambda: now[0])
        recovery.context = UID, 'WPA3', 'test'
        recovery.cached = ROWS
        with patch.object(r.os.path, 'exists', return_value=False), patch.object(r, 'visible', return_value=[]), patch.object(r, 'wifi_state', return_value='no-ipv4') as state, patch.object(r, 'run', return_value=(4, '')) as run:
            recovery.failed('NMS discovery failed')
            now[0] = 5; self.assertFalse(recovery.failed('NMS discovery failed'))
            # Native roaming changes state, but does not restart the grace timer.
            state.return_value = 'activating'
            now[0] = 20; self.assertFalse(recovery.failed('NMS discovery failed'))
            state.return_value = 'no-ipv4'
            now[0] = 30; self.assertTrue(recovery.failed('NMS discovery failed'))
            run.assert_called_once()

    def test_ip_returns_during_grace_no_reconnect(self):
        now = [0]
        recovery = r.NetworkRecovery(lambda _: None, clock=lambda: now[0])
        with patch.object(r.os.path, 'exists', return_value=False), patch.object(r, 'wifi_state', return_value='no-ipv4') as state, patch.object(r, 'run') as run:
            recovery.failed('test'); now[0] = 5; recovery.failed('test')
            state.return_value = 'connected'; now[0] = 30
            self.assertFalse(recovery.failed('test')); run.assert_not_called()

    def test_root_helper_protects_restored_ip(self):
        def command(args, timeout=3):
            if args[-1] == 'ping': return 0, 'PONG'
            if 'GENERAL.STATE' in args: return 0, '100 (connected)'
            return 0, ''
        fields = {'connection.type':'802-11-wireless', 'connection.interface-name':'wlP1p1s0', '802-11-wireless.bssid':''}
        for ready in (True, None):
            with patch.object(h.shutil, 'which', return_value='/usr/sbin/wpa_cli'), patch.object(h, 'field', side_effect=lambda u,k: fields[k]), patch.object(h, 'run', side_effect=command) as run, patch.object(h, 'ipv4_ready', return_value=ready):
                self.assertEqual(h.activate(UID, ROWS[0][1]), 3)
                self.assertFalse(any('disconnect' in c.args[0] for c in run.call_args_list))

    def test_root_helper_recovers_missing_ip_and_requires_ip_on_success(self):
        fields = {'connection.type':'802-11-wireless', 'connection.interface-name':'wlP1p1s0', '802-11-wireless.bssid':''}
        def command(args, timeout=3):
            if args[-1] == 'ping': return 0, 'PONG'
            if 'GENERAL.STATE' in args: return 0, next(states)
            if 'bssid' in args: return 0, 'OK'
            return 0, ''
        for after, expected in ((True, 0), (False, 4), (None, 4)):
            states = iter(['100 (connected)', '30 (disconnected)'])
            with patch.object(h.shutil, 'which', return_value='/usr/sbin/wpa_cli'), patch.object(h, 'field', side_effect=lambda u,k: fields[k]), patch.object(h, 'run', side_effect=command), patch.object(h, 'ipv4_ready', side_effect=[False, after]), patch.object(h, 'status', return_value={'wpa_state':'COMPLETED', 'bssid':ROWS[0][1], 'id':'0'}):
                self.assertEqual(h.activate(UID, ROWS[0][1]), expected)

class CancellationRegressionTests(unittest.TestCase):
    def invoke(self, initial='30', after='30', cancel_rc=10, activation_rc=0, state_rc=0):
        calls = []
        states = iter([initial, after])
        def command(args, timeout=3):
            calls.append(args)
            if args[-1] == 'ping': return 0, 'PONG'
            if 'GENERAL.STATE' in args:
                value = next(states)
                return (state_rc if len([c for c in calls if 'GENERAL.STATE' in c]) > 1 else 0), value
            if 'disconnect' in args: return cancel_rc, ''
            if 'up' in args: return activation_rc, ''
            if 'bssid' in args: return 0, 'OK'
            return 0, ''
        fields = {'connection.type':'802-11-wireless', 'connection.interface-name':'wlP1p1s0', '802-11-wireless.bssid':''}
        with patch.object(h.shutil, 'which', return_value='/usr/sbin/wpa_cli'), patch.object(h, 'field', side_effect=lambda u,k: fields[k]), patch.object(h, 'run', side_effect=command), patch.object(h, 'ipv4_ready', return_value=True), patch.object(h, 'status', return_value={'wpa_state':'COMPLETED', 'bssid':ROWS[0][1], 'id':'0'}):
            result = h.activate(UID, ROWS[0][1])
        return result, calls

    def test_already_disconnected_skips_cancel_and_activates(self):
        for state in ('30', '120'):
            rc, calls = self.invoke(initial=state)
            self.assertEqual(rc, 0)
            self.assertFalse(any('disconnect' in c for c in calls))
            self.assertTrue(any('up' in c for c in calls))

    def test_cancel_error_but_disconnected_can_activate(self):
        rc, calls = self.invoke(initial='60', after='30', cancel_rc=10)
        self.assertEqual(rc, 0)
        self.assertTrue(any('up' in c for c in calls))

    def test_cancel_success_but_still_active_must_not_activate(self):
        for after in ('60', '100', '110'):
            rc, calls = self.invoke(initial='60', after=after, cancel_rc=0)
            self.assertEqual(rc, 4)
            self.assertFalse(any('up' in c or 'modify' in c for c in calls))

    def test_cancel_state_query_failure_must_not_activate(self):
        rc, calls = self.invoke(initial='60', state_rc=124)
        self.assertEqual(rc, 4)
        self.assertFalse(any('up' in c for c in calls))

    def test_rejected_activation_does_not_block_next_disconnected_attempt(self):
        rc, calls = self.invoke(initial='60', activation_rc=4)
        self.assertEqual(rc, 4)
        self.assertTrue(any('modify' in c and c[-1] == '' for c in calls))
        # Next watchdog call must reach activation despite disconnect returning
        # nonzero for an already-disconnected device, as in the captured outage.
        rc, calls = self.invoke(initial='30', cancel_rc=10)
        self.assertEqual(rc, 0)
        self.assertTrue(any('up' in c for c in calls))

if __name__ == '__main__':
    unittest.main()
