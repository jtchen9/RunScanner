#!/bin/sh
# Diagnostic script to check MCS sampler status

echo "====== MCS Sampler Status Check ======"
echo ""

# Check if sampler is running
echo "1. Service Status:"
/etc/init.d/ap-mcs-sampler status 2>&1
echo ""

# Check if output file exists
echo "2. Output File:"
if [ -f "/tmp/mcs_distributions.json" ]; then
    echo "   ✓ File exists: /tmp/mcs_distributions.json"
    ls -lh /tmp/mcs_distributions.json
    
    # Check file age (use last_update from JSON if available)
    last_update=$(python3 -c "import json; data=json.load(open('/tmp/mcs_distributions.json')); mac=list(data.keys())[0]; print(data[mac].get('last_update', 0))" 2>/dev/null)
    current_time=$(date +%s)
    
    if [ -n "$last_update" ] && [ "$last_update" != "0" ]; then
        # last_update is Unix timestamp
        file_age=$((current_time - last_update))
        echo "   File age (from last_update): ${file_age} seconds"
        echo "   Last update timestamp: $last_update"
        echo "   Current timestamp: $current_time"
    else
        # Fallback to file mtime
        file_mtime=$(stat -c %Y /tmp/mcs_distributions.json 2>/dev/null || stat -f %m /tmp/mcs_distributions.json 2>/dev/null)
        file_age=$((current_time - file_mtime))
        echo "   File age (from mtime): ${file_age} seconds"
    fi
    
    if [ $file_age -lt 60 ]; then
        echo "   ✓ File is recent (< 60s)"
    elif [ $file_age -lt 120 ]; then
        echo "   ⚠ File is getting old (60-120s)"
    else
        echo "   ✗ File is too old (> 120s) - sampler may be stuck"
    fi
else
    echo "   ✗ File does not exist: /tmp/mcs_distributions.json"
fi
echo ""

# Check file content
echo "3. File Content:"
if [ -f "/tmp/mcs_distributions.json" ]; then
    echo "   File size: $(wc -c < /tmp/mcs_distributions.json) bytes"
    
    # Check if valid JSON
    if python3 -m json.tool /tmp/mcs_distributions.json > /dev/null 2>&1; then
        echo "   ✓ Valid JSON format"
        
        # Count MACs
        mac_count=$(python3 -c "import json; data=json.load(open('/tmp/mcs_distributions.json')); print(len(data))" 2>/dev/null)
        echo "   MACs in file: $mac_count"
        
        # Show MAC addresses
        echo "   MAC addresses:"
        python3 -c "import json; data=json.load(open('/tmp/mcs_distributions.json')); [print(f'     - {mac}') for mac in data.keys()]" 2>/dev/null
        
        # Show sample entry
        echo ""
        echo "   Sample entry:"
        python3 -c "import json; data=json.load(open('/tmp/mcs_distributions.json')); mac=list(data.keys())[0] if data else None; print(json.dumps({mac: data[mac]} if mac else {}, indent=6))" 2>/dev/null
    else
        echo "   ✗ Invalid JSON format"
        echo "   First 200 chars:"
        head -c 200 /tmp/mcs_distributions.json
    fi
fi
echo ""

# Check currently connected stations
echo "4. Currently Connected Stations:"
for iface in wlan0.1 wlan0.2 wlan0.3 wlan1.1 wlan1.2 wlan1.3 wlan2.1 wlan2.2 wlan2.3; do
    stations=$(iw dev $iface station dump 2>/dev/null | grep "^Station" | awk '{print $2}')
    if [ -n "$stations" ]; then
        echo "   $iface:"
        for sta in $stations; do
            echo "     - $sta"
        done
    fi
done
echo ""

# Check sample files
echo "5. Sample Files in /tmp/mcs_sampler_*:"
sampler_dirs=$(ls -d /tmp/mcs_sampler_* 2>/dev/null)
if [ -n "$sampler_dirs" ]; then
    for dir in $sampler_dirs; do
        sample_count=$(ls -1 "$dir"/sample_*.txt 2>/dev/null | wc -l)
        echo "   $dir: $sample_count sample files"
        if [ $sample_count -gt 0 ]; then
            oldest=$(ls -1t "$dir"/sample_*.txt 2>/dev/null | tail -1)
            newest=$(ls -1t "$dir"/sample_*.txt 2>/dev/null | head -1)
            echo "     Oldest: $(basename $oldest)"
            echo "     Newest: $(basename $newest)"
        fi
    done
else
    echo "   No sampler temp directories found"
fi
echo ""

# Check logs
echo "6. Recent Logs (if any):"
if [ -f "/opt/_RunScanner/ap_uploader.log" ]; then
    echo "   Last 10 lines of ap_uploader.log:"
    tail -10 /opt/_RunScanner/ap_uploader.log | sed 's/^/     /'
fi
echo ""

echo "====== End of Diagnostic ======"
