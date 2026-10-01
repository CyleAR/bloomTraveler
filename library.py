"""Atomic, user-local route and place library."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from routes import Route, RoutePoint


class Library:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"version": 1, "routes": [], "places": []}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if data.get("version") != 1 or not all(isinstance(data.get(key), list) for key in ("routes", "places")):
                raise ValueError("저장한 경로/장소 파일 형식이 올바르지 않습니다")
            self.data = data
            preferences = self.data.get("preferences")
            if isinstance(preferences, dict) and preferences.get("map_style") == "positron":
                preferences["map_style"] = "osm"

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self.data = data

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def save_map_style(self, value):
        if value not in ("osm", "bloom"):
            raise ValueError("지원하지 않는 지도 스타일입니다")
        with self.lock:
            data = self.snapshot()
            data.setdefault("preferences", {})["map_style"] = value
            self._write(data)
            return data["preferences"]

    def save(self, kind, value, identifier=None):
        if kind not in ("routes", "places"):
            raise ValueError("잘못된 저장 종류입니다")
        name = str(value.get("name", "")).strip()
        if not name or len(name) > 200:
            raise ValueError("이름은 1~200자로 지정하세요")
        if kind == "routes":
            content = Route.load(value).to_dict()
            if not content["points"]:
                raise ValueError("빈 경로는 저장할 수 없습니다")
        else:
            point = RoutePoint.load(value)
            content = {"name": name, "lat": point.lat, "lon": point.lon}
        with self.lock:
            data = self.snapshot()
            if identifier and not any(x["id"] == identifier for x in data[kind]):
                raise ValueError("저장 항목을 찾지 못했습니다")
            content.update(id=identifier or uuid4().hex, updated=datetime.now(timezone.utc).isoformat())
            data[kind] = [x for x in data[kind] if x["id"] != content["id"]]
            data[kind].insert(0, content)
            self._write(data)
            return content

    def delete(self, kind, identifier):
        if kind not in ("routes", "places"):
            raise ValueError("잘못된 저장 종류입니다")
        with self.lock:
            data = self.snapshot()
            data[kind] = [x for x in data[kind] if x["id"] != identifier]
            self._write(data)
