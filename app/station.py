"""地面站主循环：对齐视频和姿态，计算视场并贴地。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from app.aircraft.registry import default_model_id, get_model
from app.catalog import cue_marks, load_catalog
from app.compare import colorize_difference
from app.coord import bd09_to_wgs84, distance_m, enu_meters, offset_wgs, wgs84_to_bd09, wgs84_to_gcj02
from app.geometry import corners_to_wgs, project_corners, warp_north_up
from app.mqtt_client import MqttSubscriber
from app.preview_publish import PreviewPublisher
from app.telemetry import Telemetry, parse_osd, parse_srt
from app.telemetry_merge import TelemetryMerger
from app.video_source import RtspReader, decode_file


class Station:
    def __init__(self, root: Path, config: dict):
        self.root = root
        self.config = config
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.frames: list[np.ndarray] = []
        self.cues: list[Telemetry] = []
        self.decoder = ""
        self.index = 0
        self.mode = config.get("source", "file")
        self.bias_east = 0.0
        self.bias_north = 0.0
        self.seq = 0
        self.frame_jpg = b""
        self.drape_png = b""
        self.diff_png = b""
        self.pre_png = b""
        self.pre_bounds = None
        self.pre_bgr = None
        self.state: dict = {"message": "正在启动"}
        self.track_wgs: list[tuple[float, float]] = []
        self.mqtt_telem: Telemetry | None = None
        self.mqtt_rx = 0.0
        self.rtsp = RtspReader(config.get("rtsp_url", "rtsp://127.0.0.1:8554/live"))
        self.preview = PreviewPublisher()
        self.fps = 8.0
        self.paused = False
        self.catalog: list[dict] = []
        self.clip_id = ""
        self.loading = False
        self.load_error = ""
        self._load_gen = 0
        self._pending = None
        self._jump: int | None = None
        self._held_index: int | None = None
        self._last_heavy = 0.0
        self._last_bounds = None
        self._diff_ratio = 0.0
        self._ocr_last = 0.0
        self._ocr_busy = False
        self._fields_status: dict = {}
        self._missing_fields: list[str] = []
        self.merger = TelemetryMerger(
            default_hfov=float(config.get("hfov_deg", 64)),
            default_vfov=float(config.get("vfov_deg", 48)),
        )
        self._sync_aircraft_flags()
        self.mqtt = MqttSubscriber(
            config.get("mqtt_host", "127.0.0.1"),
            int(config.get("mqtt_port", 1883)),
            config.get("mqtt_topic", "thing/product/+/osd"),
            self._on_mqtt,
        )
        self._mqtt_enabled = bool(config.get("mqtt_enabled", True))

    def _sync_aircraft_flags(self):
        model = get_model(self.config.get("aircraft_id") or default_model_id())
        self.merger.default_hfov = float(self.config.get("hfov_deg", model.hfov_deg if model else 64))
        self.merger.default_vfov = float(self.config.get("vfov_deg", model.vfov_deg if model else 48))
        self.merger.require_height = bool(model.requires_manual_height) if model else False

    def load(self):
        pre_path = self.root / self.config.get("pre_image", "samples/pre_disaster.png")
        meta_path = self.root / self.config.get("pre_meta", "samples/pre_disaster.json")
        if pre_path.exists() and meta_path.exists():
            self.pre_bgr = cv2.imread(str(pre_path), cv2.IMREAD_COLOR)
            self.pre_bounds = json.loads(meta_path.read_text())
            ok, buf = cv2.imencode(".png", self.pre_bgr)
            if ok:
                self.pre_png = buf.tobytes()
        self.catalog = load_catalog(self.root)
        clip = self._clip_by_id(self.config.get("clip")) or self._clip_by_video(self.config.get("video"))
        if clip is not None:
            self.clip_id = clip["id"]
            self.frames, self.cues, self.decoder = self._read_clip(clip)
            self.preview.publish(self.root / clip["video"])
        if self._mqtt_enabled:
            self.mqtt.start()

    def apply_settings(self, changed: list[str]):
        """设置页保存后热更新。"""
        self._sync_aircraft_flags()
        if "rtsp_url" in changed:
            self.rtsp.reopen(self.config.get("rtsp_url", "rtsp://127.0.0.1:8554/live"))
        mqtt_keys = {"mqtt_host", "mqtt_port", "mqtt_topic", "mqtt_enabled"}
        if mqtt_keys & set(changed):
            enabled = bool(self.config.get("mqtt_enabled", True))
            self._mqtt_enabled = enabled
            self.mqtt.stop()
            if enabled:
                self.mqtt = MqttSubscriber(
                    self.config.get("mqtt_host", "127.0.0.1"),
                    int(self.config.get("mqtt_port", 1883)),
                    self.config.get("mqtt_topic", "thing/product/+/osd"),
                    self._on_mqtt,
                )
                self.mqtt.start()
            else:
                with self.lock:
                    self.mqtt_telem = None
                    self.mqtt_rx = 0.0

    def set_telemetry_override(self, values: dict, locked: list[str] | None = None):
        with self.lock:
            self.merger.set_manual(values, locked)

    def clear_telemetry_override(self):
        with self.lock:
            self.merger.clear_manual()

    def _clip_by_id(self, clip_id: str | None):
        if not clip_id:
            return None
        for clip in self.catalog:
            if clip["id"] == clip_id and clip["ready"]:
                return clip
        return None

    def _clip_by_video(self, video: str | None):
        if not video:
            return None
        for clip in self.catalog:
            if clip["video"] == video and clip["ready"]:
                return clip
        return None

    def _read_clip(self, clip: dict):
        frames, decoder = decode_file(str(self.root / clip["video"]))
        cues = parse_srt(
            (self.root / clip["srt"]).read_text(encoding="utf-8"),
            default_hfov=float(self.config.get("hfov_deg", 64)),
            default_vfov=float(self.config.get("vfov_deg", 48)),
        )
        count = min(len(frames), len(cues))
        if count == 0:
            raise RuntimeError("录像和姿态对不上")
        return frames[:count], cues[:count], decoder

    def _clip_state(self) -> dict:
        label = ""
        for clip in self.catalog:
            if clip["id"] == self.clip_id:
                label = clip["label"]
                break
        model = get_model(self.config.get("aircraft_id") or default_model_id())
        return {
            "clip_id": self.clip_id,
            "clip_label": label,
            "loading": self.loading,
            "aircraft_id": model.id if model else "",
            "aircraft_label": model.label if model else "",
            "ocr_enabled": bool(self.config.get("ocr_enabled", True)),
            "fields_status": dict(self._fields_status),
            "missing_fields": list(self._missing_fields),
            "manual_locked": sorted(self.merger.locked),
            "webrtc_page": self.config.get("webrtc_page", "http://127.0.0.1:8889/live/"),
            **cue_marks(self.cues, self.fps),
        }

    def _on_mqtt(self, _topic: str, payload: bytes):
        if not self._mqtt_enabled:
            return
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        telem = parse_osd(
            data,
            default_hfov=float(self.config.get("hfov_deg", 64)),
            default_vfov=float(self.config.get("vfov_deg", 48)),
        )
        if telem is None:
            return
        with self.lock:
            self.mqtt_telem = telem
            self.mqtt_rx = time.time()

    def set_mode(self, mode: str):
        self.select(mode, self.clip_id if mode == "file" else None)

    def select(self, mode: str, clip_id: str | None = None):
        if mode == "rtmp":
            self.preview.stop()
            self.rtsp.reopen()
            with self.lock:
                self._load_gen += 1
                self._pending = None
                self.loading = False
                self.mode = "rtmp"
                self.track_wgs.clear()
                self.state = dict(self.state)
                self.state["mode"] = "rtmp"
                self.state["loading"] = False
                self.state["message"] = "正在接收直播。"
                self.seq += 1
                self.state["seq"] = self.seq
            return
        if mode != "file":
            raise ValueError("mode 只能是 file 或 rtmp")
        self.catalog = load_catalog(self.root)
        clip = self._clip_by_id(clip_id or self.clip_id)
        if clip is None:
            raise ValueError("没有这段录像")
        self.preview.publish(self.root / clip["video"])
        if clip["id"] == self.clip_id and self.mode == "file" and self.frames and not self.loading:
            with self.lock:
                self.mode = "file"
                self.paused = False
            return
        with self.lock:
            self._load_gen += 1
            gen = self._load_gen
            self._pending = None
            self.loading = True
            self.paused = True
            self.state = dict(self.state)
            self.state["loading"] = True
            self.state["message"] = "正在解码这段录像…"
            self.seq += 1
            self.state["seq"] = self.seq
        threading.Thread(target=self._load_worker, args=(clip, gen), name="clip-load", daemon=True).start()

    def _load_worker(self, clip: dict, gen: int):
        try:
            frames, cues, decoder = self._read_clip(clip)
            error = ""
        except Exception as exc:
            frames, cues, decoder = [], [], ""
            error = str(exc)
        with self.lock:
            if gen != self._load_gen:
                return
            self.load_error = error
            self._pending = (frames, cues, decoder, clip["id"])

    def seek(self, time_s: float, pause: bool = True):
        with self.lock:
            if not self.cues:
                return
            self._jump = max(0, min(len(self.cues) - 1, int(time_s * self.fps)))
            self.paused = pause
            self._held_index = None
            self.mode = "file"

    def set_paused(self, paused: bool):
        with self.lock:
            self.paused = bool(paused)
            self._held_index = None

    def calibrate(self, true_lat: float, true_lon: float, observed_lat: float, observed_lon: float, observed_crs: str):
        if observed_crs == "bd09":
            observed_lon, observed_lat = bd09_to_wgs84(observed_lon, observed_lat)
        east, north = enu_meters(observed_lat, observed_lon, true_lat, true_lon)
        with self.lock:
            self.bias_east = east
            self.bias_north = north
            self.track_wgs.clear()
        return {"bias_east_m": east, "bias_north_m": north}

    def clear_bias(self):
        with self.lock:
            self.bias_east = 0.0
            self.bias_north = 0.0
            self.track_wgs.clear()

    def _maybe_ocr(self, frame: np.ndarray):
        """OCR 放到后台线程，避免 HZ 识别 10~30s 卡住直播取帧。"""
        if not self.config.get("ocr_enabled", True):
            return
        now = time.time()
        model = get_model(self.config.get("aircraft_id") or default_model_id())
        if model is None:
            return
        min_interval = 18.0 if getattr(model, "id", "") == "hz_gcs" else 0.35
        if now - self._ocr_last < min_interval or self._ocr_busy:
            return
        self._ocr_last = now
        self._ocr_busy = True
        frame_copy = frame.copy()
        threading.Thread(
            target=self._ocr_worker,
            args=(frame_copy, model.id),
            name="hud-ocr",
            daemon=True,
        ).start()

    def _ocr_worker(self, frame: np.ndarray, model_id: str):
        try:
            model = get_model(model_id)
            if model is None:
                return
            raw = model.parse_hud(frame)
            mapped: dict[str, float] = {}
            if "lat" in raw:
                mapped["lat"] = raw["lat"]
            if "lon" in raw:
                mapped["lon"] = raw["lon"]
            if "yaw" in raw:
                mapped["yaw"] = raw["yaw"]
            if "pitch_down" in raw:
                mapped["pitch"] = -abs(raw["pitch_down"]) if model.negate_pitch else raw["pitch_down"]
            elif "pitch" in raw:
                mapped["pitch"] = raw["pitch"]
            if "roll" in raw:
                mapped["roll"] = raw["roll"]
            if "rel_alt" in raw:
                mapped["rel_alt"] = raw["rel_alt"]
            with self.lock:
                self.merger.ocr = mapped
        except Exception:
            pass
        finally:
            self._ocr_busy = False

    def _live_telem(self) -> Telemetry | None:
        with self.lock:
            mqtt = self.mqtt_telem if (self.mqtt_rx and time.time() - self.mqtt_rx < 2.0) else None
            self.merger.mqtt = mqtt
            telem, status, missing = self.merger.merge()
            self._fields_status = status
            self._missing_fields = missing
            return telem

    def run(self):
        next_tick = time.perf_counter()
        while not self.stop_event.is_set():
            with self.lock:
                pending = self._pending
                self._pending = None
                if pending is not None:
                    self.loading = False
            if pending is not None:
                frames, cues, decoder, clip_id = pending
                self.frames = frames
                self.cues = cues
                self.decoder = decoder
                self.clip_id = clip_id
                self.index = 0
                self.paused = False
                self._held_index = None
                self._last_bounds = None
                self.track_wgs.clear()
                with self.lock:
                    self.mode = "file"
                if self.frames:
                    self._publish_index(0)
                else:
                    self._publish_wait(self.load_error or "这段录像解不出来。")
                continue
            with self.lock:
                mode = self.mode
                jump = self._jump
                self._jump = None
                paused = self.paused
            if mode == "file":
                if jump is not None:
                    self.index = jump
                if paused:
                    if self._held_index != self.index:
                        self._publish_index(self.index)
                        self._held_index = self.index
                    else:
                        self.stop_event.wait(0.3)
                    continue
                self._held_index = None
                self._step_file()
                next_tick += 1.0 / self.fps
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    self.stop_event.wait(delay)
                else:
                    next_tick = time.perf_counter()
            else:
                frame = self.rtsp.read(timeout_s=0.8)
                if frame is None:
                    self._publish_wait("等待 RTSP 直播。请到设置页确认取流地址，并确认 MediaMTX 已收到推流。")
                    self.stop_event.wait(0.2)
                    continue
                # 先出画面，OCR 后台跑，保证网页实时刷新
                telem = self._live_telem()
                model = get_model(self.config.get("aircraft_id") or default_model_id())
                drape_frame = model.crop_camera(frame) if model else frame
                self._publish(drape_frame, telem, "rtmp", heavy=True, preview_frame=frame)
                self._maybe_ocr(frame)

    def _step_file(self):
        if not self.frames:
            self._publish_wait("没有样例录像。请先运行 tools/generate_sample.py")
            return
        frame = self.frames[self.index]
        telem = self.cues[self.index]
        self.index = (self.index + 1) % len(self.frames)
        with self.lock:
            self._fields_status = {k: "srt" for k in ("lat", "lon", "yaw", "pitch", "height_agl")}
            self._missing_fields = []
        self._publish(frame, telem, "file", heavy=True)

    def _publish_index(self, index: int):
        frame = self.frames[index]
        telem = self.cues[index]
        self.index = index
        with self.lock:
            self._fields_status = {k: "srt" for k in ("lat", "lon", "yaw", "pitch", "height_agl")}
            self._missing_fields = []
        self._publish(frame, telem, "file", heavy=True)

    def _publish_wait(self, message: str):
        with self.lock:
            self.seq += 1
            self.state = {
                "seq": self.seq,
                "mode": self.mode,
                "message": message,
                "decoder": self.decoder,
                "mqtt_connected": self.mqtt.connected if self._mqtt_enabled else False,
                **self._clip_state(),
            }

    def _publish(
        self,
        frame: np.ndarray,
        telem: Telemetry | None,
        mode: str,
        heavy: bool = True,
        preview_frame: np.ndarray | None = None,
    ):
        now = time.perf_counter()
        if heavy and not self.paused and now - self._last_heavy < 0.35:
            heavy = False
        show = preview_frame if preview_frame is not None else frame
        ok, jpg = cv2.imencode(".jpg", show, [cv2.IMWRITE_JPEG_QUALITY, 70])
        jpg_bytes = jpg.tobytes() if ok else b""
        drape_bytes = b""
        diff_bytes = b""
        state = self._measure(frame, telem, mode, heavy=heavy)
        if state.get("drape") is not None:
            drape = state.pop("drape")
            ok, png = cv2.imencode(".png", drape.image)
            if ok:
                drape_bytes = png.tobytes()
            if self.pre_bgr is not None and self.pre_bounds is not None:
                heat, ratio = colorize_difference(drape, self.pre_bgr, self.pre_bounds)
                self._diff_ratio = ratio
                state["diff_ratio"] = ratio
                if heat is not None:
                    ok, buf = cv2.imencode(".png", heat)
                    if ok:
                        diff_bytes = buf.tobytes()
            self._last_heavy = time.perf_counter()
        else:
            state.pop("drape", None)
            state["diff_ratio"] = self._diff_ratio
        with self.lock:
            self.seq += 1
            state["seq"] = self.seq
            self.frame_jpg = jpg_bytes
            if drape_bytes:
                self.drape_png = drape_bytes
            if diff_bytes:
                self.diff_png = diff_bytes
            self.state = state

    def _measure(self, frame: np.ndarray, telem: Telemetry | None, mode: str, heavy: bool = True) -> dict:
        height, width = frame.shape[:2]
        base = {
            "mode": mode,
            "decoder": self.decoder if mode == "file" else self.rtsp.decoder,
            "message": "",
            "frame_w": width,
            "frame_h": height,
            "drape": None,
            "mqtt_connected": self.mqtt.connected if self._mqtt_enabled else False,
            "bias_east_m": self.bias_east,
            "bias_north_m": self.bias_north,
            "pre": _public_pre(self.pre_bounds),
            "paused": self.paused,
            **self._clip_state(),
        }
        if telem is None:
            if mode == "file":
                note = "样例录像没有和画面对齐的姿态字幕。"
            else:
                miss = "、".join(self._missing_fields) if self._missing_fields else "姿态"
                note = f"有画面，还缺贴地所需：{miss}。可在侧栏手动锁定，或检查 OCR/MQTT。"
            base.update({"view": "no-telemetry", "message": note})
            return base
        lat, lon = offset_wgs(telem.lat, telem.lon, self.bias_east, self.bias_north)
        footprint = project_corners(
            width,
            height,
            telem.hfov,
            telem.vfov,
            telem.yaw,
            telem.pitch,
            telem.roll,
            telem.height_agl,
        )
        bd_lon, bd_lat = wgs84_to_bd09(lon, lat)
        gcj_lon, gcj_lat = wgs84_to_gcj02(lon, lat)
        self.track_wgs.append((lat, lon))
        if len(self.track_wgs) > 400:
            self.track_wgs = self.track_wgs[-400:]
        corners_wgs = []
        corners_bd = []
        bounds = self._last_bounds
        if footprint.ok:
            corners_wgs = [{"lat": la, "lon": lo} for la, lo in corners_to_wgs(lat, lon, footprint.corners_enu)]
            for item in corners_wgs:
                blo, bla = wgs84_to_bd09(item["lon"], item["lat"])
                corners_bd.append({"lat": bla, "lon": blo})
            if heavy:
                drape = warp_north_up(frame, footprint.corners_enu, lat, lon, src_px=footprint.src_px)
                if drape is not None:
                    base["drape"] = drape
                    bounds = drape.bounds()
                    self._last_bounds = bounds
        track = self.track_wgs[-80:]
        if len(track) > 40:
            track = track[::2]
        note = (
            "垂直向下，画面贴在飞机正下方。"
            if footprint.ok and footprint.view == "nadir"
            else ("视线没有落到地面。" if not footprint.ok else "向前斜看，黄色四边形才是真实视场。高出地平线的部分没有贴。")
        )
        if self.loading:
            note = "正在解码这段录像…"
        elif self.paused:
            note = "已暂停。" + note
        duration = len(self.cues) / self.fps if self.cues else 0
        base.update(
            {
                "view": footprint.view if footprint.ok else "lost",
                "time_s": telem.time_s,
                "duration_s": duration,
                "yaw": telem.yaw,
                "pitch": telem.pitch,
                "roll": telem.roll,
                "hfov": telem.hfov,
                "vfov": telem.vfov,
                "zoom": telem.zoom,
                "height_agl": telem.height_agl,
                "telemetry_source": telem.source,
                "wgs": {"lat": lat, "lon": lon, "alt": telem.abs_alt},
                "gcj02": {"lat": gcj_lat, "lon": gcj_lon},
                "bd09": {"lat": bd_lat, "lon": bd_lon},
                "wgs_bd_offset_m": distance_m(lat, lon, bd_lat, bd_lon),
                "corners_wgs": corners_wgs,
                "corners_bd": corners_bd,
                "bounds_wgs": bounds,
                "bounds_bd": _bounds_to_bd(bounds) if bounds else None,
                "track_wgs": [{"lat": la, "lon": lo} for la, lo in track],
                "message": note,
            }
        )
        return base

    def snapshot(self):
        with self.lock:
            return dict(self.state), self.frame_jpg, self.drape_png, self.diff_png, self.pre_png


def _public_pre(bounds):
    if not bounds:
        return None
    return {
        "west": bounds["west"],
        "south": bounds["south"],
        "east": bounds["east"],
        "north": bounds["north"],
        "center_lat": bounds.get("center_lat"),
        "center_lon": bounds.get("center_lon"),
    }


def _bounds_to_bd(bounds: dict) -> dict:
    west_lon, south_lat = wgs84_to_bd09(bounds["west"], bounds["south"])
    east_lon, north_lat = wgs84_to_bd09(bounds["east"], bounds["north"])
    return {"west": west_lon, "south": south_lat, "east": east_lon, "north": north_lat}
