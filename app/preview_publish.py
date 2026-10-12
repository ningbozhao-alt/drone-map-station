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
        self._signal_publishers(signal.SIGTERM)
        deadline = time.time() + 1.5
        while time.time() < deadline and self._publisher_pids():
            time.sleep(0.05)
        if self._publisher_pids():
            self._signal_publishers(signal.SIGKILL)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        self._proc = None

    def _signal_publishers(self, sig: int):
        for pid in self._publisher_pids():
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def _publisher_pids(self) -> list[int]:
        try:
            out = subprocess.check_output(["pgrep", "-af", RTMP_URL], text=True)
        except subprocess.CalledProcessError:
            return []
        pids = []
        for line in out.splitlines():
            pid_text, _, cmd = line.partition(" ")
            exe = cmd.split()[0] if cmd else ""
            if not exe.endswith("ffmpeg") or RTMP_URL not in cmd:
                continue
            try:
                pids.append(int(pid_text))
            except ValueError:
                continue
        return pids
