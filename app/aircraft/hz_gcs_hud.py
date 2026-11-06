"""HZ 系留无人机地面站桌面（状态管理面板）OCR。

自适应扫描画面右侧数字并按量级归类，不依赖固定分辨率/窗口位置。
离线可用：本机 Tesseract，无需联网训练。
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.aircraft.base import AircraftModel


@dataclass
class _NumHit:
    value: float
    raw: str
    x: float = 0.5
    y: float = 0.5
    conf: float = 50.0


def _float_num(raw: str) -> float | None:
    cleaned = raw.replace(",", ".").replace(";", ".").replace("\"", "")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = parts[0] + "." + "".join(parts[1:])
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _recover_coord(val: float, kind: str) -> float | None:
    if kind == "lon":
        if 70 <= abs(val) <= 140:
            return val
        s = str(int(abs(val)))
        # 19008173 → 缺前导 1 的 119.008173
        if len(s) == 8 and s.startswith("19"):
            s = "1" + s
        if abs(val) < 1e7 and len(s) < 8:
            return None
        if len(s) >= 8:
            head = int(s[:3])
            if 70 <= head <= 140:
                fixed = float(s[:3] + "." + s[3:])
                if 70 <= fixed <= 140:
                    return fixed if val >= 0 else -fixed
        return None
    if kind == "lat":
        if 1 <= abs(val) <= 55:
            return val
        if abs(val) < 1e6:
            return None
        s = str(int(abs(val)))
        # 9 位以上多半是航向 359000001，不是纬度
        if len(s) >= 9:
            return None
        # 排除经度长整数 119008178
        if len(s) >= 8 and 100 <= int(s[:3]) <= 140:
            return None
        # 优先 8 位（31761480）；7 位易误读
        if len(s) == 8 and s[0] in "2345":
            fixed = float(s[:2] + "." + s[2:])
            if 1 <= fixed <= 55:
                return fixed if val >= 0 else -fixed
        if len(s) == 7 and s[0] in "2345":
            fixed = float(s[:2] + "." + s[2:])
            if 1 <= fixed <= 55:
                return fixed if val >= 0 else -fixed
        return None
    return None


def _lon_ok(val: float, raw: str) -> float | None:
    """经度：小数且 70~140（≥5 位小数），或 ≥8 位整数可恢复。"""
    digits = re.sub(r"\D", "", raw)
    if "." in raw or "," in raw:
        if not (70 <= abs(val) <= 140):
            return None
        frac = re.split(r"[.,]", raw)[-1]
        # 真经度通常 ≥6 位小数；拒 126.3624 / 126.36240（飞行时间误插点）
        if len(frac) < 6 or len(digits) < 8:
            return None
        return val
    if len(digits) < 8:
        return None
    return _recover_coord(val, "lon")


def _lat_ok(val: float, raw: str) -> float | None:
    """纬度：小数且 1~55（足够小数位），或 ≥7 位整数可恢复（排除经度）。"""
    if val < 0:
        return None  # 本机型国内坐标为正；负号多为 OCR 噪点
    if "." in raw or "," in raw:
        if not (1 <= val <= 55):
            return None
        frac = re.split(r"[.,]", raw)[-1]
        if len(frac) < 4:  # 拒 6.0 / 28.0，要 31.761480 形态
            return None
        return val
    return _recover_coord(val, "lat")


def _looks_like_coord_int(val: float) -> bool:
    """长整数是否更像经/纬度丢小数点（勿当航向）。"""
    if abs(val) < 1e6:
        return False
    s = str(int(abs(val)))
    if len(s) >= 8 and 70 <= int(s[:3]) <= 140:
        return True  # 经度 119008178
    # 纬度常见 7~8 位（31761480）；9 位如 359000001 更像航向
    if 7 <= len(s) <= 8 and s[0] in "12345" and 10 <= int(s[:2]) <= 55:
        return True
    return False


def _recover_angle_or_alt(val: float, kind: str) -> float | None:
    if kind == "yaw":
        if 0 <= val <= 360:
            return val
        if 1000 <= val <= 3600:
            return val / 10.0
        # 35906 → 359.06
        if 10000 <= abs(val) <= 36099:
            cand = abs(val) / 100.0
            if 0 <= cand <= 360:
                return cand
        if 100000 <= abs(val) <= 360999:
            cand = abs(val) / 1000.0
            if 0 <= cand <= 360:
                return cand
        # 359000001 → 359.000001（OCR 丢小数点）；排除经纬长整数
        if abs(val) >= 1e7 and not _looks_like_coord_int(val):
            s = str(int(abs(val)))
            if 8 <= len(s) <= 12:
                for n in (3, 2):
                    head = int(s[:n])
                    if head > 360:
                        continue
                    cand = float(s[:n] + "." + s[n:])
                    if 0 <= cand <= 360:
                        return cand
    if kind == "pitch":
        if val in (900, 900.0) or abs(val - 900) < 1e-6:
            return -90.0
        if -95 <= val <= 95:
            return val
        if -950 <= val <= 950:
            return val / 10.0
    if kind == "alt":
        if 0.5 < val < 20000:
            # 3590 是航向×10，不是高度
            if 3000 <= val <= 3600:
                return None
            if val >= 10000 and val % 10 == 0 and val / 10 < 5000:
                return val / 10.0
            return val
    return None


def _crop_norm(frame_bgr: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    x0, y0, x1, y1 = box
    return frame_bgr[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]


def _prep_bw(crop_bgr: np.ndarray, scale: float, mode: str = "inv_otsu") -> np.ndarray:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    if mode == "clahe":
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if float(bw.mean()) < 127:
            bw = 255 - bw
    elif mode == "bright":
        _, bw = cv2.threshold(gray, 130, 255, cv2.THRESH_BINARY)
        if float(bw.mean()) < 127:
            bw = 255 - bw
    else:
        inv = 255 - gray
        _, bw = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.resize(bw, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def _tesseract(
    bw: np.ndarray,
    *,
    lang: str = "eng",
    whitelist: str | None = "0123456789.-",
    timeout_s: float = 12.0,
) -> str:
    with tempfile.TemporaryDirectory(prefix="hz_ocr_") as tmp:
        img_path = Path(tmp) / "in.png"
        out_base = Path(tmp) / "out"
        cv2.imwrite(str(img_path), bw)
        cmd = ["tesseract", str(img_path), str(out_base), "-l", lang, "--psm", "6"]
        if whitelist:
            cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
        try:
            subprocess.run(
                cmd,
                check=False,
                timeout=timeout_s,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""
        txt_path = Path(str(out_base) + ".txt")
        if not txt_path.is_file():
            return ""
        return txt_path.read_text(encoding="utf-8", errors="ignore")


def _ocr_right_panel_text(frame_bgr: np.ndarray, timeout_s: float = 15.0) -> str:
    """兼容旧接口：右侧面板数字 OCR。"""
    crop = _crop_norm(frame_bgr, (0.55, 0.08, 0.98, 0.72))
    if crop.size == 0:
        return ""
    ch = crop.shape[0]
    scale = max(2.5, min(4.0, 900.0 / max(ch, 1)))
    return _tesseract(_prep_bw(crop, scale), timeout_s=timeout_s)


def _hits_from_text(text: str) -> list[_NumHit]:
    hits: list[_NumHit] = []
    for raw in re.findall(r"-?\d+[.,]?\d*", text):
        val = _float_num(raw)
        if val is None:
            continue
        hits.append(_NumHit(value=val, raw=raw))
    return hits


def _merge_field(dst: dict[str, float], src: dict[str, float], key: str) -> None:
    if key not in src:
        return
    if key not in dst:
        dst[key] = src[key]
        return
    # 经纬度：保留小数位更多的
    if key in ("lon", "lat"):
        a, b = dst[key], src[key]
        sa, sb = f"{a:.8f}".rstrip("0"), f"{b:.8f}".rstrip("0")
        if len(sb.split(".")[-1]) > len(sa.split(".")[-1]):
            dst[key] = b
        return
    if key == "yaw" and 300 <= src[key] <= 360 and not (300 <= dst[key] <= 360):
        dst[key] = src[key]
        return
    if key == "pitch" and abs(src[key] + 90) < abs(dst[key] + 90):
        dst[key] = src[key]


def _classify_hits(hits: list[_NumHit]) -> dict[str, float]:
    out: dict[str, float] = {}
    if not hits:
        return out

    lon_cands: list[tuple[float, float]] = []
    for hit in hits:
        lon = _lon_ok(hit.value, hit.raw)
        if lon is None:
            continue
        # 拒绝没有足够精度的整数经度（如 101）
        if "." not in hit.raw and "," not in hit.raw and abs(hit.value) < 1e7:
            continue
        score = 10.0
        if "." in hit.raw or "," in hit.raw:
            score += 50
            frac = re.split(r"[.,]", hit.raw)[-1]
            score += min(len(frac), 6) * 4
        else:
            score += 15  # 长整数恢复，弱于真小数
        lon_cands.append((score, lon))
    if lon_cands:
        lon_cands.sort(key=lambda t: t[0], reverse=True)
        out["lon"] = lon_cands[0][1]

    lat_cands: list[tuple[float, float]] = []
    for hit in hits:
        lat = _lat_ok(hit.value, hit.raw)
        if lat is None:
            continue
        if "." not in hit.raw and "," not in hit.raw and abs(hit.value) < 1e6:
            continue
        if "lon" in out and abs(lat - out["lon"]) < 0.5:
            continue
        score = 10.0
        # 真小数优先，避免 20420011→20.42 压过 31.761480
        if "." in hit.raw or "," in hit.raw:
            score += 60
            frac = re.split(r"[.,]", hit.raw)[-1]
            score += min(len(frac), 6) * 5
            if 15 <= abs(lat) <= 54:
                score += 15
        else:
            digits = re.sub(r"\D", "", hit.raw)
            score += 12 + (20 if len(digits) == 8 else 0)
            if 15 <= abs(lat) <= 54:
                score += 8
        lat_cands.append((score, lat))
    if lat_cands:
        lat_cands.sort(key=lambda t: t[0], reverse=True)
        out["lat"] = lat_cands[0][1]

    alt_cands: list[tuple[float, float]] = []
    for hit in hits:
        # 排除年份/日期
        if 1900 <= abs(hit.value) <= 2100:
            continue
        alt = _recover_angle_or_alt(hit.value, "alt")
        if alt is None or not (20 < alt < 6000):
            continue
        score = 5.0
        if abs(hit.value) >= 1000:
            score += 15
        if "." in hit.raw:
            score += 10
        # 分辨率/杂讯
        if abs(alt - 128) < 0.2 or abs(hit.value - 1280) < 1:
            score -= 40
        if abs(hit.value - 1680) < 1 or abs(hit.value - 1920) < 1:
            score -= 40
        alt_cands.append((score, alt))
    if alt_cands:
        alt_cands.sort(key=lambda t: t[0], reverse=True)
        out["rel_alt"] = alt_cands[0][1]

    yaw_cands: list[tuple[float, float]] = []
    for hit in hits:
        yaw = _recover_angle_or_alt(hit.value, "yaw")
        if yaw is None or not (0 <= yaw <= 360):
            continue
        # 排除高度、组件号、分辨率
        if abs(hit.value - 1000) < 1 or abs(hit.value - 10000) < 1:
            continue
        if abs(hit.value - 101) < 0.1 or abs(hit.value - 42) < 0.1:
            continue
        if abs(hit.value - 1280) < 1 or abs(hit.value - 1680) < 1:
            continue
        score = 5.0
        if 300 <= yaw <= 360:
            score += 35
        elif yaw <= 5:
            score += 5  # 0.0 常见于俯仰/横滚，弱候选
        # 3590→359 优于 35006→350.06
        if 1000 <= abs(hit.value) <= 3600:
            score += 45
        elif abs(hit.value) >= 1e7:
            score += 30  # 359000001 形态
        elif 10000 <= abs(hit.value) <= 36099:
            score += 8
        if "." in hit.raw and 300 <= yaw <= 360 and abs(hit.value) <= 360:
            score += 15
        yaw_cands.append((score, yaw % 360.0))
    if yaw_cands:
        yaw_cands.sort(key=lambda t: t[0], reverse=True)
        out["yaw"] = yaw_cands[0][1]

    pitch_cands: list[tuple[float, float]] = []
    for hit in hits:
        pitch = _recover_angle_or_alt(hit.value, "pitch")
        if pitch is None:
            continue
        if -95 <= pitch <= -50:
            pitch_cands.append((40 + abs(pitch), pitch))
        elif abs(pitch + 90) < 1e-6:
            pitch_cands.append((50.0, pitch))
    if pitch_cands:
        pitch_cands.sort(key=lambda t: t[0], reverse=True)
        out["pitch"] = pitch_cands[0][1]

    return out


def _find_labeled(text: str, labels: tuple[str, ...], unit: str = "") -> float | None:
    for label in labels:
        # 允许 OCR 把「经度」拆成「经 度」
        flex = r"\s*".join(re.escape(ch) for ch in label)
        pat = rf"{flex}\s*[:：]?\s*([-+]?\d+[.,]?\d*)\s*{unit}"
        m = re.search(pat, text, re.I)
        if m:
            return _float_num(m.group(1))
        pat2 = rf"{flex}.{{0,32}}?([-+]?\d+[.,]?\d+)"
        m = re.search(pat2, text, re.I | re.S)
        if m:
            return _float_num(m.group(1))
        pat3 = rf"{flex}.{{0,24}}?([-+]?\d{{4,}})"
        m = re.search(pat3, text, re.I | re.S)
        if m:
            return _float_num(m.group(1))
    return None


def parse_hz_gcs_text(text: str) -> dict[str, float]:
    """从状态管理面板 OCR 文本提取贴地字段（单测 / 文案兜底）。"""
    out: dict[str, float] = {}
    if not text:
        return out

    lon_raw = _find_labeled(text, ("经度",))
    if lon_raw is None:
        # 标签被认成乱码时，抓 119xxxxxx 形态
        m = re.search(r"(?<!\d)(1[0-3]\d{7,9})(?!\d)", text)
        if m:
            lon_raw = _float_num(m.group(1))
    if lon_raw is not None:
        lon = _recover_coord(lon_raw, "lon")
        if lon is None and 70 <= abs(lon_raw) <= 140:
            lon = lon_raw
        if lon is not None:
            out["lon"] = lon

    lat_raw = _find_labeled(text, ("纬度",))
    if lat_raw is None:
        m = re.search(r"(?<!\d)([2-5]\d{6,7})(?!\d)", text)
        if m:
            cand = _float_num(m.group(1))
            if cand is not None and _recover_coord(cand, "lat") is not None:
                lat_raw = cand
    if lat_raw is not None:
        lat = _recover_coord(lat_raw, "lat")
        if lat is None and 1 <= abs(lat_raw) <= 55:
            lat = lat_raw
        if lat is not None:
            out["lat"] = lat

    rel_raw = _find_labeled(text, ("相对高度",), r"(?:m|M|米)?")
    if rel_raw is None:
        rel_raw = _find_labeled(text, ("海拔高度", "海拔", "高程"), r"(?:m|M|米)?")
    if rel_raw is not None:
        rel = _recover_angle_or_alt(rel_raw, "alt")
        if rel is not None and rel > 0.5:
            out["rel_alt"] = rel

    yaw_raw = _find_labeled(text, ("航向角", "航向"), r"(?:°|deg)?")
    if yaw_raw is not None:
        yaw = _recover_angle_or_alt(yaw_raw, "yaw")
        if yaw is not None:
            out["yaw"] = yaw % 360.0

    gimbal_pitch = None
    pod = re.search(r"吊舱状态(.{0,320}?)(?:综合信息|其他信息|$)", text, re.S)
    if pod:
        gimbal_pitch = _find_labeled(pod.group(1), ("俯仰角", "俯仰"), r"(?:°|deg)?")
    if gimbal_pitch is None:
        m = re.search(r"俯仰(?:角)?\s*[:：]?\s*(-?\d+[.,]?\d*)\s*°?", text)
        if m:
            gimbal_pitch = _float_num(m.group(1))
        if gimbal_pitch is None or abs(gimbal_pitch) < 1e-6:
            for m in re.finditer(r"(-?\d+[.,]?\d*)\s*°?", text):
                v = _float_num(m.group(1))
                if v is not None and -95 <= v <= -60:
                    gimbal_pitch = v
                    break
    if gimbal_pitch is not None:
        pitch = _recover_angle_or_alt(gimbal_pitch, "pitch")
        if pitch is not None:
            out["pitch"] = pitch

    roll_raw = _find_labeled(text, ("横滚角", "横滚"), r"(?:°|deg)?")
    if roll_raw is not None and abs(roll_raw) <= 180:
        out["roll"] = roll_raw
    elif roll_raw is not None:
        roll = _recover_angle_or_alt(roll_raw, "pitch")
        if roll is not None and abs(roll) <= 180:
            out["roll"] = roll

    return out


def _ocr_scale(crop: np.ndarray) -> float:
    """大图放大过高会导致 Tesseract 超时返回空，限制在 2.0~2.8。"""
    ch = max(crop.shape[0], 1)
    return max(2.0, min(2.8, 800.0 / ch))


def _core_ok(out: dict[str, float]) -> bool:
    return all(k in out for k in ("lon", "lat", "yaw", "pitch"))


def parse_hz_gcs_adaptive(frame_bgr: np.ndarray) -> dict[str, float]:
    """自适应识别右侧状态面板（多裁剪数字通道，缺字段时再升级）。"""
    if frame_bgr is None or frame_bgr.size == 0:
        return {}

    all_hits: list[_NumHit] = []
    low_res = frame_bgr.shape[1] <= 1600

    def _add_ocr(box: tuple[float, float, float, float], scale: float, mode: str = "inv_otsu") -> None:
        crop = _crop_norm(frame_bgr, box)
        if crop.size == 0:
            return
        all_hits.extend(
            _hits_from_text(
                _tesseract(
                    _prep_bw(crop, scale, mode=mode),
                    lang="eng",
                    whitelist="0123456789.-",
                    timeout_s=14.0,
                )
            )
        )

    # 1) 主面板 + 右下罗盘（高分辨率通常一次够用）
    main = _crop_norm(frame_bgr, (0.55, 0.08, 0.98, 0.72))
    _add_ocr((0.55, 0.08, 0.98, 0.72), _ocr_scale(main) if main.size else 2.2)
    _add_ocr((0.72, 0.70, 0.99, 0.96), 2.4)
    out = _classify_hits(all_hits)
    if _core_ok(out):
        return out

    # 2) 位姿条补经纬/高度/俯仰
    _add_ocr((0.62, 0.14, 0.97, 0.50), 2.6)
    out = _classify_hits(all_hits)
    if _core_ok(out):
        return out

    # 3) 低分辨率：高倍经纬行（经度关键，偶发需多试一次）
    if low_res and ("lon" not in out or "lat" not in out):
        for mode in ("inv_otsu", "clahe"):
            _add_ocr((0.66, 0.20, 0.95, 0.36), 4.5, mode)
            out = _classify_hits(all_hits)
            if "lon" in out and "lat" in out:
                break
        if _core_ok(out):
            return out

    # 4) 中文标签兜底（只跑一次）
    if not all(k in out for k in ("lon", "lat")):
        crop = _crop_norm(frame_bgr, (0.60, 0.12, 0.97, 0.55))
        if crop.size:
            labeled = _tesseract(
                _prep_bw(crop, _ocr_scale(crop)),
                lang="chi_sim+eng",
                whitelist=None,
                timeout_s=14.0,
            )
            from_labels = parse_hz_gcs_text(labeled)
            all_hits.extend(_hits_from_text(labeled))
            out = _classify_hits(all_hits)
            for key in ("lon", "lat", "rel_alt", "yaw", "pitch", "roll"):
                if key not in out and key in from_labels:
                    out[key] = from_labels[key]

    if "pitch" not in out:
        for hit in all_hits:
            pitch = _recover_angle_or_alt(hit.value, "pitch")
            if pitch is not None and -95 <= pitch <= -60:
                out["pitch"] = pitch
                break

    return out


def parse_hz_gcs_rois(frame_bgr: np.ndarray) -> dict[str, float]:
    return parse_hz_gcs_adaptive(frame_bgr)


class HzGcsHud(AircraftModel):
    def __init__(self):
        super().__init__(
            id="hz_gcs",
            label="HZ 无人机 · 地面站桌面",
            notes=(
                "自适应识别右侧「状态管理」数字：经纬度、高度、航向、吊舱俯仰。"
                "窗口大小/位置变化仍可识别；请保证状态面板完整入画。"
                "离线可用（本机 Tesseract，无需联网训练）。"
            ),
            hfov_deg=60.0,
            vfov_deg=45.0,
            camera_crop=(0.12, 0.08, 0.72, 0.78),
            negate_pitch=False,
            requires_manual_height=False,
        )

    def parse_hud(self, frame_bgr: np.ndarray) -> dict[str, float]:
        return parse_hz_gcs_adaptive(frame_bgr)
