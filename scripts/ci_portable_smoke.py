"""Open the real portable WebView2 window with Internet download marks intact."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler
from xml.etree import ElementTree

from playwright.sync_api import sync_playwright
from library_order_smoke import check_library_order
from search_history_smoke import check_search_history


ZONE = "[ZoneTransfer]\nZoneId=3\nHostUrl=https://github.com/CyleAR/bloomTraveler/releases\n"


def free_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def download_mark(path):
    return Path(str(path) + ":Zone.Identifier")


def save_diagnostics(page, moves, phase):
    destination = Path("artifacts/portable-smoke")
    destination.mkdir(parents=True, exist_ok=True)
    report = {"phase": phase, "moveRequests": moves}
    try:
        report["page"] = page.evaluate("""() => ({
            viewport: [innerWidth, innerHeight],
            dragEvents: window.__bloomDragEvents || [],
            lists: Array.from(document.querySelectorAll('.library-list'), element => ({
                id: element.id, height: element.clientHeight, scrollTop: element.scrollTop,
                rows: Array.from(element.querySelectorAll('.library-row'), row => ({
                    id: row.dataset.id, top: row.getBoundingClientRect().top,
                    bottom: row.getBoundingClientRect().bottom
                }))
            }))
        })""")
    except Exception as error:
        report["captureError"] = str(error)
    (destination / "diagnostics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def check(executable):
    if os.name != "nt":
        raise RuntimeError("Portable download verification requires Windows/NTFS")
    executable = executable.resolve()
    configuration = executable.with_name(executable.name + ".config")
    setting = ElementTree.parse(configuration).find("runtime/loadFromRemoteSources")
    if setting is None or setting.get("enabled") != "true":
        raise RuntimeError("Portable .NET configuration is missing or disabled")
    with tempfile.TemporaryDirectory(prefix="bloom-portable-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        package = root / "Bloom Traveler"
        shutil.copytree(executable.parent, package)
        files = [path for path in package.rglob("*") if path.is_file()]
        for path in files:
            download_mark(path).write_text(ZONE, encoding="ascii")
        runtime = package / "_internal/pythonnet/runtime/Python.Runtime.dll"
        original_hash = hashlib.sha256(runtime.read_bytes()).hexdigest()
        original_zone = download_mark(runtime).read_bytes()
        port, debug_port = free_port(), free_port()
        while debug_port == port:
            debug_port = free_port()
        environment = {**os.environ, "BLOOM_TRAVELER_DATA_DIR": str(root / "data"),
                       "BLOOM_UI_DEBUG_PORT": str(debug_port),
                       "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--enable-unsafe-swiftshader --use-angle=swiftshader"}
        opener = build_opener(ProxyHandler({}))
        debug_url = f"http://127.0.0.1:{debug_port}"
        log_path = root / "startup.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(package / executable.name), "--preview", "--port", str(port)],
                cwd=package, env=environment, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"Native app exited: {process.returncode}")
                    try:
                        with opener.open(debug_url + "/json/version", timeout=1) as response:
                            json.load(response)
                        break
                    except (URLError, TimeoutError, ConnectionError):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Native WebView2 window did not initialize")
                        time.sleep(0.25)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(debug_url, timeout=10000)
                    page = browser.contexts[0].pages[0]
                    page.set_viewport_size({"width": 1024, "height": 600})
                    moves = []
                    page.on("request", lambda request: moves.append(request.post_data_json)
                            if request.url.endswith("/api/library/move") else None)
                    page.add_init_script("""window.__bloomDragEvents = [];
                        for (const name of ['dragstart','dragover','drop','dragend']) {
                            document.addEventListener(name, event => {
                                const row = event.target.closest('.library-row');
                                if (!row) return;
                                window.__bloomDragEvents.push({type:name,id:row.dataset.id,x:event.clientX,y:event.clientY});
                                if (window.__bloomDragEvents.length > 30) window.__bloomDragEvents.shift();
                            }, true);
                        }""")
                    phase = "native-startup"
                    try:
                        page.wait_for_selector("#map-style", timeout=20000)
                        page.wait_for_function(
                            "window.bloomMap && window.bloomMap.getCanvas().width > 0", timeout=20000)
                        with opener.open(f"http://127.0.0.1:{port}/api/state", timeout=5) as response:
                            if not json.load(response).get("preview"):
                                raise RuntimeError("Refuse a live device session")
                        page.locator("#about-button").click()
                        page.locator("#about-dialog").wait_for(state="visible")
                        page.locator("#close-about").click()
                        phase = "library-order"
                        library_order = check_library_order(page, f"http://127.0.0.1:{port}")
                        phase = "search-history"
                        search_history = check_search_history(page, f"http://127.0.0.1:{port}")
                    except Exception:
                        save_diagnostics(page, moves, phase)
                        raise
                if hashlib.sha256(runtime.read_bytes()).hexdigest() != original_hash:
                    raise RuntimeError("Bundled runtime was modified")
                if download_mark(runtime).read_bytes() != original_zone:
                    raise RuntimeError("Runtime download mark was removed or changed")
                if any(download_mark(path).read_text(encoding="ascii") != ZONE
                       for path in files):
                    raise RuntimeError("A download mark was removed or changed")
                print(json.dumps({"nativeWindow": True, "mapCanvas": True,
                                  "downloadMarkedFiles": len(files),
                                  "downloadMarksPreserved": True, "runtimeUnchanged": True,
                                  "libraryOrder": library_order, "searchHistory": search_history}))
            except Exception:
                destination = Path("artifacts/portable-smoke")
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(log_path, destination / "startup.log")
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
