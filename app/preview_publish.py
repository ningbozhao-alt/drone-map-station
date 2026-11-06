"""把当前离线录像推到 MediaMTX，让图传预览和所选片源一致。"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

RTMP_URL = "rtmp://127.0.0.1:1935/live"
LOG_PATH = "/tmp/drone-map-ffmpeg.log"


class PreviewPublisher:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._path: Path | None = None

    def publish(self, video: Path):
        video = video.resolve()
        with self._lock:
            if self._path == video and self._proc and self._proc.poll() is None:
                return
            self._stop_locked()
            log = open(LOG_PATH, "ab", buffering=0)
            self._proc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-loglevel",
                    "warning",
                    "-nostdin",
                    "-re",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(video),
                    "-c",
                    "copy",
                    "-f",
                    "flv",
                    RTMP_URL,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self._path = video

    def stop(self):
        with self._lock:
            self._stop_locked()
            self._path = None

    def _stop_locked(self):
        """只停本类拉起的样例推流，勿 pkill 同地址上的外部桌面推流。"""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except ProcessLookupError:
                return
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass