import asyncio
import shutil
import subprocess
import time

import httpx
import pytest

import app.main as main
from app.baidu.auth import Token, TokenStore
from app.baidu.client import XpanClient
from app.config import Settings
from app.library import catalogue_key, title_key
from app.stream.manager import StreamManager
from test_indexer_e2e import FakePan, scan

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")


@pytest.fixture
async def api(library, tmp_path, monkeypatch):
    s = Settings(baidu_app_key="ak_test", baidu_secret_key="sk_test", data_dir=tmp_path, music_root="/音乐")
    TokenStore(s.token_path).save(Token("AT", "RT", time.time() + 30 * 86400))
    fake = FakePan(library)
    await scan(s, fake)
    monkeypatch.setattr(main, "settings", s)
    main.app.state.library = None
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), headers={"User-Agent": "pan.baidu.com"},
                             follow_redirects=True)
    main.app.state.streams = StreamManager(s, XpanClient(s, http))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        yield s, c


def test_keys():
    assert catalogue_key("Op. 27 No. 2") == catalogue_key("Op.27/2") == catalogue_key("op 27, no.2") == "OP27-2"
    assert catalogue_key("KV 466") == catalogue_key("K. 466") == "K466"
    assert title_key("Piano Sonata No. 32, Op. 111") == title_key("Piano Sonata No 32 Op 111")


async def test_albums_and_search(api):
    s, c = api
    albums = (await c.get("/api/albums")).json()
    by = {a["title"]: a for a in albums}
    late = by["Late Piano Sonatas"]
    assert late["composers"] == ["Ludwig van Beethoven"] and late["performers"][0] == "Artur Schnabel"
    assert late["format"]["format"] == "FLAC" and late["track_count"] == 3
    for q, expect in [("schnabel", "Late Piano Sonatas"), ("贝多芬", "Late Piano Sonatas"), ("beethoven 111", "Late Piano Sonatas"),
                      ("prelude", "肖邦：前奏曲 科尔托"), ("PRÉLUDES", "肖邦：前奏曲 科尔托"), ("肖邦", "肖邦：前奏曲 科尔托"),
                      ("gnossienne", "Satie Piano")]:
        got = (await c.get("/api/search", params={"q": q})).json()["album_ids"]
        assert by[expect]["id"] in got, q
    assert (await c.get("/api/search", params={"q": "zzzz"})).json()["album_ids"] == []


async def test_album_detail(api):
    s, c = api
    albums = (await c.get("/api/albums")).json()
    late = next(a for a in albums if a["title"] == "Late Piano Sonatas")
    d = (await c.get(f"/api/albums/{late['id']}")).json()
    assert d["cover_large"].startswith("orig/") and len(d["works"]) == 2 and len(d["tracks"]) == 3
    assert (await c.get(f"/covers/{d['cover_large']}")).status_code == 200


async def test_composers(api):
    s, c = api
    comps = (await c.get("/api/composers")).json()
    names = [x["name"] for x in comps]
    assert names.index("Ludwig van Beethoven") < names.index("Frédéric Chopin") < names.index("Erik Satie")
    b = (await c.get("/api/composer", params={"name": "Ludwig van Beethoven"})).json()
    assert [w["catalogue"] for w in b["works"]] == ["Op. 110", "Op. 111"]
    rec = b["works"][1]["recordings"][0]
    assert rec["performer"] == "Artur Schnabel" and len(rec["tracks"]) == 2 and rec["year"] == 1932


async def test_transcode_alac(api):
    s, c = api
    albums = (await c.get("/api/albums")).json()
    satie = next(a for a in albums if a["title"] == "Satie Piano")
    d = (await c.get(f"/api/albums/{satie['id']}")).json()
    alac = next(t for t in d["tracks"] if t["format"] == "ALAC")
    await c.get(f"/stream/{alac['id']}")  # 先缓存到本地，转码直接读缓存文件
    for _ in range(100):
        if any(s.cache_dir.glob("*.m4a")):
            break
        await asyncio.sleep(0.02)
    full = (await c.get(f"/stream/{alac['id']}/flac")).content
    part = (await c.get(f"/stream/{alac['id']}/flac", params={"t": 0.5})).content
    assert full[:4] == b"fLaC" and part[:4] == b"fLaC" and len(part) < len(full)
    out = s.data_dir / "x.flac"
    out.write_bytes(full)
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
                         capture_output=True).stdout
    assert abs(len(pcm) / 2 / 44100 - 1.0) < 0.05


async def test_album_media(api):
    s, c = api
    albums = {a["title"]: a for a in (await c.get("/api/albums")).json()}
    enc = (await c.get(f"/api/albums/{albums['Encores & Rarities']['id']}/media")).json()
    assert [m["path"] for m in enc] == ["Scans/01.jpg", "Scans/back.jpg"]
    late = (await c.get(f"/api/albums/{albums['Late Piano Sonatas']['id']}/media")).json()
    assert [m["path"] for m in late] == ["folder.jpg"]
    fsid = enc[0]["fsid"]
    full = await c.get(f"/api/media/{fsid}")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg" and len(full.content) > 50_000
    thumb = await c.get(f"/api/media/{fsid}", params={"thumb": "true"})
    from PIL import Image
    import io
    assert max(Image.open(io.BytesIO(thumb.content)).size) == 360
    assert (await c.get("/api/media/1")).status_code == 404
