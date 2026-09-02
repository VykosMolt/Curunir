"""GeoJSON-compatible geometry with explicit uncertainty.

WGS84 only, and distances use the standard library alone so they are
deterministic. Line proximity is an equirectangular local approximation, good
enough at corridor scale and approximate by design.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

GEOMETRY_KINDS = ("POINT", "LINESTRING", "POLYGON")
EARTH_RADIUS_M = 6_371_008.8


def _check_position(position: Sequence[float]) -> tuple[float, ...]:
    if not isinstance(position, (tuple, list)) or len(position) not in (2, 3):
        raise ValueError("position must be (lon, lat) or (lon, lat, altitude_m)")
    lon, lat = float(position[0]), float(position[1])
    if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
        raise ValueError("coordinates outside WGS84 bounds")
    return (lon, lat) if len(position) == 2 else (lon, lat, float(position[2]))


@dataclass(frozen=True)
class Geometry:
    kind: str
    coordinates: tuple
    uncertainty_m: float | None = None
    crs: str = "WGS84"

    def __post_init__(self):
        if self.kind not in GEOMETRY_KINDS:
            raise ValueError(f"unsupported geometry kind: {self.kind}")
        if self.crs != "WGS84":
            raise ValueError("only WGS84 is supported")
        if self.uncertainty_m is not None and self.uncertainty_m < 0:
            raise ValueError("uncertainty must be non-negative")
        if self.kind == "POINT":
            object.__setattr__(self, "coordinates", _check_position(self.coordinates))
        elif self.kind == "LINESTRING":
            if len(self.coordinates) < 2:
                raise ValueError("linestring needs at least two positions")
            object.__setattr__(self, "coordinates", tuple(_check_position(p) for p in self.coordinates))
        else:
            ring = tuple(_check_position(p) for p in self.coordinates)
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("polygon ring must close with at least four positions")
            object.__setattr__(self, "coordinates", ring)

    def to_record(self) -> dict[str, Any]:
        coords = list(self.coordinates) if self.kind == "POINT" else [list(p) for p in self.coordinates]
        return {"kind": self.kind, "coordinates": coords, "uncertainty_m": self.uncertainty_m, "crs": self.crs}

    def to_geojson(self) -> dict[str, Any]:
        names = {"POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon"}
        coords = list(self.coordinates) if self.kind == "POINT" else [list(p) for p in self.coordinates]
        return {"type": names[self.kind], "coordinates": coords if self.kind != "POLYGON" else [coords]}


def geometry_from_record(record: Mapping[str, Any] | None) -> Geometry | None:
    if record is None:
        return None
    coords = record["coordinates"]
    coordinates = tuple(coords) if record["kind"] == "POINT" else tuple(tuple(p) for p in coords)
    return Geometry(record["kind"], coordinates, record.get("uncertainty_m"), record.get("crs", "WGS84"))


def geometry_from_geojson(value: Mapping[str, Any], uncertainty_m: float | None = None) -> Geometry:
    kinds = {"Point": "POINT", "LineString": "LINESTRING", "Polygon": "POLYGON"}
    if value.get("type") not in kinds:
        raise ValueError(f"unsupported GeoJSON geometry type: {value.get('type')}")
    kind = kinds[value["type"]]
    coords = value["coordinates"]
    if kind == "POINT":
        return Geometry(kind, tuple(coords), uncertainty_m)
    if kind == "LINESTRING":
        return Geometry(kind, tuple(tuple(p) for p in coords), uncertainty_m)
    if len(coords) != 1:
        raise ValueError("only single-ring polygons are supported")
    return Geometry(kind, tuple(tuple(p) for p in coords[0]), uncertainty_m)


def haversine_m(a: Sequence[float], b: Sequence[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def point_to_segment_m(point: Sequence[float], start: Sequence[float], end: Sequence[float]) -> float:
    lat0 = math.radians(point[1])
    to_xy = lambda p: ((p[0] - point[0]) * math.cos(lat0), p[1] - point[1])
    ax, ay = to_xy(start)
    bx, by = to_xy(end)
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    t = 0.0 if denominator == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denominator))
    nearest = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
    return haversine_m(point, nearest)


def point_to_linestring_m(point: Sequence[float], line: Sequence[Sequence[float]]) -> float:
    return min(point_to_segment_m(point, line[i], line[i + 1]) for i in range(len(line) - 1))
