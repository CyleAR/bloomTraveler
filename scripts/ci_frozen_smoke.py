"""Check a packaged app starts without sending location updates to a device."""

import argparse
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler


def check(executable):
    executable = executable.resolve()
    assets = executable.parent / "_internal" / "web"
    for name in ("index.html", "app.js", "app.css", "vendor/maplibre-gl.js",
                 "vendor/lucide.js", "styles/bloom.json", "styles/bloom-grass.png",
                 "fonts/PretendardVariable.woff2", "licenses/catalog.json"):
        if not (assets / name).is_file():
            raise RuntimeError(f"Missing bundled asset: {name}")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="bloom-ci-") as data:
        environment = {**os.environ, "BLOOM_TRAVELER_DATA_DIR": data}
        log_path = Path(data) / "startup.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(executable), "--browser", "--preview", "--port", str(port)],
                env=environment, stdout=log, stderr=log, cwd=executable.parent)
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Packaged app exited during startup")
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/api/state", timeout=2) as response:
                            state = json.load(response)
                        if not state.get("preview"):
                            raise RuntimeError("Preview mode was not enabled")
                        with opener.open(f"http://127.0.0.1:{port}/", timeout=5) as response:
                            if b"Bloom Traveler" not in response.read():
                                raise RuntimeError("Packaged UI did not load")
                        print("Packaged app startup and bundled assets: OK")
                        return
                    except (URLError, TimeoutError, ConnectionError, IncompleteRead):
                        time.sleep(0.25)
                raise TimeoutError("Packaged app did not start within 45 seconds")
            except Exception:
                print(log_path.read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    check(parser.parse_args().executable)
