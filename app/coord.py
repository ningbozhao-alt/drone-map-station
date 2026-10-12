"""WGS84、GCJ-02、BD09 互转，以及局部东北天位移。

百度地图使用 BD09。大疆遥测是 WGS84，不转换会偏几百米。
"""

from __future__ import annotations

import math

EARTH_R = 6378137.0
_A = 6378245.0
_EE = 0.00669342162296594323
_XPI = math.pi * 3000.0 / 180.0


def out_of_china(lon: float, lat: float) -> bool:
    return not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    if out_of_china(lon, lat):
        return lon, lat
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = math.radians(lat)
    magic = 1 - _EE * math.sin(radlat) ** 2
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    if out_of_china(lon, lat):
        return lon, lat
    glon, glat = wgs84_to_gcj02(lon, lat)
    return lon * 2 - glon, lat * 2 - glat


def gcj02_to_bd09(lon: float, lat: float) -> tuple[float, float]:
    z = math.hypot(lon, lat) + 0.00002 * math.sin(lat * _XPI)
    theta = math.atan2(lat, lon) + 0.000003 * math.cos(lon * _XPI)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    x = lon - 0.0065
    y = lat - 0.006
    z = math.hypot(x, y) - 0.00002 * math.sin(y * _XPI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _XPI)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lon: float, lat: float) -> tuple[float, float]:
    lon2, lat2 = wgs84_to_gcj02(lon, lat)
    return gcj02_to_bd09(lon2, lat2)


def bd09_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    lon2, lat2 = bd09_to_gcj02(lon, lat)
    return gcj02_to_wgs84(lon2, lat2)


def offset_wgs(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    dlat = north_m / EARTH_R
    dlon = east_m / (EARTH_R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def enu_meters(lat0: float, lon0: float, lat1: float, lon1: float) -> tuple[float, float]:
    north = math.radians(lat1 - lat0) * EARTH_R
    east = math.radians(lon1 - lon0) * EARTH_R * math.cos(math.radians(lat0))
    return east, north


def distance_m(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    east, north = enu_meters(lat0, lon0, lat1, lon1)
    return math.hypot(east, north)
