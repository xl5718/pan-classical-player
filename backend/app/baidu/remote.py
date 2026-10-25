import asyncio
import bisect
import io
import logging

import httpx

from .errors import BaiduError

log = logging.getLogger(__name__)

MIN_FETCH = 64 * 1024
MAX_ATTEMPTS = 4


class RemoteReader:
    """按需用 Range 读取网盘文件的任意片段，并缓存已读区间。"""

    def __init__(self, client, fsid: int, size: int, dlink: str | None = None, label: str = ""):
        self.client = client
        self.fsid = fsid
        self.size = size
        self.dlink = dlink
        self.label = label or str(fsid)
        self._resolved: str | None = None
        self._starts: list[int] = []
        self._chunks: list[bytes] = []
        self.requests = 0
        self.bytes_fetched = 0

    async def _fetch(self, start: int, end: int) -> bytes:
        headers = {"Range": f"bytes={start}-{end}"}
        dlink_refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            try:
                if self._resolved:
                    resp = await self.client.http.send(
                        self.client.http.build_request("GET", self._resolved, headers=headers), stream=True
                    )
                    if resp.status_code >= 400:
                        await resp.aclose()
                        self._resolved = None
                        continue
                else:
                    if self.dlink is None:
                        self.dlink = await self.client.get_dlink(self.fsid)
                    resp = await self.client.open_download(self.dlink, headers)
                    if resp.history:
                        self._resolved = str(resp.url)
                try:
                    want = end - start + 1
                    if resp.status_code == 206:
                        data = await resp.aread()
                    else:
                        # 服务器忽略了 Range：从头读到需要的位置为止
                        buf = bytearray()
                        async for chunk in resp.aiter_bytes():
                            buf += chunk
                            if len(buf) > end:
                                break
                        data = bytes(buf[start : end + 1])
                finally:
                    await resp.aclose()
                self.requests += 1
                self.bytes_fetched += len(data)
                if len(data) < want and end < self.size:
                    raise BaiduError("short_read", f"期望 {want} 字节，收到 {len(data)}")
                return data
            except BaiduError as e:
                if isinstance(e.errno, int) and e.errno in (403, 404, 410) and not dlink_refreshed:
                    self.dlink, self._resolved, dlink_refreshed = None, None, True
                    continue
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                err = e
            except httpx.TransportError as e:
                if attempt == MAX_ATTEMPTS - 1:
                    raise BaiduError("network", f"{self.label}: {e!r}") from e
                err = e
            delay = 1.5 * 2**attempt
            log.warning("读取 %s 失败（%r），%.1fs 后重试", self.label, err, delay)
            await asyncio.sleep(delay)
        raise BaiduError("range", f"{self.label}: 多次重试后仍失败")

    def _insert(self, start: int, data: bytes) -> None:
        i = bisect.bisect_left(self._starts, start)
        self._starts.insert(i, start)
        self._chunks.insert(i, data)

    def _gaps(self, start: int, end: int) -> list[tuple[int, int]]:
        gaps, pos = [], start
        for s, c in zip(self._starts, self._chunks):
            e = s + len(c)
            if e <= pos:
                continue
            if s > pos:
                gaps.append((pos, min(s, end)))
            pos = max(pos, e)
            if pos >= end:
                break
        if pos < end:
            gaps.append((pos, end))
        return gaps

    async def read_at(self, offset: int, n: int) -> bytes:
        end = min(offset + n, self.size)
        if offset >= end:
            return b""
        for gs, ge in self._gaps(offset, end):
            fe = min(max(ge, gs + MIN_FETCH), self.size)
            self._insert(gs, await self._fetch(gs, fe - 1))
        out = bytearray()
        pos = offset
        for s, c in zip(self._starts, self._chunks):
            e = s + len(c)
            if e <= pos or s > pos:
                continue
            take = c[pos - s : min(e, end) - s]
            out += take
            pos += len(take)
            if pos >= end:
                break
        return bytes(out)

    async def read_all(self) -> bytes:
        return await self.read_at(0, self.size)


class SyncView(io.RawIOBase):
    """给 mutagen 用的同步文件对象：在工作线程中调用，读操作转交回事件循环。"""

    def __init__(self, reader: RemoteReader, loop: asyncio.AbstractEventLoop, name: str = ""):
        self.reader = reader
        self.loop = loop
        self.pos = 0
        self.name = name

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.reader.size + offset
        self.pos = max(self.pos, 0)
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.reader.size - self.pos
        fut = asyncio.run_coroutine_threadsafe(self.reader.read_at(self.pos, n), self.loop)
        data = fut.result(timeout=180)
        self.pos += len(data)
        return data

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)
