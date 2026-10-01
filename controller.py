"""State and motion independent of the browser event loop."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import math
import queue
import threading
import time
from geographiclib.geodesic import Geodesic
from routes import Playback, Route, RoutePoint, number
from start_location import get_start_location


class PreviewDevice:
    """Preview never discovers devices or sends locations."""
    def __init__(self):
        self.events = queue.Queue()

    def start(self): pass
    def stop(self): pass
    def set_position(self, *point): pass
    def set_heartbeat(self, enabled): pass

    def restore_real_location(self):
        self.events.put(("restored", "미리보기: 모의 GPS 해제"))


class Controller:
    def __init__(self, device, library, locator=get_start_location, preview=False):
        self.device, self.library, self.locator = device, library, locator
        self.preview = preview
        self.lock = threading.RLock()
        self.route = Route()
        self.route_revision = 0
        self.route_length = 0
        self.position = None
        self.real_location = None
        self.location_revision = 0
        self.location_loading = False
        self.connected = False
        self.connection = "미리보기 · 기기 전송 없음" if preview else "기기 검색 중"
        self.speed = 15.0
        self.heartbeat = False
        self.running = False
        self.player = None
        self.keys = set()
        self.last_keys = 0
        self.logs = deque(maxlen=60)
        self.log_id = 0
        self.stop_event = threading.Event()
        self.location_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="real-location")
        self.thread = threading.Thread(target=self._worker, name="bloom-motion", daemon=True)

    def start(self):
        self.device.start()
        self.thread.start()
        self.refresh_location()

    def close(self):
        self.stop_event.set()
        self.thread.join(2)
        self.device.stop()
        self.location_pool.shutdown(wait=False, cancel_futures=True)

    def log(self, message):
        self.log_id += 1
        self.logs.append({"id": self.log_id, "time": time.strftime("%H:%M:%S"), "message": str(message)})

    def refresh_location(self):
        with self.lock:
            if self.location_loading:
                return
            self.location_loading = True
        self.location_pool.submit(self._locate)

    def _locate(self):
        try:
            point, source, accuracy = self.locator()
            point = RoutePoint.load({"lat": point[0], "lon": point[1]})
            with self.lock:
                self.real_location = {"lat": point.lat, "lon": point.lon, "source": source,
                                      "accuracy": accuracy, "updated": time.time()}
                self.location_revision += 1
        except Exception as exc:
            with self.lock:
                self.log(f"실제 위치 조회 실패: {exc}")
        finally:
            with self.lock:
                self.location_loading = False

    def state(self):
        with self.lock:
            player = self.player
            return {
                "preview": self.preview, "connected": self.connected, "connection": self.connection,
                "position": self.position, "real_location": deepcopy(self.real_location),
                "location_revision": self.location_revision, "location_loading": self.location_loading,
                "speed": self.speed, "heartbeat": self.heartbeat, "running": self.running,
                "paused": bool(player and not self.running and not player.finished),
                "route_revision": self.route_revision, "length_m": self.route_length,
                "point_count": len(self.route.points), "logs": list(self.logs),
                "playback": {"index": player.index, "phase": player.phase, "completed": player.completed,
                             "wait": round(player.wait_left, 1), "travelled": player.travelled,
                             "finished": player.finished} if player else None,
            }

    def get_route(self):
        with self.lock:
            return {"route": self.route.to_dict(), "revision": self.route_revision, "length_m": self.route_length}

    def set_route(self, route, revision=None):
        length = route.length_m
        with self.lock:
            if revision is not None and revision != self.route_revision:
                raise ValueError("경로가 변경되었습니다. 최신 경로를 다시 불러오세요")
            if self.player is not None and not self.player.finished:
                if not self.player.update_route(route):
                    self.running = False
                    self.log("남은 이동 지점이 없어 경로 이동 정지")
            else:
                self.running = False
                self.player = None
            self.keys.clear()
            self.route = route
            self.route_length = length
            self.route_revision += 1
            return {**self.get_route(), "state": self.state()}

    def _require_device(self):
        if not self.connected and not self.preview:
            raise ValueError("USB 또는 Wi-Fi 기기 연결을 확인하세요")

    def action(self, data):
        kind = data.get("type")
        if kind == "locate":
            self.refresh_location()
            return self.state()
        with self.lock:
            if kind == "speed":
                self.speed = number(data["value"], 0.1, 1000, "이동 속도")
            elif kind == "heartbeat":
                self.heartbeat = bool(data["value"])
                self.device.set_heartbeat(self.heartbeat)
            elif kind == "play":
                self._require_device()
                if self.player is None or self.player.finished:
                    self.player = Playback(self.route)
                    self.position = self.player.position
                    self.device.set_position(*self.position)
                self.keys.clear()
                self.running = True
                self.log("경로 이동 시작")
            elif kind == "pause":
                self.running = False
                self.keys.clear()
            elif kind == "stop":
                self.running = False
                self.player = None
                self.keys.clear()
            elif kind == "teleport":
                self._require_device()
                point = RoutePoint.load(data["point"])
                self.running = False
                self.player = None
                self.keys.clear()
                self.position = point.coords
                self.device.set_position(*self.position)
                self.log("순간이동 요청")
            elif kind == "restore":
                self._require_device()
                self.running = False
                self.player = None
                self.keys.clear()
                self.heartbeat = False
                self.device.restore_real_location()
                self.log("기기의 모의 GPS 해제 요청")
            elif kind == "keys":
                keys = set(data.get("keys", []))
                if not keys <= {"w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"}:
                    raise ValueError("잘못된 이동 키입니다")
                if keys:
                    self._require_device()
                    if self.position is None:
                        if self.real_location is None:
                            raise ValueError("먼저 위치를 지정하세요")
                        self.position = self.real_location["lat"], self.real_location["lon"]
                    self.running = False
                    self.player = None
                self.keys = keys
                self.last_keys = time.monotonic()
            else:
                raise ValueError("알 수 없는 동작입니다")
            return self.state()

    def _worker(self):
        last = time.monotonic()
        while not self.stop_event.wait(0.1):
            now = time.monotonic()
            elapsed = min(now - last, 0.5)
            last = now
            with self.lock:
                try:
                    while True:
                        kind, message = self.device.events.get_nowait()
                        if kind == "connected":
                            self.connected = True
                            self.connection = message
                            self.log(message)
                        elif kind in ("disconnected", "error"):
                            self.connected = False
                            self.connection = "연결 안 됨"
                            self.running = False
                            self.keys.clear()
                            self.log(message)
                        elif kind == "restored":
                            self.position = None
                            self.log(message)
                        elif kind == "notice":
                            self.log(message)
                except queue.Empty:
                    pass
                try:
                    old = self.position
                    if self.running and self.player:
                        self.position = self.player.step(elapsed, self.speed)
                        if self.player.finished:
                            self.running = False
                            self.log("경로 이동 완료")
                    elif self.keys and self.position:
                        if now - self.last_keys > 1.5:
                            self.keys.clear()
                        north = bool(self.keys & {"w", "arrowup"}) - bool(self.keys & {"s", "arrowdown"})
                        east = bool(self.keys & {"d", "arrowright"}) - bool(self.keys & {"a", "arrowleft"})
                        if north or east:
                            result = Geodesic.WGS84.Direct(*self.position, math.degrees(math.atan2(east, north)),
                                                          self.speed / 3.6 * elapsed)
                            self.position = result["lat2"], result["lon2"]
                    if self.position is not None and self.position != old:
                        self.device.set_position(*self.position)
                except Exception as exc:
                    self.running = False
                    self.keys.clear()
                    self.log(f"위치 이동 실패: {exc}")
