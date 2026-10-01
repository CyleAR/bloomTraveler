"""Persistent pymobiledevice3 connection owned by one asyncio worker thread."""

import asyncio
from contextlib import AsyncExitStack
from contextlib import suppress
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import time

# Python 3.10's Wi-Fi TCP tunnel needs sslpsk-pmd3's OpenSSL 1.1 DLLs.
# A frozen build bundles them; local development can use the installed copy.
_openssl_dll_handle = None
if sys.platform == "win32" and sys.version_info < (3, 13) and not getattr(sys, "frozen", False):
    for dll_dir in (
        os.environ.get("BLOOM_OPENSSL_DIR"),
        r"C:\Program Files\Epic Games\UE_5.6\Engine\Extras\ThirdPartyNotUE\libimobiledevice\x64",
        r"C:\Program Files\DB Browser for SQLite",
    ):
        if dll_dir and all((Path(dll_dir) / name).is_file() for name in
                           ("libssl-1_1-x64.dll", "libcrypto-1_1-x64.dll")):
            _openssl_dll_handle = os.add_dll_directory(dll_dir)
            break

from packaging.version import Version
from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import RemotePairingCompletedError
from pymobiledevice3.lockdown import (
    DEFAULT_LABEL,
    SERVICE_PORT,
    SYSTEM_BUID,
    PlistUsbmuxLockdownClient,
    UsbmuxLockdownClient,
)
from pymobiledevice3.pair_records import create_pairing_records_cache_folder, generate_host_id
from pymobiledevice3.remote import userspace_tunnel
from pymobiledevice3.remote.tunnel_service import RemotePairingLockdownService, get_remote_pairing_tunnel_services
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.service_connection import ServiceConnection
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.usbmux import PlistMuxConnection
from usbmux_compat import install_same_socket_probe


class DeviceTimeoutError(TimeoutError):
    def __init__(self, stage, seconds):
        super().__init__(f"{stage} 응답 시간 초과 ({seconds:g}초)")


async def timed(stage, awaitable, seconds):
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except asyncio.TimeoutError as exc:
        raise DeviceTimeoutError(stage, seconds) from exc


async def connect_trusted_device(serial, timeout, device=None):
    """Open a trusted lockdown session with separately bounded protocol steps."""
    if device is None:
        device = await timed("장치 선택", usbmux.select_device(serial), min(timeout, 10))
    if device is None:
        raise ConnectionError("기기를 찾지 못했습니다")
    mux = await timed("장치 mux 소켓 열기", usbmux.create_mux(), min(timeout, 10))
    try:
        sock = await timed("장치 잠금 포트 연결", mux.connect(device, SERVICE_PORT), min(timeout, 10))
    except BaseException:
        await mux.close()
        raise
    service = ServiceConnection(sock, mux_device=device)
    try:
        client = await timed("장치 mux 연결", usbmux.create_mux(), min(timeout, 10))
        async with client:
            if isinstance(client, PlistMuxConnection):
                system_buid = await timed("장치 mux 식별", client.get_buid(), min(timeout, 10))
                cls = PlistUsbmuxLockdownClient
            else:
                system_buid = SYSTEM_BUID
                cls = UsbmuxLockdownClient
        identifier = service.mux_device.serial if service.mux_device is not None else serial
        lockdown = cls(
            service,
            host_id=generate_host_id(None),
            identifier=identifier,
            label=DEFAULT_LABEL,
            system_buid=system_buid,
            pairing_records_cache_folder=create_pairing_records_cache_folder(None),
            port=SERVICE_PORT,
        )
        await timed("기기 정보 조회", lockdown._initialize(), min(timeout, 10))
        paired = await timed("기기 신뢰 관계 검증", lockdown.validate_pairing(), min(timeout, 10))
        if not paired:
            raise RuntimeError("기기가 이 PC를 신뢰하지 않습니다. 기기 잠금을 풀고 '신뢰함'을 승인하세요.")
        return lockdown
    except BaseException:
        try:
            await asyncio.wait_for(service.close(), 3)
        except Exception:
            pass
        raise


async def open_userspace_tunnel(serial, timeout, connection_type="USB", wifi_service=None):
    """Open pymobiledevice3's tunnel on the selected transport."""
    tunnel = UserspaceRsdTunnel(serial=serial, autopair=False, remotepairing_fallback=False)
    if wifi_service is not None:
        async def selected_provider(*_args):
            return wifi_service, None

        attribute = "_create_no_root_tunnel_provider"
        replacement = selected_provider
    elif connection_type == "Network":
        original_create = userspace_tunnel.create_using_usbmux

        async def network_lockdown(*args, **kwargs):
            return await original_create(*args, connection_type="Network", **kwargs)

        attribute = "create_using_usbmux"
        replacement = network_lockdown
    else:
        attribute = None

    original = getattr(userspace_tunnel, attribute) if attribute else None
    if attribute:
        setattr(userspace_tunnel, attribute, replacement)
    try:
        provider = await timed(f"{connection_type} 터널 연결", tunnel.aopen(), timeout)
        return tunnel, provider
    except BaseException:
        with suppress(Exception):
            await tunnel.aclose()
        if wifi_service is not None:
            with suppress(Exception):
                await wifi_service.close()
        raise
    finally:
        if attribute:
            setattr(userspace_tunnel, attribute, original)


class TransportSwitch(Exception):
    """A preferred USB link appeared while using Wi-Fi."""


def same_device(first, second):
    return first.replace("-", "").lower() == second.replace("-", "").lower()


class DeviceService:
    """Coalesce position changes; keep a DVT session open for idle heartbeat."""

    def __init__(self, interval=0.5, timeout=30, log_path=None):
        self.events = queue.Queue()
        self._logger = None
        if log_path is not None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                self._logger = logging.getLogger(f"bloom.device.{id(self)}")
                self._logger.setLevel(logging.INFO)
                self._logger.propagate = False
                handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                self._logger.addHandler(handler)
            except OSError:
                self._logger = None
        self.interval = interval
        self.timeout = timeout
        self._lock = threading.Lock()
        self._position = None
        self._generation = 0
        self._heartbeat = False
        self._clear = False
        self._wifi_prepared = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="device-service", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)

    def set_position(self, latitude, longitude):
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Invalid coordinates")
        with self._lock:
            self._position = (latitude, longitude)
            self._generation += 1
            self._clear = False

    def set_heartbeat(self, enabled):
        with self._lock:
            self._heartbeat = enabled

    def restore_real_location(self):
        with self._lock:
            self._heartbeat = False
            self._position = None
            self._generation += 1
            self._clear = True

    def _snapshot(self):
        with self._lock:
            return self._position, self._generation, self._heartbeat, self._clear

    def _emit(self, kind, message):
        if self._logger is not None and kind in ("connected", "disconnected", "error", "restored", "notice"):
            self._logger.info("%s: %s", kind, message)
        self.events.put((kind, message))

    def _run(self):
        try:
            install_same_socket_probe()
            asyncio.run(self._main())
        except Exception as exc:
            self._emit("error", f"통신 작업 종료: {exc}")

    async def _prepare_wifi(self, lockdown, serial):
        if serial in self._wifi_prepared:
            return
        try:
            enabled = await timed("Wi-Fi 연결 설정 조회", lockdown.get_enable_wifi_connections(), 5)
            if not enabled:
                await timed("Wi-Fi 연결 활성화", lockdown.set_enable_wifi_connections(True), 5)
        except Exception as exc:
            self._emit("notice", f"Wi-Fi 연결 설정을 활성화하지 못했습니다: {exc}")

        service = None
        try:
            service = await timed("Wi-Fi 페어링 채널 연결", RemotePairingLockdownService.create(lockdown), 8)
            try:
                await timed("Wi-Fi 페어링", service.connect(autopair=True), 10)
            except RemotePairingCompletedError:
                pass
        except Exception as exc:
            self._emit("notice", f"Wi-Fi 페어링을 준비하지 못했습니다: {exc}")
        finally:
            if service is not None:
                with suppress(Exception):
                    await service.close()
        self._wifi_prepared.add(serial)

    async def _connect(self, serial, device=None):
        stack = AsyncExitStack()
        try:
            if device is None:
                lockdown = await connect_trusted_device(serial, self.timeout)
            else:
                lockdown = await connect_trusted_device(serial, self.timeout, device=device)
            stack.push_async_callback(lockdown.close)
            version = lockdown.product_version
            connection_type = device.connection_type if device is not None else "USB"
            tunnel = None
            if connection_type == "USB" and Version(version) >= Version("17"):
                await self._prepare_wifi(lockdown, serial)
            if Version(version) >= Version("17.4"):
                tunnel, provider = await open_userspace_tunnel(serial, self.timeout, connection_type)
                stack.push_async_callback(tunnel.aclose)
            elif Version(version) >= Version("17"):
                raise RuntimeError("iOS 17.0~17.3은 이 앱의 userspace 터널을 지원하지 않습니다. 17.4 이상으로 업데이트하세요.")
            else:
                provider = lockdown
            dvt = await timed("DVT 연결", stack.enter_async_context(DvtProvider(provider)), self.timeout)
            location = await timed("위치 채널 연결", stack.enter_async_context(LocationSimulation(dvt)), self.timeout)
            return stack, tunnel, location, version
        except BaseException:
            try:
                await asyncio.wait_for(stack.aclose(), 5)
            except Exception:
                pass
            raise

    async def _connect_paired_wifi(self, wifi_service):
        stack = AsyncExitStack()
        try:
            serial = wifi_service.remote_identifier
            tunnel, provider = await open_userspace_tunnel(serial, self.timeout, "Wi-Fi", wifi_service)
            stack.push_async_callback(tunnel.aclose)
            version = provider.product_version
            dvt = await timed("DVT 연결", stack.enter_async_context(DvtProvider(provider)), self.timeout)
            location = await timed("위치 채널 연결", stack.enter_async_context(LocationSimulation(dvt)), self.timeout)
            return stack, tunnel, location, version
        except BaseException:
            with suppress(Exception):
                await asyncio.wait_for(stack.aclose(), 5)
            raise

    async def _main(self):
        last_error = None
        preferred_serial = None
        while not self._stop.is_set():
            stack = None
            was_active = False
            try:
                devices = await timed("장치 검색", usbmux.list_devices(), min(self.timeout, 10))
                devices = sorted(
                    (device for device in devices if device.connection_type in ("USB", "Network")),
                    key=lambda device: (
                        preferred_serial is not None and not same_device(device.serial, preferred_serial),
                        device.connection_type != "USB",
                    ),
                )
                failure = None
                for device in devices:
                    try:
                        stack, tunnel, location, version = await self._connect(device.serial, device=device)
                    except Exception as exc:
                        failure = exc
                        continue
                    serial = device.serial
                    transport = device.connection_type
                    break

                if stack is None:
                    wifi_services = await timed(
                        "Wi-Fi 기기 검색", get_remote_pairing_tunnel_services(bonjour_timeout=3),
                        min(self.timeout, 12),
                    )
                    wifi_services.sort(key=lambda service: (
                        preferred_serial is not None and not same_device(service.remote_identifier, preferred_serial)
                    ))
                    chosen = None
                    try:
                        for service in wifi_services:
                            try:
                                stack, tunnel, location, version = await self._connect_paired_wifi(service)
                            except Exception as exc:
                                failure = exc
                                continue
                            chosen = service
                            serial = service.remote_identifier
                            transport = "Wi-Fi"
                            break
                    finally:
                        for service in wifi_services:
                            if service is not chosen:
                                with suppress(Exception):
                                    await service.close()

                if stack is None:
                    if failure is None:
                        message = "USB/Wi-Fi 기기를 찾지 못했습니다. 같은 네트워크와 무선 연결·페어링 설정을 확인하세요."
                    else:
                        message = f"장치 통신 실패: {type(failure).__name__}: {failure}"
                    if message != last_error:
                        self._emit("disconnected", message)
                        last_error = message
                    await asyncio.sleep(2)
                    continue

                preferred_serial = serial
                label = "USB" if transport == "USB" else "Wi-Fi"
                self._emit("connected", f"{label} 연결됨 · iOS {version} · {serial}")
                was_active = True
                last_error = None
                sent_generation = -1
                last_send = 0.0
                last_check = 0.0
                while not self._stop.is_set():
                    position, generation, heartbeat, clear = self._snapshot()
                    now = time.monotonic()
                    if tunnel is not None and tunnel.rsd is None:
                        raise ConnectionError(f"{label} 터널 연결이 끊어졌습니다")
                    if now - last_check >= 3:
                        devices = await timed("장치 연결 확인", usbmux.list_devices(), min(self.timeout, 10))
                        if transport in ("USB", "Network") and not any(
                            same_device(device.serial, serial) and device.connection_type == transport
                            for device in devices
                        ):
                            raise ConnectionError(f"{label} 기기 연결이 끊어졌습니다")
                        if transport != "USB" and any(
                            same_device(device.serial, serial) and device.connection_type == "USB"
                            for device in devices
                        ):
                            raise TransportSwitch()
                        last_check = now
                    if clear:
                        await timed("실제 위치 복구", location.clear(), min(self.timeout, 10))
                        with self._lock:
                            if self._generation == generation:
                                self._clear = False
                        sent_generation = generation
                        self._emit("restored", "위치 시뮬레이션 해제 완료")
                    elif position is not None and (generation != sent_generation or
                                                     (heartbeat and now - last_send >= self.interval)):
                        await timed("위치 전송", location.set(*position), min(self.timeout, 10))
                        sent_generation = generation
                        last_send = time.monotonic()
                        self._emit("sent", position)
                    await asyncio.sleep(0.05)
            except TransportSwitch:
                pass
            except Exception as exc:
                message = f"장치 통신 실패: {type(exc).__name__}: {exc}"
                if not was_active and message != last_error:
                    self._emit("disconnected", message)
                    last_error = message
                if not was_active:
                    await asyncio.sleep(2)
            finally:
                if stack is not None:
                    try:
                        await asyncio.wait_for(stack.aclose(), 3)
                    except Exception:
                        pass
