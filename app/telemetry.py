"""SRT 与大疆 Cloud API OSD 的姿态解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Telemetry:
    lat: float
    lon: float
    rel_alt: float
    abs_alt: float
    yaw: float
    pitch: float
    roll: float
    hfov: float
    vfov: float
    time_s: float = 0.0
    zoom: float = 1.0
    source: str = ""

    @property
    def height_agl(self) -> float:
        if self.rel_alt and self.rel_alt > 0.5:
            return self.rel_alt
        return self.abs_alt


_NUM = r"([+-]?\d+(?:\.\d+)?)"


def _field(text: str, name: str) -> float | None:
    match = re.search(rf"(?:\[|\b){name}\s*:\s*{_NUM}", text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))


def _clock_to_seconds(text: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", text.strip())
    if not match:
        return 0.0
    hour, minute, second, frac = match.groups()
    base = int(hour) * 3600 + int(minute) * 60 + int(second)
    width = len(frac)
    return base + int(frac) / (10 ** width)


def parse_srt(text: str, default_hfov: float = 64.0, default_vfov: float = 48.0) -> list[Telemetry]:
    chunks = re.split(r"\n\s*\n", text.strip())
    samples: list[Telemetry] = []
    for chunk in chunks:
        if "latitude" not in chunk.lower() and "lat" not in chunk.lower():
            continue
        lat = _field(chunk, "latitude")
        lon = _field(chunk, "longitude")
        if lat is None or lon is None:
            continue
        rel_alt = _field(chunk, "rel_alt")
        abs_alt = _field(chunk, "abs_alt")
        if rel_alt is None:
            rel_alt = _field(chunk, "rel_alt") or 0.0
        if abs_alt is None:
            abs_alt = rel_alt
        yaw = _field(chunk, "gb_yaw")
        if yaw is None:
            yaw = _field(chunk, "yaw") or 0.0
        pitch = _field(chunk, "gb_pitch")
        if pitch is None:
            pitch = _field(chunk, "pitch")
        if pitch is None:
            pitch = -90.0
        roll = _field(chunk, "gb_roll")
        if roll is None:
            roll = _field(chunk, "roll") or 0.0
        hfov = _field(chunk, "hfov") or default_hfov
        vfov = _field(chunk, "vfov") or default_vfov
        time_line = ""
        for line in chunk.splitlines():
            if "-->" in line:
                time_line = line.split("-->", 1)[0]
                break
        samples.append(
            Telemetry(
                lat=lat,
                lon=lon,
                rel_alt=rel_alt or 0.0,
                abs_alt=abs_alt or 0.0,
                yaw=yaw,
                pitch=pitch,
                roll=roll,
                hfov=hfov,
                vfov=vfov,
                time_s=_clock_to_seconds(time_line),
                source="srt",
            )
        )
    return samples


def _walk(node, found: dict):
    if isinstance(node, dict):
        for key, value in node.items():
            lkey = str(key).lower()
            if lkey in {"latitude", "longitude", "height", "elevation", "attitude_head"} and isinstance(value, (int, float)):
                found[lkey] = float(value)
            if lkey in {"gimbal_pitch", "gimbal_roll", "gimbal_yaw", "zoom_factor"} and isinstance(value, (int, float)):
                found[lkey] = float(value)
            else:
                _walk(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)


def parse_osd(payload: dict, default_hfov: float = 64.0, default_vfov: float = 48.0) -> Telemetry | None:
    """解析 thing/product/{sn}/osd 这一类 JSON。相机云台优先于机身姿态。"""
    found: dict[str, float] = {}
    _walk(payload, found)
    if "latitude" not in found or "longitude" not in found:
        return None
    rel = found.get("elevation", 0.0)
    height = found.get("height", rel)
    yaw = found.get("gimbal_yaw", found.get("attitude_head", 0.0))
    pitch = found.get("gimbal_pitch", -90.0)
    roll = found.get("gimbal_roll", 0.0)
    zoom = found.get("zoom_factor", 1.0) or 1.0
    if zoom < 0.2:
        zoom = 1.0
    timestamp = payload.get("timestamp")
    time_s = float(timestamp) / 1000.0 if isinstance(timestamp, (int, float)) and timestamp > 1e11 else 0.0
    return Telemetry(
        lat=found["latitude"],
        lon=found["longitude"],
        rel_alt=rel,
        abs_alt=height,
        yaw=yaw,
        pitch=pitch,
        roll=roll,
        hfov=default_hfov / zoom,
        vfov=default_vfov / zoom,
        time_s=time_s,
        zoom=zoom,
        source="mqtt",
    )
