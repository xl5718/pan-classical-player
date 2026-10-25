import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from ..config import Settings
from ..logging_setup import register_secret
from .errors import AuthError

log = logging.getLogger(__name__)

OAUTH_BASE = "https://openapi.baidu.com/oauth/2.0"
SCOPE = "basic,netdisk"
REFRESH_MARGIN = 24 * 3600


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str = ""

    @property
    def expiring(self) -> bool:
        return time.time() > self.expires_at - REFRESH_MARGIN


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Token | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text("utf-8"))
        tok = Token(**data)
        register_secret(tok.access_token)
        register_secret(tok.refresh_token)
        return tok

    def save(self, tok: Token) -> None:
        register_secret(tok.access_token)
        register_secret(tok.refresh_token)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(tok), f)
        os.replace(tmp, self.path)


def _token_from_response(data: dict) -> Token:
    return Token(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=time.time() + int(data.get("expires_in", 0)),
        scope=data.get("scope", ""),
    )


class Authenticator:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.app_key = settings.baidu_app_key.get_secret_value()
        self.secret_key = settings.baidu_secret_key.get_secret_value()
        if not self.app_key or not self.secret_key:
            raise AuthError("config", "请在 .env 中填写 BAIDU_APP_KEY 和 BAIDU_SECRET_KEY")
        self.http = http
        self.store = TokenStore(settings.token_path)
        self._token: Token | None = self.store.load()
        self._lock = asyncio.Lock()

    async def _oauth_get(self, path: str, params: dict) -> dict:
        r = await self.http.get(f"{OAUTH_BASE}/{path}", params=params)
        try:
            data = r.json()
        except ValueError:
            raise AuthError(r.status_code, f"OAuth 返回非 JSON：{r.text[:200]}")
        return data

    async def device_login(self, on_code) -> Token:
        data = await self._oauth_get(
            "device/code",
            {"response_type": "device_code", "client_id": self.app_key, "scope": SCOPE},
        )
        if "device_code" not in data:
            raise AuthError(data.get("error", "?"), data.get("error_description", str(data)))
        register_secret(data["device_code"])
        on_code(data)
        interval = max(int(data.get("interval", 5)), 5)
        deadline = time.time() + int(data.get("expires_in", 300))
        while time.time() < deadline:
            await asyncio.sleep(interval)
            res = await self._oauth_get(
                "token",
                {
                    "grant_type": "device_token",
                    "code": data["device_code"],
                    "client_id": self.app_key,
                    "client_secret": self.secret_key,
                },
            )
            if "access_token" in res:
                tok = _token_from_response(res)
                self.store.save(tok)
                self._token = tok
                return tok
            err = res.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            raise AuthError(err or "?", res.get("error_description", ""))
        raise AuthError("expired_token", "用户码已过期，请重新运行")

    async def refresh(self) -> Token:
        async with self._lock:
            tok = self._token
            if tok is None:
                raise AuthError("no_token", "尚未授权，请先运行 pan auth")
            res = await self._oauth_get(
                "token",
                {
                    "grant_type": "refresh_token",
                    "refresh_token": tok.refresh_token,
                    "client_id": self.app_key,
                    "client_secret": self.secret_key,
                },
            )
            if "access_token" not in res:
                raise AuthError(
                    res.get("error", "?"),
                    f"刷新 token 失败：{res.get('error_description', '')}，请重新运行 pan auth",
                )
            new = _token_from_response(res)
            self.store.save(new)
            self._token = new
            log.info("access_token 已刷新，有效期至 %s", time.strftime("%Y-%m-%d %H:%M", time.localtime(new.expires_at)))
            return new

    async def access_token(self) -> str:
        if self._token is None:
            raise AuthError("no_token", "尚未授权，请先运行 pan auth")
        if self._token.expiring:
            await self.refresh()
        return self._token.access_token

    @property
    def token(self) -> Token | None:
        return self._token
