# TestAX210 — passive power-response experiment v0.2

This is a separate diagnostic tool. It does not replace `scan_wifi.sh`, publish
minute reports, modify NMS data, associate an adapter, or install a service.

**Purpose:** determine whether AX210 transmit-power requests produce repeatable
changes in received power, and which reported PHY configurations those changes
occur under. An unchanged RSSI response is also a useful result: the requested
ceiling may not be binding, or firmware behavior may differ from the request.


## Repository boundary

All new files in this distribution are under `TestAX210/`. Extracting this
archive adds no files at the repository root and does not restore or replace
`scan_wifi.sh`. Complete the previously agreed production-scanner restoration
separately before committing the cleanup and test directory.

Dependency direction is one-way: TestAX210 may use production modules when
needed; production modules must never import, load or execute TestAX210 code.
The current test is self-contained. `TestAX210/__init__.py` rejects ordinary
package imports such as `import TestAX210.receive_power`. Run the entry scripts
explicitly. This is an accidental-import guard, not a Python security sandbox;
deliberate loading by path can bypass it and is prohibited by the dependency
rule. No production path configuration or service entry point should be changed
to expose this directory. A future outward import should add the repository
root only within that test process, without modifying production startup.

`TestAX210/.gitignore` excludes captures, analysis outputs, ZIPs and Python cache
files. Offline input archives belong under `TestAX210/input/`; analysis outputs
are under `TestAX210/results/`. The robot scripts are Linux-only; the analyzer
and offline tests run on Windows or Linux.

Running a radio test still temporarily affects the selected Wi-Fi adapter and
uses the existing scanner lock to prevent concurrent access. Folder separation
does not make an active monitor/power test compatible with simultaneous
production scanning on that same adapter. The scripts never start/stop services
or change their definitions. Run them manually only during the selected test.

## Arrangement

| Device | AX210 role | Activity |
|---|---|---|
| Robot A | Ordinary associated station | Sends pings to its AP while stepping requested transmit power. |
| Robot B, e.g. Echo | Dedicated passive monitor | Receives Robot A's original air frames on the same channel. |
| Existing AP | Normal AP | Provides association and responds to pings; no AP configuration change. |

The two AX210s do not need a peer association. The observer can measure frames
addressed to the AP. MAC transmitter filtering excludes the AP's replies and
forwarded frames. No payload decryption is needed. Other uplink data transmitted
by Robot A during a phase is included: encrypted packets cannot be positively
identified as the test pings. Keep unrelated traffic on Robot A low.

Keep both robots stationary with fixed antenna orientation. Use Ethernet for
Echo's control connection (`192.168.11.174` in the current setup). The receiver's
AX210 must already be disconnected; the tool refuses to disconnect it for you.
The transmitter's AX210 must remain associated to the AP. Neither script changes
AP association, channel selection, regulatory domain, or firmware settings.

## First run: three commands

Extract the `TestAX210` folder into `D:\Data\_Action\_RunScanner` on the Windows development PC. Commit/push it through the normal workflow, then pull on each robot. Its robot path is `/opt/_RunScanner/TestAX210`. Python 3
and `iw`, `ip`, `ping` (iputils-ping), `tcpdump` are needed on the robots. The
offline analyzer runs on Windows or Linux with Python 3 and no extra packages.
Settings are at the bottom of each entry script, under `__main__`; no argument list. All output paths are anchored to the TestAX210 directory, regardless of your working directory.

Before running:

- Stop `scanner-poller` on both test robots using the existing procedure.
  The shared scanner lock prevents simultaneous scanner/capture execution.
- Check Robot A's `iw dev` channel/width/center and copy those values into
  `receive_power.py`. Its initial example is 6115 / 160 / 6185 MHz, matching the
  previously observed lab 6 GHz connection. For the known 5 GHz profile, use
  5180 / 80 / 5210. This is one band per run, not a sweeping scan.
- Both system clocks should be synchronized. Clock status is recorded. The
  analyzer uses a 1-second exclusion guard at each traffic-window edge. NTP's
  synchronized flag does not itself establish a numerical clock-error bound;
  if clocks are uncertain, verify their offset before interpreting a sweep.
- Check `restore_mode` in `transmit_power.py`. Default `auto` returns power
  control to automatic operation. `iw` does not reveal whether the prior
  setting was auto, fixed, or limit. If preserving a known manual policy,
  enter that mode and dBm explicitly. Do not assume the initial displayed
  dBm value identifies the previous policy.

1. On Robot B's Ethernet SSH connection, start the receiver:

   ```bash
   sudo python3 /opt/_RunScanner/TestAX210/receive_power.py
   ```

2. After its channel-profile line appears, start Robot A within about 30 seconds:

   ```bash
   sudo python3 /opt/_RunScanner/TestAX210/transmit_power.py
   ```

   Defaults: requested `limit` = 5, 10, 15, 20, 15, 10, 5 dBm, with 2 seconds
   settling and 10 seconds traffic per step. Pings send 1000-byte payloads at
   50/s, about 0.4 Mbit/s before protocol overhead. The gateway is discovered
   from Robot A's Wi-Fi route. If unavailable, set `target_ipv4` to the AP's
   LAN address. A short ping preflight must succeed before any power changes.

   The receiver runs for 180 seconds, or until its capture storage limit is
   reached. Wait for both commands to finish. Each prints a downloadable
   `TestAX210/results/ax210_power_...zip` archive path. Raw session folders remain under
   `TestAX210/results/`; repeated runs never overwrite existing sessions.

3. Copy both archives into `TestAX210/input/` on the Windows development PC. Rename them `tx_results.zip` and `rx_results.zip`, or edit the filenames at the bottom of `analyze_power.py`. From the repository root, run:

   ```bash
   python TestAX210/analyze_power.py
   ```

   Each analysis automatically creates a timestamped folder under `TestAX210/results/`. If necessary, set `RX_MINUS_TX_SECONDS` to the independently checked
   offset: receiver clock minus sender clock. `CLOCK_OFFSET_VERIFIED=True`
   explicitly permits that alignment without synchronized flags on both robots.

After verifying the `limit` run, repeat with `mode='fixed'` if desired. Rejection
by the driver is recorded, never converted silently to `limit`. Each run has
its own outputs. Do not interpret an accepted `fixed` request as proof of exact
radiated power. Returning to `auto` on exit is itself logged and checked.

## Outputs and interpretation

| Output | Contents |
|---|---|
| Sender archive | Requested mode/power, command results, `iw` reported power, association and tuning before/after each phase, timestamps, ping logs, explicit restoration result. |
| Receiver archive | Original Radiotap PCAPs, driver/regulatory diagnostics, tuning, clock status, tcpdump logs including dropped-packet counts, restoration result. |
| `sender_frames.csv` | Decoded frames attributed to Robot A, both RSSIs, scalar RSSI, PHY metadata, step and exclusion reason. Original packet bytes remain in the RX archive. |
| `power_response.csv` | Per-step/per-PHY counts, linear-power mean RSSIs, median and dB spread for both chains, matched-profile changes against the first adequate reference step. |
| `power_response.svg` | Browser-viewable two-chain response plots, up to eight most-observed PHY profiles. Point labels are step numbers; hover for sample count/status. All profiles remain in CSV. |
| `analysis.json` | Exclusion counts, sample coverage per step, clock alignment, run/restoration status, and model assumptions. |

The receiver uses the existing validated capture engine and Radiotap decoder,
bundled in `vendor/`. The private collector has had its legacy scan/report publication and old
standalone entry points removed. It cannot publish the production scan files. Capture limits are 256 MiB and 1 GiB free-space reserve by
default. These are polling limits, so a small write overshoot is possible.
Compression requires extra disk space, up to roughly another capture's size.

Comparisons require the same reported PHY, rate/MCS, NSS, bandwidth/RU,
guard interval, beamforming flag, STBC, DCM, coding and data subtype. Unknown
values remain unknown and form separate groups. Missing modern PHY fields and
legacy frames are labeled exploratory. Even complete reported metadata does
not reveal the precoding matrix, chain-power allocation or every firmware
power adjustment.

The analyzer excludes other transmitters, AP forwarding, retries, bad or
unverified FCS, missing chain RSSI, frequency mismatch, and switching-window
edges. It takes one observation from each recognized A-MPDU rather than
weighting the same reception once per constituent MPDU. Records without a
usable A-MPDU reference remain frame samples. These counts are not a claim of
independent statistical observations. If a profile has fewer than 20 samples,
it is reported without a power-change comparison. Check coverage: zero samples
can mean wrong tuning, no overlap in capture time, weak reception or unavailable
metadata, not zero channel gain.

For example, if a +5 dB requested step produces approximately +5 dB on both
receive branches for the same reported PHY, repeatedly in both directions,
that supports effective relative power control for that tested configuration.
A plateau indicates that further requested increases have not yielded a
corresponding measured increase. Near sensitivity, successful-frame selection
can bias the result; avoid drawing a power curve from only a few survivors.

## Relationship to channel gain and capacity

Retain the agreed independent receive-branch approximation as a **model**.
It does not change how raw RSSI is collected and is not established by this test.
Two power measurements alone do not supply complex channel coefficients or
verify receive-branch independence.

This experiment measures an AX210-to-AX210 path. It characterizes the AX210
transmitter's response; it does not substitute that 2-by-2 path for the
robot-to-AP 4-by-2 reverse-link channel. For the reverse-link capacity model,
combine the separately estimated reciprocal AP/robot channel with an explicitly
defined AX210 transmit-power budget and AP receive-noise assumptions.

Neither requested power nor `iw`-reported power is a calibrated actual
per-frame/per-chain transmit measurement. No automatic absolute path-gain
normalization, division by two, water-filling or numerical capacity result is
performed here. An absolute power reference would be required to turn a
relative power-response experiment into an absolute TX calibration. This
distinction prevents the control setting from being mistaken for measured RF
power when these results are later used in the radiomap backend.

## Recovery and limitations

- Normal completion, Python errors, Ctrl+C and SIGTERM trigger restoration.
  Inspect `restore_ok`; success means the restoration command succeeded, not
  that actual RF power was measured. SIGKILL, power loss and kernel failure
  cannot run cleanup. `manifest.json` retains the selected restore policy and
  receiver monitor-interface name for manual recovery.
- An AP channel/association change detected around a sender phase aborts the
  sweep. A brief change-and-return within a phase may not be detected. Keep
  channel and AP configuration stable throughout the experiment.
- The receiver never activates disabled 6 GHz channels by scanning; resolve
  availability through the established setup procedure before this passive run.
- Unsupported Radiotap fields are counted as decode errors, not guessed.
  Raw PCAPs remain available for a later decoder revision. Per-frame noise and
  transmitter RF output are not assumed available.
- Driver/kernel/firmware and packet-capture behavior must still be verified on
  the actual two robots. This release has offline/mocked verification only.

## Verification and sources

From the repository root, run offline tests with `python3 -m unittest discover -s TestAX210/tests -v` (or `python` on Windows). Tests cover
per-chain decoding, power-delta recovery from synthetic PCAPs, aggregation
weighting, transmitter/direction filters, missing PHY fields, clock guards,
unit conversion, receiver refusal when connected, and restoration after an
interruption. The bundled decoder was also exercised on the existing Echo
capture; this is not a hardware validation of transmit-power control.

Primary documentation:

- Linux Wireless `iw`: https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html
  (`set txpower auto|fixed|limit`, units mBm = 100 × dBm).
- Linux Wireless Intel driver: https://wireless.docs.kernel.org/en/latest/en/users/drivers/iwlwifi.html
- Radiotap field definitions: https://www.radiotap.org/fields/defined
