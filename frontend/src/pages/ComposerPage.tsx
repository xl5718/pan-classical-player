import { useEffect, useMemo, useState } from "react";
import { api, type QTrack, type Recording, type WorkGroup } from "../api";
import { Cover } from "../components/Cover";
import { PlayIcon } from "../components/icons";
import { useFetch } from "../data";
import { fmtDuration, fmtTech, roman } from "../format";
import { player } from "../player/engine";
import { usePlayer } from "../player/hooks";
import { href } from "../router";

function recQueue(r: Recording, w: WorkGroup): QTrack[] {
  return r.tracks.map((t) => ({ ...t, album_title: r.album_title, cover_path: r.cover_path, work_title: w.title }));
}

export function ComposerPage({ name, work }: { name: string; work?: string }) {
  const { data, error } = useFetch(() => api.composer(name), [name]);
  const [q, setQ] = useState("");
  const [onlyMulti, setOnlyMulti] = useState(false);
  const works = useMemo(
    () =>
      (data?.works ?? []).filter(
        (w) => (!onlyMulti || w.recordings.length > 1) && (!q || `${w.title} ${w.catalogue ?? ""}`.toLowerCase().includes(q.toLowerCase())),
      ),
    [data, q, onlyMulti],
  );
  const multi = data?.works.filter((w) => w.recordings.length > 1).length ?? 0;

  useEffect(() => {
    if (work && data) document.getElementById(`w-${work}`)?.scrollIntoView({ block: "start" });
  }, [work, data]);

  return (
    <div>
      <div className="border-b border-rule pb-5">
        <a href={href.composers()} className="label hover:text-ink">
          ← 作曲家
        </a>
        <h1 className="mt-2 text-4xl tracking-tight">{name}</h1>
        <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-3">
          <p className="label">
            {data ? `${data.works.length} 部作品 · ${multi} 部有多个录音` : error ? `加载失败：${error}` : "加载中…"}
          </p>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="筛选作品或作品号"
            className="w-56 border-0 border-b border-rule bg-transparent pb-1 font-sans text-sm outline-none focus:border-ink"
          />
          <label className="flex items-center gap-2 font-sans text-xs text-ink-2">
            <input type="checkbox" checked={onlyMulti} onChange={(e) => setOnlyMulti(e.target.checked)} />
            只看有多个录音的作品
          </label>
          <a href={href.albums({ composer: name })} className="font-sans text-xs text-ink-2 underline">
            查看相关专辑
          </a>
        </div>
      </div>
      <ol>
        {works.map((w) => (
          <WorkEntry key={w.key} w={w} open={works.length <= 3 || w.key === work} />
        ))}
      </ol>
    </div>
  );
}

function WorkEntry({ w, open: initial }: { w: WorkGroup; open: boolean }) {
  const [open, setOpen] = useState(initial);
  const [compare, setCompare] = useState(false);
  const s = usePlayer();
  const cur = s.queue[s.index];
  const n = w.recordings.length;
  return (
    <li id={`w-${w.key}`} className="border-b border-rule">
      <button className="flex w-full items-baseline gap-4 py-3.5 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="w-24 shrink-0 font-sans text-xs tabular text-muted">{w.catalogue}</span>
        <span className="min-w-0 flex-1 text-[17px] leading-snug hover:underline">{w.title}</span>
        <span className={`shrink-0 font-sans text-xs ${n > 1 ? "text-accent" : "text-muted"}`}>{n > 1 ? `${n} 个录音` : "1 个录音"}</span>
        <span className="w-3 shrink-0 font-sans text-xs text-muted">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="pb-6 pl-0 md:pl-28">
          {n > 1 && (
            <div className="mb-3 flex gap-4 font-sans text-xs">
              <button className={compare ? "text-muted hover:text-ink" : "text-ink underline"} onClick={() => setCompare(false)}>
                录音列表
              </button>
              <button className={compare ? "text-ink underline" : "text-muted hover:text-ink"} onClick={() => setCompare(true)}>
                乐章时长对比
              </button>
            </div>
          )}
          {compare ? (
            <CompareTable w={w} />
          ) : (
            <ul>
              {w.recordings.map((r) => {
                const playing = cur && r.tracks.some((t) => t.id === cur.id);
                const t0 = r.tracks[0];
                return (
                  <li key={r.work_id} className={`group grid grid-cols-[40px_1fr_auto] items-center gap-4 py-2 ${playing ? "text-accent" : ""}`}>
                    <a href={href.album(r.album_id)} className="w-10">
                      <Cover path={r.cover_path} />
                    </a>
                    <div className="min-w-0">
                      <div className="truncate">
                        {r.performer ?? "—"}
                        {r.conductor && <span className="text-ink-2"> / {r.conductor}</span>}
                      </div>
                      <div className="truncate font-sans text-xs text-muted">
                        {[r.year, r.label].filter(Boolean).join(" · ")}
                        {(r.year || r.label) && " · "}
                        <a href={href.album(r.album_id)} className="hover:underline">
                          {r.album_title}
                        </a>
                        {t0 && ` · ${fmtTech(t0.format, t0.sample_rate, t0.bit_depth)}`}
                      </div>
                    </div>
                    <div className="flex items-center gap-4 font-sans text-xs text-muted">
                      <button className="opacity-0 hover:text-ink group-hover:opacity-100" onClick={() => player.append(recQueue(r, w))}>
                        加入队列
                      </button>
                      <span className="tabular">{fmtDuration(r.duration)}</span>
                      <button className="text-ink hover:text-accent" onClick={() => player.playQueue(recQueue(r, w), 0)} aria-label="播放">
                        <PlayIcon className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

/** 乐章为行、录音为列：比较不同版本的速度 */
function CompareTable({ w }: { w: WorkGroup }) {
  const recs = w.recordings;
  const rows = Math.max(...recs.map((r) => r.tracks.length));
  const ref = recs.reduce((a, b) => (b.tracks.length > a.tracks.length ? b : a));
  const cell = (r: Recording, i: number) => {
    const byNo = r.tracks.find((t) => t.movement_no === i + 1);
    return byNo ?? (r.tracks.length === rows ? r.tracks[i] : undefined);
  };
  const totals = recs.map((r) => r.duration);
  const min = Math.min(...totals);
  const max = Math.max(...totals);
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse font-sans text-xs tabular">
        <thead>
          <tr className="text-left align-bottom">
            <th className="sticky left-0 bg-paper pb-2 pr-4 font-normal text-muted">乐章</th>
            {recs.map((r) => (
              <th key={r.work_id} className="min-w-[7rem] pb-2 pr-4 font-normal">
                <a href={href.album(r.album_id)} className="hover:underline">
                  <div className="text-ink">{r.performer ?? "—"}</div>
                  {r.conductor && <div className="text-ink-2">{r.conductor}</div>}
                  <div className="text-muted">{r.year ?? ""}</div>
                </a>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, i) => {
            const label = ref.tracks[i]?.movement_name ?? ref.tracks[i]?.title ?? "";
            const vals = recs.map((r) => cell(r, i)?.duration ?? null);
            const present = vals.filter((v): v is number => v != null);
            const lo = Math.min(...present);
            const hi = Math.max(...present);
            return (
              <tr key={i} className="border-t border-rule">
                <td className="sticky left-0 max-w-[16rem] truncate bg-paper py-1.5 pr-4 font-serif text-[13px] text-ink-2">
                  {roman(i + 1)}. {label}
                </td>
                {recs.map((r, j) => {
                  const t = cell(r, i);
                  const v = vals[j];
                  return (
                    <td key={r.work_id} className="py-1.5 pr-4">
                      {t ? (
                        <button
                          className={`hover:underline ${present.length > 1 && v === lo ? "text-ink" : present.length > 1 && v === hi ? "text-accent" : "text-ink-2"}`}
                          onClick={() => player.playQueue(recQueue(r, w), r.tracks.indexOf(t))}
                        >
                          {fmtDuration(v)}
                        </button>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          <tr className="border-t border-ink/40">
            <td className="sticky left-0 bg-paper py-1.5 pr-4 text-muted">全曲</td>
            {recs.map((r) => (
              <td key={r.work_id} className={`py-1.5 pr-4 ${r.duration === max && max !== min ? "text-accent" : "text-ink"}`}>
                {fmtDuration(r.duration)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      <p className="mt-2 font-sans text-[11px] text-muted">每行最短的时长以深色、最长的以红色标出。点时长可从该乐章开始播放。</p>
    </div>
  );
}
