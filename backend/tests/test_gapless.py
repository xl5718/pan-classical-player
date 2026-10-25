"""无缝播放验收：一段连续的正弦波被切成 3 个 FLAC（切点不在帧边界上），拼接流必须与原始采样逐一相同。"""

import array
import asyncio
import math
import shutil
import subprocess
import time
import wave

import httpx
import pytest
from mutagen.flac import FLAC

import app.main as main
from app.baidu.auth import Token, TokenStore
from app.baidu.client import XpanClient
from app.config import Settings
from app.stream.manager import StreamManager
from test_indexer_e2e import FakePan, scan

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
SR = 44100
CUTS = [0, SR + 123, 2 * SR + 777, 3 * SR]


def sine(n: int) -> array.array:
    # 频率随时间变化的正弦，任何错位、重复或丢失的采样都会被检测出来
    return array.array("h", (int(12000 * math.sin(2 * math.pi * (220 + i / 400) * i / SR)) for i in range(n)))


@pytest.fixture
async def env(tmp_path, monkeypatch):
    lib = tmp_path / "lib" / "音乐" / "Attacca"
    lib.mkdir(parents=True)
    pcm = sine(CUTS[-1])
    for k in range(3):
        wav = tmp_path / f"p{k}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm[CUTS[k] : CUTS[k + 1]].tobytes())
        out = lib / f"0{k + 1}.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), str(out)], check=True)
        f = FLAC(out)
        f["composer"], f["work"], f["movement"] = "Ludwig van Beethoven", "String Quartet No. 14, Op. 131", str(k + 1)
        f["title"], f["tracknumber"], f["album"] = f"Mvt {k + 1}", str(k + 1), "Attacca"
        f.save()
    s = Settings(baidu_app_key="ak", baidu_secret_key="sk", data_dir=tmp_path / "data", music_root="/音乐")
    TokenStore(s.token_path).save(Token("AT", "RT", time.time() + 9e5))
    fake = FakePan(lib.parent)
    conn, _ = await scan(s, fake)
    monkeypatch.setattr(main, "settings", s)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), headers={"User-Agent": "pan.baidu.com"},
                             follow_redirects=True)
    main.app.state.streams = StreamManager(s, XpanClient(s, http))
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY track_no")]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        for i in ids:  # 先缓存，拼接时 ffmpeg 直接读本地文件
            await c.get(f"/stream/{i}")
        for _ in range(100):
            if len(list(s.cache_dir.glob("*.flac"))) == 3:
                break
            await asyncio.sleep(0.02)
        yield c, ids, pcm


async def test_concat_is_sample_exact(env):
    c, ids, pcm = env
    q = ",".join(map(str, ids))
    head = await c.head(f"/stream/pcm?ids={q}")
    assert head.headers["content-type"] == "audio/wav" and head.headers["accept-ranges"] == "bytes"
    assert int(head.headers["content-length"]) == 44 + CUTS[-1] * 2
    r = await c.get(f"/stream/pcm?ids={q}")
    body = r.content
    assert body[:4] == b"RIFF" and len(body) == 44 + CUTS[-1] * 2
    got = array.array("h", body[44:])
    assert got == pcm  # 逐采样一致：没有空隙、没有重复、没有爆音


async def test_range_inside_second_file(env):
    c, ids, pcm = env
    q = ",".join(map(str, ids))
    start = 44 + (CUTS[1] + 5000) * 2 + 1  # 故意落在半个采样上
    end = start + 9999
    r = await c.get(f"/stream/pcm?ids={q}", headers={"Range": f"bytes={start}-{end}"})
    assert r.status_code == 206 and r.headers["content-range"] == f"bytes {start}-{end}/{44 + CUTS[-1] * 2}"
    assert r.content == pcm.tobytes()[start - 44 : end - 44 + 1]
    tail = await c.get(f"/stream/pcm?ids={q}", headers={"Range": "bytes=-100"})
    assert tail.content == pcm.tobytes()[-100:]


async def test_bad_plans(env):
    c, ids, _ = env
    assert (await c.get("/stream/pcm?ids=99999")).status_code == 400
    assert (await c.get("/stream/pcm?ids=")).status_code == 400
