"""把云台画面的四个角投到平面地面，并扭成正北向上的贴地图像。

角度约定与大疆 OSD / SRT 一致：
- yaw：从正北顺时针，度
- pitch：0 为水平向前，-90 为垂直向下
- roll：顺时针为正（从相机后方看）

地面是单一高程的平面。没有 DEM 时，斜视只能当态势，不能当测绘。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from app.coord import offset_wgs

NADIR_PITCH = -75.0
# 比地平线再低大约 3°。再平的射线会把贴地图拉到很远。
_MIN_DOWN = 0.05


@dataclass
class Footprint:
    corners_enu: list[tuple[float, float]]
    view: str
    height_agl: float
    src_px: list[tuple[float, float]] | None = None

    @property
    def ok(self) -> bool:
        return len(self.corners_enu) == 4


def intrinsics(width: int, height: int, hfov_deg: float, vfov_deg: float):
    fx = (width * 0.5) / math.tan(math.radians(hfov_deg) * 0.5)
    fy = (height * 0.5) / math.tan(math.radians(vfov_deg) * 0.5)
    cx = (width - 1) * 0.5
    cy = (height - 1) * 0.5
    return fx, fy, cx, cy


def camera_to_ned(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """列向量是相机轴在 NED（北、东、地）下的方向。"""
    yaw = math.radians(yaw_deg)
    alpha = math.radians(90.0 + pitch_deg)
    roll = math.radians(roll_deg)

    z = np.array(
        [math.sin(alpha) * math.cos(yaw), math.sin(alpha) * math.sin(yaw), math.cos(alpha)],
        dtype=np.float64,
    )
    z_norm = np.linalg.norm(z)
    if z_norm < 1e-9:
        z = np.array([0.0, 0.0, 1.0])
    else:
        z /= z_norm

    x = np.array([-math.sin(yaw), math.cos(yaw), 0.0], dtype=np.float64)
    y = np.cross(z, x)
    y_norm = np.linalg.norm(y)
    if y_norm < 1e-6:
        x = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        y = np.cross(z, x)
        y /= np.linalg.norm(y)
    else:
        y /= y_norm
    x = np.cross(y, z)
    x /= np.linalg.norm(x)

    c, s = math.cos(roll), math.sin(roll)
    x2 = c * x + s * y
    y2 = -s * x + c * y
    return np.column_stack([x2, y2, z])


def _pixel_dir(u: float, v: float, fx: float, fy: float, cx: float, cy: float) -> np.ndarray:
    ray = np.array([(u - cx) / fx, (v - cy) / fy, 1.0], dtype=np.float64)
    return ray / np.linalg.norm(ray)


def _ground_point(direction: np.ndarray, height_agl: float):
    if direction[2] <= _MIN_DOWN:
        return None
    scale = height_agl / float(direction[2])
    return float(direction[1] * scale), float(direction[0] * scale)


def project_corners(
    width: int,
    height: int,
    hfov_deg: float,
    vfov_deg: float,
    yaw_deg: float,
    pitch_deg: float,
    roll_deg: float,
    height_agl: float,
) -> Footprint:
    view = "nadir" if pitch_deg <= NADIR_PITCH else "oblique"
    if height_agl < 1.0 or hfov_deg <= 1 or vfov_deg <= 1 or width < 2 or height < 2:
        return Footprint([], view, height_agl)

    fx, fy, cx, cy = intrinsics(width, height, hfov_deg, vfov_deg)
    rotation = camera_to_ned(yaw_deg, pitch_deg, roll_deg)

    def look(u: float, v: float):
        return rotation @ _pixel_dir(u, v, fx, fy, cx, cy)

    pixels = ((0.0, 0.0), (float(width - 1), 0.0), (float(width - 1), float(height - 1)), (0.0, float(height - 1)))
    src_px: list[tuple[float, float]] = []
    corners: list[tuple[float, float]] = []
    for u, v in pixels:
        point = _ground_point(look(u, v), height_agl)
        if point is None:
            # 这一角朝向天。沿画面竖边往下找到刚落到地面的位置。
            v_fail, v_hit = v, float(height - 1)
            if _ground_point(look(u, v_hit), height_agl) is None:
                return Footprint([], view, height_agl)
            for _ in range(24):
                v_mid = (v_fail + v_hit) * 0.5
                if _ground_point(look(u, v_mid), height_agl) is None:
                    v_fail = v_mid
                else:
                    v_hit = v_mid
            v = v_hit
            point = _ground_point(look(u, v), height_agl)
            if point is None:
                return Footprint([], view, height_agl)
        src_px.append((u, v))
        corners.append(point)
    if max(p[1] for p in src_px) - min(p[1] for p in src_px) < 8:
        return Footprint([], view, height_agl)
    return Footprint(corners, view, height_agl, src_px)


def corners_to_wgs(lat: float, lon: float, corners_enu: list[tuple[float, float]]):
    return [offset_wgs(lat, lon, east, north) for east, north in corners_enu]


@dataclass
class Drape:
    image: np.ndarray
    west: float
    south: float
    east: float
    north: float

    def bounds(self) -> dict:
        return {"west": self.west, "south": self.south, "east": self.east, "north": self.north}


def warp_north_up(
    frame_bgr: np.ndarray,
    corners_enu: list[tuple[float, float]],
    lat: float,
    lon: float,
    max_px: int = 720,
    src_px: list[tuple[float, float]] | None = None,
) -> Drape | None:
    if len(corners_enu) != 4:
        return None
    easts = [c[0] for c in corners_enu]
    norths = [c[1] for c in corners_enu]
    min_e, max_e = min(easts), max(easts)
    min_n, max_n = min(norths), max(norths)
    span_e = max(max_e - min_e, 1.0)
    span_n = max(max_n - min_n, 1.0)
    long_side = max(span_e, span_n)
    mpp = long_side / float(max_px)
    out_w = max(2, int(round(span_e / mpp)))
    out_h = max(2, int(round(span_n / mpp)))

    if src_px is None:
        src = np.float32([[0, 0], [frame_bgr.shape[1] - 1, 0], [frame_bgr.shape[1] - 1, frame_bgr.shape[0] - 1], [0, frame_bgr.shape[0] - 1]])
    else:
        src = np.float32(src_px)
    dst = np.float32([
        [(east - min_e) / mpp, (max_n - north) / mpp]
        for east, north in corners_enu
    ])
    homography = cv2.getPerspectiveTransform(src, dst)
    bgra = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA)
    warped = cv2.warpPerspective(
        bgra,
        homography,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    south_lat, west_lon = offset_wgs(lat, lon, min_e, min_n)
    north_lat, east_lon = offset_wgs(lat, lon, max_e, max_n)
    return Drape(warped, west_lon, south_lat, east_lon, north_lat)


def photograph(
    ground_bgr: np.ndarray,
    east_min: float,
    north_max: float,
    ground_mpp: float,
    east: float,
    north: float,
    height_agl: float,
    yaw_deg: float,
    pitch_deg: float,
    roll_deg: float,
    hfov_deg: float,
    vfov_deg: float,
    width: int,
    height: int,
) -> np.ndarray:
    """用同一套投影，把地面影像“拍”成一帧相机画面。样例录像靠这个生成。"""
    footprint = project_corners(width, height, hfov_deg, vfov_deg, yaw_deg, pitch_deg, roll_deg, height_agl)
    photo = np.zeros((height, width, 3), np.uint8)
    if not footprint.ok:
        return photo
    ground_pts = []
    for corner_east, corner_north in footprint.corners_enu:
        abs_e = east + corner_east
        abs_n = north + corner_north
        gx = (abs_e - east_min) / ground_mpp
        gy = (north_max - abs_n) / ground_mpp
        ground_pts.append([gx, gy])
    image_pts = np.float32(footprint.src_px or [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    homography = cv2.getPerspectiveTransform(np.float32(ground_pts), image_pts)
    return cv2.warpPerspective(ground_bgr, homography, (width, height), flags=cv2.INTER_LINEAR)
