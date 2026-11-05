# Targeted Wi-Fi recovery: Alpha pilot

Apply on top of Robot-WiFi-Recovery-paced.patch (the version used on
2026-11-05). This replaces robot_network_recovery.py and its sudo permission,
and adds a root-owned activation helper. Existing robot_agent.py hooks stay.
No separate service, SSID, AP list, driver change, or saved BSSID lock is added.

## Why

The captured outage showed a long need-auth state, requested BSSIDs differing
from actual authentication attempts, and slow retries before AP6 was tried.
The patch allows native activation 25 seconds, then cancels a stalled attempt.
It selects untried candidates matching the last working SSID/security before
revisiting one. Failed candidates have a 30-second cooldown; attempts have a
5-second minimum gap. Existing agent polling/discovery adds latency: these are
eligibility intervals, not guaranteed recovery times or a 120-second SLA.

The helper temporarily restricts the profile to the selected BSSID in memory,
activates it with a 10-second nmcli wait, checks the actual supplicant BSSID,
then clears both profile and active supplicant restrictions. Failed activation
is explicitly cancelled because a client-side nmcli timeout alone does not
cancel it. The disk profile is not edited. Profile pin restoration runs in
finally, including interruption/error paths. SIGKILL or power loss cannot run
finally; power loss clears the in-memory change. A cleanup error is logged and
requires investigation before further tests. Deliberately pinned profiles are
refused. sudo only permits the validated installed helper and the existing
fixed-interface generic activation fallback.

An already connected Wi-Fi interface is left alone when the NMS is unavailable.
The pause marker /run/robot-network-recovery.pause is retained. A successful
NMS poll resets recovery. No mobility command is replayed by this code.

This does not repair an AP rejecting authentication or a weak AP6 radio link.
It ensures alternative candidates get a targeted attempt and records the
actual result. Same-SSID APs with a different advertised security string are
not selected by this version; the captured four 6-GHz APs matched.

## Install on Alpha after pulling the shared code

Run with the robot stationary and no experiment in progress:

```sh
cd /opt/_RunScanner
sudo visudo -cf tools/scanner-agent-wifi-recovery.sudoers
sudo install -d -o root -g root -m 755 /usr/local/libexec
sudo install -o root -g root -m 755 tools/scanner-wifi-activate.py /usr/local/libexec/scanner-wifi-activate
sudo install -o root -g root -m 440 tools/scanner-agent-wifi-recovery.sudoers /etc/sudoers.d/scanner-agent-wifi-recovery
sudo systemctl restart scanner-agent
sudo systemctl status scanner-agent --no-pager -l
```

Run each line separately. Stop if validation or installation fails.
The helper requires wpa_cli and its local control socket (already used on Alpha).
Every participating robot needs the helper and sudoers installed after pulling.
Updates to the helper source require reinstalling its root-owned copy.

## Validation

Nine mocked tests passed during preparation, covering candidate rotation,
stalled activation, healthy-link protection, pause/race protection, network
isolation, target mismatch, success cleanup and timeout cleanup.
Run locally with:

```sh
python3 -m unittest discover -s tests -p test_wifi_recovery_targeted.py
```

Hardware behavior remains to be validated on Alpha. First confirm normal NMS
reports. Repeat the serving-AP disassociation sequence with the local console
ready: AP4 -> AP1 -> AP5, according to actual association. Do not manually cut
AX210 during this test and do not block AP6. Record when blocks are issued so
expiry does not masquerade as a successful alternative-AP test. Use wifi.sh
while offline and after recovery. Check NET_RECOVERY logs for requested and
actual BSSID. A missing NET_RECOVERY entry during a fast native roam is normal.
AP6 success means actual BSSID B8:82:F2:45:F5:B9 plus resumed NMS reports;
an nmcli success or a later return to AP4 alone is insufficient.

```sh
sudo journalctl -u scanner-agent --since '10 minutes ago' --no-pager -o short-iso | grep NET_RECOVERY
```

If helper-error=pin-cleanup-failed appears, stop this test and keep wifi.sh logs.
Do not disable authentication, increase sudo privileges, or change drivers to
make this patch run.

## Rollback

Reverse this patch in the shared repository and pull the restored code on
Alpha. Reinstall its restored tools/scanner-agent-wifi-recovery.sudoers using
visudo validation and install above, then restart scanner-agent. The unused
root helper may remain or be removed. If rolling back during an outage, first
let the in-flight helper finish. No persistent profile changes need reversing
following normal execution.
