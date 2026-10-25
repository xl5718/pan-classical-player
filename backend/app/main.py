import logging
import posixpath
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .config import FROZEN, PROJECT_ROOT, get_settings
from .db import connect
from .logging_setup import register_secret, setup_logging

settings = get_settings()
# 打包后的应用把日志写进数据目录（data/logs/app.log），方便排查
setup_logging(settings.log_level, log_file=settings.data_dir / "logs" / "app.log" if FROZEN else None)
register_secret(settings.baidu_app_key.get_secret_value())
register_secret(settings.baidu_secret_key.get_secret_value())

log = logging.getLogger("app")

CONTENT_TYPES = {
    "FLAC": "audio/flac", "ALAC": "audio/mp4", "AAC": "audio/mp4", "MP3": "audio/mpeg",
    "WAV": "audio/wav", "APE": "audio/x-ape", "WavPack": "audio/x-wavpack",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.streams = None
    if settings.baidu_app_key.get_secret_value() and settings.token_path.exists():
        from .baidu.client import XpanClient
        from .stream.manager import StreamManager

        client = XpanClient(settings)
        app.state.streams = StreamManager(settings, client)
        app.state.streams.evict()
    yield
    if app.state.streams:
        await app.state.streams.aclose()
        await app.state.streams.client.aclose()


app = FastAPI(title="Pan Player", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "music_root": settings.music_root,
        "credentials_configured": bool(settings.baidu_app_key.get_secret_value()),
        "authorized": settings.token_path.exists(),
    }


def db():
    return connect(settings.db_path)


def _library():
    from .library import Library

    lib = getattr(app.state, "library", None)
    if lib is None:
        lib = app.state.library = Library(db)
    lib.refresh()
    return lib


@app.post("/api/client-log")
def client_log(body: dict) -> dict:
    """前端的未捕获错误写进后端日志（白屏等问题时用来排查）。"""
    msg = str(body.get("message", ""))[:500]
    stack = str(body.get("stack", ""))[:2000]
    log.warning("前端错误：%s\n%s", msg, stack)
    return {"ok": True}


@app.get("/api/albums")
def albums() -> list[dict]:
    return _library().albums


@app.get("/api/search")
def search(q: str) -> dict:
    return {"album_ids": _library().search(q)}


@app.get("/api/albums/{album_id}")
def album(album_id: int) -> dict:
    conn = db()
    a = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if not a:
        raise HTTPException(404)
    works = [dict(w) for w in conn.execute("SELECT * FROM works WHERE album_id=? ORDER BY seq", (album_id,))]
    tracks = [dict(t) for t in conn.execute(
        "SELECT * FROM tracks WHERE album_id=? ORDER BY disc_no, track_no, id", (album_id,)
    )]
    orig = next(iter(sorted((settings.covers_dir / "orig").glob(f"{album_id}.*"))), None) if a["cover_path"] else None
    return {**dict(a), "cover_large": f"orig/{orig.name}" if orig else a["cover_path"], "works": works, "tracks": tracks}


@app.get("/api/albums/{album_id}/delete-plan")
async def album_delete_plan(album_id: int) -> dict:
    from .album_delete import DeleteError, make_plan

    mgr = _streams()
    try:
        return await make_plan(db(), mgr.client, settings, album_id)
    except DeleteError as e:
        raise HTTPException(e.status, str(e))


@app.post("/api/albums/{album_id}/delete")
async def album_delete(album_id: int, body: dict) -> dict:
    from .album_delete import DeleteError, execute
    from .baidu.errors import BaiduError

    mgr = _streams()
    try:
        return await execute(db(), mgr.client, settings, album_id, str(body.get("token", "")), mgr.dir)
    except DeleteError as e:
        raise HTTPException(e.status, str(e))
    except BaiduError as e:
        raise HTTPException(502, f"网盘删除失败：{e}")


@app.get("/api/albums/{album_id}/media")
def album_media(album_id: int) -> list[dict]:
    from .images import list_album_media

    return list_album_media(db(), album_id)


@app.get("/api/media/{fsid}")
async def media(fsid: int, thumb: bool = False):
    from fastapi.responses import FileResponse

    from .baidu.errors import BaiduError
    from .images import get_media

    mgr = _streams()
    try:
        path, ctype = await get_media(db(), mgr.client, settings, fsid, thumb)
    except FileNotFoundError:
        raise HTTPException(404)
    except BaiduError as e:
        raise HTTPException(502, f"从网盘读取失败：{e}")
    return FileResponse(path, media_type=ctype, headers={"Cache-Control": "private, max-age=604800"})


@app.get("/api/composers")
def composers() -> list[dict]:
    return _library().composers()


@app.get("/api/composer")
def composer(name: str) -> dict:
    return _library().composer_works(name)


def _spec(track_id: int):
    from .stream.downloader import FileSpec

    t = db().execute("SELECT fsid, size, server_mtime, format, path FROM tracks WHERE id=?", (track_id,)).fetchone()
    if not t:
        raise HTTPException(404, "曲目不存在")
    ext = posixpath.splitext(t["path"])[1].lower() or ".bin"
    return FileSpec(t["fsid"], t["size"], t["server_mtime"], ext, t["path"]), CONTENT_TYPES.get(t["format"], "application/octet-stream")


def _streams():
    if app.state.streams is None:
        raise HTTPException(503, "尚未授权或未配置应用凭证")
    return app.state.streams


@app.api_route("/stream/pcm", methods=["GET", "HEAD"])
async def stream_pcm(request: Request, ids: str):
    """把若干曲目（各自所在的文件）拼成一条可拖动的虚拟 WAV：用于无缝播放与浏览器不支持的格式。"""
    from .stream.http_range import parse_range
    from .stream.pcm import PlanError, make_plan, render

    mgr = _streams()
    try:
        plan = make_plan(db(), [int(x) for x in ids.split(",") if x])
    except (PlanError, ValueError) as e:
        raise HTTPException(400, str(e))
    size = plan.size
    rng = parse_range(request.headers.get("range"), size)
    base = {"Accept-Ranges": "bytes", "Content-Type": "audio/wav", "Cache-Control": "no-store"}
    if rng == "unsatisfiable":
        return Response(status_code=416, headers={**base, "Content-Range": f"bytes */{size}"})
    if rng is None:
        start, end, status = 0, size - 1, 200
        headers = {**base, "Content-Length": str(size)}
    else:
        start, end = rng
        status = 206
        headers = {**base, "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"}
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers)
    base_url = f"http://127.0.0.1:{request.url.port or settings.backend_port}"

    async def body():
        try:
            async for chunk in render(mgr, plan, start, end, base_url):
                yield chunk
        except Exception as e:  # noqa: BLE001
            log.warning("PCM 推流中断 ids=%s @%s：%s", ids, start, e)

    return StreamingResponse(body(), status_code=status, headers=headers)


@app.api_route("/stream/{track_id}", methods=["GET", "HEAD"])
async def stream(track_id: int, request: Request):
    from .stream.http_range import parse_range

    mgr = _streams()
    spec, ctype = _spec(track_id)
    size = spec.size
    rng = parse_range(request.headers.get("range"), size)
    base = {"Accept-Ranges": "bytes", "Content-Type": ctype, "Cache-Control": "private, max-age=3600"}
    if rng == "unsatisfiable":
        return Response(status_code=416, headers={**base, "Content-Range": f"bytes */{size}"})
    if rng is None:
        start, end, status = 0, size - 1, 200
        headers = {**base, "Content-Length": str(size)}
    else:
        start, end = rng
        status = 206
        headers = {**base, "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"}
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers)

    async def body():
        try:
            async for chunk in mgr.read(spec, start, end):
                yield chunk
        except Exception as e:  # noqa: BLE001  连接中断时浏览器会重新发起 Range 请求
            log.warning("推流中断 track=%s @%s-%s：%s", track_id, start, end, e)

    return StreamingResponse(body(), status_code=status, headers=headers)


@app.get("/stream/{track_id}/flac")
async def stream_transcoded(track_id: int, request: Request, t: float = 0.0):
    """ALAC（Chrome）、APE、WavPack 等浏览器不能直接播放的格式：用 ffmpeg 实时转成 FLAC。从 t 秒开始。"""
    import asyncio
    mgr = _streams()
    spec, _ = _spec(track_id)
    ffmpeg = settings.ffmpeg
    if not ffmpeg:
        raise HTTPException(501, "未安装 ffmpeg（brew install ffmpeg）")
    cached = mgr.cached(spec)
    src = str(cached) if cached else f"http://127.0.0.1:{request.url.port or settings.backend_port}/stream/{track_id}"
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if t > 0:
        cmd += ["-ss", f"{t:.3f}"]
    cmd += ["-i", src, "-map", "0:a:0", "-vn", "-c:a", "flac", "-compression_level", "0", "-f", "flac", "pipe:1"]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def body():
        try:
            while True:
                data = await proc.stdout.read(64 * 1024)
                if not data:
                    break
                yield data
            await proc.wait()
            if proc.returncode:
                err = (await proc.stderr.read()).decode(errors="replace")[:300]
                log.warning("转码失败 track=%s：%s", track_id, err)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    return StreamingResponse(body(), media_type="audio/flac", headers={"Cache-Control": "no-store", "Accept-Ranges": "none"})


@app.post("/api/prefetch/{track_id}")
async def prefetch(track_id: int) -> dict:
    mgr = _streams()
    spec, _ = _spec(track_id)
    dl = mgr.ensure(spec, "prefetch")
    return {"cached": dl is None}


@app.get("/api/cache")
async def cache_stats() -> dict:
    return _streams().stats()


@app.get("/covers/{name:path}")
def cover(name: str):
    from fastapi.responses import FileResponse

    base = settings.covers_dir.resolve()
    path = (base / name).resolve()
    if not path.is_file() or base not in path.parents:
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


def _frontend_dir():
    import os
    import sys
    from pathlib import Path

    if os.environ.get("FRONTEND_DIST"):
        return Path(os.environ["FRONTEND_DIST"])
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", ".")) / "frontend_dist"
    return PROJECT_ROOT / "frontend" / "dist"


# 打包后的 .app（以及 `npm run build` 之后）由后端直接提供前端页面；开发时由 Vite 提供
if (_dist := _frontend_dir()).is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
