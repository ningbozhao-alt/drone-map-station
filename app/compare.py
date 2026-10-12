"""灾前影像与当前贴地帧的差值。"""

from __future__ import annotations

import cv2
import numpy as np

from app.geometry import Drape


def colorize_difference(drape: Drape, pre_bgr: np.ndarray, pre_bounds: dict, threshold: int = 28):
    """把灾前图采样到当前贴地网格上，变化大的像素涂成红色。"""
    height, width = drape.image.shape[:2]
    pre_h, pre_w = pre_bgr.shape[:2]
    span_lon = pre_bounds["east"] - pre_bounds["west"]
    span_lat = pre_bounds["north"] - pre_bounds["south"]
    if span_lon == 0 or span_lat == 0:
        return None, 0.0

    def to_pre(lon: float, lat: float):
        x = (lon - pre_bounds["west"]) / span_lon * (pre_w - 1)
        y = (pre_bounds["north"] - lat) / span_lat * (pre_h - 1)
        return x, y

    drape_px = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    pre_px = np.float32([
        to_pre(drape.west, drape.north),
        to_pre(drape.east, drape.north),
        to_pre(drape.east, drape.south),
        to_pre(drape.west, drape.south),
    ])
    # 本机 OpenCV 的 warpPerspective 会先求逆，矩阵应按「源图 → 目标图」来写。
    homography = cv2.getPerspectiveTransform(pre_px, drape_px)
    warped = cv2.warpPerspective(pre_bgr, homography, (width, height))
    live = drape.image[:, :, :3]
    alpha = drape.image[:, :, 3]
    delta = cv2.absdiff(live, warped).mean(axis=2)
    valid = (alpha > 16) & (delta > threshold)
    heat = np.zeros((height, width, 4), np.uint8)
    heat[:, :, 2] = 220
    heat[:, :, 3] = np.where(valid, 170, 0).astype(np.uint8)
    covered = int((alpha > 16).sum())
    ratio = float(valid.sum()) / covered if covered else 0.0
    return heat, ratio
