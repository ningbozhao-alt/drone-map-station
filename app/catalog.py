"""本机离线录像目录，以及一段录像里最朝下、最朝前的时刻。"""

from __future__ import annotations

import json
from pathlib import Path


def load_catalog(root: Path) -> list[dict]:
    path = root / "samples" / "catalog.json"
    if not path.is_file():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    clips = []
    for item in items:
        video = root / item["video"]
        srt = root / item["srt"]
        clips.append(
            {
                "id": item["id"],
                "label": item["label"],
                "video": item["video"],
                "srt": item["srt"],
                "ready": video.is_file() and srt.is_file() and video.stat().st_size > 1024 and srt.stat().st_size > 0,
            }
        )
    return clips


def cue_marks(cues, fps: float) -> dict:
    """nadir_t 是俯仰最朝下的时刻，oblique_t 是最接近水平的时刻。"""
    if not cues or fps <= 0:
        return {"nadir_t": 0.0, "oblique_t": 0.0, "pitch_min": None, "pitch_max": None}
    steep = min(range(len(cues)), key=lambda i: (cues[i].pitch, i))
    level = max(range(len(cues)), key=lambda i: (cues[i].pitch, -i))
    pitches = [cue.pitch for cue in cues]
    return {
        "nadir_t": steep / fps,
        "oblique_t": level / fps,
        "pitch_min": min(pitches),
        "pitch_max": max(pitches),
    }
