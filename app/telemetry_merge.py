"""姿态合并：手动锁定 > OCR > MQTT > 未锁定手动。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.telemetry import Telemetry

FIELD_KEYS = ("lat", "lon", "rel_alt", "abs_alt", "yaw", "pitch", "roll", "hfov", "vfov", "zoom")


@dataclass
class TelemetryMerger:
    manual: dict[str, float] = field(default_factory=dict)
    locked: set[str] = field(default_factory=set)
    ocr: dict[str, float] = field(default_factory=dict)
    mqtt: Telemetry | None = None
    default_hfov: float = 64.0
    default_vfov: float = 48.0
    require_height: bool = False

    def set_manual(self, values: dict, locked: list[str] | None = None):
        for key, value in values.items():
            if key == "height_agl":
                key = "rel_alt"
            if key not in FIELD_KEYS:
                continue
            if value is None or value == "":
                self.manual.pop(key, None)
                continue
            try:
                self.manual[key] = float(value)
            except (TypeError, ValueError):
                continue
        if locked is not None:
            norm = set()
            for item in locked:
                norm.add("rel_alt" if item == "height_agl" else item)
            self.locked = {k for k in norm if k in FIELD_KEYS}

    def clear_manual(self):
        self.manual.clear()
        self.locked.clear()

    def _pick(self, key: str, *extra_defaults: tuple[str, float | None]) -> tuple[float | None, str]:
        if key in self.locked and key in self.manual:
            val = self.manual[key]
            # 视场被误锁成 0 时不能盖掉默认值，否则贴地几何直接失败
            if key in ("hfov", "vfov") and val <= 1.0:
                pass
            else:
                return val, "manual"
        if key in self.ocr:
            return float(self.ocr[key]), "ocr"
        if self.mqtt is not None:
            mqtt_map = {
                "lat": self.mqtt.lat,
                "lon": self.mqtt.lon,
                "rel_alt": self.mqtt.rel_alt,
                "abs_alt": self.mqtt.abs_alt,
                "yaw": self.mqtt.yaw,
                "pitch": self.mqtt.pitch,
                "roll": self.mqtt.roll,
                "hfov": self.mqtt.hfov,
                "vfov": self.mqtt.vfov,
                "zoom": self.mqtt.zoom,
            }
            if key in mqtt_map and mqtt_map[key] is not None:
                # MQTT pitch default -90 when absent is still a number; accept it
                return float(mqtt_map[key]), "mqtt"
        if key in self.manual:
            return self.manual[key], "manual"
        for name, val in extra_defaults:
            if val is not None:
                return float(val), name
        return None, "missing"

    def merge(self) -> tuple[Telemetry | None, dict[str, str], list[str]]:
        status: dict[str, str] = {}
        values: dict[str, float] = {}

        for key in ("lat", "lon", "yaw", "pitch", "roll", "rel_alt", "abs_alt"):
            val, src = self._pick(key)
            status[key] = src
            if val is not None:
                values[key] = val

        hfov, hfov_src = self._pick("hfov", ("default", self.default_hfov))
        vfov, vfov_src = self._pick("vfov", ("default", self.default_vfov))
        zoom, zoom_src = self._pick("zoom", ("default", 1.0))
        status["hfov"], status["vfov"], status["zoom"] = hfov_src, vfov_src, zoom_src
        if hfov is not None:
            values["hfov"] = hfov
        if vfov is not None:
            values["vfov"] = vfov
        if zoom is not None:
            values["zoom"] = zoom

        if "roll" not in values:
            values["roll"] = 0.0
            status["roll"] = "default"

        height = values.get("rel_alt") if values.get("rel_alt", 0) > 0.5 else values.get("abs_alt")
        if height is not None and height >= 0.5:
            status["height_agl"] = status["rel_alt"] if values.get("rel_alt", 0) > 0.5 else status["abs_alt"]
        else:
            status["height_agl"] = "missing"
            height = None

        missing: list[str] = []
        for key in ("lat", "lon", "yaw", "pitch"):
            if key not in values:
                missing.append(key)
        if height is None:
            missing.append("height_agl")

        if missing:
            return None, status, missing

        used = {status[k] for k in ("lat", "lon", "yaw", "pitch", "height_agl") if status.get(k) not in ("missing", "default")}
        if used == {"manual"}:
            source = "manual"
        elif used == {"ocr"}:
            source = "ocr"
        elif used == {"mqtt"}:
            source = "mqtt"
        elif "manual" in used:
            source = "manual+mix"
        elif "ocr" in used:
            source = "ocr+mix"
        else:
            source = "mixed"

        telem = Telemetry(
            lat=values["lat"],
            lon=values["lon"],
            rel_alt=float(values.get("rel_alt") or height or 0.0),
            abs_alt=float(values.get("abs_alt") or height or 0.0),
            yaw=values["yaw"],
            pitch=values["pitch"],
            roll=values.get("roll", 0.0),
            hfov=values.get("hfov", self.default_hfov),
            vfov=values.get("vfov", self.default_vfov),
            zoom=values.get("zoom", 1.0),
            source=source,
        )
        return telem, status, []
