import unittest
from unittest.mock import Mock, patch
import requests
from robot_network_recovery import NetworkRecovery, ACTIVATE, wifi_state
from common_nms import fetch_commands


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        profile = patch("robot_network_recovery.working_profile", return_value=None)
        self.profile = profile.start()
        self.addCleanup(profile.stop)
        self.now = 0
        self.log = Mock()
        self.r = NetworkRecovery(self.log, clock=lambda: self.now, sleep=lambda n: None)

    @patch('robot_network_recovery.wifi_state',return_value='disconnected')
    @patch('robot_network_recovery.run',side_effect=[(4,''),(0,'')])
    def test_reject_then_alternate(self,run,state):
        from robot_ap_roster import ROWS
        a,b,c,d=[ROWS[n][0].upper() for n in ('AP1','AP4','AP2','AP3')]
        from robot_ap_roster import radios_for
        self.r.allowed=radios_for('twin-scout-bravo')
        self.r.profile=('uuid','mesh')
        self.r.table={a:(-40,6115,0),b:(-60,6115,0),c:(-21,6115,0),d:(-31,6115,0)}
        self.assertTrue(self.r.failed('offline'))
        self.assertEqual([v.args[0][-1] for v in run.call_args_list],[a,b])

    @patch('robot_network_recovery.run')
    def test_scan_filter(self,run):
        from robot_network_recovery import learn
        from robot_ap_roster import ROWS,RADIOS
        self.assertEqual(len(RADIOS),18)
        def entry(addr,name):
            return f'BSS {addr}(on wlP1p1s0)\n\tfreq: 6115\n\tlast seen: 5000 ms ago\n\tsignal: -52.00 dBm\n\tSSID: {name}\n'
        a=ROWS['AP4'][0]
        run.return_value=(0,entry(a,'mesh')+entry(ROWS['AP2'][0],'office')+entry('00:00:00:00:00:01','mesh'))
        from robot_ap_roster import radios_for
        t={};learn(t,'mesh',100,radios_for('bravo'))
        self.assertEqual(t,{a.upper():(-52.,6115,95.)})

    def test_all_sites(self):
        from robot_ap_roster import radios_for, ROWS
        for names,aps in [('alpha bravo charlie delta',[1,4,5,6]),('echo',[2]),('foxtrot golf hotel india julia',[3])]:
            expected={m.upper() for i in aps for m in ROWS['AP'+str(i)]}
            for n in names.split():
                self.assertEqual(radios_for('twin-scout-'+n),expected)
        self.assertFalse(radios_for('unregistered'))

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
