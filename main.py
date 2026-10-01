"""Bloom Traveler's MapLibre/WebView2 desktop entry point."""

import argparse
import ctypes
import os
from pathlib import Path
import sys
import time
from app_server import AppServer
from controller import Controller, PreviewDevice
from geocoder import Geocoder
from library import Library

VERSION = "2.0.0"


def data_directory():
    return Path(os.environ.get("BLOOM_TRAVELER_DATA_DIR") or
                Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BloomTraveler")


def main():
    parser = argparse.ArgumentParser(description="Bloom Traveler")
    parser.add_argument("--browser", action="store_true", help="Run the UI in a browser")
    parser.add_argument("--preview", action="store_true", help="Preview without device communication")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    mutex = None
    if sys.platform == "win32" and not args.preview:
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\BloomTravelerDesktop")
        if kernel.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(None, "Bloom Traveler가 이미 실행 중입니다.", "Bloom Traveler", 0x40)
            kernel.CloseHandle(ctypes.c_void_p(mutex))
            return
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    if args.preview:
        device = PreviewDevice()
    else:
        from device_service import DeviceService
        device = DeviceService(log_path=Path(os.environ.get("BLOOM_TRAVELER_LOG_DIR", directory)) / "device.log")
    controller = Controller(device, Library(directory / "library.json"), preview=args.preview)
    assets = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "web"
    server = AppServer(controller, Geocoder(), assets, args.port)
    try:
        controller.start()
        server.start()
        if args.browser:
            print(f"Bloom Traveler {VERSION}: {server.url}", flush=True)
            while True: time.sleep(0.5)
        else:
            import webview
            from desktop_ui import attach_local_assets
            webview.settings["ALLOW_DOWNLOADS"] = True
            debug_port = os.environ.get("BLOOM_UI_DEBUG_PORT") if args.preview else None
            if debug_port:
                webview.settings["REMOTE_DEBUGGING_PORT"] = int(debug_port)
            window = webview.create_window(f"Bloom Traveler {VERSION}", html="<html><body></body></html>",
                                          width=1320, height=900, min_size=(780, 600), background_color="#f7f8fa")
            window.events.before_show += lambda: attach_local_assets(window, server, "?qa=1" if debug_port else "")
            webview.start(gui="edgechromium", private_mode=False, storage_path=str(directory / "webview"),
                          icon=str(assets / "app.ico"))
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        controller.close()
        if mutex: ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            ctypes.windll.user32.MessageBoxW(None, f"앱을 시작하지 못했습니다.\n{exc}", "Bloom Traveler", 0x10)
        raise
