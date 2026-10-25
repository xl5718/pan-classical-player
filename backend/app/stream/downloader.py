"""把网盘文件分块并发下载到本地缓存，同时允许边下边读；按播放位置决定先下哪一块。"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..baidu.client import XpanClient
from ..baidu.errors import BaiduError

log = logging.getLogger(__name__)

CHUNK = 8 * 1024 * 1024          # 初始分段大小
SPLIT_MIN = 1024 * 1024          # 拖动位置距已下载处超过此值时，就地切开新分段
PENDING, INFLIGHT, DONE = 0, 1, 2
MAX_CHUNK_ATTEMPTS = 5
DLINK_TTL = 3600


@dataclass
class FileSpec:
    fsid: int
    size: int
    mtime: int
    ext: str
    path: str  # 网盘路径，仅用于日志

    @property
    def key(self) -> str:
        return f"{self.fsid}_{self.mtime}"


class DlinkCache:
    def __init__(self, client: XpanClient):
        self.client = client
        self._links: dict[int, tuple[str, float]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def get(self, fsid: int, force: bool = False) -> str:
        lock = self._locks.setdefault(fsid, asyncio.Lock())
        async with lock:
            hit = self._links.get(fsid)
            if hit and not force and time.monotonic() - hit[1] < DLINK_TTL:
                return hit[0]
            link = await self.client.get_dlink(fsid)
            self._links[fsid] = (link, time.monotonic())
            return link


class Seg:
    __slots__ = ("start", "end", "state", "got")

    def __init__(self, start: int, end: int):
        self.start, self.end, self.state, self.got = start, end, PENDING, 0

    @property
    def filled(self) -> int:
        return self.start + self.got


class FileDownload:
    def __init__(self, spec: FileSpec, part: Path, final: Path, client: XpanClient, dlinks: DlinkCache,
                 max_workers: int, on_complete):
        self.spec = spec
        self.part = part
        self.final = final
        self.client = client
        self.dlinks = dlinks
        self.max_workers = max_workers
        self.on_complete = on_complete
        self.segs = [Seg(s, min(s + CHUNK, spec.size)) for s in range(0, max(spec.size, 1), CHUNK)]
        self.focus = 0
        self.workers: set[asyncio.Task] = set()
        self.error: Exception | None = None
        self.complete = False
        self.started = time.monotonic()
        self.bytes_done = 0
        self._event = asyncio.Event()
        self._resolved: str | None = None
        self._failures = 0
        fd = os.open(part, os.O_RDWR | os.O_CREAT, 0o600)
        os.ftruncate(fd, spec.size)
        self._fd = fd

    # ---- 状态查询 ----
    def _index(self, pos: int) -> int:
        lo, hi = 0, len(self.segs) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.segs[mid].start <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def available_end(self, pos: int) -> int:
        """从 pos 起连续可读到的位置（不含）。"""
        end = pos
        for seg in self.segs[self._index(pos):]:
            if seg.filled <= end:
                break
            end = seg.filled
            if seg.filled < seg.end:
                break
        return end

    @property
    def done_chunks(self) -> int:
        return sum(1 for s in self.segs if s.state == DONE)

    @property
    def n(self) -> int:
        return len(self.segs)

    def _bump(self) -> None:
        self._event.set()
        self._event = asyncio.Event()

    async def wait_change(self, timeout: float) -> None:
        ev = self._event
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    # ---- 调度 ----
    def _split(self, i: int, pos: int) -> int:
        seg = self.segs[i]
        new = Seg(pos, seg.end)
        seg.end = pos  # 若该段正在下载，worker 写到 pos 就会停下
        self.segs.insert(i + 1, new)
        return i + 1

    def set_focus(self, pos: int) -> None:
        if self.complete or self.error:
            return
        i = self._index(min(pos, self.spec.size - 1))
        seg = self.segs[i]
        if seg.state != DONE and pos - seg.filled > SPLIT_MIN:
            i = self._split(i, pos)
            seg = self.segs[i]
        self.focus = i
        if seg.state == PENDING:
            # 播放位置所在分段没人下载：立即单独开一条连接，不等其他分段
            self._spawn(seg)
        self.ensure_workers()

    def _spawn(self, first: Seg | None = None) -> None:
        if first is not None:
            first.state = INFLIGHT
        t = asyncio.create_task(self._worker(first))
        self.workers.add(t)
        t.add_done_callback(self.workers.discard)

    def _alive(self) -> int:
        return sum(1 for w in self.workers if not w.done())

    def ensure_workers(self) -> None:
        if self.complete or self.error:
            return
        while self._alive() < self.max_workers and any(s.state == PENDING for s in self.segs):
            self._spawn()

    def _next_seg(self) -> Seg | None:
        order = list(range(self.focus, len(self.segs))) + list(range(0, self.focus))
        for i in order:
            if self.segs[i].state == PENDING:
                return self.segs[i]
        # 没有待下载的分段：把剩余最多的进行中分段切一半，分担给空闲连接
        best = max(
            (i for i, s in enumerate(self.segs) if s.state == INFLIGHT),
            key=lambda i: self.segs[i].end - self.segs[i].filled,
            default=None,
        )
        if best is not None:
            seg = self.segs[best]
            rest = seg.end - seg.filled
            if rest > 2 * SPLIT_MIN:
                return self.segs[self._split(best, seg.filled + rest // 2)]
        return None

    async def _worker(self, first: Seg | None = None) -> None:
        prev_end = None
        while not self.error:
            nxt = None
            if prev_end is not None:
                # 先接着下紧挨着的下一段，保证播放位置之后的数据连续到达
                j = self._index(prev_end)
                cand = self.segs[j] if prev_end < self.spec.size else None
                if cand is not None and cand.start == prev_end and cand.state == PENDING:
                    nxt = cand
            if first is not None:
                seg, first = first, None
            elif nxt is not None:
                seg = nxt
            else:
                if self._alive() > self.max_workers:
                    return
                seg = self._next_seg()
                if seg is None:
                    break
            seg.state = INFLIGHT
            try:
                await self._fetch(seg)
                seg.state = DONE
                self._failures = 0
                prev_end = seg.end
            except asyncio.CancelledError:
                seg.state = PENDING
                raise
            except Exception as e:  # noqa: BLE001
                seg.state = PENDING
                prev_end = None
                self._failures += 1
                if self._failures >= MAX_CHUNK_ATTEMPTS:
                    self.error = e
                    log.error("下载失败 %s：%s", self.spec.path, e)
                else:
                    delay = min(1.5 * 2 ** (self._failures - 1), 20)
                    log.warning("分段 @%d 下载出错（%r），%.1fs 后重试：%s", seg.start, e, delay, self.spec.path)
                    await asyncio.sleep(delay)
            finally:
                self._bump()
        if all(s.state == DONE for s in self.segs) and not self.complete:
            self._finish()

    async def _open(self, start: int, end: int, force_dlink: bool = False) -> httpx.Response:
        headers = {"Range": f"bytes={start}-{end}"}
        if self._resolved and not force_dlink:
            resp = await self.client.http.send(
                self.client.http.build_request("GET", self._resolved, headers=headers), stream=True
            )
            if resp.status_code < 400:
                return resp
            await resp.aclose()
            self._resolved = None
        link = await self.dlinks.get(self.spec.fsid, force=force_dlink)
        resp = await self.client.open_download(link, headers)
        if resp.history:
            self._resolved = str(resp.url)
        return resp

    async def _fetch(self, seg: Seg) -> None:
        refreshed = False
        while seg.filled < seg.end:
            start = seg.filled
            try:
                resp = await self._open(start, seg.end - 1)
            except BaiduError as e:
                # dlink 过期或失效：重新获取一次再试
                if not refreshed and isinstance(e.errno, int) and e.errno in (403, 404, 410):
                    refreshed = True
                    self._resolved = None
                    await self.dlinks.get(self.spec.fsid, force=True)
                    continue
                raise
            try:
                if resp.status_code != 206:
                    raise BaiduError(resp.status_code, f"期望 206，收到 {resp.status_code}")
                async for data in resp.aiter_bytes(64 * 1024):
                    room = seg.end - seg.filled
                    if room <= 0:
                        break
                    data = data[:room]
                    os.pwrite(self._fd, data, seg.filled)
                    seg.got += len(data)
                    self.bytes_done += len(data)
                    self._bump()
            finally:
                await resp.aclose()
            if seg.filled < seg.end:
                raise BaiduError("short_read", f"分段 @{seg.start} 未读完")

    def _finish(self) -> None:
        os.fsync(self._fd)
        os.close(self._fd)
        self._fd = -1
        os.replace(self.part, self.final)
        self.complete = True
        el = time.monotonic() - self.started
        log.info("已缓存 %s（%.1f MB，%.1fs，%.1f MB/s）", self.spec.path, self.spec.size / 1e6, el,
                 self.spec.size / 1e6 / max(el, 0.001))
        self._bump()
        self.on_complete(self)

    async def cancel(self) -> None:
        for t in list(self.workers):
            t.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
        if not self.complete:
            self.part.unlink(missing_ok=True)
