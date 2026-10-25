import re
from dataclasses import dataclass, field

_TOKEN = re.compile(r'"([^"]*)"|(\S+)')


def decode_text(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    high = [i for i, b in enumerate(raw) if b >= 0x80]
    isolated = sum(
        1 for i in high
        if (i == 0 or raw[i - 1] < 0x80) and (i + 1 >= len(raw) or raw[i + 1] < 0x80)
    )
    if high and isolated / len(high) > 0.5:
        return raw.decode("cp1252", errors="replace")
    for enc, probe in (("shift_jis", re.compile(r"[\u3040-\u30ff]")), ("gbk", None), ("big5", None), ("gb18030", None)):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if probe is None or probe.search(text):
            return text
    return raw.decode("gb18030", errors="replace")


@dataclass
class CueTrack:
    number: int
    title: str | None = None
    performer: str | None = None
    composer: str | None = None
    start: float | None = None
    end: float | None = None
    rem: dict = field(default_factory=dict)


@dataclass
class CueFile:
    name: str
    tracks: list[CueTrack] = field(default_factory=list)


@dataclass
class CueSheet:
    title: str | None = None
    performer: str | None = None
    composer: str | None = None
    rem: dict = field(default_factory=dict)
    files: list[CueFile] = field(default_factory=list)

    @property
    def track_count(self) -> int:
        return sum(len(f.tracks) for f in self.files)


def _msf(s: str) -> float | None:
    m = re.match(r"^(\d+):(\d{1,2}):(\d{1,2})$", s)
    if not m:
        return None
    mm, ss, ff = map(int, m.groups())
    return mm * 60 + ss + ff / 75


def parse_cue(text: str) -> CueSheet:
    sheet = CueSheet()
    cur_file: CueFile | None = None
    cur: CueTrack | None = None
    for line in text.splitlines():
        toks = [a if a is not None and a != "" or b is None else b for a, b in _TOKEN.findall(line)]
        toks = [t for t in toks if t is not None]
        if not toks:
            continue
        cmd = toks[0].upper()
        args = toks[1:]
        if cmd == "FILE" and args:
            name = args[0] if len(args) <= 2 else " ".join(args[:-1])
            cur_file = CueFile(name=name)
            sheet.files.append(cur_file)
            cur = None
        elif cmd == "TRACK" and args and cur_file is not None:
            try:
                n = int(args[0])
            except ValueError:
                continue
            cur = CueTrack(number=n)
            if len(args) < 2 or args[1].upper() == "AUDIO":
                cur_file.tracks.append(cur)
        elif cmd in ("TITLE", "PERFORMER", "SONGWRITER") and args:
            val = " ".join(args).strip()
            target = cur if cur is not None else sheet
            attr = {"TITLE": "title", "PERFORMER": "performer", "SONGWRITER": "composer"}[cmd]
            setattr(target, attr, val)
        elif cmd == "REM" and len(args) >= 2:
            key = args[0].upper()
            val = " ".join(args[1:]).strip()
            target = cur if cur is not None else sheet
            target.rem[key] = val
            if key == "COMPOSER":
                target.composer = target.composer or val
        elif cmd == "INDEX" and len(args) >= 2 and cur is not None:
            if args[0] in ("01", "1"):
                cur.start = _msf(args[1])
    for f in sheet.files:
        f.tracks = [t for t in f.tracks if t.start is not None]
        for a, b in zip(f.tracks, f.tracks[1:]):
            a.end = b.start
    return sheet
