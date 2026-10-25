import argparse
import asyncio
import json
import sys
import time
from urllib.parse import urlsplit

from .baidu.client import XpanClient
from .baidu.errors import BaiduError
from .config import get_settings
from .db import connect
from .logging_setup import redact, setup_logging

AUDIO_EXT = {".flac", ".m4a", ".mp3", ".wav", ".ape", ".dsf", ".aiff"}


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_speed(bps: float) -> str:
    return f"{human_size(bps)}/s ({bps * 8 / 1e6:.1f} Mbit/s)"


async def cmd_auth(args, s) -> None:
    async with XpanClient(s) as c:
        def show(data: dict) -> None:
            print("\n请在浏览器打开以下地址，并输入用户码完成授权：")
            print(f"  地址  : {data.get('verification_url')}")
            print(f"  用户码: {data.get('user_code')}")
            if data.get("qrcode_url"):
                print(f"  （或扫码：{data['qrcode_url']}）")
            print(f"  {int(data.get('expires_in', 300)) // 60} 分钟内有效，等待确认中…\n")

        tok = await c.auth.device_login(show)
        exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(tok.expires_at))
        print(f"授权成功，token 已保存到 {s.token_path}（access_token 有效期至 {exp}）")
        info = await c.uinfo()
        print(f"网盘账号：{info.get('baidu_name')}  会员类型：{info.get('vip_type')}")


async def cmd_whoami(args, s) -> None:
    async with XpanClient(s) as c:
        if args.refresh:
            await c.auth.refresh()
        info = await c.uinfo()
        exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(c.auth.token.expires_at))
        print(f"账号：{info.get('baidu_name')}  vip_type：{info.get('vip_type')}  token 有效期至：{exp}")


async def first_files(c: XpanClient, root: str, n: int) -> list[dict]:
    out = []
    async for item in c.iter_files(root):
        if not item.get("isdir"):
            out.append(item)
            if len(out) >= n:
                break
    return out


async def cmd_ls(args, s) -> None:
    root = args.path or s.music_root
    async with XpanClient(s) as c:
        files = await first_files(c, root, args.n)
    print(f"{root} 下的前 {len(files)} 个文件：")
    for f in files:
        print(f"  {f['fs_id']:>16}  {human_size(f.get('size', 0)):>10}  {f['path']}")


async def cmd_speedtest(args, s) -> None:
    root = s.music_root
    async with XpanClient(s) as c:
        t = time.perf_counter()
        files = await first_files(c, root, 50)
        print(f"列出前 50 个文件：{time.perf_counter() - t:.2f}s")
        for f in files:
            print(f"  {f['fs_id']:>16}  {human_size(f.get('size', 0)):>10}  {f['path']}")

        if args.fsid:
            target = next((f for f in files if f["fs_id"] == args.fsid), None)
            if not target:
                meta = (await c.filemetas([args.fsid]))[0]
                target = {"fs_id": args.fsid, "path": meta.get("path", "?"), "size": meta.get("size", 0)}
        else:
            target = next((f for f in files if f["path"].lower().endswith(".flac")), None)
            if not target:
                print("前 50 个文件中没有 FLAC，继续向下查找…")
                async for f in c.iter_files(root):
                    if not f.get("isdir") and f["path"].lower().endswith(".flac"):
                        target = f
                        break
            if not target:
                print("曲库中没有找到 FLAC，请用 --fsid 指定")
                return

        print(f"\n测速文件：{target['path']}（{human_size(target.get('size', 0))}）")
        t = time.perf_counter()
        dlink = await c.get_dlink(target["fs_id"])
        print(f"filemetas 取 dlink：{time.perf_counter() - t:.2f}s")

        out = s.cache_dir / "speedtest.tmp"
        t0 = time.perf_counter()
        resp = await c.open_download(dlink)
        try:
            hops = [urlsplit(str(h.url)).netloc for h in resp.history] + [urlsplit(str(resp.url)).netloc]
            ttfb = None
            total = 0
            windows: list[float] = []
            win_start, win_bytes = None, 0
            with open(out, "wb") as fp:
                async for chunk in resp.aiter_bytes(256 * 1024):
                    now = time.perf_counter()
                    if ttfb is None:
                        ttfb = now - t0
                        win_start = now
                    fp.write(chunk)
                    total += len(chunk)
                    win_bytes += len(chunk)
                    if now - win_start >= 1.0:
                        windows.append(win_bytes / (now - win_start))
                        print(f"\r  已下载 {human_size(total)}  当前 {human_speed(windows[-1])}   ", end="", flush=True)
                        win_start, win_bytes = now, 0
            elapsed = time.perf_counter() - t0
        finally:
            await resp.aclose()
            if not args.keep:
                out.unlink(missing_ok=True)

        print("\n\n== 测速结果 ==")
        print(f"跳转路径     : {' -> '.join(hops)}  (HTTP {resp.status_code})")
        print(f"首字节时间   : {ttfb:.2f}s")
        print(f"总耗时       : {elapsed:.2f}s")
        print(f"文件大小     : {human_size(total)}" + ("" if not target.get("size") or total == target["size"] else f"（与网盘记录 {target['size']} 不一致！）"))
        if elapsed > (ttfb or 0):
            print(f"平均速度     : {human_speed(total / (elapsed - ttfb))}（不含首字节等待）")
        if windows:
            ws = sorted(windows)
            print(f"每秒速度     : 最低 {human_speed(ws[0])} / 中位 {human_speed(ws[len(ws)//2])} / 最高 {human_speed(ws[-1])}")
        if args.keep:
            print(f"文件已保留：{out}")


def shape(v, depth=0):
    if isinstance(v, dict):
        return {k: ("<redacted>" if k in {"dlink", "access_token"} else shape(x, depth + 1)) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(v[0], depth + 1), f"...共 {len(v)} 项"] if v else []
    return v


async def cmd_probe(args, s) -> None:
    """打印各接口的原始返回结构，用于核对文档与实际行为。"""
    async with XpanClient(s) as c:
        print("== uinfo ==")
        print(json.dumps(shape(await c.uinfo()), ensure_ascii=False, indent=2))

        print("\n== list（MUSIC_ROOT 第一层）==")
        data = await c._api("file", "list", {"dir": s.music_root, "start": 0, "limit": 5, "web": 0})
        print(json.dumps(shape(data), ensure_ascii=False, indent=2))
        f = None
        async for item in c.iter_files(s.music_root):
            if not item.get("isdir") and item["path"].lower().endswith((".flac", ".m4a", ".mp3")):
                f = item
                break
        if not f:
            print("没有找到音频文件，跳过后续")
            return
        print(f"\n用于后续测试的文件：{f['path']}")

        print("\n== filemetas（dlink=1, extra=1, needmedia=1）==")
        data = await c._api("multimedia", "filemetas", {"fsids": json.dumps([f["fs_id"]]), "dlink": 1, "extra": 1, "needmedia": 1})
        print(json.dumps(shape(data), ensure_ascii=False, indent=2))
        dlink = data["list"][0]["dlink"]
        q = dict(p.split("=", 1) for p in urlsplit(dlink).query.split("&") if "=" in p)
        print(f"dlink 主机：{urlsplit(dlink).netloc}  expires 参数：{q.get('expires')}")

        print("\n== Range 请求 bytes=0-1023 ==")
        t0 = time.perf_counter()
        resp = await c.open_download(dlink, headers={"Range": "bytes=0-1023"})
        body = await resp.aread()
        await resp.aclose()
        for h in resp.history:
            print(f"  {h.status_code} {urlsplit(str(h.url)).netloc}")
        print(f"  {resp.status_code} {urlsplit(str(resp.url)).netloc}  耗时 {time.perf_counter() - t0:.2f}s  收到 {len(body)} 字节")
        for k in ("content-range", "accept-ranges", "content-length", "content-type", "content-md5", "etag"):
            print(f"  {k}: {resp.headers.get(k)}")
        print(f"  文件头：{body[:4]!r}")


async def cmd_diag(args, s) -> None:
    """逐个路径、逐个接口尝试，定位访问范围限制。"""
    paths = args.paths or ["/", "/apps", "/画", s.music_root]
    async with XpanClient(s) as c:
        for path in paths:
            for endpoint, method, params in (
                ("file", "list", {"dir": path, "start": 0, "limit": 3}),
                ("multimedia", "listall", {"path": path, "recursion": 1, "start": 0, "limit": 3}),
            ):
                try:
                    data = await c._api(endpoint, method, params, max_retries=1)
                    names = [x.get("path") for x in data.get("list", [])]
                    print(f"OK   {method:8} {path}  -> {names}")
                except BaiduError as e:
                    print(f"FAIL {method:8} {path}  -> errno={e.errno} {e.message[:80]}")


async def _scan(s, full: bool) -> None:
    from .indexer.scanner import Scanner

    conn = connect(s.db_path)
    async with XpanClient(s) as c:
        stats = await Scanner(s, c, conn, full=full).run()
    print("\n== 扫描完成 ==")
    print(f"目录 {stats.get('dirs', 0)}，新增文件 {stats.get('new', 0)}，变化 {stats.get('changed', 0)}，删除 {stats.get('removed', 0)}")
    print(f"需要重建的专辑 {stats.get('dirty_albums', 0)}，写入 {stats.get('albums_written', 0)}，移除 {stats.get('albums_removed', 0)}")
    print(f"解析文件 {stats.get('parsed', 0)}（失败 {stats.get('parse_errors', 0)}，读取 {stats.get('meta_mb', 0)} MB），封面 {stats.get('covers', 0)}（失败 {stats.get('cover_errors', 0)}）")
    if not stats.get("walk_complete", True):
        print("注意：部分目录列取失败，本次没有删除任何记录，可稍后重新运行 pan scan")
    print(f"用时 {stats.get('seconds')}s")
    print(f"压缩包 {stats.get('zips', 0)} 个（清单：{s.data_dir / 'zip_list.txt'}），暂不支持的音频 {stats.get('unsupported', 0)} 个")
    print()
    _print_stats(conn)


async def cmd_scan(args, s) -> None:
    await _scan(s, full=False)


async def cmd_rescan(args, s) -> None:
    await _scan(s, full=True)


def _print_stats(conn) -> None:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    print(f"专辑 {q('SELECT COUNT(*) FROM albums')}（元数据不全 {q('SELECT COUNT(*) FROM albums WHERE incomplete=1')}，无封面 {q('SELECT COUNT(*) FROM albums WHERE cover_path IS NULL')}）")
    print(f"作品 {q('SELECT COUNT(*) FROM works')}，曲目 {q('SELECT COUNT(*) FROM tracks')}（CUE 分轨 {q('SELECT COUNT(*) FROM tracks WHERE start_offset IS NOT NULL')}）")
    print(f"作曲家 {q('SELECT COUNT(DISTINCT composer) FROM works')}")
    fmts = conn.execute("SELECT format, COUNT(*) n FROM tracks GROUP BY format ORDER BY n DESC").fetchall()
    print("格式：" + "，".join(f"{r['format']} {r['n']}" for r in fmts))


async def cmd_stats(args, s) -> None:
    _print_stats(connect(s.db_path))


async def cmd_show(args, s) -> None:
    conn = connect(s.db_path)
    if args.query.isdigit():
        albums = conn.execute("SELECT * FROM albums WHERE id=?", (int(args.query),)).fetchall()
    else:
        like = f"%{args.query}%"
        albums = conn.execute(
            "SELECT * FROM albums WHERE title LIKE ? OR folder_path LIKE ? OR album_artist LIKE ? ORDER BY title LIMIT 20",
            (like, like, like),
        ).fetchall()
    if not albums:
        print("没有找到")
    if len(albums) > 1:
        for a in albums:
            print(f"  [{a['id']}] {a['title']}  —  {a['album_artist'] or ''}  ({a['folder_path']})")
        return
    for a in albums:
        flag = "  [元数据不全]" if a["incomplete"] else ""
        print(f"[{a['id']}] {a['title']}{flag}")
        dash = lambda v: v if v not in (None, "") else "—"  # noqa: E731
        print(f"    艺术家 {dash(a['album_artist'])} · 厂牌 {dash(a['label'])} · 年份 {dash(a['year'])} · {a['disc_count']} 碟 · 封面 {dash(a['cover_path'])}")
        print(f"    {a['folder_path']}")
        tracks = conn.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY disc_no, track_no, id", (a["id"],)).fetchall()
        works = {w["id"]: w for w in conn.execute("SELECT * FROM works WHERE album_id=?", (a["id"],))}
        last = object()
        for t in tracks:
            if t["work_id"] != last:
                last = t["work_id"]
                w = works.get(t["work_id"])
                print(f"\n  {w['composer'] or '?'} — {w['title']}" + (f"  [{w['catalogue']}]" if w["catalogue"] else "") if w else "\n  （未归入作品）")
            dur = f"{int(t['duration'] // 60)}:{int(t['duration'] % 60):02d}" if t["duration"] else "?"
            tech = f"{t['format']} {t['sample_rate'] or '?'}Hz/{t['bit_depth'] or '?'}bit"
            name = f"{t['movement_no']}. {t['movement_name']}" if t["movement_no"] else (t["movement_name"] or t["title"])
            cue = f" @{t['start_offset']:.2f}s" if t["start_offset"] is not None else ""
            inc = " *" if t["incomplete"] else ""
            print(f"    {t['disc_no']}-{t['track_no'] or '?':>2}  {name}  ({dur}, {tech}){cue}{inc}")
            if t["performer"] or t["conductor"]:
                print(f"          {t['performer'] or ''}" + (f" / 指挥 {t['conductor']}" if t["conductor"] else ""))


async def cmd_zips(args, s) -> None:
    path = s.data_dir / "zip_list.txt"
    print(path.read_text("utf-8") if path.exists() else "尚未扫描")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pan")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config", help="显示当前配置（不含密钥）")
    sub.add_parser("auth", help="设备码授权")
    p = sub.add_parser("whoami", help="查看账号与 token 状态")
    p.add_argument("--refresh", action="store_true", help="强制刷新 access_token")
    p = sub.add_parser("ls", help="列出曲库前 N 个文件")
    p.add_argument("path", nargs="?")
    p.add_argument("-n", type=int, default=50)
    p = sub.add_parser("speedtest", help="列出前 50 个文件并完整下载一首 FLAC 测速")
    p.add_argument("--fsid", type=int)
    p.add_argument("--keep", action="store_true", help="保留下载的文件")
    sub.add_parser("probe", help="打印接口原始返回结构，核对接口行为")
    sub.add_parser("scan", help="增量扫描曲库")
    sub.add_parser("rescan", help="全量重新读取所有元数据与封面")
    sub.add_parser("stats", help="索引统计")
    p = sub.add_parser("show", help="查看专辑（id 或关键字）")
    p.add_argument("query")
    sub.add_parser("zips", help="列出曲库中的压缩包")
    p = sub.add_parser("diag", help="测试不同路径/接口的访问权限")
    p.add_argument("paths", nargs="*")
    args = parser.parse_args()

    s = get_settings()
    setup_logging(s.log_level)
    if args.cmd == "config":
        print(f"MUSIC_ROOT   = {s.music_root}")
        print(f"DATA_DIR     = {s.data_dir}")
        print(f"CACHE_MAX_GB = {s.cache_max_gb}")
        print(f"AppKey 已配置: {bool(s.baidu_app_key.get_secret_value())}")
        print(f"已授权       : {s.token_path.exists()}")
        return

    handler = globals()[f"cmd_{args.cmd}"]
    try:
        asyncio.run(handler(args, s))
    except KeyboardInterrupt:
        sys.exit(130)
    except BaiduError as e:
        print(redact(f"\n错误：{e}" + (f"（request_id={e.request_id}）" if e.request_id else "")), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(redact(f"\n未预期的错误：{e!r}"), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
