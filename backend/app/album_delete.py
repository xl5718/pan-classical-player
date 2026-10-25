"""删除专辑：先生成删除计划给用户确认，确认时重新核对网盘内容没有变化，再调用官方接口删除。"""

import hashlib
import logging
import posixpath

from .baidu.client import XpanClient
from .config import Settings

log = logging.getLogger(__name__)


class DeleteError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


async def make_plan(conn, client: XpanClient, s: Settings, album_id: int) -> dict:
    a = conn.execute("SELECT id, title, folder_path FROM albums WHERE id=?", (album_id,)).fetchone()
    if not a:
        raise DeleteError(404, "专辑不存在")
    root = s.music_root.rstrip("/") or "/"
    folder = a["folder_path"]
    if not _under(folder, root):
        raise DeleteError(400, "专辑不在曲库根目录之内，拒绝删除")
    nested = [
        r["folder_path"] for r in conn.execute("SELECT folder_path FROM albums WHERE id != ?", (album_id,))
        if r["folder_path"] != folder and _under(r["folder_path"], folder)
    ]
    # 重新列一次网盘上的实际内容，而不是只看本地索引
    entries = [e async for e in client.walk(folder)]
    files = [e for e in entries if not e.get("isdir")]
    if folder != root and not nested:
        mode, targets = "folder", files
        paths = [folder]
    else:
        mode = "files"
        if folder == root:
            targets = [f for f in files if posixpath.dirname(f["path"]) == root]
        else:
            targets = [f for f in files if not any(_under(f["path"], n) for n in nested)]
        paths = sorted(f["path"] for f in targets)
    if not paths:
        raise DeleteError(409, "网盘上已经找不到这张专辑的文件")
    sig = "\n".join(sorted(f"{f['path']}|{f.get('size', 0)}|{f.get('server_mtime', 0)}" for f in targets))
    token = hashlib.sha256(f"{mode}\n{sig}".encode()).hexdigest()[:24]
    return {
        "album_id": album_id,
        "title": a["title"],
        "folder": folder,
        "mode": mode,
        "paths": paths,
        "file_count": len(targets),
        "total_size": sum(int(f.get("size", 0)) for f in targets),
        "files": sorted(f["path"][len(folder) + 1 :] if f["path"].startswith(folder + "/") else f["path"] for f in targets)[:200],
        "nested_albums": nested,
        "token": token,
    }


async def execute(conn, client: XpanClient, s: Settings, album_id: int, token: str, cache_dir) -> dict:
    plan = await make_plan(conn, client, s, album_id)
    if plan["token"] != token:
        raise DeleteError(409, "网盘上的内容在确认之后发生了变化，请重新确认")
    log.info("删除专辑 %s：%s %s", album_id, plan["mode"], plan["paths"][:3])
    results = await client.delete(plan["paths"])
    ok = [r["path"] for r in results if r.get("errno", 0) in (0, -9)]
    failed = [{"path": r.get("path"), "errno": r.get("errno")} for r in results if r.get("errno", 0) not in (0, -9)]

    # 同步本地索引：删除对应文件记录、专辑、封面与音频缓存
    fsids = set()
    conn.execute("BEGIN")
    for p in ok:
        for r in conn.execute("SELECT fs_id FROM files WHERE path = ? OR path LIKE ?", (p, p.rstrip("/") + "/%")):
            fsids.add(r["fs_id"])
        conn.execute("DELETE FROM files WHERE path = ? OR path LIKE ?", (p, p.rstrip("/") + "/%"))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE album_id=? AND fsid NOT IN (%s)" % ",".join("?" * len(fsids)) if fsids
        else "SELECT COUNT(*) FROM tracks WHERE album_id=?",
        (album_id, *fsids),
    ).fetchone()[0]
    if remaining == 0:
        conn.execute("DELETE FROM albums WHERE id=?", (album_id,))
    conn.execute("COMMIT")
    if remaining == 0:
        for p in [s.covers_dir / f"{album_id}.jpg", *(s.covers_dir / "orig").glob(f"{album_id}.*")]:
            p.unlink(missing_ok=True)
    for fid in fsids:
        for p in cache_dir.glob(f"{fid}_*"):
            p.unlink(missing_ok=True)
    return {"deleted": len(ok), "failed": failed, "album_removed": remaining == 0, "mode": plan["mode"]}
