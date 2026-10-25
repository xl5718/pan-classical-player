import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FROZEN = getattr(sys, "frozen", False)  # 打包成 .app 运行时为 True

# 配置目录：源码运行时是项目根目录；打包后的 .app 用 ~/Library/Application Support/唱片目录
CONFIG_DIR = Path(
    os.environ.get("PAN_PLAYER_HOME")
    or (Path.home() / "Library" / "Application Support" / "唱片目录" if FROZEN else PROJECT_ROOT)
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=CONFIG_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    baidu_app_key: SecretStr = SecretStr("")
    baidu_secret_key: SecretStr = SecretStr("")
    music_root: str = "/"
    data_dir: Path = CONFIG_DIR / "data"
    cache_max_gb: float = 5.0
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    log_level: str = "INFO"
    list_concurrency: int = 4
    scan_concurrency: int = 8
    play_connections: int = 3
    prefetch_connections: int = 2
    ffmpeg_path: str = ""
    ui_debug: bool = False  # 桌面版：打开 Web 检查器

    def model_post_init(self, __context) -> None:
        if not self.data_dir.is_absolute():
            self.data_dir = (CONFIG_DIR / self.data_dir).resolve()
        for sub in ("", "covers", "cache"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

    @property
    def token_path(self) -> Path:
        return self.data_dir / "token.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.sqlite3"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def cache_max_bytes(self) -> int:
        return int(self.cache_max_gb * 1024**3)

    @property
    def ffmpeg(self) -> str | None:
        """从 Finder 启动的 .app 没有终端的 PATH，所以也要找 Homebrew 的常见位置。"""
        for c in (self.ffmpeg_path, shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if c and Path(c).is_file() and os.access(c, os.X_OK):
                return c
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
