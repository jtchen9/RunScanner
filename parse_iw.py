#!/usr/bin/env python3
import sys, re, csv, json

if len(sys.argv) != 3:
    print("Usage: parse_iw.py OUT_CSV OUT_JSON", file=sys.stderr)
    sys.exit(2)

out_csv, out_json = sys.argv[1], sys.argv[2]
text = sys.stdin.read()

# If iw produced nothing, write empty JSON
if not text.strip():
    with open(out_json, "w") as f: json.dump([], f)
    with open(out_csv, "w"): pass
    sys.exit(0)

mac_re    = re.compile(r'^BSS\s+([0-9A-Fa-f:]{17})\(', re.M)
freq_re   = re.compile(r'^\s*freq:\s*([0-9]+(?:\.[0-9]+)?)', re.M)
signal_re = re.compile(r'^\s*signal:\s*(-?\d+(?:\.\d+)?)', re.M)
ssid_re   = re.compile(r'^\s*SSID:\s*(.*)$', re.M)

# Find start offsets of each BSS block
starts = [m.start() for m in re.finditer(r'(?m)^BSS\s+[0-9A-Fa-f:]{17}\(', text)]
if starts and starts[0] != 0:
    starts = [0] + starts
starts.append(len(text))

entries = []
for i in range(len(starts)-1):
    block = text[starts[i]:starts[i+1]]

    m = mac_re.search(block)
    if not m:
        continue
    bssid = m.group(1)

    # last freq in block
    mfs = list(freq_re.finditer(block))
    freq = float(mfs[-1].group(1)) if mfs else None

    # strongest signal in block
    sigs = [float(s.group(1)) for s in signal_re.finditer(block)]
    signal = max(sigs) if sigs else None

    # last SSID (may be empty)
    mss = list(ssid_re.finditer(block))
    ssid = (mss[-1].group(1).strip() if mss else "") or "<hidden>"

    entries.append({"bssid": bssid, "ssid": ssid, "freq": freq, "signal": signal})

# Deduplicate by BSSID keeping strongest signal (and last freq for that best signal)
by_bssid = {}
for e in entries:
    key = e["bssid"].lower()
    cur = by_bssid.get(key)
    if cur is None or (e["signal"] is not None and (cur["signal"] is None or e["signal"] > cur["signal"])):
        by_bssid[key] = e

entries = list(by_bssid.values())
    
with open(out_csv, "w", newline="") as f:
    w = csv.writer(f)
    for e in entries:
        w.writerow([
            e["bssid"],
            e["ssid"],
            "" if e["freq"]   is None else e["freq"],
            "" if e["signal"] is None else e["signal"],
        ])

with open(out_json, "w") as f:
    json.dump(entries, f, ensure_ascii=False)
