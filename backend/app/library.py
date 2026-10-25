"""面向前端的曲库查询：专辑列表（带聚合字段）、全文搜索、作曲家与同一作品的不同录音。"""

import re
import sqlite3
import threading
import unicodedata
from collections import Counter, defaultdict

from .indexer.composers import COMPOSERS


def fold(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", s))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def catalogue_key(cat: str | None) -> str | None:
    if not cat:
        return None
    k = fold(cat).upper()
    k = re.sub(r"\bOPUS\b", "OP", k)
    k = re.sub(r"\bKV\b", "K", k)
    k = re.sub(r"[\s.]+", "", k)
    k = re.sub(r",?NO", "-", k)
    k = k.replace("/", "-")
    return k


def title_key(title: str) -> str:
    t = fold(title)
    t = re.sub(r"[\"'“”‘’()\[\]]", "", t)
    t = re.sub(r"[\s,.;:：\-–—/]+", " ", t).strip()
    return t


_ALIASES = {canon: [a.lstrip("=") for a in aliases] for canon, aliases in COMPOSERS.items()}


def _surname_key(name: str) -> str:
    name = re.sub(r"\(.*?\)|[^\w\s'-]", " ", name)
    parts = name.replace(",", " ").split()
    if not parts:
        return name
    last = parts[-1]
    if last in ("II", "Jr.", "Sr.") and len(parts) > 1:
        last = parts[-2]
    return fold(last)


class Library:
    """以专辑表的（数量, 最近更新时间）为版本号，缓存聚合结果；扫描在后台更新时自动失效。"""

    def __init__(self, connect):
        self.connect = connect
        self._sig = None
        self._lock = threading.Lock()
        self.albums: list[dict] = []
        self.search_text: dict[int, str] = {}

    def _signature(self, conn) -> tuple:
        return tuple(conn.execute("SELECT COUNT(*), MAX(updated_at), (SELECT COUNT(*) FROM tracks) FROM albums").fetchone())

    def refresh(self) -> None:
        conn = self.connect()
        sig = self._signature(conn)
        if sig == self._sig:
            return
        with self._lock:
            if sig == self._sig:
                return
            self._build(conn)
            self._sig = sig

    def _build(self, conn: sqlite3.Connection) -> None:
        albums = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, folder_path, title, album_artist, label, year, cover_path, incomplete, disc_count, updated_at FROM albums"
        )}
        comp = defaultdict(Counter)
        texts = defaultdict(list)
        for w in conn.execute("SELECT album_id, composer, title FROM works"):
            if w["composer"]:
                comp[w["album_id"]][w["composer"]] += 1
            texts[w["album_id"]].append(w["title"])
        perf = defaultdict(Counter)
        fmt = defaultdict(Counter)
        added = defaultdict(int)
        counts = Counter()
        dur = defaultdict(float)
        for t in conn.execute(
            "SELECT album_id, title, movement_name, performer, conductor, composer, format, sample_rate, bit_depth,"
            " server_mtime, duration FROM tracks"
        ):
            a = t["album_id"]
            counts[a] += 1
            dur[a] += t["duration"] or 0
            for p in (t["performer"], t["conductor"]):
                if p:
                    for name in re.split(r"\s*;\s*", p):
                        perf[a][name] += 1
            fmt[a][(t["format"], t["sample_rate"], t["bit_depth"])] += 1
            added[a] = max(added[a], t["server_mtime"] or 0)
            texts[a] += [t["title"], t["movement_name"] or "", t["composer"] or ""]
        out = []
        search = {}
        for aid, a in albums.items():
            composers = [c for c, _ in comp[aid].most_common()]
            performers = [p for p, _ in perf[aid].most_common(8)]
            f = fmt[aid].most_common(1)[0][0] if fmt[aid] else (None, None, None)
            a.update(
                composers=composers, performers=performers, track_count=counts[aid],
                duration=round(dur[aid]), added=added[aid],
                format={"format": f[0], "sample_rate": f[1], "bit_depth": f[2], "mixed": len(fmt[aid]) > 1},
            )
            out.append(a)
            parts = [a["title"], a["album_artist"], a["label"], str(a["year"] or ""), a["folder_path"].rsplit("/", 1)[-1],
                     *composers, *performers, *texts[aid]]
            for c in composers:
                parts += _ALIASES.get(c, [])
            search[aid] = fold(" ".join(p for p in parts if p))
        self.albums = sorted(out, key=lambda a: fold(a["title"]))
        self.search_text = search

    def search(self, q: str) -> list[int]:
        terms = [t for t in fold(q).split() if t]
        if not terms:
            return []
        return [aid for aid, text in self.search_text.items() if all(t in text for t in terms)]

    def composers(self) -> list[dict]:
        conn = self.connect()
        rows = conn.execute(
            """SELECT w.composer AS name, COUNT(DISTINCT w.id) AS recordings, COUNT(DISTINCT w.album_id) AS albums
               FROM works w WHERE w.composer IS NOT NULL GROUP BY w.composer"""
        ).fetchall()
        out = []
        for r in rows:
            out.append({**dict(r), "sort": _surname_key(r["name"]), "known": r["name"] in COMPOSERS})
        out.sort(key=lambda c: (c["sort"], fold(c["name"])))
        return out

    def composer_works(self, name: str) -> dict:
        conn = self.connect()
        rows = conn.execute(
            """SELECT w.id AS work_id, w.title, w.catalogue, w.album_id, a.title AS album_title, a.year, a.label,
                      a.cover_path, a.album_artist
               FROM works w JOIN albums a ON a.id = w.album_id WHERE w.composer = ?""",
            (name,),
        ).fetchall()
        if not rows:
            return {"name": name, "works": []}
        ids = [r["work_id"] for r in rows]
        tracks = defaultdict(list)
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            for t in conn.execute(
                f"SELECT * FROM tracks WHERE work_id IN ({','.join('?' * len(chunk))}) ORDER BY disc_no, track_no, id",
                chunk,
            ):
                tracks[t["work_id"]].append(dict(t))
        groups: dict[str, dict] = {}
        for r in rows:
            ck = catalogue_key(r["catalogue"])
            key = f"cat:{ck}" if ck else f"title:{title_key(r['title'])}"
            g = groups.setdefault(key, {"key": key, "titles": Counter(), "catalogue": r["catalogue"], "recordings": []})
            g["titles"][r["title"]] += 1
            ts = tracks.get(r["work_id"], [])
            perf = Counter(t["performer"] for t in ts if t["performer"])
            cond = Counter(t["conductor"] for t in ts if t["conductor"])
            g["recordings"].append({
                "work_id": r["work_id"], "album_id": r["album_id"], "album_title": r["album_title"],
                "album_artist": r["album_artist"], "year": r["year"], "label": r["label"], "cover_path": r["cover_path"],
                "performer": perf.most_common(1)[0][0] if perf else r["album_artist"],
                "conductor": cond.most_common(1)[0][0] if cond else None,
                "duration": round(sum(t["duration"] or 0 for t in ts), 1),
                "tracks": ts,
            })
        works = []
        for g in groups.values():
            g["title"] = g.pop("titles").most_common(1)[0][0]
            # 同一专辑里被拆成多段的同一作品（例如每个乐章标题略有不同）合并为一个录音
            merged: dict[int, dict] = {}
            out_recs = []
            for rec in g["recordings"]:
                m = merged.get(rec["album_id"])
                nos = {t["movement_no"] for t in rec["tracks"] if t["movement_no"]}
                if m is None or nos & {t["movement_no"] for t in m["tracks"] if t["movement_no"]}:
                    merged[rec["album_id"]] = rec
                    out_recs.append(rec)
                else:
                    m["tracks"] = sorted(m["tracks"] + rec["tracks"], key=lambda t: (t["disc_no"], t["track_no"] or 0, t["id"]))
                    m["duration"] = round(m["duration"] + rec["duration"], 1)
            g["recordings"] = out_recs
            g["recordings"].sort(key=lambda x: (x["year"] or 9999, fold(x["performer"] or "")))
            works.append(g)
        prefixes = Counter(_cat_prefix(g["catalogue"]) for g in works if g["catalogue"])
        rank = {p: i for i, (p, _) in enumerate(prefixes.most_common())}
        works.sort(key=lambda g: _work_sort(g, rank))
        return {"name": name, "works": works}


def _cat_prefix(cat: str | None) -> str:
    m = re.match(r"\s*([A-Za-z]+)", cat or "")
    return m.group(1).upper().rstrip(".") if m else ""


def _work_sort(g: dict, rank: dict):
    """作品号排序：该作曲家最常用的目录体系（Op./BWV/K.…）在前，按编号；无作品号的按标题排在最后。"""
    cat = g["catalogue"] or ""
    nums = [int(x) for x in re.findall(r"\d+", cat)[:2]]
    return (0 if cat else 1, rank.get(_cat_prefix(cat), 99), nums, title_key(g["title"]))
