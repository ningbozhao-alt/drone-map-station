"""机型注册表。新增机型：实现 AircraftModel 并在此登记。"""

from __future__ import annotations

from app.aircraft.base import AircraftModel
from app.aircraft.dji_m30t_hud import DjiM30tHud
from app.aircraft.hz_gcs_hud import HzGcsHud

_MODELS: dict[str, AircraftModel] = {}


def _ensure():
    if _MODELS:
        return
    for model in (DjiM30tHud(), HzGcsHud()):
        _MODELS[model.id] = model


def list_models() -> list[dict]:
    _ensure()
    return [m.public() for m in _MODELS.values()]


def get_model(model_id: str | None) -> AircraftModel | None:
    _ensure()
    if not model_id:
        return None
    return _MODELS.get(model_id)


def default_model_id() -> str:
    _ensure()
    return next(iter(_MODELS))
