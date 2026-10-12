"""文件硬解和 RTSP 直播取帧。优先走 Rockchip mppvideodec。"""

from __future__ import annotations

import time

import numpy as np


def _gst():
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    return Gst


def sample_to_bgr(sample) -> np.ndarray:
    Gst = _gst()
    buf = sample.get_buffer()
    caps = sample.get_caps().get_structure(0)
    width = caps.get_value("width")
    height = caps.get_value("height")
    ok, mapping = buf.map(Gst.MapFlags.READ)
    if not ok:
        raise RuntimeError("无法映射视频帧")
    try:
        raw = np.frombuffer(mapping.data, dtype=np.uint8)
        need = height * width * 3
        if raw.size < need:
            raise RuntimeError(f"帧长度不足: {raw.size} < {need}")
        frame = raw[:need].reshape((height, width, 3)).copy()
    finally:
        buf.unmap(mapping)
    return frame


def _pull_all(pipeline_desc: str, limit: int = 2000) -> list[np.ndarray]:
    Gst = _gst()
    pipeline = Gst.parse_launch(pipeline_desc)
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    frames: list[np.ndarray] = []
    try:
        while len(frames) < limit:
            sample = sink.emit("try-pull-sample", 2 * Gst.SECOND)
            if sample is None:
                break
            frames.append(sample_to_bgr(sample))
    finally:
        pipeline.set_state(Gst.State.NULL)
    return frames


def decode_file(path: str) -> tuple[list[np.ndarray], str]:
    quoted = path.replace('"', "")
    attempts = [
        (
            "mppvideodec",
            f'filesrc location="{quoted}" ! qtdemux ! h264parse ! '
            f"mppvideodec dma-feature=false arm-afbc=false format=BGR ! "
            f"video/x-raw,format=BGR ! appsink name=sink emit-signals=false sync=false max-buffers=2 drop=false",
        ),
        (
            "avdec_h264",
            f'filesrc location="{quoted}" ! qtdemux ! h264parse ! avdec_h264 ! videoconvert ! '
            f"video/x-raw,format=BGR ! appsink name=sink emit-signals=false sync=false max-buffers=2 drop=false",
        ),
    ]
    errors = []
    for name, desc in attempts:
        try:
            frames = _pull_all(desc)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if frames:
            return frames, name
        errors.append(f"{name}: 没有解出帧")
    import cv2

    cap = cv2.VideoCapture(path)
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise RuntimeError("解码失败: " + "; ".join(errors))
    return frames, "opencv"


class RtspReader:
    """从 MediaMTX 的 RTSP 回环读直播，解码器用 mppvideodec。"""

    def __init__(self, url: str):
        self.url = url
        self.decoder = "mppvideodec"
        self.pipeline = None
        self.sink = None
        self._gst = None

    def _open(self):
        Gst = _gst()
        self._gst = Gst
        desc = (
            f"rtspsrc location={self.url} latency=200 protocols=tcp ! "
            "rtph264depay ! h264parse ! "
            "mppvideodec dma-feature=false arm-afbc=false format=BGR ! "
            "video/x-raw,format=BGR ! appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true"
        )
        self.pipeline = Gst.parse_launch(desc)
        self.sink = self.pipeline.get_by_name("sink")
        self.pipeline.set_state(Gst.State.PLAYING)
        self.decoder = "mppvideodec"

    def read(self, timeout_s: float = 0.4):
        if self.pipeline is None:
            try:
                self._open()
            except Exception:
                return None
        Gst = self._gst
        sample = self.sink.emit("try-pull-sample", int(timeout_s * Gst.SECOND))
        if sample is None:
            return None
        try:
            return sample_to_bgr(sample)
        except Exception:
            return None

    def close(self):
        if self.pipeline is not None and self._gst is not None:
            self.pipeline.set_state(self._gst.State.NULL)
            self.pipeline = None
