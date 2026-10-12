#!/bin/bash
# 启动样例地面站：生成录像、MediaMTX、RTMP 推流、本地页面。
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f samples/flight.mp4 || ! -f samples/flight.srt ]]; then
  python3 tools/generate_sample.py
fi

if ! ss -ltn | grep -q ':1935 '; then
  ./vendor/mediamtx mediamtx.yml > /tmp/drone-map-mediamtx.log 2>&1 &
  sleep 0.4
fi

if ! pgrep -f 'rtmp://127.0.0.1:1935/live' >/dev/null 2>&1; then
  ffmpeg -loglevel warning -nostdin -re -stream_loop -1 -i samples/flight.mp4 -c copy -f flv rtmp://127.0.0.1:1935/live > /tmp/drone-map-ffmpeg.log 2>&1 &
fi

exec python3 -m app.server
