# Wi-Fi recovery: outage handling (Alpha pilot)

Apply Robot-WiFi-Recovery-outage-fix.patch on top of the installed
Robot-WiFi-Recovery-cancel-fix.patch. Do not reverse or reapply older patches.
The existing robot_agent.py integration, service, installer and sudoers stay
unchanged. Reinstall the root helper after pulling this revision.

## Changes

- Both address readers treat a successful empty JSON list as no IPv4. Invalid
  JSON or a failed inspection is still unknown. This closes a confirmed code
  gap; the earlier outage log did not capture the raw JSON, so its precise
  cause remains unproven.
- Prolonged NMS transport/discovery failure with retained IPv4 is now eligible
  for recovery after 30 seconds. Such attempts are spaced at least 30 seconds
  after the preceding retained-IP attempt completes. The helper requires the
  explicit nms-unreachable argument to permit this intervention.
- A newly observed transition from activating/no-IP to connected gets another
  10 seconds for a successful NMS poll before retained-IP intervention.
- After an agent restart, profile context can be obtained without a successful
  NMS poll: use the active unpinned Wi-Fi profile, or a single unambiguous
  autoconnect-enabled profile compatible with wlP1p1s0. Ambiguity/restrictions
  are logged; no profile credentials are read or written.
- During eligible outages, request a scan at most once per 60 seconds. The
  helper permits scanning only while disconnected, failed or connected, not
  during an ongoing activation. The scan request is asynchronous; subsequent
  candidate reads can use newly discovered BSSIDs. Cached candidates remain
  available as fallback. A failed scan does not prevent an activation attempt.
- Unknown state, pause markers and missing context now produce rate-limited
  wait logs rather than silent indefinite skips. A transient unknown state
  does not reset the native-activation grace timer.

## Recovery policy

Normal connected operation causes no recovery commands. A successful NMS poll
ends the outage and resets candidate history. Association, DHCP, and helper
success alone never declare NMS recovery.

Disconnected devices are eligible after the initial 5-second wait. Native
activation or missing IPv4 gets 25 seconds from first observation. Targeted
attempts rotate through matching BSSIDs, untried first, then least recently
attempted, with a 30-second per-candidate cooldown. The minimum delay after a
targeted attempt is 5 seconds (30 seconds for an IP-present intervention).

With a previously healthy connection, candidates match its SSID and advertised
security. Restored context without an active BSSID uses the saved profile's
SSID and leaves security compatibility to NetworkManager; incompatible APs
may fail and the next candidate is tried. No AP list or SSID is hardcoded.

The root helper validates profile/interface restrictions, rechecks state,
sets a temporary in-memory BSSID, activates with a 10-second nmcli wait, checks
actual BSSID and usable IPv4, and restores unrestricted roaming. Existing
cancellation and cleanup protections remain. No motion command is replayed.

A server outage and a broken AP path can both look like NMS transport failure.
This revision cannot distinguish them definitively. Retained-IP recovery is
therefore paced, not guaranteed useful during an NMS server outage. It does
not treat an HTTP application error as a reason to change APs.

## Limits kept explicit

Recovery is called by the existing agent loop, not an independent service.
Long command execution, NMS discovery/poll timeouts, or registration before the
agent starts can delay it. A 120-second recovery deadline is a test target,
not a guarantee. Service startup's existing registration gate is unchanged.

Disabled radios, unmanaged/unavailable interfaces, ambiguous saved profiles,
and deliberate BSSID pins are not overridden. Inspection failures are logged
and retried on subsequent failure calls; they do not authorize blind resets.
An intentionally pinned profile may still be activated by the pre-existing
NetworkManager automatic fallback, but its pin is never changed.

This revision does not fix weak RF, AP admission policy, DHCP servers, mesh
backhaul or a dead NMS. It makes recoverable conditions actionable and leaves
logs for remaining failures. SIGKILL cannot run temporary-pin cleanup; a
pin-cleanup error needs inspection before further tests.

## Apply on the shared Windows repository

Place the patch in the repository root and run:

```powershell
git apply --check .\Robot-WiFi-Recovery-outage-fix.patch
git apply .\Robot-WiFi-Recovery-outage-fix.patch
```

Review, commit and push using the normal shared-code workflow. Then on Alpha,
stationary with no experiment active and NMS running:

```sh
cd /opt/_RunScanner
git pull
sudo bash tools/install_wifi_recovery.sh
```

The installer validates and installs the updated root-owned helper and the
existing sudoers rule, then restarts scanner-agent. Pulling alone is not enough.
There are no new manual permission commands or persistent network settings.

## Verification

29 mocked regression tests pass, covering address parsing, missing-IP recovery,
retained-IP pacing and target rotation, restart context, ambiguous/pinned
profiles, fresh scan candidates, native recovery races, cancellation, cleanup
and reset after successful NMS polling. Run from repository root:

```sh
python3 -m unittest discover -s tests -p test_wifi_recovery_targeted.py
```

Mocks validate control logic, not physical AX210/NetworkManager behavior.

Two supervised physical tests on Alpha are sufficient for this revision:

1. With AP blocks expired and NMS reports normal, run
   `sudo nmcli device disconnect wlP1p1s0`. Confirm NMS reports return within
   120 seconds. Capture `bash ~/wifi.sh` and look for watchdog activation and
   `NET_RECOVERY NMS poll restored`.
2. Keep AP6 nearby. Disassociate the actual serving AP for 299 seconds, then
   repeat on the new serving AP only after reports resume. Leave at least one
   AP unblocked; do not manually disconnect AX210. Confirm each transition
   restores NMS reports within 120 seconds. Native roaming without watchdog
   logs is valid. If stuck, run `bash ~/wifi.sh` locally before manual recovery.

After these pass, freeze the revision for the next overnight run. Do not add
more patches without new evidence. Save the outage capture if another failure
occurs. No finite test suite proves all corner cases absent.
