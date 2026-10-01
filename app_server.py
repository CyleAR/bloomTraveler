"""Loopback-only API and locally bundled UI."""

from functools import partial
import base64
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import gzip
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, quote, urlsplit
from routes import Route, export_gpx, import_gpx


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, controller, geocoder, assets, port=0):
        self.controller, self.geocoder = controller, geocoder
        self.token = secrets.token_urlsafe(32)
        self.compressed = {"/" + name: gzip.compress((Path(assets) / name).read_bytes())
                           for name in ("vendor/maplibre-gl.js", "vendor/lucide.js", "vendor/maplibre-gl.css",
                                        "licenses/catalog.json") if (Path(assets) / name).is_file()}
        self.assets = Path(assets)
        super().__init__(("127.0.0.1", port), partial(Handler, directory=str(assets)))
        self.url = f"http://127.0.0.1:{self.server_port}"
        self.thread = threading.Thread(target=self.serve_forever, name="bloom-web", daemon=True)

    def start(self): self.thread.start()

    def document(self):
        document = (self.assets / "index.html").read_text(encoding="utf-8")
        hashes = []
        for filename in ("vendor/maplibre-gl.js", "vendor/lucide.js", "app.js"):
            script = (self.assets / filename).read_text(encoding="utf-8").replace("</script", "<\\/script")
            digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
            hashes.append("'sha256-" + digest + "'")
            tag = '<script defer src="/' + filename + '"></script>'
            document = document.replace(tag, "" if filename == "app.js" else "<script>" + script + "</script>")
            if filename == "app.js":
                document = document.replace("</body>", "<script>" + script + "</script></body>")
        return document.encode("utf-8"), self.content_security_policy(hashes)

    @staticmethod
    def content_security_policy(hashes=()):
        return ("default-src 'self'; script-src 'self' 'unsafe-eval' blob: " + " ".join(hashes) + "; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self' https://tiles.openfreemap.org https://tile.openstreetmap.org; "
                "img-src 'self' data: blob: https://tiles.openfreemap.org https://tile.openstreetmap.org; worker-src 'self' blob:; "
                "font-src 'self' data:; object-src 'none'; base-uri 'self'")

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(2)


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args): pass

    def handle(self):
        try:
            super().handle()
        except ConnectionError:
            self.close_connection = True

    def _allowed(self, mutate=False):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.close_connection = True
            self._json({"error": "잘못된 요청 주소"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.url:
            self.close_connection = True
            self._json({"error": "외부 페이지 요청은 허용하지 않습니다"}, 403)
            return False
        if mutate and not secrets.compare_digest(self.headers.get("X-Bloom-Token", ""), self.server.token):
            self.close_connection = True
            self._json({"error": "요청 인증 실패"}, 403)
            return False
        return True

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._allowed(): return
        path = urlsplit(self.path).path
        controller = self.server.controller
        try:
            if path == "/" or path == "/index.html":
                body, self.document_policy = self.server.document()
                compressed = "gzip" in self.headers.get("Accept-Encoding", "")
                if compressed:
                    body = gzip.compress(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                if compressed:
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                for offset in range(0, len(body), 16384):
                    self.wfile.write(body[offset:offset + 16384])
            elif path in self.server.compressed and "gzip" in self.headers.get("Accept-Encoding", ""):
                body = self.server.compressed[path]
                self.send_response(200)
                content_type = "application/json; charset=utf-8" if path.endswith(".json") else "text/css" if path.endswith(".css") else "application/javascript"
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/bootstrap":
                self._json({"token": self.server.token, "state": controller.state(),
                            **controller.get_route(), "library": controller.library.snapshot()})
            elif path == "/api/state": self._json(controller.state())
            elif path == "/api/route": self._json(controller.get_route())
            elif path == "/api/library": self._json(controller.library.snapshot())
            elif path == "/api/search":
                query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                self._json({"places": self.server.geocoder.search(query)})
            elif path == "/api/export":
                route = Route.load(controller.get_route()["route"])
                if not route.points: raise ValueError("내보낼 경로가 없습니다")
                body = export_gpx(route).encode("utf-8")
                filename = quote(route.name + ".gpx", safe="")
                self.send_response(200)
                self.send_header("Content-Type", "application/gpx+xml; charset=utf-8")
                self.send_header("Content-Disposition", f"attachment; filename=Bloom-route.gpx; filename*=UTF-8''{filename}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path.startswith("/api/"): self._json({"error": "요청을 찾지 못했습니다"}, 404)
            else: super().do_GET()
        except ConnectionError:
            self.close_connection = True
        except (ValueError, TypeError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            with controller.lock: controller.log(f"요청 실패: {type(exc).__name__}: {exc}")
            self._json({"error": "요청에 실패했습니다. 연결 상태를 확인하고 다시 시도하세요."}, 502)

    def do_POST(self):
        if not self._allowed(mutate=True): return
        controller = self.server.controller
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16 * 1024 * 1024:
                self.close_connection = True
                raise ValueError("요청 파일 크기는 16MB 이하여야 합니다")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            path = urlsplit(self.path).path
            if path == "/api/action": self._json(controller.action(data))
            elif path == "/api/preferences": self._json(controller.library.save_map_style(data["map_style"]))
            elif path == "/api/route": self._json(controller.set_route(Route.load(data["route"]), data.get("revision")))
            elif path == "/api/import": self._json(controller.set_route(import_gpx(data["gpx"]), data.get("revision")))
            elif path == "/api/library/save": self._json(controller.library.save(data["kind"], data["value"], data.get("id")))
            elif path == "/api/library/delete":
                controller.library.delete(data["kind"], data["id"])
                self._json({"ok": True})
            else: self._json({"error": "요청을 찾지 못했습니다"}, 404)
        except ConnectionError:
            self.close_connection = True
        except (ValueError, TypeError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            with controller.lock: controller.log(f"변경 실패: {type(exc).__name__}: {exc}")
            self._json({"error": "변경에 실패했습니다. 앱 로그를 확인하세요."}, 500)

    def list_directory(self, path): self.send_error(404)

    def end_headers(self):
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cache-Control", "no-transform")
        self.send_header("Content-Security-Policy", getattr(self, "document_policy", self.server.content_security_policy()))
        super().end_headers()
