"""机型适配器协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class AircraftModel:
    """一种遥控器 HUD / 相机布局。"""

    id: str
    label: str
    notes: str = ""
    hfov_deg: float = 64.0
    vfov_deg: float = 48.0
    # 相对整帧的相机视口 (x0,y0,x1,y1)，录屏流贴地时裁这里
    camera_crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    # 大疆：俯角正数表示向下，站内 pitch 取负；若已是 0=水平/-90=下，则 negate_pitch=False
    negate_pitch: bool = True
    requires_manual_height: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def parse_hud(self, frame_bgr: np.ndarray) -> dict[str, float]:
        """从一帧 HUD 画面解析字段。只返回认到的键。"""
        raise NotImplementedError

    def crop_camera(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        x0, y0, x1, y1 = self.camera_crop
        xa, ya = max(0, int(x0 * w)), max(0, int(y0 * h))
        xb, yb = min(w, int(x1 * w)), min(h, int(y1 * h))
        if xb - xa < 8 or yb - ya < 8:
            return frame_bgr
        return frame_bgr[ya:yb, xa:xb].copy()

    def public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "notes": self.notes,
            "hfov_deg": self.hfov_deg,
            "vfov_deg": self.vfov_deg,
            "requires_manual_height": self.requires_manual_height,
            "camera_crop": list(self.camera_crop),
        }
