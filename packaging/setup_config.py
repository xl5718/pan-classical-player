"""把项目的 .env 同步到 .app 的配置目录，并让 .app 使用项目现有的数据（授权、索引、封面、缓存）。"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path.home() / "Library" / "Application Support" / "唱片目录"
SKIP = ("DATA_DIR=", "BACKEND_HOST=", "BACKEND_PORT=")


def main() -> None:
    src = ROOT / ".env"
    if not src.exists():
        raise SystemExit("项目目录下没有 .env，请先完成 make setup 并填写凭证")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in src.read_text("utf-8").splitlines() if not ln.strip().startswith(SKIP)]
    lines.append(f"DATA_DIR={(ROOT / 'data').resolve()}")
    dst = CONFIG_DIR / ".env"
    dst.write_text("\n".join(lines) + "\n", "utf-8")
    os.chmod(dst, 0o600)
    print(f"配置已写入 {dst}（数据目录：{ROOT / 'data'}）")


if __name__ == "__main__":
    main()
