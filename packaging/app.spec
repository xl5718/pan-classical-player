# PyInstaller 打包配置：make app
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "packaging", "build", "icon.icns")

a = Analysis(
    [os.path.join(ROOT, "backend", "desktop_entry.py")],
    pathex=[os.path.join(ROOT, "backend")],
    datas=[(os.path.join(ROOT, "frontend", "dist"), "frontend_dist")],
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("app") + ["MediaPlayer", "AppKit", "PyObjCTools.AppHelper"],
    excludes=["pytest", "ruff", "tkinter", "PyInstaller"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RecordCatalog", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="RecordCatalog")
app = BUNDLE(
    coll,
    name="唱片目录.app",
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier="io.github.pan-classical-player",
    info_plist={
        "CFBundleName": "唱片目录",
        "CFBundleDisplayName": "唱片目录",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHighResolutionCapable": True,
        "NSAppTransportSecurity": {
            "NSAllowsLocalNetworking": True,
            "NSExceptionDomains": {
                "127.0.0.1": {"NSExceptionAllowsInsecureHTTPLoads": True},
                "localhost": {"NSExceptionAllowsInsecureHTTPLoads": True},
            },
        },
    },
)
