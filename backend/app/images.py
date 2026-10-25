"""专辑里的图片与 PDF（封面、小册子扫描等）：列出、按需从网盘下载并缓存、生成缩略图。"""

import asyncio
import io
import re
from pathlib import Path

from PIL import Image, ImageOps

from .baidu.remote import RemoteReader
from .config import Settings

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
DOC_EXT = {".pdf"}
THUMB = 360
MAX_BYTES = 80 * 1024 * 1024

_locks: dict[str, asyncio.Lock] = {}


def _natural(s: str):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", s)]


def list_album_media(conn, album_id: int) -> list[dict]:
    a = conn.execute("SELECT folder_path FROM albums WHERE id=?", (album_id,)).fetchone()
    if not a:
        return []
    folder = a["folder_path"].rstrip("/")
    nested = [
        r["folder_path"] for r in conn.execute("SELECT folder_path FROM albums WHERE id != ?", (album_id,))
        if r["folder_path"].startswith(folder + "/")
    ]
    exts = sorted(IMAGE_EXT | DOC_EXT)
    rows = conn.execute(
        f"SELECT fs_id, path, name, ext, size, server_mtime FROM files WHERE ext IN ({','.join('?' * len(exts))})"
        " AND (dir = ? OR dir LIKE ?)",
        (*exts, folder, folder + "/%"),
    ).fetchall()
    out = []
    for r in rows:
        if not r["path"].startswith(folder + "/"):  # LIKE 里的 _ % 可能多匹配
            continue
        if any(r["path"].startswith(n + "/") for n in nested) or r["size"] > MAX_BYTES:
            continue
        rel = r["path"][len(folder) + 1 :]
        out.append({
            "fsid": r["fs_id"], "name": r["name"], "path": rel, "size": r["size"],
            "kind": "pdf" if r["ext"] in DOC_EXT else "image",
        })
    # 封面类图片排在前面，其余按路径自然顺序
    front = re.compile(r"cover|front|folder|封面", re.I)
    out.sort(key=lambda x: (x["kind"] != "image", not front.search(x["name"]), _natural(x["path"])))
    return out


async def get_media(conn, client, s: Settings, fsid: int, thumb: bool) -> tuple[Path, str]:
    r = conn.execute("SELECT fs_id, path, ext, size, server_mtime FROM files WHERE fs_id=?", (fsid,)).fetchone()
    if not r or r["ext"] not in IMAGE_EXT | DOC_EXT:
        raise FileNotFoundError(fsid)
    base = s.data_dir / "images"
    base.mkdir(exist_ok=True)
    key = f"{r['fs_id']}_{r['server_mtime']}"
    orig = base / f"{key}{r['ext']}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if not orig.exists():
            reader = RemoteReader(client, r["fs_id"], r["size"], label=r["path"])
            data = await reader.read_all()
            tmp = orig.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(orig)
        if r["ext"] in DOC_EXT:
            return orig, "application/pdf"
        needs_convert = r["ext"] in {".tif", ".tiff", ".bmp"}  # 浏览器不能直接显示
        if not thumb and not needs_convert:
            return orig, "image/png" if r["ext"] == ".png" else "image/jpeg" if r["ext"] in {".jpg", ".jpeg"} else f"image/{r['ext'][1:]}"
        out = base / f"{key}_{'t' if thumb else 'v'}.jpg"
        if not out.exists():
            await asyncio.to_thread(_render, orig, out, THUMB if thumb else None)
        return out, "image/jpeg"


def _render(src: Path, dst: Path, size: int | None) -> None:
    img = Image.open(io.BytesIO(src.read_bytes()))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if size:
        img.thumbnail((size, size), Image.LANCZOS)
    img.save(dst, "JPEG", quality=85 if size else 92, optimize=True)
