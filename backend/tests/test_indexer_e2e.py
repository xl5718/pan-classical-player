"""用 ffmpeg 生成真实音频文件，模拟网盘接口（list / filemetas / 302 + Range 下载），端到端验证索引器。"""

import io
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from app.baidu.auth import Token, TokenStore
from app.baidu.client import XpanClient
from app.config import Settings
from app.db import connect
from app.indexer.scanner import Scanner

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
ROOT = "/音乐"


def ffmpeg(out: Path, seconds: float, *extra: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-ar", "44100", *extra, str(out)],
        check=True,
    )


def jpeg(w: int, h: int, color, noise: bool = False) -> bytes:
    img = Image.new("RGB", (w, h), color)
    if noise:
        import random
        px = img.load()
        for x in range(0, w, 2):
            for y in range(0, h, 2):
                px[x, y] = (random.randrange(256), random.randrange(256), random.randrange(256))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def flac_tags(path: Path, picture: bytes | None = None, **tags) -> None:
    f = FLAC(path)
    for k, v in tags.items():
        f[k] = v
    if picture:
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/jpeg", picture
        f.add_picture(pic)
        # 把图片块挪到注释块之前，验证"按需补取"
        blocks = f.metadata_blocks
        pblock = blocks.pop()
        blocks.insert(1, pblock)
    f.save()


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    d = tmp_path_factory.mktemp("lib") / "音乐"
    alb = d / "Beethoven" / "Schnabel - Late Sonatas"
    big_pic = jpeg(1600, 1600, (120, 30, 30), noise=True)
    assert len(big_pic) > 300_000
    ffmpeg(alb / "CD1" / "01 Maestoso.flac", 2.0, "-sample_fmt", "s16")
    flac_tags(alb / "CD1" / "01 Maestoso.flac", picture=big_pic,
              composer="Ludwig van Beethoven", work="Piano Sonata No. 32 in C minor, Op. 111",
              movementname="Maestoso – Allegro con brio ed appassionato", movement="1",
              title="Piano Sonata No. 32 in C minor, Op. 111: I. Maestoso", album="Late Piano Sonatas",
              albumartist="Artur Schnabel", artist="Artur Schnabel", label="EMI", date="1932", discnumber="1",
              tracknumber="1")
    ffmpeg(alb / "CD1" / "02.flac", 2.5, "-sample_fmt", "s32")
    flac_tags(alb / "CD1" / "02.flac", composer="Ludwig van Beethoven",
              title="Piano Sonata No. 32 in C minor, Op. 111: II. Arietta. Adagio molto semplice e cantabile",
              album="Late Piano Sonatas", artist="Artur Schnabel", discnumber="1", tracknumber="2")
    ffmpeg(alb / "CD2" / "01.flac", 1.5)
    flac_tags(alb / "CD2" / "01.flac", composer="Ludwig van Beethoven",
              title="Piano Sonata No. 31 in A-flat major, Op. 110: I. Moderato cantabile molto espressivo",
              album="Late Piano Sonatas", artist="Artur Schnabel", tracknumber="1")
    (alb / "folder.jpg").write_bytes(jpeg(800, 800, (10, 60, 120)))

    img = d / "整轨" / "Cortot 1933"
    ffmpeg(img / "Cortot - Chopin.flac", 30.0)
    cue = (
        'REM DATE 1933\nREM COMPOSER "Frédéric Chopin"\nPERFORMER "Alfred Cortot"\nTITLE "肖邦：前奏曲 科尔托"\n'
        'FILE "Cortot - Chopin.wav" WAVE\n'
        '  TRACK 01 AUDIO\n    TITLE "24 Préludes, Op. 28: No. 1 in C major"\n    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    TITLE "24 Préludes, Op. 28: No. 2 in A minor"\n    INDEX 00 00:09:00\n    INDEX 01 00:10:30\n'
        '  TRACK 03 AUDIO\n    TITLE "夜曲 降E大调 Op. 9 No. 2"\n    INDEX 01 00:20:00\n'
    )
    (img / "Cortot - Chopin.cue").write_bytes(cue.encode("gbk", errors="replace").replace(b"?", b"e"))

    misc = d / "杂项"
    ffmpeg(misc / "03 Gymnopédie No. 1.mp3", 1.0, "-b:a", "128k")
    ffmpeg(misc / "alac.m4a", 1.0, "-c:a", "alac")
    m = MP4(misc / "alac.m4a")
    m["©nam"], m["©wrt"], m["©alb"], m["©ART"] = "Gnossienne No. 1", "Erik Satie", "Satie Piano", "Aldo Ciccolini"
    m["covr"] = [MP4Cover(jpeg(300, 300, (0, 120, 0)), MP4Cover.FORMAT_JPEG)]
    m.save()
    (d / "big.zip").write_bytes(b"PK\x03\x04" + b"\0" * 100)

    # 无 COMPOSER 标签：作曲家写在标题前缀 / 专辑名里；APEv2 标签是 GBK 编码；扩展名与内容不符
    hmv = d / "合集" / "Encores & Rarities"
    ffmpeg(hmv / "01.flac", 1.0)
    flac_tags(hmv / "01.flac", title="SCHUMANN Fantasiestücke, Op. 12: No. 2, Aufschwung", artist="Mark Hambourg",
              album="Encores & Rarities", tracknumber="1")
    ffmpeg(hmv / "02.flac", 1.0)
    flac_tags(hmv / "02.flac", title="6 Songs, Op.47, 1. Die gefangenen Sänger", artist="Martyn Hill",
              album="Weber: Songs", tracknumber="2")
    ffmpeg(hmv / "03.wv", 1.0)
    _append_gbk_ape(hmv / "03.wv", {"Title": "夜曲 降E大调", "Artist": "傅聪", "Album": "Encores & Rarities",
                                    "Composer": "肖邦", "Track": "3"})
    ffmpeg(hmv / "04.wav", 1.0, "-c:a", "flac", "-f", "flac")
    flac_tags(hmv / "04.wav", title="Gnossienne No. 3", composer="SATIE", album="Encores & Rarities", tracknumber="4")
    (hmv / "Scans").mkdir()
    (hmv / "Scans" / "back.jpg").write_bytes(jpeg(500, 500, (200, 200, 200), noise=True))
    (hmv / "Scans" / "01.jpg").write_bytes(jpeg(500, 500, (90, 0, 90), noise=True))
    return d


def _append_gbk_ape(path: Path, items: dict) -> None:
    body = b""
    for k, v in items.items():
        val = v.encode("gbk")
        body += len(val).to_bytes(4, "little") + (0).to_bytes(4, "little") + k.encode() + b"\0" + val
    size = len(body) + 32

    def hdr(flags):
        return (b"APETAGEX" + (2000).to_bytes(4, "little") + size.to_bytes(4, "little")
                + len(items).to_bytes(4, "little") + flags.to_bytes(4, "little") + b"\0" * 8)

    with open(path, "ab") as f:
        f.write(hdr(0xA0000000) + body + hdr(0x80000000))


class FakePan:
    def __init__(self, base: Path):
        self.base = base
        self.ids: dict[int, Path] = {}
        self.mtime: dict[Path, int] = {}
        self.calls: dict[str, int] = {}

    def _rel(self, p: Path) -> str:
        return "/" + p.relative_to(self.base.parent).as_posix()

    def _id(self, p: Path) -> int:
        fid = abs(hash(self._rel(p))) % 10**15
        self.ids[fid] = p
        return fid

    def _entry(self, p: Path) -> dict:
        return {"fs_id": self._id(p), "path": self._rel(p), "server_filename": p.name, "isdir": int(p.is_dir()),
                "size": 0 if p.is_dir() else p.stat().st_size, "server_mtime": self.mtime.get(p, 1700000000),
                "md5": "x"}

    def handler(self, req: httpx.Request) -> httpx.Response:
        host, params = req.url.host, dict(req.url.params)
        key = params.get("method") or host
        self.calls[key] = self.calls.get(key, 0) + 1
        if host == "pan.baidu.com":
            if params["method"] == "listall":
                return httpx.Response(200, json={"errno": 20020, "show_msg": "该路径不在允许的访问范围内"})
            if params["method"] == "list":
                d = self.base.parent / params["dir"].lstrip("/")
                if not d.is_dir():
                    return httpx.Response(200, json={"errno": -9})
                return httpx.Response(200, json={"errno": 0, "list": [self._entry(p) for p in sorted(d.iterdir())]})
            if params["method"] == "filemetas":
                import json
                ids = json.loads(params["fsids"])
                assert len(ids) <= 100
                return httpx.Response(200, json={"errno": 0, "list": [
                    {"fs_id": i, "dlink": f"https://d.pcs.baidu.com/file/{i}?fid={i}&expires=8h"} for i in ids]})
        if host == "d.pcs.baidu.com":
            assert req.headers["user-agent"] == "pan.baidu.com" and "access_token" in params
            return httpx.Response(302, headers={"Location": f"https://nd.baidupcs.com{req.url.path}?sig=1"})
        if host == "nd.baidupcs.com":
            data = self.ids[int(req.url.path.rsplit("/", 1)[1])].read_bytes()
            rng = req.headers.get("range")
            s, e = rng.split("=")[1].split("-")
            s, e = int(s), min(int(e), len(data) - 1)
            return httpx.Response(206, content=data[s : e + 1], headers={"Content-Range": f"bytes {s}-{e}/{len(data)}"})
        raise AssertionError(f"unexpected {req.url}")


@pytest.fixture
def env(library, tmp_path):
    s = Settings(baidu_app_key="ak_test", baidu_secret_key="sk_test", data_dir=tmp_path, music_root=ROOT,
                 scan_concurrency=4)
    TokenStore(s.token_path).save(Token("AT", "RT", time.time() + 30 * 86400))
    fake = FakePan(library)
    return s, fake


async def scan(s, fake, full=False):
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), headers={"User-Agent": "pan.baidu.com"},
                             follow_redirects=True)
    conn = connect(s.db_path)
    async with XpanClient(s, http) as c:
        stats = await Scanner(s, c, conn, full=full).run()
    return conn, stats


async def test_full_index(env, library):
    s, fake = env
    conn, stats = await scan(s, fake)
    assert stats["walk_complete"] and stats["parse_errors"] == 0, stats
    assert stats["zips"] == 1

    albums = {r["title"]: dict(r) for r in conn.execute("SELECT * FROM albums")}
    assert set(albums) == {"Late Piano Sonatas", "肖邦：前奏曲 科尔托", "Satie Piano", "Encores & Rarities"}, albums.keys()

    late = albums["Late Piano Sonatas"]
    assert late["folder_path"] == "/音乐/Beethoven/Schnabel - Late Sonatas"
    assert (late["album_artist"], late["label"], late["year"], late["disc_count"]) == ("Artur Schnabel", "EMI", 1932, 2)
    assert late["cover_path"] and late["cover_src"].startswith("file:")
    with Image.open(s.covers_dir / late["cover_path"]) as im:
        assert max(im.size) == 600

    works = conn.execute("SELECT * FROM works WHERE album_id=? ORDER BY seq", (late["id"],)).fetchall()
    assert [(w["title"], w["catalogue"]) for w in works] == [
        ("Piano Sonata No. 32 in C minor, Op. 111", "Op. 111"),
        ("Piano Sonata No. 31 in A-flat major, Op. 110", "Op. 110"),
    ]
    tr = conn.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY disc_no, track_no", (late["id"],)).fetchall()
    assert [(t["disc_no"], t["track_no"], t["movement_no"], t["movement_name"]) for t in tr] == [
        (1, 1, 1, "Maestoso – Allegro con brio ed appassionato"),
        (1, 2, 2, "Arietta. Adagio molto semplice e cantabile"),
        (2, 1, 1, "Moderato cantabile molto espressivo"),
    ]
    assert tr[0]["work_id"] == tr[1]["work_id"] != tr[2]["work_id"]
    assert (tr[0]["format"], tr[0]["sample_rate"], tr[0]["bit_depth"]) == ("FLAC", 44100, 16)
    assert tr[1]["bit_depth"] == 24 or tr[1]["bit_depth"] == 32
    assert abs(tr[0]["duration"] - 2.0) < 0.05
    assert not any(t["incomplete"] for t in tr)

    cortot = albums["肖邦：前奏曲 科尔托"]
    assert (cortot["album_artist"], cortot["year"]) == ("Alfred Cortot", 1933)
    ct = conn.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY track_no", (cortot["id"],)).fetchall()
    assert [(t["start_offset"], t["end_offset"]) for t in ct] == [(0.0, 10.4), (10.4, 20.0), (20.0, None)]
    assert abs(ct[2]["duration"] - 10.0) < 0.05
    assert len({t["fsid"] for t in ct}) == 1
    assert ct[0]["composer"] == "Frédéric Chopin" and ct[0]["movement_no"] == 1
    assert ct[0]["work_id"] == ct[1]["work_id"]
    assert cortot["cover_path"] is None

    satie = albums["Satie Piano"]
    rows = {r["format"]: r for r in conn.execute("SELECT * FROM tracks WHERE album_id=?", (satie["id"],))}
    st, mt = rows["ALAC"], rows["MP3"]
    assert (st["composer"], st["title"], st["incomplete"]) == ("Erik Satie", "Gnossienne No. 1", 0)
    assert satie["cover_path"] and satie["cover_src"].startswith("emb:")
    assert (mt["title"], mt["track_no"], mt["incomplete"]) == ("Gymnopédie No. 1", 3, 1)
    assert abs(mt["duration"] - 1.0) < 0.1
    assert satie["incomplete"] == 1

    enc = albums["Encores & Rarities"]
    et = conn.execute("SELECT t.*, w.title AS wtitle FROM tracks t LEFT JOIN works w ON w.id=t.work_id WHERE t.album_id=? ORDER BY track_no", (enc["id"],)).fetchall()
    got = [(t["composer"], t["wtitle"], t["movement_no"], t["format"]) for t in et]
    assert got == [
        ("Robert Schumann", "Fantasiestücke, Op. 12", 2, "FLAC"),
        ("Carl Maria von Weber", "6 Songs, Op.47", 1, "FLAC"),
        ("Frédéric Chopin", "夜曲 降E大调", None, "WavPack"),
        ("Erik Satie", "Gnossienne No. 3", None, "FLAC"),
    ], got
    assert et[2]["performer"] == "傅聪" and not any(t["incomplete"] for t in et)
    assert enc["cover_path"] and enc["cover_src"].startswith("file:")


async def test_incremental(env, library):
    s, fake = env
    await scan(s, fake)
    before = dict(fake.calls)
    conn, stats = await scan(s, fake)
    assert stats.get("parsed", 0) == 0 and stats["dirty_albums"] == 0
    assert fake.calls.get("nd.baidupcs.com", 0) == before.get("nd.baidupcs.com", 0)

    p = library / "杂项" / "alac.m4a"
    fake.mtime[p] = 1800000000
    conn, stats = await scan(s, fake)
    assert stats["changed"] == 1 and stats["dirty_albums"] == 1 and stats["parsed"] == 1

    victim = library / "整轨" / "Cortot 1933" / "Cortot - Chopin.cue"
    backup = victim.read_bytes()
    victim.unlink()
    try:
        conn, stats = await scan(s, fake)
        assert stats["removed"] == 1
        t = conn.execute("SELECT * FROM tracks WHERE path LIKE '%Cortot - Chopin.flac'").fetchall()
        assert len(t) == 1 and t[0]["start_offset"] is None
    finally:
        victim.write_bytes(backup)


async def test_rescan_rereads_everything(env):
    s, fake = env
    await scan(s, fake)
    conn, stats = await scan(s, fake, full=True)
    assert stats["parsed"] == 11 and stats["covers"] == 3


async def test_resume_after_interrupted_parse(env, monkeypatch):
    s, fake = env
    from app.indexer import scanner as sc

    async def boom(self, groups, prog, totals):
        raise KeyboardInterrupt

    monkeypatch.setattr(sc.Scanner, "parse", boom)
    with pytest.raises(KeyboardInterrupt):
        await scan(s, fake)
    monkeypatch.undo()
    conn, stats = await scan(s, fake)
    assert stats["dirty_albums"] == 4 and conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 4


async def test_albums_written_batch_by_batch(env, monkeypatch):
    s, fake = env
    from app.indexer import scanner as sc

    monkeypatch.setattr(sc, "ALBUM_BATCH", 1)
    real_parse = sc.Scanner.parse
    calls = {"n": 0}

    async def flaky(self, groups, prog, totals):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        await real_parse(self, groups, prog, totals)

    monkeypatch.setattr(sc.Scanner, "parse", flaky)
    with pytest.raises(KeyboardInterrupt):
        await scan(s, fake)
    conn = connect(s.db_path)
    assert conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 2
    monkeypatch.setattr(sc.Scanner, "parse", real_parse)
    conn, stats = await scan(s, fake)
    assert stats["parsed"] == 4  # 只读取中断后剩下两张专辑的文件
    assert conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 4
