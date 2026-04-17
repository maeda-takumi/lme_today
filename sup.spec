# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# Selenium は内部で遅延 import が多いため、実行時 import エラー回避のために
# 配下モジュールをまとめて hiddenimports に含める。
selenium_hiddenimports = collect_submodules('selenium')

a = Analysis(
    ['ui_main.py'],
    pathex=[],
    binaries=[],
    datas=[('icons/icon.ico', 'icons'), ('icons/icon.png', 'icons')],
    hiddenimports=selenium_hiddenimports,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SUP-ADMIN',
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
    icon=['icons\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SUP-ADMIN',
)
