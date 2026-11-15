#!/bin/bash
# usage: [GOV=performance] [KEEP=1] diag.sh <label> <seconds> [extra rpicam-vid args...]
# SUDOPW read from stdin's first line when GOV is set (script itself comes after via bash -c).
label=$1; secs=$2; shift 2
d=~/preflight/diag; mkdir -p $d; cd $d
gov_path=/sys/devices/system/cpu/cpufreq/policy0/scaling_governor
if [ -n "$GOV" ]; then echo "$SUDOPW" | sudo -S -p "" sh -c "echo $GOV > $gov_path"; fi
base=(-n -t $((secs*1000)) --width 1920 --height 1080 --framerate 30 --codec h264
      --shutter 20000 --gain 2 --awbgains 1.5,1.5
      --mode 2304:1296:10:P --autofocus-mode manual --lens-position 1)
( while :; do echo "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq) $(vcgencmd measure_clock core | cut -d= -f2) $(vcgencmd measure_temp | tr -dc 0-9.)"; sleep 0.5; done ) > $label.freq &
fl=$!
rpicam-vid "${base[@]}" "$@" --save-pts $label.pts -o $label.h264 > $label.log 2>&1
rc=$?
kill $fl 2>/dev/null
if [ -n "$GOV" ]; then echo "$SUDOPW" | sudo -S -p "" sh -c "echo ondemand > $gov_path"; fi
gaps=$(grep -v '^#' $label.pts | awk 'NR>1{d=$1-p; if(d>50)g++} {p=$1} END{printf "frames=%d drops=%d", NR, g+0}')
clk=$(awk '{a+=$1; if($2>399e6)c++; if($3>t)t=$3} END{printf "arm_avg=%dMHz core400=%d%% tmax=%.1fC", a/NR/1000, 100*c/NR, t}' $label.freq)
mbps=$(awk -v s=$(stat -c %s $label.h264) -v t=$secs 'BEGIN{printf "%.1fMbps", s*8/t/1e6}')
echo "$label rc=$rc $gaps $mbps $clk gov=$(cat $gov_path)"
grep -iE "error|abort|terminate|what\(\)" $label.log | tail -2
[ -z "$KEEP" ] && rm -f $label.h264
