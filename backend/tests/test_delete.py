import json
import shutil
import time
from urllib.parse import parse_qs

import httpx
import pytest

import app.main as main
from app.baidu.auth import Token, TokenStore
from app.baidu.client import XpanClient
from app.config import Settings
from app.stream.manager import StreamManager
from test_indexer_e2e import FakePan, scan


class TrashPan(FakePan):
    def __init__(self, base, trash):
        super().__init__(base)
        self.trash = trash
        self.deleted: list[str] = []

    def handler(self, req):
        p = dict(req.url.params)
        if req.url.host == "pan.baidu.com" and p.get("method") == "filemanager":
            assert req.method == "POST" and p["opera"] == "delete"
            form = parse_qs(req.content.decode())
            info = []
            for path in json.loads(form["filelist"][0]):
                local = self.base.parent / path.lstrip("/")
                if local.exists():
                    shutil.move(str(local), str(self.trash / local.name))
                    self.deleted.append(path)
                    info.append({"path": path, "errno": 0})
                else:
                    info.append({"path": path, "errno": -9})
            return httpx.Response(200, json={"errno": 0, "info": info, "request_id": 1})
        return super().handler(req)


@pytest.fixture
async def env(library, tmp_path, monkeypatch):
    lib = tmp_path / "lib" / "音乐"
    shutil.copytree(library, lib)
    # 制造嵌套：/音乐/整轨 下直接放一个音频文件，同时它的子文件夹 Cortot 1933 是另一张专辑
    shutil.copy(lib / "Beethoven" / "Schnabel - Late Sonatas" / "CD2" / "01.flac", lib / "整轨" / "loose.flac")
    trash = tmp_path / "trash"
    trash.mkdir()
    s = Settings(baidu_app_key="ak", baidu_secret_key="sk", data_dir=tmp_path / "data", music_root="/音乐")
    TokenStore(s.token_path).save(Token("AT", "RT", time.time() + 9e5))
    fake = TrashPan(lib, trash)
    conn, _ = await scan(s, fake)
    monkeypatch.setattr(main, "settings", s)
    main.app.state.library = None
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), headers={"User-Agent": "pan.baidu.com"},
                             follow_redirects=True)
    main.app.state.streams = StreamManager(s, XpanClient(s, http))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        yield s, conn, fake, lib, c


def album_id(conn, folder):
    return conn.execute("SELECT id FROM albums WHERE folder_path=?", (folder,)).fetchone()[0]


async def test_delete_whole_folder(env):
    s, conn, fake, lib, c = env
    aid = album_id(conn, "/音乐/Beethoven/Schnabel - Late Sonatas")
    plan = (await c.get(f"/api/albums/{aid}/delete-plan")).json()
    assert plan["mode"] == "folder" and plan["paths"] == ["/音乐/Beethoven/Schnabel - Late Sonatas"]
    assert plan["file_count"] == 4 and "CD1/01 Maestoso.flac" in plan["files"]
    assert (await c.post(f"/api/albums/{aid}/delete", json={"token": "wrong"})).status_code == 409
    assert fake.deleted == []
    r = (await c.post(f"/api/albums/{aid}/delete", json={"token": plan["token"]})).json()
    assert r == {"deleted": 1, "failed": [], "album_removed": True, "mode": "folder"}
    assert not (lib / "Beethoven" / "Schnabel - Late Sonatas").exists()
    assert conn.execute("SELECT COUNT(*) FROM albums WHERE id=?", (aid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE album_id=?", (aid,)).fetchone()[0] == 0
    assert not (s.covers_dir / f"{aid}.jpg").exists()
    ids = [a["id"] for a in (await c.get("/api/albums")).json()]
    assert aid not in ids and len(ids) == 4


async def test_nested_album_only_deletes_own_files(env):
    s, conn, fake, lib, c = env
    aid = album_id(conn, "/音乐/整轨")
    plan = (await c.get(f"/api/albums/{aid}/delete-plan")).json()
    assert plan["mode"] == "files" and plan["paths"] == ["/音乐/整轨/loose.flac"]
    assert plan["nested_albums"] == ["/音乐/整轨/Cortot 1933"]
    await c.post(f"/api/albums/{aid}/delete", json={"token": plan["token"]})
    assert fake.deleted == ["/音乐/整轨/loose.flac"]
    assert (lib / "整轨" / "Cortot 1933" / "Cortot - Chopin.flac").exists()
    assert album_id(conn, "/音乐/整轨/Cortot 1933")


async def test_plan_detects_changes(env):
    s, conn, fake, lib, c = env
    aid = album_id(conn, "/音乐/杂项")
    plan = (await c.get(f"/api/albums/{aid}/delete-plan")).json()
    (lib / "杂项" / "new.jpg").write_bytes(b"x")  # 确认之后网盘里多了文件
    assert (await c.post(f"/api/albums/{aid}/delete", json={"token": plan["token"]})).status_code == 409
    assert fake.deleted == []
