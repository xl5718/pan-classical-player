import asyncio
import logging
import os
import time
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path

from ..baidu.client import XpanClient
from ..config import Settings
from .downloader import DlinkCache, FileDownload, FileSpec

log = logging.getLogger(__name__)

READ_BLOCK = 256 * 1024
STALL_TIMEOUT = 90
MAX_ACTIVE = 4


class StreamManager:
    def __init__(self, settings: Settings, client: XpanClient):
        self.s = settings
        self.client = client
        self.dlinks = DlinkCache(client)
        self.dir = settings.cache_dir
        self.downloads: dict[str, FileDownload] = {}
        self.kind: dict[str, str] = {}  # play / prefetch
        self.pins: Counter[str] = Counter()
        for p in self.dir.glob("*.part"):
            p.unlink(missing_ok=True)

    def final_path(self, spec: FileSpec) -> Path:
        return self.dir / f"{spec.key}{spec.ext}"

    def cached(self, spec: FileSpec) -> Path | None:
        p = self.final_path(spec)
        if p.exists() and p.stat().st_size == spec.size:
            return p
        return None

    def ensure(self, spec: FileSpec, kind: str = "play", focus: int | None = None) -> FileDownload | None:
        """确保文件在缓存中或正在下载；已完整缓存时返回 None。"""
        if self.cached(spec):
            os.utime(self.final_path(spec))
            return None
        dl = self.downloads.get(spec.key)
        if dl and (dl.error or dl.complete):
            self.downloads.pop(spec.key, None)
            dl = None
        workers = self.s.play_connections if kind == "play" else self.s.prefetch_connections
        if dl is None:
            self._make_room()
            part = self.dir / f"{spec.key}.part"
            dl = FileDownload(spec, part, self.final_path(spec), self.client, self.dlinks, workers, self._completed)
            self.downloads[spec.key] = dl
            self.kind[spec.key] = kind
            log.info("开始%s %s（%.1f MB）", "缓存" if kind == "play" else "预取", spec.path, spec.size / 1e6)
        elif kind == "play" and self.kind.get(spec.key) != "play":
            self.kind[spec.key] = "play"
            dl.max_workers = max(dl.max_workers, workers)
        if focus is not None:
            dl.set_focus(focus)  # 先给播放位置单独开连接，再补足其余连接
        else:
            dl.ensure_workers()
        return dl

    def _completed(self, dl: FileDownload) -> None:
        self.downloads.pop(dl.spec.key, None)
        self.kind.pop(dl.spec.key, None)
        self.evict()

    def _make_room(self) -> None:
        active = [k for k, d in self.downloads.items() if not d.complete and not d.error]
        if len(active) < MAX_ACTIVE:
            return
        victims = sorted(
            (k for k in active if self.kind.get(k) == "prefetch" and not self.pins[k]),
            key=lambda k: self.downloads[k].started,
        ) or sorted((k for k in active if not self.pins[k]), key=lambda k: self.downloads[k].started)
        for k in victims[: len(active) - MAX_ACTIVE + 1]:
            dl = self.downloads.pop(k)
            self.kind.pop(k, None)
            log.info("下载任务过多，取消 %s", dl.spec.path)
            asyncio.create_task(dl.cancel())

    def evict(self) -> None:
        files = [p for p in self.dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
        total = sum(p.stat().st_size for p in files)
        total += sum(d.spec.size for d in self.downloads.values() if not d.complete)
        limit = self.s.cache_max_bytes
        if total <= limit:
            return
        for p in sorted(files, key=lambda p: p.stat().st_mtime):
            key = p.stem
            if self.pins[key] or key in self.downloads:
                continue
            size = p.stat().st_size
            p.unlink(missing_ok=True)
            total -= size
            log.info("缓存超出上限，删除 %s（%.1f MB）", p.name, size / 1e6)
            if total <= limit:
                break

    async def read(self, spec: FileSpec, start: int, end: int) -> AsyncIterator[bytes]:
        """产出 [start, end] 字节；未下载到的部分等待下载器写入。"""
        self.pins[spec.key] += 1
        fd = -1
        try:
            dl = self.ensure(spec, "play", focus=start)
            if dl is None:
                fd = os.open(self.final_path(spec), os.O_RDONLY)
                pos = start
                while pos <= end:
                    data = os.pread(fd, min(READ_BLOCK, end + 1 - pos), pos)
                    if not data:
                        break
                    pos += len(data)
                    yield data
                return
            fd = os.open(dl.part if not dl.complete else dl.final, os.O_RDONLY)
            pos = start
            last_progress = time.monotonic()
            while pos <= end:
                avail = dl.available_end(pos)
                if avail > pos:
                    n = min(avail, end + 1, pos + READ_BLOCK) - pos
                    data = os.pread(fd, n, pos)
                    pos += len(data)
                    last_progress = time.monotonic()
                    yield data
                    continue
                if dl.error:
                    raise dl.error
                if time.monotonic() - last_progress > STALL_TIMEOUT:
                    raise TimeoutError(f"等待数据超时：{spec.path} @ {pos}")
                dl.set_focus(pos)
                await dl.wait_change(5)
        finally:
            if fd >= 0:
                os.close(fd)
            self.pins[spec.key] -= 1
            final = self.final_path(spec)
            if final.exists():
                os.utime(final)

    def stats(self) -> dict:
        files = [p for p in self.dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
        return {
            "cached_files": len(files),
            "cached_bytes": sum(p.stat().st_size for p in files),
            "limit_bytes": self.s.cache_max_bytes,
            "downloads": [
                {
                    "path": d.spec.path,
                    "kind": self.kind.get(k),
                    "size": d.spec.size,
                    "done_chunks": d.done_chunks,
                    "chunks": d.n,
                    "bytes": d.bytes_done,
                    "workers": len(d.workers),
                    "mbps": round(d.bytes_done / 1e6 / max(time.monotonic() - d.started, 0.001), 2),
                    "error": str(d.error) if d.error else None,
                }
                for k, d in self.downloads.items()
            ],
        }

    async def aclose(self) -> None:
        for d in list(self.downloads.values()):
            await d.cancel()
