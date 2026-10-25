"""把一个专辑文件夹内的文件（含分碟子文件夹、CUE）组装成专辑 / 作品 / 曲目。"""

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field

from . import composers
from .classical import (
    TEMPO, extract_catalogue, looks_like_work, nfc, parse_movement, split_title, strip_composer_prefix, work_key,
)
from .cue import CueSheet, decode_text, parse_cue

DISC_DIR = re.compile(r"^(?:cd|disc|disk|dvd|sacd)\s*[-_.#]?\s*(\d{1,3})(?:\b|[^\d])", re.I)
DISC_DIR_ZH = re.compile(r"^第\s*(\d{1,3})\s*[张碟枚]")
FILENAME = re.compile(r"^(?:(\d{1,2})[-.](?=\d))?(\d{1,3})\s*[.\-_ )]\s*(.+)$")
COVER_STEMS = ("cover", "folder", "front", "封面")
DISC_IN_TEXT = re.compile(r"(?:\bdis[ck]|\bcd|第)\s*[-_.#]?\s*0?(\d{1,2})(?!\d)", re.I)
COVER_HINT = re.compile(r"cover|front|folder|封面|albumart|正面", re.I)
NOT_COVER = re.compile(r"back|booklet|book|inlay|inside|\bin\b|\bcd\b|disc|label|tracklist|tray|digipak|obi|spine|背|内页|cd\d|small", re.I)
PLACEHOLDER = re.compile(r"^(?:track|piste|titel|audio\s*track|音轨|曲目)?\s*\d+$", re.I)
ASSEMBLY_VERSION = 5


def disc_of_dir(name: str) -> int | None:
    m = DISC_DIR.match(name) or DISC_DIR_ZH.match(name)
    return int(m.group(1)) if m else None


def album_folder_of(dir_path: str, root: str) -> tuple[str, int | None]:
    base = posixpath.basename(dir_path)
    disc = disc_of_dir(base)
    parent = posixpath.dirname(dir_path)
    if disc is not None and dir_path != root and parent.startswith(root):
        return parent, disc
    return dir_path, None


@dataclass
class FileRec:
    fs_id: int
    path: str
    dir: str
    name: str
    ext: str
    kind: str
    size: int
    server_mtime: int
    md5: str | None = None
    meta: dict = field(default_factory=dict)


def _first(tags: dict, key: str) -> str | None:
    v = tags.get(key)
    return v[0] if v else None


def _joined(tags: dict, key: str) -> str | None:
    v = tags.get(key)
    return "; ".join(v) if v else None


def _int(s) -> int | None:
    if s is None:
        return None
    m = re.match(r"\s*(\d+)", str(s))
    return int(m.group(1)) if m else None


def _year(s) -> int | None:
    m = re.search(r"(1[89]\d\d|20\d\d)", str(s or ""))
    return int(m.group(1)) if m else None


def _stem(name: str) -> str:
    return posixpath.splitext(nfc(name).casefold())[0]


def _resolve_cue_file(ref: str, cue: FileRec, audio_in_dir: list[FileRec], n_files: int) -> FileRec | None:
    ref_name = nfc(posixpath.basename(ref.replace("\\", "/"))).casefold()
    for a in audio_in_dir:
        if nfc(a.name).casefold() == ref_name:
            return a
    for a in audio_in_dir:
        if _stem(a.name) == _stem(ref_name):
            return a
    if n_files == 1:
        for a in audio_in_dir:
            if _stem(a.name) == _stem(cue.name):
                return a
        if len(audio_in_dir) == 1:
            return audio_in_dir[0]
    return None


def _natural(s: str):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s.casefold())]


def pick_cover(images: list[FileRec], folder: str) -> tuple[FileRec | None, FileRec | None]:
    """返回（明确命名的封面, 猜测的封面）。"""
    named = guess = None
    for f in sorted(images, key=lambda f: (f.dir != folder, f.path)):
        if _stem(f.name) in COVER_STEMS:
            return f, None
    hinted = [f for f in images if COVER_HINT.search(f.name) and not NOT_COVER.search(f.name)]
    if hinted:
        return sorted(hinted, key=lambda f: (f.dir != folder, _natural(f.name)))[0], None
    rest = [f for f in images if not NOT_COVER.search(f.name) and f.size > 20_000]
    if rest:
        guess = sorted(rest, key=lambda f: (f.dir != folder, _natural(f.name)))[0]
    return named, guess


def path_composer(folder: str, root: str) -> str | None:
    rel = posixpath.relpath(folder, root)
    for part in reversed(rel.split("/")):
        c = composers.find_one(part)
        if c:
            return c
    return None


def assemble(folder: str, root: str, files: list[FileRec]) -> dict | None:
    audio = sorted((f for f in files if f.kind == "audio"), key=lambda f: f.path)
    if not audio:
        return None
    cues = sorted((f for f in files if f.kind == "cue"), key=lambda f: f.path)
    images = [f for f in files if f.kind == "image"]

    def disc_for(f: FileRec) -> int:
        return disc_of_dir(posixpath.basename(f.dir)) if f.dir != folder else None

    raw_tracks: list[dict] = []
    covered: set[int] = set()
    sheets: list[CueSheet] = []

    def add_cue_tracks(sheet: CueSheet, cue_path: str, mapping: dict[int, FileRec]):
        idx = len(sheets) - 1
        explicit = _int(sheet.rem.get("DISCNUMBER"))
        if explicit is None:
            for text in (sheet.title, posixpath.basename(cue_path)):
                m = DISC_IN_TEXT.search(text or "")
                if m:
                    explicit = int(m.group(1))
                    break
        for fi, cf in enumerate(sheet.files):
            a = mapping.get(fi)
            if a is None or a.fs_id in covered:
                continue
            covered.add(a.fs_id)
            for t in cf.tracks:
                raw_tracks.append({"file": a, "cue": sheet, "cue_track": t, "cue_path": cue_path,
                                   "cue_idx": idx, "cue_disc": explicit})

    for cue in cues:
        text = cue.meta.get("cue_text")
        if not text:
            continue
        sheet = parse_cue(text)
        if not sheet.track_count:
            continue
        local_audio = [a for a in audio if a.dir == cue.dir and a.fs_id not in covered]
        mapping = {}
        for i, cf in enumerate(sheet.files):
            r = _resolve_cue_file(cf.name, cue, local_audio, len(sheet.files))
            if r is not None:
                mapping[i] = r
        if mapping:
            sheets.append(sheet)
            add_cue_tracks(sheet, cue.path, mapping)

    for a in audio:
        if a.fs_id in covered:
            continue
        cs = _first(a.meta.get("tags", {}), "cuesheet")
        if cs:
            sheet = parse_cue(cs)
            if sheet.track_count > 1 and len(sheet.files) == 1:
                sheets.append(sheet)
                add_cue_tracks(sheet, a.path + "#CUESHEET", {0: a})
                continue
        raw_tracks.append({"file": a})

    # 同一文件夹里有多张 CUE 整轨时，按 CUE 顺序（或其中写明的碟号）区分碟
    cue_dirs = Counter()
    for idx in {(rt["file"].dir, rt["cue_idx"]) for rt in raw_tracks if "cue_idx" in rt}:
        cue_dirs[idx[0]] += 1
    cue_rank = {}
    for d in cue_dirs:
        idxs = sorted({rt["cue_idx"] for rt in raw_tracks if rt.get("cue_idx") is not None and rt["file"].dir == d})
        cue_rank.update({(d, i): n + 1 for n, i in enumerate(idxs)})

    album_tags: dict[str, Counter] = {k: Counter() for k in ("album", "albumartist", "label", "date", "disctotal")}
    folder_comp = path_composer(folder, root)
    tracks = []
    for rt in raw_tracks:
        f: FileRec = rt["file"]
        m = f.meta
        tags = m.get("tags", {})
        incomplete = bool(m.get("error"))
        ct = rt.get("cue_track")
        sheet: CueSheet | None = rt.get("cue")

        for k in album_tags:
            if _first(tags, k):
                album_tags[k][_first(tags, k)] += 1
        if sheet:
            if sheet.title:
                album_tags["album"][sheet.title] += 2
            if sheet.performer:
                album_tags["albumartist"][sheet.performer] += 1
            if sheet.rem.get("DATE"):
                album_tags["date"][sheet.rem["DATE"]] += 1

        if ct is not None:
            title = ct.title or (f"Track {ct.number}")
            composer = ct.composer or sheet.composer or _joined(tags, "composer")
            performer = ct.performer or sheet.performer or _joined(tags, "performer") or _joined(tags, "artist")
            track_no = ct.number
            start, end = ct.start, ct.end
            file_dur = m.get("duration")
            duration = (end if end is not None else file_dur) - start if (end is not None or file_dur) else None
            work_tag = movement_tag = mvt_no_tag = None
            if not ct.title:
                incomplete = True
        else:
            title = _first(tags, "title")
            track_no = _int(_first(tags, "tracknumber"))
            if not title:
                incomplete = True
                stem = posixpath.splitext(f.name)[0]
                fm = FILENAME.match(stem)
                title = fm.group(3).strip() if fm else stem
                if fm and track_no is None:
                    track_no = int(fm.group(2))
            composer = _joined(tags, "composer")
            performer = _joined(tags, "performer") or _joined(tags, "artist")
            start = end = None
            duration = m.get("duration")
            work_tag = _first(tags, "work")
            movement_tag = _first(tags, "movementname")
            mvt_no_tag = _int(_first(tags, "movement"))

        title_for_parse = re.sub(r"^\d{2,3}[\s.\-_]+(?=[^\W\d])", "", title)
        bare = False
        composer = composers.canonical(composer)
        if not composer:
            pc, stripped = composers.split_prefix(title)
            if pc:
                composer, title_for_parse = pc, stripped
            else:
                album_text = (sheet.title if sheet and sheet.title else None) or _first(tags, "album")
                composer = (
                    composers.find_one(work_tag) or composers.find_one(album_text)
                    or composers.find_one(_joined(tags, "artist")) or folder_comp
                )
        if not composer:
            incomplete = True

        work = mvt_name = None
        work_src = None
        mvt_no = mvt_no_tag
        if work_tag:
            work_src = "tag"
            work = work_tag
            if movement_tag:
                mvt_name = movement_tag
            else:
                rest = title
                if title.casefold().startswith(work_tag.casefold()):
                    rest = re.sub(r"^[\s:：,\-–—]+", "", title[len(work_tag):]) or title
                pm = parse_movement(rest)
                mvt_name = pm[1] if pm else rest
                mvt_no = mvt_no or (pm[0] if pm else None)
        else:
            sp = split_title(title_for_parse, composer)
            if sp:
                work_src = "split"
                work, n, mvt_name = sp
                mvt_no = mvt_no or n
                if movement_tag:
                    mvt_name = movement_tag
            elif (pm := parse_movement(title_for_parse)) is not None:
                # 只有乐章名（'IV. Adagio'）：作品名稍后取专辑名
                mvt_no, mvt_name = mvt_no or pm[0], pm[1] or title_for_parse
                work = None
                bare = True
            elif composer and not PLACEHOLDER.match(title_for_parse):
                work_src = "single"
                work = strip_composer_prefix(title_for_parse, composer)

        disc_no = _int(_first(tags, "discnumber")) or disc_for(f)
        if disc_no is None and rt.get("cue_idx") is not None and cue_dirs[f.dir] > 1:
            disc_no = rt.get("cue_disc") or cue_rank[(f.dir, rt["cue_idx"])]
        disc_no = disc_no or 1
        tracks.append({
            "fsid": f.fs_id, "path": f.path, "disc_no": disc_no, "track_no": track_no, "title": title,
            "work": work, "movement_name": mvt_name, "movement_no": mvt_no, "composer": composer,
            "performer": performer, "conductor": _joined(tags, "conductor"), "duration": duration,
            "format": m.get("format") or f.ext.lstrip(".").upper(), "sample_rate": m.get("sample_rate"),
            "bit_depth": m.get("bit_depth"), "size": f.size, "md5": f.md5, "server_mtime": f.server_mtime,
            "start_offset": start, "end_offset": end, "cue_path": rt.get("cue_path"), "incomplete": int(incomplete),
            "has_picture": m.get("has_picture", False), "ext": f.ext, "bare": bare, "work_src": work_src,
        })

    tracks.sort(key=lambda t: (t["disc_no"], t["track_no"] if t["track_no"] is not None else 9999, t["path"], t["start_offset"] or 0))

    # 紧跟在某部作品后面、标题只写速度/体裁的曲目，归入该作品（CUE 里作品名常只出现在第一乐章）
    prev = None
    for t in tracks:
        own = t["work_src"] in ("tag", "split")
        if own:
            prev = t
            continue
        title_ = t["movement_name"] if t["bare"] else t["title"]
        if (
            prev is not None
            and prev["disc_no"] == t["disc_no"]
            and (t["composer"] in (None, prev["composer"]))
            and (
                (TEMPO.match(title_.strip()) and not looks_like_work(title_))
                or (t["bare"] and t["movement_no"] == (prev["movement_no"] or 1) + 1)
            )
        ):
            t["work"], t["work_src"], t["bare"] = prev["work"], "inherit", False
            t["composer"] = prev["composer"]
            t["movement_name"] = title_
            if prev["movement_no"] is None:
                prev["movement_no"] = 1
            t["movement_no"] = (prev["movement_no"] or 1) + 1 if t["movement_no"] is None or t["movement_no"] <= (prev["movement_no"] or 0) else t["movement_no"]
            t["incomplete"] = 0 if t["composer"] else 1
            prev = t
        else:
            prev = None

    # 只有乐章名的曲目：若整张专辑只有一组且专辑名像作品名（'Violin Sonata No.1 in G Op.78'），用专辑名作作品名
    album_title = album_tags["album"].most_common(1)[0][0] if album_tags["album"] else posixpath.basename(folder)
    runs, prev_no = 0, None
    for t in tracks:
        if not t["bare"]:
            prev_no = None
            continue
        if prev_no is None or (t["movement_no"] or 0) <= prev_no:
            runs += 1
        prev_no = t["movement_no"] or 0
    base = composers.split_prefix(album_title)[1]
    use_album = runs == 1 and looks_like_work(base)
    for t in tracks:
        if t["bare"]:
            t["work"] = base if use_album else None
            t["incomplete"] = 1

    works: list[dict] = []
    index: dict[tuple, int] = {}
    for t in tracks:
        if not t["work"]:
            t["work_idx"] = None
            continue
        key = work_key(t["composer"], t["work"])
        if key not in index:
            index[key] = len(works)
            works.append({"composer": t["composer"], "title": t["work"], "catalogue": extract_catalogue(t["work"]), "tracks": []})
        t["work_idx"] = index[key]
        works[index[key]]["tracks"].append(t)
    for w in works:
        if len(w["tracks"]) > 1:
            for i, t in enumerate(w["tracks"], 1):
                if t["movement_no"] is None:
                    t["movement_no"] = i
                if t["movement_name"] is None:
                    t["movement_name"] = t["title"]
        del w["tracks"]

    def top(k):
        c = album_tags[k]
        return c.most_common(1)[0][0] if c else None

    title = top("album")
    incomplete = any(t["incomplete"] for t in tracks)
    if not title:
        title = posixpath.basename(folder)
        incomplete = True
    album_artist = top("albumartist")
    if not album_artist:
        perfs = Counter(t["performer"] for t in tracks if t["performer"])
        album_artist = perfs.most_common(1)[0][0] if perfs else None

    cover_file, cover_guess = pick_cover(images, folder)

    discs = {t["disc_no"] for t in tracks}
    return {
        "folder_path": folder,
        "title": title,
        "album_artist": album_artist,
        "label": top("label"),
        "year": _year(top("date")),
        "disc_count": max([len(discs), max(discs), _int(top("disctotal")) or 1]),
        "incomplete": int(incomplete),
        "works": works,
        "tracks": tracks,
        "cover_file": cover_file,
        "cover_guess": cover_guess,
    }


def decode_cue_bytes(raw: bytes) -> str:
    return decode_text(raw)
