import { useMemo, useState } from "react";
import { api, toQueue, type AlbumDetail, type Track, type Work } from "../api";
import { Cover } from "../components/Cover";
import { DeleteAlbumButton } from "../components/DeleteAlbum";
import { Lightbox, slidesFor } from "../components/Lightbox";
import { PlayIcon } from "../components/icons";
import { useFetch } from "../data";
import { fmtDuration, fmtTech, roman } from "../format";
import { player } from "../player/engine";
import { usePlayer } from "../player/hooks";
import { albumsBackHref, href } from "../router";

type Block = { kind: "work"; work: Work; tracks: Track[] } | { kind: "single"; track: Track };

function blocks(tracks: Track[], works: Map<number, Work>): Block[] {
  const out: Block[] = [];
  for (const t of tracks) {
    const w = t.work_id != null ? works.get(t.work_id) : undefined;
    const last = out[out.length - 1];
    if (w && last?.kind === "work" && last.work.id === w.id) last.tracks.push(t);
    else if (w) out.push({ kind: "work", work: w, tracks: [t] });
    else out.push({ kind: "single", track: t });
  }
  return out;
}

export function AlbumPage({ id }: { id: number }) {
  const { data: a, error } = useFetch(() => api.album(id), [id]);
  const { data: media } = useFetch(() => api.media(id), [id]);
  const [viewer, setViewer] = useState(false);
  const s = usePlayer();
  const playingId = s.queue[s.index]?.id;

  const works = useMemo(() => new Map((a?.works ?? []).map((w) => [w.id, w])), [a]);
  const discs = useMemo(() => {
    const m = new Map<number, Track[]>();
    for (const t of a?.tracks ?? []) m.set(t.disc_no, [...(m.get(t.disc_no) ?? []), t]);
    return [...m.entries()].sort((x, y) => x[0] - y[0]);
  }, [a]);

  if (error) return <p className="py-20 font-sans text-sm text-muted">无法加载专辑：{error}</p>;
  if (!a) return <p className="py-20 font-sans text-sm text-muted">加载中…</p>;

  const queue = toQueue(a.tracks, { title: a.title, cover_path: a.cover_path }, works);
  const playFrom = (t: Track) => player.playQueue(queue, a.tracks.indexOf(t));
  const sub = (ts: Track[]) => queue.filter((q) => ts.some((t) => t.id === q.id));
  const total = a.tracks.reduce((x, t) => x + (t.duration ?? 0), 0);
  const techs = new Map<string, number>();
  for (const t of a.tracks) {
    const k = fmtTech(t.format, t.sample_rate, t.bit_depth);
    techs.set(k, (techs.get(k) ?? 0) + 1);
  }
  const mainTech = [...techs.entries()].sort((x, y) => y[1] - x[1])[0]?.[0];
  const composers = [...new Set(a.works.map((w) => w.composer).filter(Boolean))] as string[];

  return (
    <>
    <a href={albumsBackHref()} className="label mb-5 inline-block hover:text-ink">
      ← 返回专辑
    </a>
    <article className="grid gap-x-12 gap-y-8 md:grid-cols-[minmax(0,360px)_minmax(0,1fr)] lg:grid-cols-[minmax(0,440px)_minmax(0,1fr)]">
      <div className="md:sticky md:top-24 md:self-start">
        <button
          className="block w-full cursor-zoom-in"
          onClick={() => slidesFor(media ?? [], a.cover_large).length > 0 && setViewer(true)}
          title="浏览专辑图片"
        >
          <Cover path={a.cover_large} alt={a.title} className="shadow-[0_1px_0_var(--rule)]" />
        </button>
        {media && media.length > 0 && (
          <button className="mt-2 font-sans text-xs text-muted hover:text-ink" onClick={() => setViewer(true)}>
            查看小册子与图片（{media.length}）
          </button>
        )}
        {viewer && <Lightbox slides={slidesFor(media ?? [], a.cover_large)} onClose={() => setViewer(false)} />}
        <dl className="mt-6 grid grid-cols-[5.5rem_1fr] gap-y-1.5 font-sans text-xs">
          {a.label && <Row k="厂牌" v={a.label} link={href.albums({ label: a.label })} />}
          {a.year && <Row k="年份" v={String(a.year)} />}
          <Row k="碟数" v={`${a.disc_count} · ${a.tracks.length} 轨 · ${fmtDuration(total)}`} />
          {mainTech && <Row k="格式" v={techs.size > 1 ? `${mainTech} 等` : mainTech} />}
          <Row k="位置" v={a.folder_path} mono />
        </dl>
        {a.incomplete ? (
          <p className="mt-4 border-l-2 border-accent pl-3 font-sans text-xs leading-relaxed text-ink-2">
            元数据不全：部分曲目缺少作曲家或标题标签，以下信息有一部分是按文件名推测的（标 * 的曲目）。
          </p>
        ) : null}
        <DeleteAlbumButton albumId={a.id} />
      </div>

      <div className="min-w-0">
        {composers.length > 0 && (
          <div className="label">
            {composers.map((c, i) => (
              <span key={c}>
                {i > 0 && " · "}
                <a href={href.composer(c)} className="hover:text-ink">
                  {c}
                </a>
              </span>
            ))}
          </div>
        )}
        <h1 className="mt-2 text-3xl leading-tight tracking-tight md:text-4xl">{a.title}</h1>
        {a.album_artist && (
          <p className="mt-2 text-lg text-ink-2">
            <a href={href.albums({ performer: a.album_artist })} className="hover:underline">
              {a.album_artist}
            </a>
          </p>
        )}
        <div className="mt-6 flex gap-3 font-sans text-sm">
          <button className="flex items-center gap-2 bg-ink px-4 py-1.5 text-paper hover:opacity-85" onClick={() => player.playQueue(queue, 0)}>
            <PlayIcon className="h-3 w-3" /> 播放全部
          </button>
          <button className="border border-rule px-4 py-1.5 hover:border-ink" onClick={() => player.append(queue)}>
            加入队列
          </button>
        </div>

        <div className="mt-10">
          {discs.map(([disc, tracks]) => (
            <section key={disc} className="mb-10">
              {discs.length > 1 && <h2 className="label mb-2 border-b border-rule pb-2">第 {disc} 碟</h2>}
              {blocks(tracks, works).map((b) =>
                b.kind === "work" ? (
                  <WorkBlock
                    key={`w${b.work.id}`}
                    work={b.work}
                    tracks={b.tracks}
                    playingId={playingId}
                    mainTech={mainTech}
                    onPlay={playFrom}
                    onQueue={() => player.append(sub(b.tracks))}
                  />
                ) : (
                  <TrackRow key={b.track.id} t={b.track} playing={playingId === b.track.id} mainTech={mainTech} onPlay={playFrom} solo />
                ),
              )}
            </section>
          ))}
        </div>
      </div>
    </article>
    </>
  );
}

function Row({ k, v, link, mono }: { k: string; v: string; link?: string; mono?: boolean }) {
  return (
    <>
      <dt className="text-muted">{k}</dt>
      <dd className={`min-w-0 break-words ${mono ? "text-[11px] text-muted" : "text-ink-2"}`}>
        {link ? (
          <a href={link} className="hover:underline">
            {v}
          </a>
        ) : (
          v
        )}
      </dd>
    </>
  );
}

function WorkBlock({
  work,
  tracks,
  playingId,
  mainTech,
  onPlay,
  onQueue,
}: {
  work: Work;
  tracks: Track[];
  playingId?: number;
  mainTech?: string;
  onPlay: (t: Track) => void;
  onQueue: () => void;
}) {
  const total = tracks.reduce((x, t) => x + (t.duration ?? 0), 0);
  const single = tracks.length === 1 && !tracks[0].movement_name;
  return (
    <div className="group/work border-t border-rule py-4 first:border-t-0">
      <div className="flex items-baseline gap-3">
        <button className="min-w-0 flex-1 text-left" onClick={() => onPlay(tracks[0])}>
          {work.composer && <div className="label">{work.composer}</div>}
          <div className="mt-0.5 text-[17px] leading-snug hover:underline">{work.title}</div>
        </button>
        <div className="flex shrink-0 items-baseline gap-3 font-sans text-xs text-muted">
          <button className="opacity-0 hover:text-ink group-hover/work:opacity-100" onClick={onQueue}>
            加入队列
          </button>
          {work.composer && (
            <a className="opacity-0 hover:text-ink group-hover/work:opacity-100" href={href.composer(work.composer)} title="同一作品的其他录音">
              其他录音
            </a>
          )}
          <span className="tabular">{fmtDuration(total)}</span>
        </div>
      </div>
      {single ? (
        <TrackRow t={tracks[0]} playing={playingId === tracks[0].id} mainTech={mainTech} onPlay={onPlay} hidden />
      ) : (
        <ol className="mt-2">
          {tracks.map((t) => (
            <TrackRow key={t.id} t={t} playing={playingId === t.id} mainTech={mainTech} onPlay={onPlay} />
          ))}
        </ol>
      )}
    </div>
  );
}

function TrackRow({
  t,
  playing,
  mainTech,
  onPlay,
  solo,
  hidden,
}: {
  t: Track;
  playing: boolean;
  mainTech?: string;
  onPlay: (t: Track) => void;
  solo?: boolean;
  hidden?: boolean;
}) {
  const tech = fmtTech(t.format, t.sample_rate, t.bit_depth);
  const name = t.movement_name ?? t.title;
  if (hidden)
    return (
      <div className="mt-1 flex justify-end font-sans text-[11px] text-muted">
        {playing && <span className="mr-auto text-accent">正在播放</span>}
        {tech !== mainTech && tech}
      </div>
    );
  return (
    <li
      className={`group flex cursor-pointer items-baseline gap-3 py-1.5 ${solo ? "border-t border-rule py-3 first:border-t-0" : "pl-1"} ${playing ? "text-accent" : ""}`}
      onClick={() => onPlay(t)}
    >
      <span className="w-8 shrink-0 text-right font-sans text-xs tabular text-muted">
        {playing ? "▸" : solo ? (t.track_no ?? "") : t.movement_no ? roman(t.movement_no) : (t.track_no ?? "")}
      </span>
      <span className={`min-w-0 flex-1 group-hover:underline ${solo ? "text-[16px]" : "text-[15px]"}`}>
        {name}
        {t.incomplete ? <span className="ml-1 text-accent">*</span> : null}
        {solo && t.performer && <span className="ml-2 font-sans text-xs text-muted">{t.performer}</span>}
      </span>
      {tech !== mainTech && <span className="hidden font-sans text-[11px] text-muted sm:inline">{tech}</span>}
      <span className="w-12 shrink-0 text-right font-sans text-xs tabular text-muted">{fmtDuration(t.duration)}</span>
    </li>
  );
}

export type { AlbumDetail };
