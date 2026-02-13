WID=$(cat /home/pi/_RunScanner/voice/llm_browser_wid.txt)

xdotool windowactivate --sync "$WID"
sleep 0.2

echo "=== window geometry (what your REL uses) ==="
xdotool getwindowgeometry --shell "$WID"
echo

echo "=== move to (0,0) relative-to-window (xdotool's definition) ==="
xdotool mousemove --sync --window "$WID" 0 0
xdotool getmouselocation --shell
echo

echo "=== move to (100,100) relative-to-window (xdotool's definition) ==="
xdotool mousemove --sync --window "$WID" 100 100
xdotool getmouselocation --shell
echo

===

=== window geometry (what your REL uses) === 
WINDOW=12582916 
X=115 
Y=131 
WIDTH=710 
HEIGHT=460 
SCREEN=0 
=== move to (0,0) relative-to-window (xdotool's definition) === 
X=90 
Y=69 
SCREEN=0 
WINDOW=12582916 
=== move to (100,100) relative-to-window (xdotool's definition) === 
X=190 
Y=169 
SCREEN=0 
WINDOW=12582916 

===

dx = 115 - 90 = 25
dy = 131 - 69 = 62

REL_xdotool.x = REL_measured.x + 25
REL_xdotool.y = REL_measured.y + 62

===

# after run this script, move the mouse to the mic button in 5 seconds
WID=$(cat /home/pi/_RunScanner/voice/llm_browser_wid.txt)

echo ">>> You have 5 seconds. Move mouse onto the MIC button now..."
sleep 5

eval "$(xdotool getmouselocation --shell)"
MX="$X"; MY="$Y"

eval "$(xdotool getwindowgeometry --shell "$WID")"
WX="$X"; WY="$Y"

echo "REL=($((MX-WX)),$((MY-WY)))  ABS=($MX,$MY)  WIN_TL=($WX,$WY)"

===

WID=$(cat /home/pi/_RunScanner/voice/llm_browser_wid.txt)
MIC_RX=655 (fill in)
MIC_RY=404 (fill in)

xdotool windowactivate --sync "$WID"
sleep 0.2
xdotool mousemove --sync --window "$WID" "$MIC_RX" "$MIC_RY" click 1

===

