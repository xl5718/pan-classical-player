"""只读文件头部，解析音频标签与技术参数。"""

import asyncio
import io

from mutagen.apev2 import APEBadItemError, APEBinaryValue, APETextValue
from mutagen.flac import FLAC, Picture
from mutagen.monkeysaudio import MonkeysAudio, MonkeysAudioInfo
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack, WavPackInfo

from ..baidu.remote import RemoteReader, SyncView
from .classical import nfc

HEAD_BYTES = 256 * 1024
PARSER_VERSION = 2

AUDIO_EXT = {".flac", ".m4a", ".mp4", ".mp3", ".ape", ".wv", ".wav"}
FORMAT_NAME = {".flac": "FLAC", ".mp3": "MP3", ".ape": "APE", ".wv": "WavPack", ".wav": "WAV"}
MUTAGEN_CLS = {".m4a": MP4, ".mp4": MP4, ".mp3": MP3, ".ape": MonkeysAudio, ".wv": WavPack, ".wav": WAVE}

ALIASES = {
    "album artist": "albumartist", "album_artist": "albumartist", "albumartist": "albumartist",
    "organization": "label", "publisher": "label", "label": "label", "recordlabel": "label",
    "date": "date", "year": "date", "originaldate": "originaldate", "originalyear": "originaldate",
    "tracknumber": "tracknumber", "track": "tracknumber", "discnumber": "discnumber", "disc": "discnumber",
    "disctotal": "disctotal", "totaldiscs": "disctotal",
    "composer": "composer", "work": "work", "movementname": "movementname", "movement": "movement",
    "movementnumber": "movement", "conductor": "conductor", "performer": "performer", "artist": "artist",
    "title": "title", "album": "album", "cuesheet": "cuesheet", "catalognumber": "catalognumber",
}
ID3_FRAMES = {
    "TCOM": "composer", "TIT2": "title", "TPE1": "artist", "TPE2": "albumartist", "TPE3": "conductor",
    "TALB": "album", "TDRC": "date", "TYER": "date", "TPUB": "label", "TPOS": "discnumber",
    "TRCK": "tracknumber", "MVNM": "movementname", "MVIN": "movement",
}
MP4_KEYS = {
    "©wrt": "composer", "©wrk": "work", "©mvn": "movementname", "©mvi": "movement", "©nam": "title",
    "©ART": "artist", "aART": "albumartist", "©alb": "album", "©day": "date",
}


def _add(tags: dict, key: str | None, value) -> None:
    if not key or value is None:
        return
    vals = value if isinstance(value, list) else [value]
    for v in vals:
        if isinstance(v, bytes):
            v = v.decode("utf-8", errors="replace")
        s = nfc(str(v))
        if s:
            tags.setdefault(key, [])
            if s not in tags[key]:
                tags[key].append(s)


def normalize_tags(audio) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    raw = audio.tags
    if raw is None:
        return tags
    if isinstance(audio, MP4):
        for k, v in raw.items():
            if k in MP4_KEYS:
                _add(tags, MP4_KEYS[k], v)
            elif k == "trkn" and v:
                _add(tags, "tracknumber", f"{v[0][0]}/{v[0][1]}" if v[0][1] else v[0][0])
            elif k == "disk" and v:
                _add(tags, "discnumber", v[0][0])
                if v[0][1]:
                    _add(tags, "disctotal", v[0][1])
            elif k.startswith("----:"):
                _add(tags, ALIASES.get(k.rsplit(":", 1)[-1].lower()), [bytes(x) for x in v])
        return tags
    if hasattr(raw, "getall"):  # ID3
        for frame in raw.values():
            fid = frame.FrameID
            if fid in ID3_FRAMES:
                _add(tags, ID3_FRAMES[fid], [str(t) for t in getattr(frame, "text", [])])
            elif fid == "TXXX":
                _add(tags, ALIASES.get(frame.desc.lower()), list(frame.text))
            elif fid == "TMCL":
                _add(tags, "performer", [f"{n} ({r})" if r else n for r, n in frame.people])
        return tags
    for k, v in raw.items():  # Vorbis comment / APEv2
        if isinstance(v, APEBinaryValue):
            continue
        if isinstance(v, APETextValue):
            v = list(v)
        _add(tags, ALIASES.get(k.lower()), v)
    return tags


def _info(audio, ext: str) -> dict:
    info = audio.info
    fmt = FORMAT_NAME.get(ext)
    if isinstance(audio, MP4):
        fmt = "ALAC" if getattr(info, "codec", "") == "alac" else "AAC"
    return {
        "format": fmt,
        "duration": round(float(getattr(info, "length", 0) or 0), 3) or None,
        "sample_rate": getattr(info, "sample_rate", None),
        "bit_depth": getattr(info, "bits_per_sample", None) or None,
        "channels": getattr(info, "channels", None),
    }


async def _flac_blocks(reader: RemoteReader) -> list[tuple[int, int, int]]:
    off = 0
    head = await reader.read_at(0, 10)
    if head[:3] == b"ID3":
        sz = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        off = 10 + sz + (10 if head[5] & 0x10 else 0)
        head = await reader.read_at(off, 4)
    if (await reader.read_at(off, 4)) != b"fLaC":
        raise ValueError("不是有效的 FLAC 文件")
    off += 4
    blocks = []
    while True:
        h = await reader.read_at(off, 4)
        if len(h) < 4:
            raise ValueError("FLAC 元数据块被截断")
        btype, length = h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        blocks.append((btype, off, length))
        off += 4 + length
        if h[0] & 0x80:
            return blocks


async def read_flac(reader: RemoteReader) -> tuple[dict, dict]:
    blocks = await _flac_blocks(reader)
    keep = [b for b in blocks if b[0] in (0, 4, 5)]
    parts = [b"fLaC"]
    for i, (btype, off, length) in enumerate(keep):
        data = await reader.read_at(off + 4, length)
        flag = 0x80 if i == len(keep) - 1 else 0
        parts.append(bytes([flag | btype]) + length.to_bytes(3, "big") + data)
    audio = FLAC(io.BytesIO(b"".join(parts)))
    info = _info(audio, ".flac")
    info["samples"] = audio.info.total_samples or None
    info["has_picture"] = any(b[0] == 6 for b in blocks)
    return info, normalize_tags(audio)


def sniff(head: bytes, ext: str) -> str:
    """按文件头判断真实格式（例如扩展名是 .wav、内容其实是 APE）。"""
    if head[:4] == b"fLaC" or (head[:3] == b"ID3" and ext == ".flac"):
        return ".flac"
    if head[:4] == b"MAC ":
        return ".ape"
    if head[:4] == b"wvpk":
        return ".wv"
    if head[:4] == b"RIFF":
        return ".wav"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return ".mp3"
    return ext


async def _lenient_ape_tags(reader: RemoteReader) -> dict[str, list[str]]:
    """标签里有非 UTF-8（多为 GBK）文本时 mutagen 会整体报错，这里手动解析 APEv2 并逐项解码。"""
    tags: dict[str, list[str]] = {}
    for end in (reader.size, reader.size - 128):
        footer = await reader.read_at(end - 32, 32)
        if footer[:8] == b"APETAGEX":
            break
    else:
        return tags
    size = int.from_bytes(footer[12:16], "little")
    count = int.from_bytes(footer[16:20], "little")
    data = await reader.read_at(end - size, size - 32)
    pos = 0
    for _ in range(count):
        if pos + 8 > len(data):
            break
        vlen = int.from_bytes(data[pos : pos + 4], "little")
        flags = int.from_bytes(data[pos + 4 : pos + 8], "little")
        kend = data.index(b"\0", pos + 8)
        key = data[pos + 8 : kend].decode("ascii", errors="replace")
        raw = data[kend + 1 : kend + 1 + vlen]
        pos = kend + 1 + vlen
        if (flags >> 1) & 3:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="replace")
        _add(tags, ALIASES.get(key.lower()), [v for v in text.split("\0") if v])
    return tags


async def read_meta(reader: RemoteReader, ext: str) -> dict:
    head = await reader.read_at(0, HEAD_BYTES)
    real = sniff(head[:12], ext)
    if real == ".flac":
        info, tags = await read_flac(reader)
    else:
        loop = asyncio.get_running_loop()
        view = SyncView(reader, loop, name=f"x{real}")
        try:
            audio = await asyncio.to_thread(MUTAGEN_CLS[real], view)
            info, tags = _info(audio, real), normalize_tags(audio)
            info["has_picture"] = bool(_pictures(audio))
        except APEBadItemError:
            info_cls = MonkeysAudioInfo if real == ".ape" else WavPackInfo
            view.seek(0)
            ainfo = await asyncio.to_thread(info_cls, view)
            info = {
                "format": FORMAT_NAME[real], "duration": round(ainfo.length, 3),
                "sample_rate": ainfo.sample_rate, "bit_depth": getattr(ainfo, "bits_per_sample", None),
                "channels": ainfo.channels, "has_picture": False,
            }
            tags = await _lenient_ape_tags(reader)
    if real != ext:
        info["real_ext"] = real
    return {**info, "tags": tags, "_pv": PARSER_VERSION}


def _pictures(audio) -> list[tuple[int, bytes, str]]:
    out = []
    tags = audio.tags
    if tags is None:
        return out
    if isinstance(audio, MP4):
        for c in tags.get("covr", []):
            out.append((3, bytes(c), "image/png" if c.imageformat == 14 else "image/jpeg"))
    elif hasattr(tags, "getall"):
        for f in tags.getall("APIC"):
            out.append((f.type, f.data, f.mime))
    else:
        for k, v in tags.items():
            if k.lower().startswith("cover art") and isinstance(v, APEBinaryValue):
                data = bytes(v.value)
                out.append((3 if "front" in k.lower() else 0, data.split(b"\0", 1)[-1], "image/jpeg"))
    return out


async def read_embedded_cover(reader: RemoteReader, ext: str) -> bytes | None:
    ext = sniff((await reader.read_at(0, 12)), ext)
    if ext == ".flac":
        pics = []
        for btype, off, length in await _flac_blocks(reader):
            if btype == 6:
                p = Picture(await reader.read_at(off + 4, length))
                pics.append((p.type, p.data, p.mime))
    elif ext in MUTAGEN_CLS:
        loop = asyncio.get_running_loop()
        audio = await asyncio.to_thread(MUTAGEN_CLS[ext], SyncView(reader, loop, name=f"x{ext}"))
        pics = _pictures(audio)
    else:
        return None
    if not pics:
        return None
    pics.sort(key=lambda p: (p[0] != 3, -len(p[1])))
    return pics[0][1]
