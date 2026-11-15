#!/bin/bash
# usage: synctest.sh <client|server|monitor> <minutes>
# Detaches from SSH. Writes to ~/synctest/: <host>.h264 .pts .log, and <host>.mon.csv
# (one line per minute: time, temp, throttled, chrony last offset).
role=$1; mins=$2
d=${OUTDIR:-~/synctest}; mkdir -p $d; cd $d
h=$(hostname)
mon() {
  echo "time,temp_c,throttled,chrony_offset_s" > $h.mon.csv
  for i in $(seq 0 $((mins+4))); do
    echo "$(date +%H:%M:%S),$(vcgencmd measure_temp | tr -dc 0-9.),$(vcgencmd get_throttled | cut -d= -f2),$(chronyc -n tracking | awk -F': ' '/Last offset/{print $2+0}')" >> $h.mon.csv
    sleep 60
  done
}
setsid nohup bash -c "$(declare -f mon); h=$h; mins=$mins; mon" >/dev/null 2>&1 < /dev/null &
[ "$role" = monitor ] && { echo "$h monitor started"; exit 0; }
setsid nohup rpicam-vid -n -t $(( (mins+2) * 60000 )) --sync $role \
  --mode 2304:1296:10:P --width 1920 --height 1080 --framerate 30 \
  --codec h264 --bitrate 16000000 --denoise cdn_off \
  --shutter 20000 --gain 2 --awbgains 1.5,1.5 \
  --autofocus-mode manual --lens-position 1 \
  --save-pts $h.pts -o $h.h264 > $h.log 2>&1 < /dev/null &
sleep 3
echo "$h $role started pid=$(pgrep -n rpicam-vid)"
