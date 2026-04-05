#!/bin/sh
# MCS Distribution Sampler Daemon
# Continuously samples MCS distribution for all connected stations
# Samples every 1 second, aggregates every 60 seconds
# Output: /tmp/mcs_distributions.json

SAMPLE_INTERVAL=1  # Sample every 1 second
AGGREGATE_SAMPLES=60  # Aggregate every 60 seconds to match upload interval
OUTPUT_FILE="/tmp/mcs_distributions.json"
TMPDIR="/tmp/mcs_sampler_$$"

cleanup() {
    rm -rf "$TMPDIR"
    exit 0
}

trap cleanup TERM INT

# Create temp directory
mkdir -p "$TMPDIR"

# Function to extract MCS from bitrate line
extract_mcs() {
    line="$1"
    echo "$line" | awk '{
        if (match($0, /EHT-MCS [0-9]+/)) {
            sub(/^.*EHT-MCS /, "")
            sub(/ .*/, "")
            print
            exit
        }
        if (match($0, /HE-MCS [0-9]+/)) {
            sub(/^.*HE-MCS /, "")
            sub(/ .*/, "")
            print
            exit
        }
        if (match($0, /VHT-MCS [0-9]+/)) {
            sub(/^.*VHT-MCS /, "")
            sub(/ .*/, "")
            print
            exit
        }
        if (match($0, /MCS [0-9]+/)) {
            sub(/^.*MCS /, "")
            sub(/ .*/, "")
            print
            exit
        }
        print "-1"
    }'
}

# Function to sample once for all interfaces
sample_all() {
    timestamp=$(date +%s)
    
    for iface in wlan0.1 wlan0.2 wlan0.3 wlan0.4 wlan0.5 \
                 wlan1.1 wlan1.2 wlan1.3 wlan1.4 wlan1.5 \
                 wlan2.1 wlan2.2 wlan2.3 wlan2.4 wlan2.5; do
        
        iw dev "$iface" station dump 2>/dev/null | awk -v iface="$iface" -v ts="$timestamp" '
        /^Station / {
            if (mac != "") {
                print iface "|" mac "|" tx_pkts "|" tx_mcs
            }
            mac=$2
            tx_pkts=""
            tx_mcs="-1"
            next
        }
        /^[ \t]+tx packets:/ {
            tx_pkts=$3
            next
        }
        /^[ \t]+tx bitrate:/ {
            line=$0
            if (match($0, /EHT-MCS [0-9]+/)) {
                sub(/^.*EHT-MCS /, "", line)
                sub(/ .*/, "", line)
                tx_mcs=line
            } else if (match($0, /HE-MCS [0-9]+/)) {
                sub(/^.*HE-MCS /, "", line)
                sub(/ .*/, "", line)
                tx_mcs=line
            } else if (match($0, /VHT-MCS [0-9]+/)) {
                sub(/^.*VHT-MCS /, "", line)
                sub(/ .*/, "", line)
                tx_mcs=line
            } else if (match($0, /MCS [0-9]+/)) {
                sub(/^.*MCS /, "", line)
                sub(/ .*/, "", line)
                tx_mcs=line
            }
            next
        }
        END {
            if (mac != "") {
                print iface "|" mac "|" tx_pkts "|" tx_mcs
            }
        }
        ' >> "$TMPDIR/sample_$timestamp.txt"
    done
}

# Main loop
echo "MCS Sampler Daemon started (PID: $$)" >&2
echo "Sample interval: ${SAMPLE_INTERVAL}s, Aggregate interval: ${AGGREGATE_SAMPLES} samples" >&2

# Initialize tracking files
rm -f "$TMPDIR"/sample_*.txt
: > "$TMPDIR/prev_packets.txt"

sample_iteration=0

while true; do
    # Take a sample
    sample_all
    sample_iteration=$((sample_iteration + 1))
    
    # Calculate distributions every 60 seconds (to match upload interval)
    sample_count=$(ls -1 "$TMPDIR"/sample_*.txt 2>/dev/null | wc -l)
    
    if [ "$sample_count" -ge $AGGREGATE_SAMPLES ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Aggregating $sample_count samples (iteration $sample_iteration)" >&2
        # Get list of most recent 5 sample files to identify currently connected devices
        recent_samples=$(ls -1t "$TMPDIR"/sample_*.txt 2>/dev/null | head -n 5)
        
        # Process accumulated samples
        awk -F'|' -v recent_files="$recent_samples" '
        BEGIN {
            print "{"
            first_mac=1
            # Parse recent files list to identify currently connected MACs
            n_recent = split(recent_files, recent_arr, "\n")
        }
        FNR==1 {
            # Track which file we are processing
            current_file = FILENAME
            # Check if this is one of the recent files
            is_recent = 0
            for (r=1; r<=n_recent; r++) {
                if (current_file == recent_arr[r]) {
                    is_recent = 1
                    break
                }
            }
        }
        {
            iface=$1
            mac=$2
            tx_pkts=$3 + 0
            tx_mcs=$4
            
            key=mac
            
            # Mark MACs seen in recent files as currently connected
            if (is_recent) {
                currently_connected[key]=1
            }
            
            if (!(key in seen)) {
                seen[key]=1
                order[++n]=key
                prev_tx[key]=tx_pkts
                mac_iface[key]=iface
                next
            }
            
            dtx = tx_pkts - prev_tx[key]
            if (dtx < 0) dtx = 0
            
            tx_total[key] += dtx
            tx_hist[key "|" tx_mcs] += dtx
            
            prev_tx[key] = tx_pkts
            mac_iface[key] = iface
        }
        END {
            # Only output MACs that are currently connected (seen in recent samples)
            for (i=1; i<=n; i++) {
                mac=order[i]
                
                # Skip MACs not currently connected
                if (!(mac in currently_connected)) continue
                
                # Skip MACs with no traffic
                if (tx_total[mac] <= 0) continue
                
                if (first_mac == 0) {
                    print ","
                }
                first_mac=0
                
                printf "  \"%s\": {\n", mac
                printf "    \"interface\": \"%s\",\n", mac_iface[mac]
                printf "    \"tx_mcs_distribution\": {"
                
                first_mcs=1
                for (m=0; m<=15; m++) {
                    key=mac "|" m
                    if (key in tx_hist && tx_hist[key] > 0) {
                        if (first_mcs == 0) printf ","
                        first_mcs=0
                        printf "\n      \"%d\": %d", m, tx_hist[key]
                    }
                }
                
                printf "\n    },\n"
                printf "    \"total_packets\": %d,\n", tx_total[mac]
                printf "    \"last_update\": %d\n", systime()
                printf "  }"
            }
            print "\n}"
        }
        ' "$TMPDIR"/sample_*.txt > "$OUTPUT_FILE.tmp"
        
        # Check how many MACs were written
        mac_count=$(grep -c "tx_mcs_distribution" "$OUTPUT_FILE.tmp" 2>/dev/null || echo 0)
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Generated JSON with $mac_count MACs" >&2
        
        mv "$OUTPUT_FILE.tmp" "$OUTPUT_FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Updated $OUTPUT_FILE" >&2
        
        # Clean old samples, keep last 10 seconds for overlap margin
        find "$TMPDIR" -name "sample_*.txt" -type f | sort | head -n -10 | xargs rm -f 2>/dev/null || true
    fi
    
    sleep "$SAMPLE_INTERVAL"
done
