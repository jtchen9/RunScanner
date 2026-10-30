import unittest
from unittest.mock import Mock, patch
import requests
from robot_network_recovery import NetworkRecovery, ACTIVATE, wifi_state
from common_nms import fetch_commands


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.log = Mock()
        self.r = NetworkRecovery(self.log, clock=lambda: self.now, sleep=lambda n: None)

    @patch('robot_network_recovery.wifi_state', return_value='disconnected')
    @patch('robot_network_recovery.run', return_value=(10, ''))
    def test_only_two_attempts_across_repeated_failures(self, run, state):
        self.assertTrue(self.r.failed('offline'))
        for _ in range(10):
            self.assertFalse(self.r.failed('offline'))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], ACTIVATE)
        self.r.nms_ok()
        self.r.failed('new outage')
        self.assertEqual(run.call_count, 4)

    @patch('robot_network_recovery.wifi_state', return_value='connected')
    @patch('robot_network_recovery.run')
    def test_nms_down_wifi_healthy_no_reset(self, run, state):
        self.assertFalse(self.r.failed('offline'))
        self.now = 41
        state.return_value = 'disconnected'
        self.assertFalse(self.r.failed('offline'))
        run.assert_not_called()

    @patch('robot_network_recovery.wifi_state', return_value='unknown')
    @patch('robot_network_recovery.run')
    def test_unknown_no_reset(self, run, state):
        self.assertFalse(self.r.failed('offline'))
        run.assert_not_called()

    @patch('robot_network_recovery.wifi_state', return_value='disconnected')
    @patch('robot_network_recovery.run', return_value=(0, ''))
    def test_nmcli_success_does_not_reset_budget(self, run, state):
        self.r.failed('offline')
        self.r.failed('offline')
        self.r.failed('offline')
        self.assertEqual(run.call_count, 2)

    @patch('robot_network_recovery.os.path.exists', return_value=True)
    @patch('robot_network_recovery.run')
    def test_maintenance_pause(self, run, exists):
        self.assertFalse(self.r.failed('offline'))
        run.assert_not_called()

    @patch('robot_network_recovery.run')
    def test_device_states(self, run):
        for raw, wanted in [('100 (connected)', 'connected'), ('30 (disconnected)', 'disconnected'),
                            ('60 (need authentication)', 'disconnected'), ('20 (unavailable)', 'unknown'),
                            ('10 (unmanaged)', 'unknown'), ('nonsense', 'unknown')]:
            run.return_value = (0, raw)
            self.assertEqual(wifi_state(), wanted)

    @patch('common_nms.requests.post')
    def test_transport_vs_http_and_bad_json(self, post):
        def fetch():
            return fetch_commands('http://nms', 'robot', 20, 10)
        post.side_effect = requests.exceptions.Timeout('timed out')
        self.assertTrue(fetch()[1]['transport_failure'])
        post.side_effect = None
        post.return_value = Mock(status_code=503, text='unavailable')
        self.assertNotIn('transport_failure', fetch()[1])
        post.return_value = Mock(status_code=200)
        post.return_value.json.side_effect = ValueError('bad JSON')
        self.assertNotIn('transport_failure', fetch()[1])


if __name__ == '__main__':
    unittest.main()
