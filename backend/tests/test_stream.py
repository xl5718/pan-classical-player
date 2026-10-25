import asyncio
import json
import os
import time

import httpx
import pytest

import app.main as main
from app.baidu.auth import Token, TokenStore
from app.baidu.client import XpanClient
from app.config import Settings
from app.db import connect
from app.stream import downloader
from app.stream.manager import StreamManager

CHUNK = 64 * 1024


class FakePan:
    def __init__(self, files: dict[int, bytes]):
        self.files = files
        self.version: dict[int, int] = {}
        self.expired: set[tuple[int, int]] = set()
        self.ranges: list[tuple[int, str]] = []
        self.filemetas = 0
        self.fail_next_body = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = dict(req.url.params)
        if req.url.host == "pan.baidu.com":
            assert p["method"] == "filemetas" and p["dlink"] == "1"
            self.filemetas += 1
            out = []
            for i in json.loads(p["fsids"]):
                v = self.version[i] = self.version.get(i, 0) + 1
                out.append({"fs_id": i, "dlink": f"https://d.pcs.baidu.com/file/{i}?v={v}"})
            return httpx.Response(200, json={"errno": 0, "list": out})
        if req.url.host == "d.pcs.baidu.com":
            assert req.headers["user-agent"] == "pan.baidu.com" and p.get("access_token")
            fid, v = int(req.url.path.rsplit("/", 1)[1]), int(p["v"])
            if (fid, v) in self.expired:
                return httpx.Response(403, text="expired")
            return httpx.Response(302, headers={"Location": f"https://nd.baidupcs.com/{fid}?v={v}&sig=x"})
        if req.url.host == "nd.baidupcs.com":
            fid, v = int(req.url.path.lstrip("/")), int(p["v"])
            if (fid, v) in self.expired:
                return httpx.Response(403, text="expired")
            data = self.files[fid]
            rng = req.headers["range"]
            self.ranges.append((fid, rng))
            s, e = (int(x) for x in rng.split("=")[1].split("-"))
            e = min(e, len(data) - 1)
            body = data[s : e + 1]
            if self.fail_next_body:
                self.fail_next_body -= 1
                body = body[: len(body) // 2]  # 模拟连接中途断开
            return httpx.Response(206, content=body, headers={"Content-Range": f"bytes {s}-{e}/{len(data)}"})
        raise AssertionError(req.url)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "CHUNK", CHUNK)
    monkeypatch.setattr(downloader, "SPLIT_MIN", 16 * 1024)
    s = Settings(baidu_app_key="ak_test", baidu_secret_key="sk_test", data_dir=tmp_path, music_root="/m",
                 cache_max_gb=1)
    TokenStore(s.token_path).save(Token("AT", "RT", time.time() + 30 * 86400))
    files = {101: os.urandom(CHUNK * 5 + 1234), 102: os.urandom(CHUNK * 3), 103: os.urandom(CHUNK * 2 + 7)}
    conn = connect(s.db_path)
    conn.execute("INSERT INTO albums (id, folder_path, title, updated_at) VALUES (1, '/m/a', 'A', 0)")
    for i, (fid, data) in enumerate(files.items(), 1):
        conn.execute(
            "INSERT INTO tracks (id, album_id, fsid, path, title, size, server_mtime, format) VALUES (?,1,?,?,?,?,1,'FLAC')",
            (i, fid, f"/m/a/{i:02d}.flac", f"t{i}", len(data)),
        )
    fake = FakePan(files)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), headers={"User-Agent": "pan.baidu.com"},
                             follow_redirects=True)
    monkeypatch.setattr(main, "settings", s)
    mgr = StreamManager(s, XpanClient(s, http))
    main.app.state.streams = mgr
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t")
    return s, fake, files, mgr, client


async def settle(mgr):
    for _ in range(200):
        if not any(not d.complete and not d.error for d in mgr.downloads.values()):
            return
        await asyncio.sleep(0.01)


async def test_full_get_then_cached(env):
    s, fake, files, mgr, c = env
    r = await c.get("/stream/1")
    assert r.status_code == 200 and r.content == files[101]
    assert r.headers["accept-ranges"] == "bytes" and r.headers["content-type"] == "audio/flac"
    assert int(r.headers["content-length"]) == len(files[101])
    await settle(mgr)
    assert (s.cache_dir / "101_1.flac").read_bytes() == files[101]
    n = len(fake.ranges)
    r = await c.get("/stream/1", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == files[101][10:20]
    assert len(fake.ranges) == n  # 命中缓存，不再请求网盘


async def test_ranges(env):
    s, fake, files, mgr, c = env
    d = files[102]
    cases = {
        "bytes=0-1": (206, d[0:2], f"bytes 0-1/{len(d)}"),
        "bytes=100000-100999": (206, d[100000:101000], f"bytes 100000-100999/{len(d)}"),
        f"bytes={len(d) - 10}-": (206, d[-10:], f"bytes {len(d) - 10}-{len(d) - 1}/{len(d)}"),
        "bytes=-500": (206, d[-500:], f"bytes {len(d) - 500}-{len(d) - 1}/{len(d)}"),
        f"bytes=5-{len(d) + 100}": (206, d[5:], f"bytes 5-{len(d) - 1}/{len(d)}"),
    }
    for rng, (status, body, cr) in cases.items():
        r = await c.get("/stream/2", headers={"Range": rng})
        assert (r.status_code, r.headers["content-range"]) == (status, cr), rng
        assert r.content == body and int(r.headers["content-length"]) == len(body), rng
    r = await c.get("/stream/2", headers={"Range": f"bytes={len(d)}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{len(d)}"
    r = await c.head("/stream/2", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-length"] == "100" and r.content == b""
    assert (await c.get("/stream/999")).status_code == 404


async def test_seek_fetches_target_chunk_first(env):
    s, fake, files, mgr, c = env
    d = files[101]
    start = CHUNK * 4 + 40 * 1024
    r = await c.get("/stream/1", headers={"Range": f"bytes={start}-{start + 99}"})
    assert r.content == d[start : start + 100]
    starts = [int(rg.split("=")[1].split("-")[0]) for fid, rg in fake.ranges if fid == 101]
    # 直接从拖动位置开始下载，且早于文件开头
    assert start in starts and starts.index(start) < starts.index(0), starts


async def test_expired_dlink_is_refreshed(env):
    s, fake, files, mgr, c = env
    fake.expired.add((103, 1))
    r = await c.get("/stream/3")
    assert r.status_code == 200 and r.content == files[103]
    assert fake.version[103] >= 2


async def test_broken_connection_resumes(env):
    s, fake, files, mgr, c = env
    fake.fail_next_body = 2
    r = await c.get("/stream/2")
    assert r.content == files[102]


async def test_prefetch_and_lru(env, monkeypatch):
    s, fake, files, mgr, c = env
    r = await c.post("/api/prefetch/2")
    assert r.json() == {"cached": False}
    await settle(mgr)
    assert (s.cache_dir / "102_1.flac").exists()
    assert (await c.post("/api/prefetch/2")).json() == {"cached": True}

    # 上限只够放两个文件：再缓存两首后，最久未用的 102 被淘汰
    monkeypatch.setattr(type(s), "cache_max_bytes", property(lambda self: len(files[101]) + len(files[103]) + 10))
    os.utime(s.cache_dir / "102_1.flac", (1, 1))
    await c.get("/stream/1")
    await settle(mgr)
    await c.get("/stream/3")
    await settle(mgr)
    left = sorted(p.name for p in s.cache_dir.iterdir())
    assert left == ["101_1.flac", "103_1.flac"], left
    stats = (await c.get("/api/cache")).json()
    assert stats["cached_files"] == 2
