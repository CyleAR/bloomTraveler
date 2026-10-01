"""Route data, point extensions, and GPX interchange."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from xml.etree import ElementTree as ET
from uuid import uuid4

from defusedxml.ElementTree import fromstring as safe_xml
from geographiclib.geodesic import Geodesic
import gpxpy
import gpxpy.gpx

BLOOM_NS = "https://bloomgps.local/gpx/1"


def number(value, low, high, label):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} 값이 범위를 벗어났습니다")
    return value


@dataclass
class RoutePoint:
    lat: float
    lon: float
    name: str = ""
    speed: float | None = None
    wait: float = 0
    instant: bool = False
    segment: int = 0
    elevation: float | None = None
    time: str | None = None
    comment: str | None = None
    description: str | None = None
    symbol: str | None = None
    extensions: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex, compare=False)

    @classmethod
    def load(cls, data):
        if not isinstance(data, dict):
            raise ValueError("잘못된 경로 지점입니다")
        point = cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})
        point.lat = number(point.lat, -90, 90, "위도")
        point.lon = number(point.lon, -180, 180, "경도")
        point.wait = number(point.wait, 0, 86400, "대기 시간")
        if point.speed is not None:
            point.speed = number(point.speed, 0.1, 1000, "속도")
        if point.elevation is not None:
            point.elevation = number(point.elevation, -12000, 100000, "고도")
        point.name = str(point.name)[:200]
        point.segment = int(number(point.segment, 0, 1000000, "구간"))
        if not isinstance(point.id, str) or not point.id or len(point.id) > 64:
            raise ValueError("잘못된 경로 지점 ID입니다")
        if not isinstance(point.instant, bool):
            raise ValueError("순간이동 설정이 잘못되었습니다")
        if point.time:
            datetime.fromisoformat(point.time.replace("Z", "+00:00"))
        if not isinstance(point.extensions, list):
            raise ValueError("잘못된 GPX 확장입니다")
        if not isinstance(point.metadata, dict):
            raise ValueError("잘못된 GPX 지점 속성입니다")
        for extension in point.extensions:
            safe_xml(extension, forbid_dtd=True)
        return point

    @property
    def coords(self):
        return self.lat, self.lon


@dataclass
class Route:
    name: str = "새 경로"
    points: list[RoutePoint] = field(default_factory=list)
    repeat: bool = False
    return_mode: str = "instant"
    return_speed: float = 20
    repeat_count: int = 0
    template: str | None = None

    @classmethod
    def load(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("points", []), list):
            raise ValueError("잘못된 경로 데이터입니다")
        mode = data.get("return_mode", "instant")
        if mode not in ("instant", "straight", "reverse"):
            raise ValueError("잘못된 복귀 방식입니다")
        template = data.get("template")
        if template:
            safe_xml(template, forbid_dtd=True)
        points = [RoutePoint.load(point) for point in data.get("points", [])]
        if len({point.id for point in points}) != len(points):
            raise ValueError("경로 지점 ID가 중복되었습니다")
        return cls(
            name=str(data.get("name", "새 경로")).strip()[:200] or "새 경로",
            points=points,
            repeat=bool(data.get("repeat", False)), return_mode=mode,
            return_speed=number(data.get("return_speed", 20), 0.1, 1000, "복귀 속도"),
            repeat_count=int(number(data.get("repeat_count", 0), 0, 1000000, "반복 횟수")),
            template=template,
        )

    def to_dict(self):
        return asdict(self)

    @property
    def length_m(self):
        return sum(distance(a.coords, b.coords) for a, b in zip(self.points, self.points[1:])
                   if a.segment == b.segment)


def distance(a, b):
    return Geodesic.WGS84.Inverse(*a, *b)["s12"]


def _extension_values(point):
    values = {}
    for root in point.extensions:
        for child in root.iter():
            if child.tag.startswith("{" + BLOOM_NS + "}") and child.text:
                values[child.tag.split("}", 1)[1]] = child.text.strip()
    return values


def import_gpx(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    try:
        safe_xml(text, forbid_dtd=True)
        gpx = gpxpy.parse(text)
    except Exception as exc:
        raise ValueError("GPX XML을 읽지 못했습니다. 파일 형식을 확인하세요") from exc
    route = Route(name=gpx.name or "가져온 경로", template=text)
    groups = []
    for track in gpx.tracks:
        route.name = track.name or route.name
        groups.extend(segment.points for segment in track.segments)
    for item in gpx.routes:
        route.name = item.name or route.name
        groups.append(item.points)
    if not groups and gpx.waypoints:
        groups = [gpx.waypoints]
    for segment, points in enumerate(groups):
        for original in points:
            ext = _extension_values(original)
            speed = float(ext["speed"]) if ext.get("speed") else None
            if speed == 0:
                speed = None
            point = RoutePoint(
                lat=original.latitude, lon=original.longitude, name=original.name or "",
                segment=segment, speed=speed, wait=float(ext.get("wait", 0)),
                instant=ext.get("instant", "0").lower() in ("1", "true"),
                elevation=original.elevation, time=original.time.isoformat() if original.time else None,
                comment=original.comment, description=original.description, symbol=original.symbol,
                extensions=[ET.tostring(x, encoding="unicode") for x in original.extensions],
                metadata={key: getattr(original, key) for key in gpxpy.gpx.GPXTrackPoint.__slots__
                          if key not in ("latitude", "longitude", "elevation", "time", "name", "comment",
                                         "description", "symbol", "extensions")
                          and getattr(original, key, None) is not None},
            )
            route.points.append(RoutePoint.load(asdict(point)))
            if "returnSpeed" in ext and len(route.points) == 1:
                route.return_speed = number(ext["returnSpeed"], 0.1, 1000, "복귀 속도")
    if not route.points:
        raise ValueError("GPX 파일에 경로 지점이 없습니다")
    for extension in gpx.extensions:
        if extension.tag == "{" + BLOOM_NS + "}playback":
            settings = {x.tag.split("}")[-1]: x.text for x in extension}
            route.repeat = settings.get("repeat") == "1"
            route.return_mode = settings.get("returnMode", "instant")
            route.repeat_count = int(settings.get("repeatCount", "0"))
    return Route.load(route.to_dict())


def export_gpx(route):
    gpx = gpxpy.parse(route.template) if route.template else gpxpy.gpx.GPX()
    gpx.version = "1.1"
    gpx.creator = "Bloom Traveler 2.0"
    gpx.name = route.name
    gpx.nsmap["bloom"] = BLOOM_NS
    gpx.tracks = []
    gpx.routes = []
    track = gpxpy.gpx.GPXTrack(name=route.name)
    gpx.tracks.append(track)
    previous = None
    for index, point in enumerate(route.points):
        if previous != point.segment:
            segment = gpxpy.gpx.GPXTrackSegment()
            track.segments.append(segment)
            previous = point.segment
        original = gpxpy.gpx.GPXTrackPoint(
            latitude=point.lat, longitude=point.lon, elevation=point.elevation,
            time=datetime.fromisoformat(point.time.replace("Z", "+00:00")) if point.time else None,
            name=point.name or None, comment=point.comment, symbol=point.symbol,
        )
        original.description = point.description
        for key, value in point.metadata.items():
            if key in gpxpy.gpx.GPXTrackPoint.__slots__ and key not in (
                    "latitude", "longitude", "elevation", "time", "name", "comment", "description", "symbol", "extensions"):
                setattr(original, key, value)
        original.extensions = [ET.fromstring(x) for x in point.extensions]
        # Replace only our fields; other applications' extensions stay intact.
        for root in list(original.extensions):
            known = {"{" + BLOOM_NS + "}" + key for key in ("speed", "wait", "instant", "returnSpeed")}
            if root.tag in known:
                original.extensions.remove(root)
            else:
                for parent in root.iter():
                    for child in list(parent):
                        if child.tag in known:
                            parent.remove(child)
        values = {"wait": point.wait, "instant": int(point.instant)}
        if point.speed is not None:
            values["speed"] = point.speed
        if index == 0:
            values["returnSpeed"] = route.return_speed
        for key, value in values.items():
            original.extensions.append(ET.Element("{" + BLOOM_NS + "}" + key))
            original.extensions[-1].text = str(value)
        segment.points.append(original)
    gpx.extensions = [x for x in gpx.extensions if x.tag != "{" + BLOOM_NS + "}playback"]
    playback = ET.Element("{" + BLOOM_NS + "}playback")
    for key, value in {"repeat": int(route.repeat), "returnMode": route.return_mode,
                       "repeatCount": route.repeat_count}.items():
        ET.SubElement(playback, "{" + BLOOM_NS + "}" + key).text = str(value)
    gpx.extensions.append(playback)
    return gpx.to_xml()


class Playback:
    """Geodesic motion with waits, point speeds, and three loop return modes."""

    def __init__(self, route, position=None):
        if not route.points:
            raise ValueError("경로에 지점을 추가하세요")
        if route.repeat and not any(distance(a.coords, b.coords) > 0.01
                                    for a, b in zip(route.points, route.points[1:])):
            raise ValueError("반복 경로에는 서로 다른 위치의 지점이 필요합니다")
        self.route = deepcopy(route)
        self.position = position or route.points[0].coords
        self.index = 0
        self.phase = "forward"
        self.completed = 0
        self.wait_left = 0.0
        self.arrived = False
        self.finished = False
        self.travelled = 0.0
        self._line = None
        self._length = 0.0
        self._offset = 0.0

    def update_route(self, route):
        """Retarget at the current position while retaining progress and loop state."""
        old_points = self.route.points
        old_target = old_points[self.index]
        old_ids = {point.id for point in old_points}
        self.route = deepcopy(route)
        self._line = None
        self._length = self._offset = 0.0
        if not route.points or (route.repeat and not any(
                distance(a.coords, b.coords) > 0.01 for a, b in zip(route.points, route.points[1:]))):
            self.finished = True
            self.arrived = False
            self.wait_left = 0
            return False

        indices = {point.id: index for index, point in enumerate(route.points)}
        target = 0 if self.phase == "straight" else indices.get(old_target.id)
        if target is None:
            # Deleting a target skips to the next surviving point in travel order.
            remaining = old_points[self.index+1:] if self.phase == "forward" else old_points[:self.index][::-1]
            target = next((indices[point.id] for point in remaining if point.id in indices), None)
            if target is None:
                fresh = [index for index, point in enumerate(route.points) if point.id not in old_ids]
                if fresh:
                    target = fresh[0] if self.phase == "forward" else fresh[-1]
        if target is None:
            self.wait_left = 0
            if not route.repeat:
                self.finished = True
                self.arrived = False
                return False
            self.index = len(route.points)-1 if self.phase == "forward" else 0
            self.arrived = True
            return True

        self.index = target
        point = route.points[target]
        if point.id == old_target.id and point.coords == old_target.coords and self.arrived:
            waited = max(0, old_target.wait-self.wait_left)
            self.wait_left = max(0, point.wait-waited)
        else:
            self.arrived = False
            self.wait_left = 0
        return True

    def _next(self):
        points = self.route.points
        if self.phase == "reverse":
            if self.index > 0:
                self.index -= 1
                return
            self.phase = "forward"
            self.index = 1
            return
        if self.phase == "straight":
            self.phase = "forward"
            self.index = 1
            return
        if self.index < len(points) - 1:
            self.index += 1
            return
        self.completed += 1
        if not self.route.repeat or (self.route.repeat_count and self.completed >= self.route.repeat_count):
            self.finished = True
        elif self.route.return_mode == "instant":
            self.index = 0
            self.position = points[0].coords
        elif self.route.return_mode == "straight":
            self.phase = "straight"
            self.index = 0
        else:
            self.phase = "reverse"
            self.index = len(points) - 2

    def step(self, elapsed, default_speed):
        remaining = max(0, elapsed)
        transitions = 0
        while remaining > 0 and not self.finished:
            point = self.route.points[self.index]
            if self.arrived:
                wait = min(remaining, self.wait_left)
                remaining -= wait
                self.wait_left -= wait
                if self.wait_left > 0:
                    break
                self._next()
                self.arrived = False
                self._line = None
                transitions += 1
                # Zero-length/instant routes cannot occupy the worker indefinitely.
                if transitions >= 256:
                    break
                continue
            if self._line is None:
                result = Geodesic.WGS84.Inverse(*self.position, *point.coords)
                self._length = result["s12"]
                self._line = Geodesic.WGS84.Line(*self.position, result["azi1"])
                self._offset = 0.0
            previous_index = self.index - 1 if self.phase == "forward" else self.index + 1
            gap = (0 <= previous_index < len(self.route.points) and self.phase != "straight"
                   and point.segment != self.route.points[previous_index].segment)
            instant = point.instant or gap
            speed = self.route.return_speed if self.phase != "forward" else (point.speed or default_speed)
            speed_mps = max(0, speed) / 3.6
            if self._length <= 0.001 or instant:
                used = 0
            elif speed_mps == 0:
                break
            else:
                used = min(remaining, (self._length - self._offset) / speed_mps)
                self._offset += used * speed_mps
                self.travelled += used * speed_mps
            remaining -= used
            if instant or self._length - self._offset <= 0.001:
                self.position = point.coords
                self.arrived = True
                self.wait_left = point.wait
            else:
                value = self._line.Position(self._offset)
                self.position = value["lat2"], value["lon2"]
                break
        return self.position
