"""读写 config.json，供设置页按模块更新。"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.aircraft.registry import default_model_id, get_model, list_models

STREAM_KEYS = ("rtsp_url", "webrtc_page", "rtmp_hint_port", "rtmp_ingest")
MQTT_KEYS = ("mqtt_host", "mqtt_port", "mqtt_topic", "mqtt_enabled")
AIRCRAFT_KEYS = ("aircraft_id", "ocr_enabled", "hfov_deg", "vfov_deg")


def _local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _rtmp_path(url: str) -> str:
    try:
        parsed = urlparse(url)
        path = parsed.path or "/live"
        return path if path.startswith("/") else f"/{path}"
    except Exception:
        return "/live"


def _default_rtmp(config: dict) -> tuple[str, str, int]:
    """返回 (展示用推流地址, 板子IP推流地址, 端口)。"""
    port = int(config.get("rtmp_hint_port", 1935))
    ip = _local_ip()
    path = "/live"
    stored = str(config.get("rtmp_ingest") or "").strip()
    if stored and _valid_url(stored, ("rtmp", "rtmps")):
        path = _rtmp_path(stored)
        try:
            parsed = urlparse(stored)
            if parsed.port:
                port = int(parsed.port)
        except Exception:
            pass
        ingest = stored
    else:
        ingest = f"rtmp://{ip}:{port}{path}"
    lan = f"rtmp://{ip}:{port}{path}"
    return ingest, lan, port


def load_raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_raw(path: Path, config: dict):
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def public_settings(config: dict) -> dict:
    ingest, lan, port = _default_rtmp(config)
    aircraft_id = config.get("aircraft_id") or default_model_id()
    model = get_model(aircraft_id)
    return {
        "stream": {
            "rtmp_ingest": ingest,
            "rtmp_ingest_lan": lan,
            "rtmp_ingest_localhost": f"rtmp://127.0.0.1:{port}{_rtmp_path(ingest)}",
            "rtsp_url": config.get("rtsp_url", "rtsp://127.0.0.1:8554/live"),
            "webrtc_page": config.get("webrtc_page", "http://127.0.0.1:8889/live/"),
            "rtmp_hint_port": port,
        },
        "mqtt": {
            "mqtt_enabled": bool(config.get("mqtt_enabled", True)),
            "mqtt_host": config.get("mqtt_host", "127.0.0.1"),
            "mqtt_port": int(config.get("mqtt_port", 1883)),
            "mqtt_topic": config.get("mqtt_topic", "thing/product/+/osd"),
        },
        "aircraft": {
            "aircraft_id": aircraft_id,
            "ocr_enabled": bool(config.get("ocr_enabled", True)),
            "hfov_deg": float(config.get("hfov_deg", model.hfov_deg if model else 64)),
            "vfov_deg": float(config.get("vfov_deg", model.vfov_deg if model else 48)),
            "models": list_models(),
            "notes": model.notes if model else "",
            "requires_manual_height": bool(model.requires_manual_height) if model else False,
        },
        "baidu_ak_configured": bool(config.get("baidu_ak")),
    }


def _valid_url(url: str, schemes: tuple[str, ...]) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in schemes and bool(parsed.netloc or parsed.path)


def apply_section(config: dict, section: str, body: dict) -> dict[str, Any]:
    """就地更新 config，返回变更键列表。"""
    changed: list[str] = []
    if section == "stream":
        if "rtmp_ingest" in body:
            url = str(body["rtmp_ingest"]).strip()
            if not _valid_url(url, ("rtmp", "rtmps")):
                raise ValueError("推流地址必须是 rtmp:// 开头")
            config["rtmp_ingest"] = url
            changed.append("rtmp_ingest")
            try:
                parsed = urlparse(url)
                if parsed.port:
                    config["rtmp_hint_port"] = int(parsed.port)
                    changed.append("rtmp_hint_port")
            except Exception:
                pass
        if "rtsp_url" in body:
            url = str(body["rtsp_url"]).strip()
            if not _valid_url(url, ("rtsp", "rtsps")):
                raise ValueError("站内取流必须是 rtsp:// 地址")
            config["rtsp_url"] = url
            changed.append("rtsp_url")
        if "webrtc_page" in body:
            url = str(body["webrtc_page"]).strip()
            if not _valid_url(url, ("http", "https")):
                raise ValueError("预览页必须是 http(s) 地址")
            config["webrtc_page"] = url
            changed.append("webrtc_page")
        if "rtmp_hint_port" in body:
            config["rtmp_hint_port"] = int(body["rtmp_hint_port"])
            changed.append("rtmp_hint_port")
    elif section == "mqtt":
        if "mqtt_enabled" in body:
            config["mqtt_enabled"] = bool(body["mqtt_enabled"])
            changed.append("mqtt_enabled")
        if "mqtt_host" in body:
            config["mqtt_host"] = str(body["mqtt_host"]).strip() or "127.0.0.1"
            changed.append("mqtt_host")
        if "mqtt_port" in body:
            config["mqtt_port"] = int(body["mqtt_port"])
            changed.append("mqtt_port")
        if "mqtt_topic" in body:
            config["mqtt_topic"] = str(body["mqtt_topic"]).strip()
            changed.append("mqtt_topic")
    elif section == "aircraft":
        if "aircraft_id" in body:
            model_id = str(body["aircraft_id"]).strip()
            if get_model(model_id) is None:
                raise ValueError(f"未知机型: {model_id}")
            config["aircraft_id"] = model_id
            changed.append("aircraft_id")
            model = get_model(model_id)
            if model and "hfov_deg" not in body:
                config["hfov_deg"] = model.hfov_deg
                changed.append("hfov_deg")
            if model and "vfov_deg" not in body:
                config["vfov_deg"] = model.vfov_deg
                changed.append("vfov_deg")
        if "ocr_enabled" in body:
            config["ocr_enabled"] = bool(body["ocr_enabled"])
            changed.append("ocr_enabled")
        if "hfov_deg" in body:
            config["hfov_deg"] = float(body["hfov_deg"])
            changed.append("hfov_deg")
        if "vfov_deg" in body:
            config["vfov_deg"] = float(body["vfov_deg"])
            changed.append("vfov_deg")
    else:
        raise ValueError("section 只能是 stream、mqtt 或 aircraft")
    return {"changed": changed}
