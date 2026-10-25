import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Iterable

import httpx

from ..config import Settings
from .auth import Authenticator
from .errors import RATE_LIMIT_ERRNOS, SCOPE_DENIED_ERRNO, TOKEN_INVALID_ERRNOS, AuthError, BaiduError, RateLimited

log = logging.getLogger(__name__)

PAN_BASE = "https://pan.baidu.com/rest/2.0/xpan"
USER_AGENT = "pan.baidu.com"
FILEMETAS_BATCH = 100
LISTALL_LIMIT = 1000

TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=15.0)


def make_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


class XpanClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.http = http or make_http()
        self.auth = Authenticator(settings, self.http)
        self._listall_denied = False

    async def aclose(self) -> None:
        await self.http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def _api(self, endpoint: str, method: str, params: dict, max_retries: int = 5, data: dict | None = None) -> dict:
        url = f"{PAN_BASE}/{endpoint}"
        refreshed = False
        attempt = 0
        while True:
            q = {"method": method, **params, "access_token": await self.auth.access_token()}
            try:
                if data is None:
                    r = await self.http.get(url, params=q)
                else:
                    r = await self.http.post(url, params=q, data=data)
                if r.status_code >= 500:
                    raise BaiduError(r.status_code, f"HTTP {r.status_code}")
                body = r.json()
            except (httpx.TransportError, BaiduError, ValueError) as e:
                if attempt >= max_retries:
                    raise BaiduError("network", f"{endpoint}?method={method} 失败：{e!r}") from e
                await self._backoff(attempt, f"{method}: {e!r}")
                attempt += 1
                continue

            errno = body.get("errno", 0)
            if errno == 0:
                return body
            req_id = str(body.get("request_id", ""))
            if errno in TOKEN_INVALID_ERRNOS and not refreshed:
                log.warning("%s 返回 errno=%s（token 失效），尝试刷新", method, errno)
                await self.auth.refresh()
                refreshed = True
                continue
            if errno in RATE_LIMIT_ERRNOS:
                if attempt >= max_retries:
                    raise RateLimited(errno, "命中频率限制，重试次数已用尽", req_id)
                await self._backoff(attempt, f"{method}: 命中频率限制 errno={errno}", base=2.0)
                attempt += 1
                continue
            if errno in TOKEN_INVALID_ERRNOS:
                raise AuthError(errno, "token 无效，请重新运行 pan auth", req_id)
            msg = body.get("show_msg") or body.get("errmsg") or ""
            err = BaiduError(errno, msg, req_id)
            err.body = body
            raise err

    @staticmethod
    async def _backoff(attempt: int, why: str, base: float = 1.0) -> None:
        delay = min(base * 2**attempt, 60) * (0.75 + random.random() / 2)
        log.warning("%s，%.1fs 后重试（第 %d 次）", why, delay, attempt + 1)
        await asyncio.sleep(delay)

    async def uinfo(self) -> dict:
        return await self._api("nas", "uinfo", {})

    async def listall(self, path: str, recursion: bool = True) -> AsyncIterator[dict]:
        start = 0
        while True:
            data = await self._api(
                "multimedia",
                "listall",
                {
                    "path": path,
                    "recursion": int(recursion),
                    "start": start,
                    "limit": LISTALL_LIMIT,
                    "web": 0,
                },
            )
            for item in data.get("list", []):
                yield item
            if not data.get("has_more"):
                return
            start = int(data.get("cursor", start + LISTALL_LIMIT))

    async def list_dir(self, path: str) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            data = await self._api(
                "file", "list", {"dir": path, "start": start, "limit": LISTALL_LIMIT, "web": 0, "folder": 0}
            )
            items = data.get("list", [])
            out.extend(items)
            if len(items) < LISTALL_LIMIT:
                return out
            start += LISTALL_LIMIT

    async def walk(self, root: str) -> AsyncIterator[dict]:
        """用 list 深度优先遍历，按目录逐层产出条目（含目录本身）。"""
        stack = [root]
        while stack:
            path = stack.pop()
            items = await self.list_dir(path)
            subdirs = []
            for item in items:
                yield item
                if item.get("isdir"):
                    subdirs.append(item["path"])
            stack.extend(reversed(subdirs))

    async def iter_files(self, root: str) -> AsyncIterator[dict]:
        """递归列出 root 下所有条目：优先 listall，不可用时回退到 list 遍历。"""
        if not self._listall_denied:
            try:
                async for item in self.listall(root):
                    yield item
                return
            except BaiduError as e:
                if e.errno != SCOPE_DENIED_ERRNO:
                    raise
                self._listall_denied = True
                log.info("listall 不在本应用访问范围内（errno=20020），改用 list 逐层遍历")
        async for item in self.walk(root):
            yield item

    async def filemetas(self, fsids: Iterable[int], dlink: bool = False, **extra) -> list[dict]:
        ids = list(fsids)
        out: list[dict] = []
        for i in range(0, len(ids), FILEMETAS_BATCH):
            batch = ids[i : i + FILEMETAS_BATCH]
            params = {"fsids": json.dumps(batch), "dlink": int(dlink), **extra}
            data = await self._api("multimedia", "filemetas", params)
            out.extend(data.get("list", []))
        return out

    async def delete(self, paths: list[str]) -> list[dict]:
        """删除网盘文件或文件夹（官方 filemanager 接口，opera=delete）。返回每个路径的结果。"""
        results: list[dict] = []
        for i in range(0, len(paths), 100):
            batch = paths[i : i + 100]
            try:
                body = await self._api(
                    "file", "filemanager", {"opera": "delete"}, max_retries=1,
                    data={"async": 0, "filelist": json.dumps(batch, ensure_ascii=False), "ondup": "fail"},
                )
                info = body.get("info") or [{"path": p, "errno": 0} for p in batch]
            except BaiduError as e:
                info = (getattr(e, "body", None) or {}).get("info")
                if not info:
                    raise
            results.extend(info)
        return results

    async def get_dlink(self, fsid: int) -> str:
        metas = await self.filemetas([fsid], dlink=True)
        if not metas or not metas[0].get("dlink"):
            raise BaiduError("no_dlink", f"fsid={fsid} 未返回 dlink")
        return metas[0]["dlink"]

    async def open_download(self, dlink: str, headers: dict | None = None) -> httpx.Response:
        """返回已发送、尚未读取 body 的响应；调用方负责 aclose()。"""
        sep = "&" if "?" in dlink else "?"
        url = f"{dlink}{sep}access_token={await self.auth.access_token()}"
        req = self.http.build_request("GET", url, headers=headers or {})
        resp = await self.http.send(req, stream=True)
        if resp.status_code >= 400:
            body = (await resp.aread())[:300]
            await resp.aclose()
            raise BaiduError(resp.status_code, f"下载失败 HTTP {resp.status_code}: {body!r}")
        return resp
