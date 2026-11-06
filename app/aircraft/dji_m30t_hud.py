"""大疆 M30T 录屏 HUD（对照 无人机2.jpg 自定义叠加布局）。"""

from __future__ import annotations

import re

import cv2
import numpy as np

from app.aircraft.base import AircraftModel

_PANEL = (0.005, 0.62, 0.34, 0.97)


def _ocr_panel(frame_bgr: np.ndarray) -> str:
    try:
        import pytesseract
    except ImportError:
        return ""
    h, w = frame_bgr.shape[:2]
    x0, y0, x1, y1 = _PANEL
    crop = frame_bgr[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]
    if crop.size == 0:
        return ""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    up = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    chunks = []
    for image in (up, bw, 255 - bw):
        try:
            text = pytesseract.image_to_string(image, lang="chi_sim+eng", config="--psm 6")
        except Exception:
            continue
        if text and text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def _fix_num(raw: str) -> float | None:
    cleaned = raw.replace(",", ".").replace(";", ".").replace(":", ".").replace("@", ".")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = parts[0] + "." + "".join(parts[1:])
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_m30t_hud_text(text: str) -> dict[str, float]:
    """从 OCR 文本提取姿态。忽略目标点坐标与可疑 H。"""
    out: dict[str, float] = {}
    if not text:
        return out
    # 俯角 10.90deg —— OCR 常把点认成 : 或 ;
    pitch_m = re.search(r"(?:俯角|原始俯角).{0,8}?([0-9]+[.,:;]?[0-9]*)\s*deg", text, re.I)
    if pitch_m:
        pitch = _fix_num(pitch_m.group(1))
        if pitch is not None and 0 <= pitch <= 95:
            out["pitch_down"] = pitch
    if "pitch_down" not in out:
        for raw in re.findall(r"([0-9]+[.,][0-9]+)\s*deg", text, re.I):
            val = _fix_num(raw)
            if val is not None and 1.0 <= val <= 89.0:
                out["pitch_down"] = val
                break

    yaw_m = re.search(r"方位角\s*[:：]?\s*([0-9]+[.,]?[0-9]*)\s*deg", text, re.I)
    if yaw_m:
        yaw = _fix_num(yaw_m.group(1))
        if yaw is not None and 0 <= yaw <= 360:
            out["yaw"] = yaw
    if "yaw" not in out:
        for raw in re.findall(r"([0-9]+[.,][0-9]+)\s*deg", text, re.I):
            val = _fix_num(raw)
            if val is not None and 90 <= val <= 360:
                out["yaw"] = val
                break

    drone_lat = re.search(r"无人机纬度\s*[:：]?\s*([0-9]+\.[0-9]+)", text)
    drone_lon = re.search(r"无人机经度\s*[:：]?\s*([0-9]+\.[0-9]+)", text)
    if drone_lat:
        lat = _fix_num(drone_lat.group(1))
        if lat is not None:
            out["lat"] = lat
    if drone_lon:
        lon = _fix_num(drone_lon.group(1))
        if lon is not None:
            out["lon"] = lon

    # 只有目标纬度时：取「无人机纬度」后的数；经度用非目标块的小数经度，或回退到面板上的经度
    if "lat" not in out:
        m = re.search(r"无人机[^\d]{0,12}([0-9]+\.[0-9]+)", text)
        if m:
            lat = _fix_num(m.group(1))
            if lat is not None and 0 < abs(lat) <= 90:
                out["lat"] = lat
    if "lon" not in out:
        # 避免把目标经度当成唯一来源时，仍可用（与飞机几乎重合）；标为 lon
        m = re.search(r"(?<!目标)经度\s*[:：]?\s*([0-9]+\.[0-9]+)", text)
        if not m:
            m = re.search(r"经度\s*[:：]?\s*([0-9]+\.[0-9]+)", text)
        if m:
            lon = _fix_num(m.group(1))
            if lon is not None and abs(lon) <= 180:
                out["lon"] = lon
    return out


class DjiM30tHud(AircraftModel):
    def __init__(self):
        super().__init__(
            id="dji_m30t_wide",
            label="大疆 M30T · 广角 HUD",
            notes="录屏叠加可识别飞机经纬度、俯角、方位角。离地高度请手动锁定，勿用画面上的 H。",
            hfov_deg=64.0,
            vfov_deg=48.0,
            # 去掉左右菜单与底栏仪表，保留主相机视口
            camera_crop=(0.07, 0.07, 0.93, 0.78),
            negate_pitch=True,
            requires_manual_height=True,
        )

    def parse_hud(self, frame_bgr: np.ndarray) -> dict[str, float]:
        text = _ocr_panel(frame_bgr)
        return parse_m30t_hud_text(text)
