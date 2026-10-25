import asyncio
import json
import time

import httpx
import pytest

from app.baidu import client as client_mod
from app.baidu.auth import Token, TokenStore
from app.baidu.client import XpanClient
from app.baidu.errors import RateLimited
from app.config import Settings
from app.logging_setup import redact


@pytest.fixture
def settings(tmp_path):
    return Settings(baidu_app_key="AK_test_123", baidu_secret_key="SK_test_456", data_dir=tmp_path, music_root="/音乐")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", fast)


def make(settings, handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": "pan.baidu.com"}, follow_redirects=True)
    return XpanClient(settings, http)


def save_token(settings, expires_in=30 * 86400):
    TokenStore(settings.token_path).save(Token("AT_old", "RT_old", time.time() + expires_in))


async def test_device_flow(settings):
    polls = {"n": 0}

    def h(req):
        p = dict(req.url.params)
        if req.url.path.endswith("/device/code"):
            assert p["response_type"] == "device_code" and p["scope"] == "basic,netdisk"
            return httpx.Response(200, json={"device_code": "DC", "user_code": "UC", "verification_url": "https://openapi.baidu.com/device", "expires_in": 300, "interval": 5})
        if req.url.path.endswith("/token"):
            assert p["grant_type"] == "device_token" and p["code"] == "DC"
            polls["n"] += 1
            if polls["n"] < 3:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 2592000, "scope": "basic netdisk"})
        raise AssertionError(req.url)

    c = make(settings, h)
    shown = []
    tok = await c.auth.device_login(shown.append)
    assert tok.access_token == "AT1" and polls["n"] == 3 and shown[0]["user_code"] == "UC"
    assert json.loads(settings.token_path.read_text())["refresh_token"] == "RT1"
    assert settings.token_path.stat().st_mode & 0o777 == 0o600


async def test_refresh_on_invalid_token_then_listall_pagination(settings):
    save_token(settings)
    seen = []

    def h(req):
        p = dict(req.url.params)
        if req.url.path.endswith("/token"):
            assert p["grant_type"] == "refresh_token" and p["refresh_token"] == "RT_old"
            return httpx.Response(200, json={"access_token": "AT_new", "refresh_token": "RT_new", "expires_in": 2592000})
        assert p["method"] == "listall" and p["recursion"] == "1"
        seen.append((p["access_token"], p["start"]))
        if p["access_token"] == "AT_old":
            return httpx.Response(200, json={"errno": 111})
        if p["start"] == "0":
            return httpx.Response(200, json={"errno": 0, "has_more": 1, "cursor": 1000, "list": [{"fs_id": 1, "path": "/音乐/a.flac", "isdir": 0}]})
        return httpx.Response(200, json={"errno": 0, "has_more": 0, "cursor": 2000, "list": [{"fs_id": 2, "path": "/音乐/b.flac", "isdir": 0}]})

    c = make(settings, h)
    items = [x async for x in c.listall("/音乐")]
    assert [i["fs_id"] for i in items] == [1, 2]
    assert seen == [("AT_old", "0"), ("AT_new", "0"), ("AT_new", "1000")]
    assert TokenStore(settings.token_path).load().refresh_token == "RT_new"


async def test_proactive_refresh_when_expiring(settings):
    save_token(settings, expires_in=3600)

    def h(req):
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "AT_new", "refresh_token": "RT_new", "expires_in": 2592000})
        assert req.url.params["access_token"] == "AT_new"
        return httpx.Response(200, json={"errno": 0, "baidu_name": "x"})

    assert (await make(settings, h).uinfo())["baidu_name"] == "x"


async def test_rate_limit_backoff_and_give_up(settings):
    save_token(settings)
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(200, json={"errno": 31034}) if n["c"] < 3 else httpx.Response(200, json={"errno": 0, "ok": 1})

    assert (await make(settings, h).uinfo())["ok"] == 1 and n["c"] == 3
    with pytest.raises(RateLimited):
        await make(settings, lambda r: httpx.Response(200, json={"errno": 31034})).uinfo()


async def test_filemetas_batches_of_100(settings):
    save_token(settings)
    batches = []

    def h(req):
        ids = json.loads(req.url.params["fsids"])
        batches.append(len(ids))
        return httpx.Response(200, json={"errno": 0, "list": [{"fs_id": i} for i in ids]})

    out = await make(settings, h).filemetas(range(250), dlink=True)
    assert batches == [100, 100, 50] and len(out) == 250


async def test_download_redirect_keeps_ua_and_range(settings):
    save_token(settings)

    def h(req):
        assert req.headers["user-agent"] == "pan.baidu.com"
        if req.url.host == "d.pcs.baidu.com":
            assert req.url.params["access_token"] == "AT_old"
            return httpx.Response(302, headers={"Location": "https://cdn.example.com/f"})
        assert req.headers["range"] == "bytes=0-3"
        return httpx.Response(206, content=b"fLaC", headers={"Content-Range": "bytes 0-3/100"})

    c = make(settings, h)
    r = await c.open_download("https://d.pcs.baidu.com/file/x?fid=1&expires=8h", {"Range": "bytes=0-3"})
    assert r.status_code == 206 and await r.aread() == b"fLaC"


def test_redaction():
    s = "https://d.pcs.baidu.com/file?fid=1&access_token=121.abc.def&errno=0 {\"refresh_token\": \"122.x\"}"
    out = redact(s)
    assert "121.abc" not in out and "122.x" not in out and "fid=1" in out and "errno=0" in out


def test_timeouts_set():
    assert client_mod.TIMEOUT.connect and client_mod.TIMEOUT.read


async def test_listall_denied_falls_back_to_list_walk(settings):
    save_token(settings)
    tree = {
        "/音乐": [{"path": "/音乐/A", "isdir": 1}, {"path": "/音乐/x.flac", "isdir": 0}],
        "/音乐/A": [{"path": "/音乐/A/CD1", "isdir": 1}, {"path": "/音乐/A/cover.jpg", "isdir": 0}],
        "/音乐/A/CD1": [{"path": "/音乐/A/CD1/01.flac", "isdir": 0}],
    }

    def h(req):
        p = dict(req.url.params)
        if p["method"] == "listall":
            return httpx.Response(200, json={"errno": 20020, "show_msg": "该路径不在允许的访问范围内"})
        assert p["method"] == "list"
        return httpx.Response(200, json={"errno": 0, "list": tree[p["dir"]]})

    c = make(settings, h)
    paths = [i["path"] async for i in c.iter_files("/音乐")]
    assert paths == ["/音乐/A", "/音乐/x.flac", "/音乐/A/CD1", "/音乐/A/cover.jpg", "/音乐/A/CD1/01.flac"]
    assert c._listall_denied
