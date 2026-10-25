"""把一个或多个音频文件拼成一条"虚拟 WAV"：长度精确、支持任意 Range，浏览器可以像普通文件一样拖动。

用途：
- 浏览器不能直接播放的格式（APE、WavPack、Chrome 上的 ALAC）；
- 无缝播放：同一作品的多个分轨文件由 ffmpeg 的 concat 滤镜在采样级拼接，乐章之间没有任何空隙。
"""

import asyncio
import json
import logging
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .downloader import FileSpec
from .manager import StreamManager

log = logging.getLogger(__name__)

HEADER = 44
MAX_WAV = 0xFFFFFFFF - 64


@dataclass
class Part:
    track_id: int
    spec: FileSpec
    frames: int
    sample_rate: int
    channels: int
    bits: int


@dataclass
class Plan:
    parts: list[Part]
    sample_rate: int
    channels: int
    bits: int  # 输出位深：16 或 24

    @property
    def frame_size(self) -> int:
        return self.channels * self.bits // 8

    @property
    def total_frames(self) -> int:
        return sum(p.frames for p in self.parts)

    @property
    def size(self) -> int:
        return HEADER + self.total_frames * self.frame_size

    def header(self) -> bytes:
        data = self.total_frames * self.frame_size
        bps = self.bits // 8
        return (
            b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, self.channels, self.sample_rate,
                                    self.sample_rate * self.channels * bps, self.channels * bps, self.bits)
            + b"data" + struct.pack("<I", data)
        )


class PlanError(ValueError):
    pass


def make_plan(conn, track_ids: list[int]) -> Plan:
    if not track_ids:
        raise PlanError("缺少曲目")
    parts: list[Part] = []
    for tid in track_ids:
        row = conn.execute(
            """SELECT t.id, t.fsid, t.size, t.server_mtime, t.path, t.sample_rate, t.bit_depth, f.meta_json
               FROM tracks t LEFT JOIN files f ON f.fs_id = t.fsid WHERE t.id = ?""",
            (tid,),
        ).fetchone()
        if not row:
            raise PlanError(f"曲目 {tid} 不存在")
        meta = json.loads(row["meta_json"] or "{}")
        sr = meta.get("sample_rate") or row["sample_rate"]
        dur = meta.get("duration")
        if not sr or not dur:
            raise PlanError(f"曲目 {tid} 缺少采样率或时长")
        frames = meta.get("samples") or round(dur * sr)
        ext = "." + row["path"].rsplit(".", 1)[-1].lower()
        spec = FileSpec(row["fsid"], row["size"], row["server_mtime"], ext, row["path"])
        parts.append(Part(row["id"], spec, int(frames), int(sr), int(meta.get("channels") or 2),
                          int(meta.get("bit_depth") or row["bit_depth"] or 16)))
    sr, ch = parts[0].sample_rate, parts[0].channels
    if any(p.sample_rate != sr or p.channels != ch for p in parts):
        raise PlanError("拼接的文件采样率或声道数不一致")
    plan = Plan(parts, sr, ch, 24 if any(p.bits > 16 for p in parts) else 16)
    if plan.size > MAX_WAV:
        raise PlanError("拼接结果超过 4GB")
    return plan


def _ffmpeg_cmd(ffmpeg: str, plan: Plan, inputs: list[str], first: int, offset_frames: int) -> list[str]:
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    for i, src in enumerate(inputs[first:]):
        if i == 0 and offset_frames:
            cmd += ["-ss", f"{offset_frames / plan.sample_rate:.6f}"]
        cmd += ["-i", src]
    n = len(inputs) - first
    if n > 1:
        graph = "".join(f"[{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
        cmd += ["-filter_complex", graph, "-map", "[a]"]
    else:
        cmd += ["-map", "0:a:0"]
    fmt = "s24le" if plan.bits == 24 else "s16le"
    cmd += ["-ac", str(plan.channels), "-ar", str(plan.sample_rate), "-f", fmt, "pipe:1"]
    return cmd


async def render(mgr: StreamManager, plan: Plan, start: int, end: int, base_url: str) -> AsyncIterator[bytes]:
    """产出虚拟 WAV 的 [start, end] 字节。"""
    if start < HEADER:
        yield plan.header()[start : min(end + 1, HEADER)]
        start = HEADER
    if start > end:
        return
    ffmpeg = mgr.s.ffmpeg
    if not ffmpeg:
        raise RuntimeError("未安装 ffmpeg")
    fs = plan.frame_size
    data_off = start - HEADER
    frame = data_off // fs
    skip = data_off - frame * fs
    want = end - start + 1

    first, acc = 0, 0
    for i, p in enumerate(plan.parts):
        if frame < acc + p.frames:
            first = i
            break
        acc += p.frames
    else:
        first = len(plan.parts) - 1
    offset = frame - acc

    inputs = []
    for p in plan.parts:
        cached = mgr.cached(p.spec)
        inputs.append(str(cached) if cached else f"{base_url}/stream/{p.track_id}")
    for p in plan.parts[first + 1 :]:
        mgr.ensure(p.spec, "prefetch")

    proc = await asyncio.create_subprocess_exec(
        *_ffmpeg_cmd(ffmpeg, plan, inputs, first, offset),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    sent = 0
    try:
        while sent < want:
            data = await proc.stdout.read(256 * 1024)
            if not data:
                break
            if skip:
                cut = min(skip, len(data))
                data, skip = data[cut:], skip - cut
            data = data[: want - sent]
            if data:
                sent += len(data)
                yield data
        if sent < want:
            # 实际解码出的采样比按时长估算的略少：用静音补足，保证与声明的长度一致
            await proc.wait()
            if proc.returncode:
                err = (await proc.stderr.read()).decode(errors="replace")[:300]
                log.warning("PCM 渲染失败：%s", err)
                return
            pad = want - sent
            while pad > 0:
                n = min(pad, 256 * 1024)
                pad -= n
                yield b"\0" * n
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
