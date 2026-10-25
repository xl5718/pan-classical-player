import asyncio
import json
import logging
import posixpath
import time
from collections import defaultdict

from ..baidu.client import XpanClient
from ..baidu.errors import BaiduError
from ..baidu.remote import RemoteReader
from ..config import Settings
from ..progress import Progress
from .assemble import ASSEMBLY_VERSION, FileRec, album_folder_of, assemble
from .covers import save_cover
from .cue import decode_text
from .media import AUDIO_EXT, PARSER_VERSION, read_embedded_cover, read_meta

log = logging.getLogger(__name__)

CUE_EXT = {".cue"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
ARCHIVE_EXT = {".zip", ".rar", ".7z"}
ALBUM_BATCH = 50
UNSUPPORTED_AUDIO = {".dsf", ".dff", ".iso", ".tak", ".tta", ".ogg", ".opus", ".aiff", ".aif"}


def kind_of(ext: str) -> str:
    if ext in AUDIO_EXT:
        return "audio"
    if ext in CUE_EXT:
        return "cue"
    if ext in IMAGE_EXT:
        return "image"
    if ext in ARCHIVE_EXT:
        return "zip"
    if ext in UNSUPPORTED_AUDIO:
        return "unsupported"
    return "other"


def _rec(row) -> FileRec:
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    return FileRec(
        fs_id=row["fs_id"], path=row["path"], dir=row["dir"], name=row["name"], ext=row["ext"],
        kind=row["kind"], size=row["size"], server_mtime=row["server_mtime"], md5=row["md5"], meta=meta,
    )


class Scanner:
    def __init__(self, settings: Settings, client: XpanClient, conn, full: bool = False):
        self.s = settings
        self.c = client
        self.db = conn
        self.full = full
        self.root = settings.music_root.rstrip("/") or "/"
        self.stats = defaultdict(int)

    async def walk(self) -> tuple[list[dict], bool]:
        entries: list[dict] = []
        queue: asyncio.Queue[str] = asyncio.Queue()
        queue.put_nowait(self.root)
        failed: list[str] = []
        prog = Progress("扫描目录")
        dirs = 0

        async def worker():
            nonlocal dirs
            while True:
                path = await queue.get()
                try:
                    for item in await self.c.list_dir(path):
                        if item.get("isdir"):
                            queue.put_nowait(item["path"])
                        else:
                            entries.append(item)
                    dirs += 1
                    prog.update(1, f"文件 {len(entries)}")
                except BaiduError as e:
                    failed.append(path)
                    log.error("列目录失败 %s：%s", path, e)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self.s.list_concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
        prog.close()
        self.stats["dirs"] = dirs
        if failed:
            log.warning("%d 个目录列取失败，本次不删除任何已索引的文件", len(failed))
        return entries, not failed

    def sync_files(self, entries: list[dict], complete: bool) -> set[str]:
        old = {r["fs_id"]: r for r in self.db.execute("SELECT fs_id, path, dir, kind, server_mtime FROM files")}
        dirty: set[str] = set()
        seen = set()

        def mark(dir_path: str, kind: str):
            if kind in ("audio", "cue", "image"):
                dirty.add(album_folder_of(dir_path, self.root)[0])
            if kind == "image" and dir_path != self.root:
                dirty.add(album_folder_of(posixpath.dirname(dir_path), self.root)[0])

        self.db.execute("BEGIN")
        for e in entries:
            fid = int(e["fs_id"])
            seen.add(fid)
            path = e["path"]
            d, name = posixpath.split(path)
            ext = posixpath.splitext(name)[1].lower()
            kind = kind_of(ext)
            mtime = int(e.get("server_mtime", 0))
            o = old.get(fid)
            if o is not None and o["server_mtime"] == mtime and o["path"] == path:
                continue
            if o is not None:
                mark(o["dir"], o["kind"])
                self.stats["changed"] += 1
            else:
                self.stats["new"] += 1
            mark(d, kind)
            self.db.execute(
                """INSERT INTO files (fs_id, path, dir, name, ext, kind, size, server_mtime, md5)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(fs_id) DO UPDATE SET path=excluded.path, dir=excluded.dir, name=excluded.name,
                     ext=excluded.ext, kind=excluded.kind, size=excluded.size, md5=excluded.md5,
                     server_mtime=excluded.server_mtime""",
                (fid, path, d, name, ext, kind, int(e.get("size", 0)), mtime, e.get("md5")),
            )
        if complete:
            for fid, o in old.items():
                if fid not in seen:
                    mark(o["dir"], o["kind"])
                    self.db.execute("DELETE FROM files WHERE fs_id=?", (fid,))
                    self.stats["removed"] += 1
        self.db.execute("COMMIT")
        row = self.db.execute("SELECT value FROM kv WHERE key='assembly_version'").fetchone()
        if self.full or row is None or row["value"] != str(ASSEMBLY_VERSION):
            for r in self.db.execute("SELECT DISTINCT dir FROM files WHERE kind='audio'"):
                dirty.add(album_folder_of(r["dir"], self.root)[0])
        for r in self.db.execute("SELECT dir FROM files WHERE kind='audio' AND error IS NOT NULL"):
            dirty.add(album_folder_of(r["dir"], self.root)[0])
        return dirty

    def load_groups(self, folders: set[str]) -> dict[str, list[FileRec]]:
        groups: dict[str, list[FileRec]] = defaultdict(list)
        audio_folders = {
            album_folder_of(r["dir"], self.root)[0]
            for r in self.db.execute("SELECT DISTINCT dir FROM files WHERE kind='audio'")
        }
        for r in self.db.execute("SELECT * FROM files WHERE kind IN ('audio','cue','image')"):
            folder = album_folder_of(r["dir"], self.root)[0]
            if r["kind"] == "image" and folder not in audio_folders and r["dir"] != self.root:
                # Scans/、Artwork/ 等纯图片子文件夹归到上一级专辑
                folder = album_folder_of(posixpath.dirname(r["dir"]), self.root)[0]
            if folder in folders:
                groups[folder].append(_rec(r))
        return groups

    def _needs_parse(self, f: FileRec) -> bool:
        if f.kind not in ("audio", "cue"):
            return False
        if self.full or f.meta.get("_mtime") != f.server_mtime:
            return True
        return bool(f.meta.get("error")) and f.meta.get("_pv") != PARSER_VERSION

    async def parse(self, groups: dict[str, list[FileRec]], prog: Progress, totals: dict) -> None:
        jobs = [f for fs in groups.values() for f in fs if self._needs_parse(f)]
        if not jobs:
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.s.scan_concurrency * 4)

        async def producer():
            for i in range(0, len(jobs), 100):
                batch = jobs[i : i + 100]
                try:
                    metas = await self.c.filemetas([f.fs_id for f in batch], dlink=True)
                    links = {m["fs_id"]: m.get("dlink") for m in metas}
                except BaiduError as e:
                    log.warning("批量获取 dlink 失败（%s），改为逐个获取", e)
                    links = {}
                for f in batch:
                    await queue.put((f, links.get(f.fs_id)))
            for _ in range(self.s.scan_concurrency):
                await queue.put(None)

        async def worker():
            while (job := await queue.get()) is not None:
                f, dlink = job
                reader = RemoteReader(self.c, f.fs_id, f.size, dlink, label=f.path)
                err = None
                try:
                    if f.kind == "cue":
                        meta = {"cue_text": decode_text(await reader.read_all())}
                    else:
                        meta = await read_meta(reader, f.ext)
                except Exception as e:  # noqa: BLE001  单个文件失败不影响整体扫描
                    err = f"{type(e).__name__}: {e}"
                    meta = {"error": err, "_pv": PARSER_VERSION}
                    totals["errors"] += 1
                    log.warning("解析失败 %s：%s", f.path, err)
                meta["_mtime"] = f.server_mtime
                f.meta = meta
                self.db.execute(
                    "UPDATE files SET meta_json=?, meta_mtime=?, error=? WHERE fs_id=?",
                    (json.dumps(meta, ensure_ascii=False), f.server_mtime, err, f.fs_id),
                )
                totals["requests"] += reader.requests
                totals["bytes"] += reader.bytes_fetched
                prog.update(1, f"请求 {totals['requests']} · 流量 {totals['bytes'] / 1e6:.1f} MB · 失败 {totals['errors']}")

        await asyncio.gather(producer(), *[worker() for _ in range(self.s.scan_concurrency)])
        self.stats["parsed"] += len(jobs)
        self.stats["parse_errors"] = totals["errors"]
        self.stats["meta_mb"] = round(totals["bytes"] / 1e6, 1)

    def write_albums(self, groups: dict[str, list[FileRec]], dirty) -> list[tuple[int, dict]]:
        need_cover = []
        now = int(time.time())
        self.db.execute("BEGIN")
        for folder in sorted(dirty):
            album = assemble(folder, self.root, groups.get(folder, []))
            row = self.db.execute("SELECT id, cover_src, cover_path FROM albums WHERE folder_path=?", (folder,)).fetchone()
            if album is None:
                if row:
                    self.db.execute("DELETE FROM albums WHERE id=?", (row["id"],))
                    self.stats["albums_removed"] += 1
                continue
            vals = (album["title"], album["album_artist"], album["label"], album["year"], album["disc_count"], album["incomplete"], now)
            if row:
                aid = row["id"]
                self.db.execute(
                    "UPDATE albums SET title=?, album_artist=?, label=?, year=?, disc_count=?, incomplete=?, updated_at=? WHERE id=?",
                    (*vals, aid),
                )
                self.db.execute("DELETE FROM tracks WHERE album_id=?", (aid,))
                self.db.execute("DELETE FROM works WHERE album_id=?", (aid,))
            else:
                aid = self.db.execute(
                    "INSERT INTO albums (folder_path, title, album_artist, label, year, disc_count, incomplete, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (folder, *vals),
                ).lastrowid
            work_ids = []
            for i, w in enumerate(album["works"]):
                work_ids.append(self.db.execute(
                    "INSERT INTO works (album_id, composer, title, catalogue, seq) VALUES (?,?,?,?,?)",
                    (aid, w["composer"], w["title"], w["catalogue"], i),
                ).lastrowid)
            for t in album["tracks"]:
                self.db.execute(
                    """INSERT INTO tracks (album_id, work_id, fsid, path, disc_no, track_no, title, movement_name,
                         movement_no, composer, performer, conductor, duration, format, sample_rate, bit_depth, size,
                         md5, server_mtime, start_offset, end_offset, cue_path, incomplete)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (aid, work_ids[t["work_idx"]] if t["work_idx"] is not None else None, t["fsid"], t["path"],
                     t["disc_no"], t["track_no"], t["title"], t["movement_name"], t["movement_no"], t["composer"],
                     t["performer"], t["conductor"], t["duration"], t["format"], t["sample_rate"], t["bit_depth"],
                     t["size"], t["md5"], t["server_mtime"], t["start_offset"], t["end_offset"], t["cue_path"],
                     t["incomplete"]),
                )
            src = None
            t0 = next((t for t in album["tracks"] if t["has_picture"]), None)
            cf = album["cover_file"] or (None if t0 else album["cover_guess"])
            if cf is not None:
                src = {"key": f"file:{cf.fs_id}:{cf.server_mtime}", "fsid": cf.fs_id, "size": cf.size, "ext": None}
            elif t0:
                src = {"key": f"emb:{t0['fsid']}:{t0['server_mtime']}", "fsid": t0["fsid"], "size": t0["size"], "ext": t0["ext"]}
            cover_ok = row and row["cover_path"] and (self.s.covers_dir / row["cover_path"]).exists()
            if src is None:
                self.db.execute("UPDATE albums SET cover_path=NULL, cover_src=NULL WHERE id=?", (aid,))
            elif self.full or not cover_ok or row["cover_src"] != src["key"]:
                need_cover.append((aid, src))
            self.stats["albums_written"] += 1
        self.db.execute("COMMIT")
        return need_cover

    async def fetch_covers(self, jobs: list[tuple[int, dict]]) -> None:
        if not jobs:
            return
        sem = asyncio.Semaphore(self.s.scan_concurrency)

        async def one(aid: int, src: dict):
            async with sem:
                reader = RemoteReader(self.c, src["fsid"], src["size"], label=src["key"])
                try:
                    if src["ext"] is None:
                        data = await reader.read_all()
                    else:
                        await reader.read_at(0, 256 * 1024)
                        data = await read_embedded_cover(reader, src["ext"])
                    if data:
                        name = await asyncio.to_thread(save_cover, data, self.s.covers_dir, aid)
                        self.db.execute("UPDATE albums SET cover_path=?, cover_src=? WHERE id=?", (name, src["key"], aid))
                        self.stats["covers"] += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("封面获取失败（专辑 %s）：%s", aid, e)
                    self.stats["cover_errors"] += 1

        await asyncio.gather(*(one(a, s) for a, s in jobs))

    async def run(self) -> dict:
        t0 = time.monotonic()
        entries, complete = await self.walk()
        dirty = self.sync_files(entries, complete)
        # 上次扫描中断时未写完的专辑也要重建
        row = self.db.execute("SELECT value FROM kv WHERE key='pending_albums'").fetchone()
        if row:
            dirty |= set(json.loads(row["value"]))
        groups = self.load_groups(dirty)
        existing = {r["folder_path"] for r in self.db.execute("SELECT folder_path FROM albums")}
        dirty = {f for f in dirty if f in existing or any(x.kind == "audio" for x in groups.get(f, []))}
        self._set_kv("pending_albums", json.dumps(sorted(dirty), ensure_ascii=False))
        self.stats["dirty_albums"] = len(dirty)
        folders = sorted(dirty)
        n_jobs = sum(1 for fs in groups.values() for f in fs if self._needs_parse(f))
        prog = Progress("读取元数据", n_jobs)
        totals = {"requests": 0, "bytes": 0, "errors": 0}
        remaining = set(dirty)
        for i in range(0, len(folders), ALBUM_BATCH):
            batch = folders[i : i + ALBUM_BATCH]
            bgroups = {f: groups[f] for f in batch if f in groups}
            await self.parse(bgroups, prog, totals)
            cover_jobs = self.write_albums(bgroups, batch)
            await self.fetch_covers(cover_jobs)
            remaining -= set(batch)
            self._set_kv("pending_albums", json.dumps(sorted(remaining), ensure_ascii=False))
        prog.close()
        self.db.execute("DELETE FROM kv WHERE key='pending_albums'")
        self._set_kv("assembly_version", str(ASSEMBLY_VERSION))
        self._set_kv("last_scan", str(int(time.time())))
        self.write_zip_list()
        self.stats["seconds"] = round(time.monotonic() - t0, 1)
        self.stats["walk_complete"] = complete
        return dict(self.stats)

    def _set_kv(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
        )

    def write_zip_list(self) -> None:
        rows = self.db.execute("SELECT path, size FROM files WHERE kind='zip' ORDER BY path").fetchall()
        out = self.s.data_dir / "zip_list.txt"
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(f"{r['size'] / 1e9:8.2f} GB  {r['path']}\n")
        self.stats["zips"] = len(rows)
        self.stats["unsupported"] = self.db.execute("SELECT COUNT(*) FROM files WHERE kind='unsupported'").fetchone()[0]
