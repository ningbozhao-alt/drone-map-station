"""本地页面、姿态推送和贴地图片接口。"""

from __future__ import annotations

import json
import mimetypes
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.aircraft.registry import list_models
from app.catalog import load_catalog
from app.settings_store import apply_section, load_raw, public_settings, save_raw
from app.station import Station

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"
TILE_CACHE = ROOT / "cache" / "tiles"
TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
_TILE_PATH = re.compile(r"^/tiles/(\d+)/(\d+)/(\d+)\.jpg$")


class Handler(BaseHTTPRequestHandler):
    station: Station = None  # type: ignore
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        line = str(args[0]) if args else ""
        if line.startswith("GET /api/events") or line.startswith("GET /tiles/"):
            return
        super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, content_type: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode() or "{}")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/events":
            self._events()
            return
        if path == "/api/state":
            state, *_ = self.station.snapshot()
            self._json(200, _public_state(state))
            return
        if path == "/api/sources":
            station = self.station
            station.catalog = load_catalog(station.root)
            with station.lock:
                active = station.clip_id
                mode = station.mode
                loading = station.loading
            clips = [{"id": clip["id"], "label": clip["label"], "ready": clip["ready"]} for clip in station.catalog]
            self._json(200, {"clips": clips, "active": active, "mode": mode, "loading": loading})
            return
        if path == "/api/config":
            cfg = dict(self.station.config)
            cfg["webrtc_page"] = cfg.get("webrtc_page", "http://127.0.0.1:8889/live/")
            self._json(200, cfg)
            return
        if path == "/api/settings":
            self._json(200, public_settings(self.station.config))
            return
        if path == "/api/aircraft":
            self._json(200, {"models": list_models(), "active": self.station.config.get("aircraft_id")})
            return
        if path == "/api/telemetry_override":
            with self.station.lock:
                self._json(
                    200,
                    {
                        "manual": dict(self.station.merger.manual),
                        "locked": sorted(self.station.merger.locked),
                        "ocr": dict(self.station.merger.ocr),
                        "fields_status": dict(self.station._fields_status),
                        "missing_fields": list(self.station._missing_fields),
                    },
                )
            return
        state, frame, drape, diff, pre = self.station.snapshot()
        if path == "/api/frame.jpg":
            self._send(200 if frame else 404, frame or b"", "image/jpeg")
            return
        if path == "/api/drape.png":
            self._send(200 if drape else 404, drape or b"", "image/png")
            return
        if path == "/api/diff.png":
            self._send(200 if diff else 404, diff or b"", "image/png")
            return
        if path == "/api/pre.png":
            self._send(200 if pre else 404, pre or b"", "image/png")
            return
        tile = _TILE_PATH.match(path)
        if tile:
            body = read_tile(int(tile.group(1)), int(tile.group(2)), int(tile.group(3)))
            self._send(200 if body else 404, body or b"", "image/jpeg")
            return
        if path == "/":
            path = "/index.html"
        file_path = (WEB / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(WEB)) or not file_path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self._send(200, file_path.read_bytes(), mime)

    def do_PUT(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/settings":
                section = body.get("section")
                if not section:
                    raise ValueError("缺少 section")
                result = apply_section(self.station.config, section, body)
                save_raw(CONFIG_PATH, self.station.config)
                self.station.apply_settings(result["changed"])
                self._json(200, {"ok": True, **result, "settings": public_settings(self.station.config)})
                return
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"ok": False})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/source":
                self.station.select(body.get("mode", "file"), body.get("id"))
                self._json(200, {"ok": True, "mode": body.get("mode", "file"), "id": body.get("id")})
                return
            if path == "/api/seek":
                self.station.seek(float(body.get("t", 0)), pause=bool(body.get("pause", True)))
                self._json(200, {"ok": True})
                return
            if path == "/api/playback":
                self.station.set_paused(bool(body.get("paused", False)))
                self._json(200, {"ok": True, "paused": bool(body.get("paused", False))})
                return
            if path == "/api/calibrate":
                result = self.station.calibrate(
                    float(body["true_lat"]),
                    float(body["true_lon"]),
                    float(body["observed_lat"]),
                    float(body["observed_lon"]),
                    body.get("observed_crs", "wgs84"),
                )
                self._json(200, {"ok": True, **result})
                return
            if path == "/api/calibrate/reset":
                self.station.clear_bias()
                self._json(200, {"ok": True})
                return
            if path == "/api/aircraft":
                model_id = str(body.get("id") or "").strip()
                result = apply_section(self.station.config, "aircraft", {"aircraft_id": model_id})
                save_raw(CONFIG_PATH, self.station.config)
                self.station.apply_settings(result["changed"])
                self._json(200, {"ok": True, "active": model_id, "settings": public_settings(self.station.config)})
                return
            if path == "/api/telemetry_override":
                if body.get("clear"):
                    self.station.clear_telemetry_override()
                else:
                    self.station.set_telemetry_override(body.get("values") or {}, body.get("locked"))
                self._json(200, {"ok": True})
                return
            if path == "/api/settings":
                section = body.get("section")
                if not section:
                    raise ValueError("缺少 section")
                result = apply_section(self.station.config, section, body)
                save_raw(CONFIG_PATH, self.station.config)
                self.station.apply_settings(result["changed"])
                self._json(200, {"ok": True, **result, "settings": public_settings(self.station.config)})
                return
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"ok": False})

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        last = -1
        try:
            while not self.station.stop_event.is_set():
                state, *_ = self.station.snapshot()
                seq = state.get("seq", 0)
                if seq != last:
                    last = seq
                    data = json.dumps(_public_state(state), ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                if self.station.stop_event.wait(0.25):
                    break
        except (BrokenPipeError, ConnectionResetError):
            return


def read_tile(z: int, y: int, x: int) -> bytes | None:
    """拉取 Esri 卫星瓦片并缓存。坐标系是 WGS84，和无人机位置一致。"""
    limit = 1 << z
    if not (0 <= z <= 19 and 0 <= x < limit and 0 <= y < limit):
        return None
    path = TILE_CACHE / str(z) / str(y) / f"{x}.jpg"
    if path.is_file() and path.stat().st_size > 64:
        return path.read_bytes()
    request = urllib.request.Request(TILE_URL.format(z=z, y=y, x=x), headers={"User-Agent": "drone-map-station/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = response.read()
    except Exception:
        return None
    if not data.startswith(b"\xff\xd8"):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _public_state(state: dict) -> dict:
    out = {key: value for key, value in state.items() if key != "drape"}
    return out


def prefetch_offline_tiles():
    """把样例航线附近的卫星瓦片先存到磁盘，离线时仍能铺底图。"""
    import math

    south, west, north, east = 30.265, 120.145, 30.285, 120.168

    def row(lat, z):
        n = 1 << z
        rad = math.radians(max(-85, min(85, lat)))
        return int((1 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2 * n)

    def col(lon, z):
        return int((lon + 180) / 360 * (1 << z))

    for z in (15, 16, 17):
        y0, y1 = sorted((row(north, z), row(south, z)))
        x0, x1 = sorted((col(west, z), col(east, z)))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                read_tile(z, y, x)


def load_config() -> dict:
    config = load_raw(CONFIG_PATH)
    config.setdefault("aircraft_id", "dji_m30t_wide")
    config.setdefault("ocr_enabled", True)
    config.setdefault("mqtt_enabled", True)
    config.setdefault("rtmp_hint_port", 1935)
    return config


def main():
    config = load_config()
    station = Station(ROOT, config)
    print("正在用硬件解码器读取样例录像…")
    station.load()
    label = next((clip["label"] for clip in station.catalog if clip["id"] == station.clip_id), station.clip_id or "无")
    print(f"解码器 {station.decoder or '无'}，帧数 {len(station.frames)}，姿态 {len(station.cues)}，片源 {label}")
    Handler.station = station
    thread = __import__("threading").Thread(target=station.run, daemon=True, name="station")
    thread.start()
    __import__("threading").Thread(target=prefetch_offline_tiles, daemon=True, name="tiles").start()
    host = config.get("host", "0.0.0.0")
    port = int(config.get("port", 8090))
    server = ThreadingHTTPServer((host, port), Handler)
    webrtc = config.get("webrtc_page", "http://127.0.0.1:8889/live/")
    print(f"地面站 http://127.0.0.1:{port}/")
    print(f"图传预览 {webrtc}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        station.stop_event.set()
        station.preview.stop()
        station.mqtt.stop()
        station.rtsp.close()
        server.server_close()


if __name__ == "__main__":
    main()
