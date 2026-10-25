"""Mac 桌面应用入口：在后台线程启动后端，用原生窗口（WKWebView）显示界面，并接入系统的「正在播放」与媒体键。"""

import logging
import socket
import threading
import time

log = logging.getLogger("desktop")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int):
    import uvicorn

    from .main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="on"))
    thread = threading.Thread(target=server.run, name="backend", daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("后端启动失败，详见日志")


class NowPlaying:
    """把网页里的播放状态同步到 macOS 控制中心 / 锁屏的「正在播放」，并把媒体键转发给网页里的播放器。"""

    def __init__(self, covers_dir):
        self.window = None
        self.covers_dir = covers_dir
        self._art_path = None
        self._artwork = None
        try:
            import MediaPlayer  # noqa: F401  pyobjc-framework-MediaPlayer

            self.available = True
        except ImportError:
            self.available = False
            log.warning("缺少 pyobjc-framework-MediaPlayer，媒体键与「正在播放」不可用")

    def _js(self, code: str) -> None:
        # 媒体键回调在主线程上，evaluate_js 会等待主线程，必须换个线程调用以免死锁
        if self.window is not None:
            threading.Thread(target=self.window.evaluate_js, args=(code,), daemon=True).start()

    def install(self, window) -> None:
        self.window = window
        _hide_title(window)
        if not self.available:
            return
        from MediaPlayer import MPRemoteCommandCenter

        cc = MPRemoteCommandCenter.sharedCommandCenter()

        def bind(command, code):
            command.setEnabled_(True)
            command.addTargetWithHandler_(lambda event: (self._js(code), 0)[1])

        bind(cc.togglePlayPauseCommand(), "window.__player && window.__player.toggle()")
        bind(cc.playCommand(), "window.__player && window.__player.play()")
        bind(cc.pauseCommand(), "window.__player && window.__player.pause()")
        bind(cc.nextTrackCommand(), "window.__player && window.__player.next()")
        bind(cc.previousTrackCommand(), "window.__player && window.__player.prev()")
        seek = cc.changePlaybackPositionCommand()
        seek.setEnabled_(True)
        seek.addTargetWithHandler_(
            lambda event: (self._js(f"window.__player && window.__player.seek({float(event.positionTime()):.3f})"), 0)[1]
        )

    def update(self, info: dict) -> None:
        if not self.available:
            return
        from PyObjCTools import AppHelper

        def safe():
            try:
                self._apply(info)
            except Exception:  # noqa: BLE001
                log.exception("更新「正在播放」失败")

        AppHelper.callAfter(safe)

    def _apply(self, info: dict) -> None:
        from MediaPlayer import (
            MPMediaItemPropertyAlbumTitle,
            MPMediaItemPropertyArtist,
            MPMediaItemPropertyArtwork,
            MPMediaItemPropertyPlaybackDuration,
            MPMediaItemPropertyTitle,
            MPNowPlayingInfoCenter,
            MPNowPlayingInfoPropertyElapsedPlaybackTime,
            MPNowPlayingInfoPropertyPlaybackRate,
        )

        center = MPNowPlayingInfoCenter.defaultCenter()
        if not info.get("title"):
            center.setNowPlayingInfo_(None)
            center.setPlaybackState_(0)  # unknown
            return
        playing = bool(info.get("playing"))
        d = {
            MPMediaItemPropertyTitle: info.get("title") or "",
            MPMediaItemPropertyArtist: info.get("artist") or "",
            MPMediaItemPropertyAlbumTitle: info.get("album") or "",
            MPMediaItemPropertyPlaybackDuration: float(info.get("duration") or 0),
            MPNowPlayingInfoPropertyElapsedPlaybackTime: float(info.get("position") or 0),
            MPNowPlayingInfoPropertyPlaybackRate: 1.0 if playing else 0.0,
        }
        art = self._art(info.get("cover"))
        if art is not None:
            d[MPMediaItemPropertyArtwork] = art
        center.setNowPlayingInfo_(d)
        center.setPlaybackState_(1 if playing else 2)  # playing / paused

    def _art(self, cover):
        if not cover:
            return None
        path = (self.covers_dir / cover).resolve()
        if path == self._art_path:
            return self._artwork
        try:
            from AppKit import NSImage
            from MediaPlayer import MPMediaItemArtwork

            img = NSImage.alloc().initWithContentsOfFile_(str(path))
            if img is None:
                return None
            self._artwork = MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(img.size(), lambda size: img)
            self._art_path = path
            return self._artwork
        except Exception as e:  # noqa: BLE001
            log.warning("封面无法用于「正在播放」：%s", e)
            return None


def _hide_title(window) -> None:
    """隐藏窗口标题文字：界面顶部已有「唱片目录」，系统标题在新版 macOS 上会与红绿灯按钮重叠。"""
    try:
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView

        def apply():
            inst = BrowserView.instances.get(window.uid)
            if inst is not None:
                inst.window.setTitleVisibility_(1)  # NSWindowTitleHidden

        AppHelper.callAfter(apply)
    except Exception:  # noqa: BLE001
        log.exception("无法隐藏窗口标题")


def _patch_cocoa(window, state: dict) -> None:
    """在 pywebview 创建窗口之前给它的 Cocoa 代理类补上几个方法：
    - 点关闭按钮只隐藏窗口，音乐继续在后台播放；点程序坞图标重新显示；⌘Q 才真正退出
    - 网页进程意外退出（白屏）时记录日志并自动重新加载
    """
    import objc
    from webview.platforms.cocoa import BrowserView

    def applicationShouldTerminate_(self, app):
        state["quitting"] = True
        return 1  # NSTerminateNow

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible):
        window.show()
        return True

    def webViewWebContentProcessDidTerminate_(self, webview):
        log.error("网页进程意外退出，正在重新加载")
        webview.reload()

    def add(cls, func, sel: bytes, sig: bytes) -> bool:
        try:
            objc.classAddMethods(cls, [objc.selector(func, selector=sel, signature=sig)])
            return True
        except Exception:  # noqa: BLE001
            log.exception("无法添加 %s", sel.decode())
            return False

    # 签名需与 pywebview 原有实现一致（BOOL 返回值在这里是 I）
    quit_ok = add(BrowserView.AppDelegate, applicationShouldTerminate_, b"applicationShouldTerminate:", b"I@:@")
    add(BrowserView.AppDelegate, applicationShouldHandleReopen_hasVisibleWindows_,
        b"applicationShouldHandleReopen:hasVisibleWindows:", b"Z@:@Z")
    add(BrowserView.BrowserDelegate, webViewWebContentProcessDidTerminate_,
        b"webViewWebContentProcessDidTerminate:", b"v@:@")
    if not quit_ok:
        return  # 无法区分 ⌘Q 与关闭按钮时，保持原来的"关闭即退出"，以免无法退出

    def on_closing():
        if state.get("quitting"):
            return True
        window.hide()
        return False  # 取消关闭

    window.events.closing += on_closing


class Bridge:
    """暴露给网页的接口：window.pywebview.api.*"""

    def __init__(self, now: NowPlaying):
        self._now = now

    def now_playing(self, info: dict) -> bool:
        self._now.update(info or {})
        return True


def main() -> None:
    import webview

    from .config import CONFIG_DIR, get_settings

    s = get_settings()
    port = _free_port()
    server = _start_server(port)
    log.info("后端已启动：127.0.0.1:%s，配置目录 %s", port, CONFIG_DIR)

    now = NowPlaying(s.covers_dir)
    window = webview.create_window(
        "唱片目录",
        f"http://127.0.0.1:{port}/",
        js_api=Bridge(now),
        width=1320,
        height=880,
        min_size=(900, 600),
        background_color="#F6F3EC",
        text_select=True,
    )
    state: dict = {}
    try:
        _patch_cocoa(window, state)
    except Exception:  # noqa: BLE001
        log.exception("无法设置后台播放与崩溃恢复，关闭窗口将直接退出")
    storage = CONFIG_DIR / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    # private_mode=False：保留 localStorage（播放队列、音量、深浅色设置）
    # 在 .env 里设 UI_DEBUG=true 可打开 Web 检查器排查问题
    webview.start(now.install, window, private_mode=False, storage_path=str(storage), debug=s.ui_debug)
    server.should_exit = True
