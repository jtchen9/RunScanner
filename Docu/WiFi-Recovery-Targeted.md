# Wi-Fi recovery: cancellation correction (Alpha pilot)

This is the current guide, replacing the earlier targeted-recovery guide.
Apply Robot-WiFi-Recovery-cancel-fix.patch on top of the already
installed Robot-WiFi-Recovery-ipv4-from-targeted.patch. Do not reverse or
reapply earlier patches. Existing robot_agent.py integration stays unchanged.

## Cancellation correction

The 11:01-11:10 capture showed the helper repeatedly aborting with
cancel-failed before reaching activation because the interface was already
disconnected. The helper now skips cancellation for states 30/120. For an
ongoing activation or missing-IP connection it cancels, then verifies the
resulting state. A nonzero cancel return is acceptable only if the new state
is confirmed disconnected/failed. Unknown or active state prevents activation.
This replaces the faulty prerequisite; no service, permission, retry timer,
or network profile configuration is added.

## Previous IPv4 correction retained

- Replaced the unconditional NetworkManager state-100 exemption. State 100
  now reads kernel IPv4 addresses: no usable IPv4 is a recoverable condition.
- Replaced the root helper's unconditional rejection of state 100 with an
  independent missing-IPv4 check immediately before disconnecting.
- Removed association-only success: a targeted activation must return success,
  match the actual BSSID, and have usable IPv4. NMS polling still determines
  end-to-end recovery.
- Replaced separate manual installation steps with tools/install_wifi_recovery.sh.
- Replaced the earlier guide and extended the existing test file. No parallel
  recovery module, new daemon, driver change, or persistent network pin is added.

The captured 2026-11-05 10:28:03 state was associated with AP5 and reported
NetworkManager state 100, but had no IPv4 address. AP6 accepted Alpha twice;
these logs do not establish why it roamed away or why DHCP failed. This change
addresses the confirmed robot recovery gap; it does not claim to fix the AP
or mesh root cause.

## Current behavior

Recovery runs only from the existing scanner-agent failure path. After NMS
failure, it normally allows 5 seconds before checking recovery eligibility.
Native activation or association without usable IPv4 gets a 25-second grace
from first observation. Switching between these two states does not restart
that timer. A targeted attempt can then cancel the stalled activation and try
another matching candidate. Agent discovery/poll delays add to these timings.
Recovery inside 120 seconds is not guaranteed.

Kernel address inspection uses ip -j -4 address show dev wlP1p1s0. Private LAN
IPv4 addresses are accepted; loopback, link-local, multicast, and unspecified
addresses are not. Inspection errors are unknown, not proof of no address.
A connected interface with usable IPv4 is left alone on NMS failure. This
narrow correction does not diagnose bad routes, stale addresses, broken
backhaul with a retained address, or IPv6-only networks. This lab uses IPv4.

The helper rechecks missing IPv4 before disconnecting a state-100 interface.
If the address has returned, or inspection fails, it skips intervention.
It temporarily targets a BSSID in memory and gives nmcli activation 10 seconds.
It verifies actual BSSID and IPv4, cancels failed activation, and restores the
unrestricted profile and active supplicant BSSID setting in finally. No disk
profile is edited. SIGKILL cannot execute finally; cleanup failures are logged
and require investigation. NMS success, not helper success, resets the outage.

Existing pacing remains: untried matching candidates first, then least recently
attempted; 30-second per-candidate cooldown and 5-second minimum between targeted
attempts. Matching uses the last working profile's SSID/security; no AP roster
or SSID is hardcoded. Different advertised security strings are not included.
The generic startup fallback remains for a disconnected interface when no
remembered target exists. A pause marker at /run/robot-network-recovery.pause
still suspends recovery. No mobility commands are replayed.

## Install after git pull

Use Alpha only until the pilot passes. Keep NMS available; agent startup includes
registration, which can fail for reasons unrelated to this installer. Run while
stationary with no experiment active:

```sh
cd /opt/_RunScanner
git pull
sudo bash tools/install_wifi_recovery.sh
```

No chmod is needed when invoking through bash. The installer validates source
syntax, tools and sudoers before stopping scanner-agent. It stages root-owned
copies, stops the agent, replaces the helper and sudoers at their existing
paths, verifies byte equality, then starts and checks the agent. It stops on
error; if installation fails after stopping the agent, fix the reported error
and rerun it. It does not delete unrelated system files or reset profiles.
Repeated installation is supported. Pulling alone is insufficient because the
helper at /usr/local/libexec/scanner-wifi-activate must match the source.
The sudoers content and privileges are unchanged by this revision.

## Verification and pilot

20 mocked tests pass, including the previous target-rotation/cleanup tests and
new tests for missing IPv4, malformed/failed inspection, grace across roaming,
address recovery before intervention, helper race protection, and IPv4-required
activation success. Five additional regression tests cover already-disconnected retry, cancellation
errors with completed disconnection, cancellation success with an active state,
state inspection failure, and retry after rejected activation. Run tests from the repo root:

```sh
python3 -m unittest discover -s tests -p test_wifi_recovery_targeted.py
```

Real NetworkManager/AX210 behavior still needs the Alpha pilot. Let old AP
blocks expire; confirm normal NMS reports. Repeat serving-AP disassociation,
following the actual AP order. Leave AP6 unblocked; do not manually cut AX210.
Capture wifi.sh locally if NMS connectivity is lost, and again after recovery.
Expected new evidence is NET_RECOVERY state=no-ipv4, then a targeted request if
IPv4 stays absent past the grace, followed by helper-result containing actual
BSSID and ipv4=True/False/None. Only resumed NMS polls certify recovery.
A fast native roam may need no intervention. If IPv4 recovers during the grace,
no targeted attempt is expected. If helper-error=pin-cleanup-failed appears,
stop the pilot and preserve logs before further changes.

## Rollback

Reverse only Robot-WiFi-Recovery-cancel-fix.patch in the shared repository,
commit and pull. Then run sudo bash tools/install_wifi_recovery.sh to restore
the installed helper. This returns the previous version, including its known
cancel-failed defect; use it only to isolate an unexpected regression.
