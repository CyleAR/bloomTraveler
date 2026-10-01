# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs
from PyInstaller.utils.hooks import copy_metadata
from pathlib import Path
import os
import sys

datas = [('app.ico', 'web'), ('web', 'web')]
binaries = []
hiddenimports = ['webview.platforms.winforms', 'webview.platforms.edgechromium', 'clr_loader.netfx']
datas += copy_metadata('pymobiledevice3')
# Keep device resource files, but do not bundle pymobiledevice3's CLI and optional
# screen/video modules. The application imports its device paths explicitly.
datas += collect_data_files('pymobiledevice3')
tmp_ret = collect_all('pytun_pmd3')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pmd_pytcp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
if sys.version_info < (3, 13):
    openssl_dirs = [
        os.environ.get('BLOOM_OPENSSL_DIR'),
        r'C:\Program Files\Epic Games\UE_5.6\Engine\Extras\ThirdPartyNotUE\libimobiledevice\x64',
        r'C:\Program Files\DB Browser for SQLite',
    ]
    openssl_dir = next((Path(folder) for folder in openssl_dirs if folder and all(
        (Path(folder) / name).is_file() for name in
        ('libssl-1_1-x64.dll', 'libcrypto-1_1-x64.dll'))), None)
    if openssl_dir is None:
        raise RuntimeError('OpenSSL 1.1 DLLs not found; set BLOOM_OPENSSL_DIR before building')
    binaries += collect_dynamic_libs('sslpsk_pmd3')
    hiddenimports += ['sslpsk_pmd3.sslpsk', 'sslpsk_pmd3._sslpsk_openssl1']
    binaries += [(str(openssl_dir / name), '.') for name in
                 ('libssl-1_1-x64.dll', 'libcrypto-1_1-x64.dll')]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'customtkinter', 'tkintermapview', 'tkinter',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3',
        # Optional pymobiledevice3 CLI and screen/video features unused by this app.
        'av', 'PIL', 'IPython', 'jedi', 'parso',
        'pyreadline3', 'xonsh', 'pydantic', 'pydantic_core',
        'pymobiledevice3.tunneld',
        'pymobiledevice3.remote.core_device.hevc_av',
        'pymobiledevice3.services.web_protocol.cdp_screencast',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

from build_licenses import generate_notices
generate_notices(list(a.scripts) + list(a.pure) + list(a.binaries))
license_root = Path('web/licenses')
license_data = [('web/licenses/' + path.relative_to(license_root).as_posix(), str(path.resolve()), 'DATA')
                for path in license_root.rglob('*') if path.is_file()]
replaced = {entry[0] for entry in license_data}
a.datas = [entry for entry in a.datas if entry[0].replace('\\', '/') not in replaced] + license_data

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Bloom Traveler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Bloom Traveler',
)
