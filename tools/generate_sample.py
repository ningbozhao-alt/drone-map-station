#!/usr/bin/env python3
"""生成一段可回放的灾害样例：灾前正射、带洪水的当前地面、以及对应的相机录像和 SRT。"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.coord import offset_wgs
from app.geometry import photograph

CENTER_LAT = 30.274084
CENTER_LON = 120.155070
EXTENT = 400.0
MPP = 1.0
SIZE = int(EXTENT * 2 / MPP)
FPS = 8
DURATION = 12.0
WIDTH = 640
HEIGHT = 480
HFOV = 64.0
VFOV = 48.0


def paint(flood: bool) -> np.ndarray:
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    img[:] = (70, 130, 72)
    for meter in range(-int(EXTENT), int(EXTENT) + 1, 50):
        x = int((meter + EXTENT) / MPP)
        y = int((EXTENT - meter) / MPP)
        cv2.line(img, (x, 0), (x, SIZE - 1), (90, 150, 90), 1)
        cv2.line(img, (0, y), (SIZE - 1, y), (90, 150, 90), 1)
    cv2.line(img, (0, SIZE // 2), (SIZE - 1, SIZE // 2), (210, 210, 210), 10)
    cv2.line(img, (SIZE // 2, 0), (SIZE // 2, SIZE - 1), (210, 210, 210), 10)
    for east, north, w, h in ((-180, 80, 70, 40), (60, -120, 50, 50), (-40, 160, 80, 36), (140, 40, 46, 70)):
        x0 = int((east + EXTENT) / MPP)
        y0 = int((EXTENT - north) / MPP)
        cv2.rectangle(img, (x0, y0 - int(h / MPP)), (x0 + int(w / MPP), y0), (60, 70, 80), -1)
    if flood:
        # 盖在起飞后北飞的正射航带上，方便和灾前道路对齐后看出新增积水。
        x0 = int((-40 + EXTENT) / MPP)
        x1 = int((90 + EXTENT) / MPP)
        y0 = int((EXTENT - (-70)) / MPP)
        y1 = int((EXTENT - (-150)) / MPP)
        overlay = img.copy()
        cv2.rectangle(overlay, (x0, y0), (x1, y1), (210, 90, 30), -1)
        img = cv2.addWeighted(overlay, 0.85, img, 0.15, 0)
    return img


def pose(t: float):
    east = 30.0 * math.sin(t / 2.0)
    north = -150.0 + t * 25.0
    if t < 4.0:
        pitch, yaw = -90.0, 0.0
    elif t < 8.0:
        pitch = -90.0 + (t - 4.0) / 4.0 * 40.0
        yaw = 0.0
    else:
        pitch = -50.0
        yaw = (t - 8.0) / 4.0 * 50.0
    return east, north, 100.0, yaw, pitch, 0.0


def clock(t: float) -> str:
    ms = int(round(t * 1000))
    sec, milli = divmod(ms, 1000)
    minute, sec = divmod(sec, 60)
    return f"00:{minute:02d}:{sec:02d},{milli:03d}"


def main():
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    pre = paint(False)
    current = paint(True)
    cv2.imwrite(str(out / "pre_disaster.png"), pre)
    south, west = offset_wgs(CENTER_LAT, CENTER_LON, -EXTENT, -EXTENT)
    north, east = offset_wgs(CENTER_LAT, CENTER_LON, EXTENT, EXTENT)
    meta = {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "center_lat": CENTER_LAT,
        "center_lon": CENTER_LON,
        "mpp": MPP,
    }
    (out / "pre_disaster.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    frames = int(DURATION * FPS)
    srt = []
    ffmpeg = subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
            "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
            "-g", "8", "-bf", "0",
            str(out / "flight.mp4"),
        ],
        stdin=subprocess.PIPE,
    )
    assert ffmpeg.stdin is not None
    for index in range(frames):
        t = index / FPS
        east_m, north_m, alt, yaw, pitch, roll = pose(t)
        lat, lon = offset_wgs(CENTER_LAT, CENTER_LON, east_m, north_m)
        photo = photograph(
            current, -EXTENT, EXTENT, MPP,
            east_m, north_m, alt, yaw, pitch, roll, HFOV, VFOV, WIDTH, HEIGHT,
        )
        ffmpeg.stdin.write(photo.tobytes())
        start = clock(t)
        end = clock(t + 1.0 / FPS)
        srt.append(
            "\n".join([
                str(index + 1),
                f"{start} --> {end}",
                (
                    f"[latitude: {lat:.7f}] [longitude: {lon:.7f}] "
                    f"[rel_alt: {alt:.2f} abs_alt: {alt + 20:.2f}] "
                    f"[gb_yaw: {yaw:.2f} gb_pitch: {pitch:.2f} gb_roll: {roll:.2f}] "
                    f"[hfov: {HFOV:.2f}] [vfov: {VFOV:.2f}]"
                ),
                "",
            ])
        )
        if index % 16 == 0:
            print(f"生成帧 {index}/{frames}")
    ffmpeg.stdin.close()
    code = ffmpeg.wait()
    if code != 0:
        raise SystemExit(f"ffmpeg 退出 {code}")
    (out / "flight.srt").write_text("\n".join(srt), encoding="utf-8")
    print(f"已写入 {out / 'flight.mp4'} 和 SRT，共 {frames} 帧")


if __name__ == "__main__":
    main()
