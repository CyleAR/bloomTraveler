"""Atomic, user-local route and place library."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from routes import Route, RoutePoint

RECENT_SEARCH_LIMIT = 20


class Library:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"version": 1, "routes": [], "places": [], "recent_searches": []}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if data.get("version") != 1 or not all(isinstance(data.get(key), list) for key in ("routes", "places")):
                raise ValueError("저장한 경로/장소 파일 형식이 올바르지 않습니다")
            self.data = data
            history = self.data.get("recent_searches", [])
            self.data["recent_searches"] = [query.strip() for query in history
                                            if isinstance(query, str) and 0 < len(query.strip()) <= 200][:RECENT_SEARCH_LIMIT] if isinstance(history, list) else []
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

    def remember_search(self, query):
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 200:
            raise ValueError("검색어는 1~200자로 입력하세요")
        query = query.strip()
        with self.lock:
            data = self.snapshot()
            previous = data["recent_searches"]
            history = [query] + [item for item in previous if item.casefold() != query.casefold()]
            data["recent_searches"] = history[:RECENT_SEARCH_LIMIT]
            if data["recent_searches"] != previous:
                self._write(data)
            return data["recent_searches"]

    def delete_search(self, query=None):
        if query is not None and (not isinstance(query, str) or not 0 < len(query.strip()) <= 200):
            raise ValueError("삭제할 검색어가 올바르지 않습니다")
        with self.lock:
            data = self.snapshot()
            previous = data["recent_searches"]
            data["recent_searches"] = [] if query is None else [
                item for item in previous if item.casefold() != query.strip().casefold()]
            if data["recent_searches"] != previous:
                self._write(data)
            return data["recent_searches"]

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
            if identifier:
                index = next(index for index, item in enumerate(data[kind]) if item["id"] == identifier)
                data[kind][index] = content
            else:
                data[kind].insert(0, content)
            self._write(data)
            return content

    def move(self, kind, identifier, target_id, position):
        if kind not in ("routes", "places") or position not in ("before", "after"):
            raise ValueError("잘못된 순서 변경 요청입니다")
        with self.lock:
            data = self.snapshot()
            items = data[kind]
            item = next((item for item in items if item["id"] == identifier), None)
            if item is None or not any(item["id"] == target_id for item in items):
                raise ValueError("순서를 바꿀 저장 항목을 찾지 못했습니다")
            if identifier == target_id:
                return data
            # Move by stable IDs so filtered lists and concurrent additions stay intact.
            items.remove(item)
            index = next(index for index, item in enumerate(items) if item["id"] == target_id)
            items.insert(index + (position == "after"), item)
            if items != self.data[kind]:
                self._write(data)
            return data

    def delete(self, kind, identifier):
        if kind not in ("routes", "places"):
            raise ValueError("잘못된 저장 종류입니다")
        with self.lock:
            data = self.snapshot()
            data[kind] = [x for x in data[kind] if x["id"] != identifier]
            self._write(data)
