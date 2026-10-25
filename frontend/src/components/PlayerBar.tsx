import { useEffect, useState } from "react";
import { fmtDuration, fmtTech, surname, trackLabel } from "../format";
import { player } from "../player/engine";
import { usePlayer, usePosition } from "../player/hooks";
import { href } from "../router";
import { Cover } from "./Cover";
import { NextIcon, PauseIcon, PlayIcon, PrevIcon, QueueIcon, VolumeIcon } from "./icons";
import { QueuePanel } from "./QueuePanel";

function Progress() {
  const pos = usePosition();
  const dur = player.duration;
  const [drag, setDrag] = useState<number | null>(null);
  const shown = drag ?? pos;
  const pct = dur ? Math.min(100, (shown / dur) * 100) : 0;
  return (
    <div className="flex w-full items-center gap-3 font-sans text-[11px] text-muted tabular">
      <span className="w-10 text-right">{fmtDuration(shown)}</span>
      <input
        type="range"
        className="slider flex-1"
        min={0}
        max={dur || 1}
        step={0.1}
        value={Math.min(shown, dur || 1)}
        style={{ ["--fill" as string]: `${pct}%` }}
        aria-label="播放进度"
        onChange={(e) => setDrag(Number(e.target.value))}
        onPointerUp={() => {
          if (drag != null) player.seek(drag);
          setDrag(null);
        }}
        onKeyUp={() => {
          if (drag != null) player.seek(drag);
          setDrag(null);
        }}
      />
      <span className="w-10">{fmtDuration(dur)}</span>
    </div>
  );
}

export function PlayerBar() {
  const s = usePlayer();
  const t = s.queue[s.index] ?? null;
  const [queueOpen, setQueueOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.code === "Space") {
        e.preventDefault();
        player.toggle();
      } else if (e.code === "ArrowRight" && e.shiftKey) player.next();
      else if (e.code === "ArrowLeft" && e.shiftKey) player.prev();
      else if (e.code === "ArrowRight") player.seek(player.position + 10);
      else if (e.code === "ArrowLeft") player.seek(player.position - 10);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const volPct = (s.muted ? 0 : s.volume) * 100;
  return (
    <>
      {queueOpen && <QueuePanel onClose={() => setQueueOpen(false)} />}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-rule bg-paper/95 backdrop-blur supports-[backdrop-filter]:bg-paper/85">
        <div className="mx-auto grid max-w-[1400px] grid-cols-[1fr_auto] items-center gap-x-6 gap-y-1 px-4 py-2.5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,1fr)] md:px-8">
          <div className="flex min-w-0 items-center gap-3">
            {t ? (
              <a href={href.album(t.album_id)} className="w-11 shrink-0">
                <Cover path={t.cover_path} />
              </a>
            ) : (
              <div className="h-11 w-11 shrink-0 bg-panel" />
            )}
            <div className="min-w-0 leading-tight">
              {t ? (
                <>
                  <div className="truncate text-[15px]">{trackLabel(t)}</div>
                  <div className="truncate font-sans text-xs text-ink-2">
                    {t.composer && (
                      <a href={href.composer(t.composer)} className="hover:underline">
                        {surname(t.composer)}
                      </a>
                    )}
                    {t.composer && t.work_title && t.work_title !== trackLabel(t) ? " · " : ""}
                    {t.work_title !== trackLabel(t) && t.work_title}
                  </div>
                  <div className="truncate font-sans text-xs text-muted">
                    {[t.performer, t.conductor].filter(Boolean).join(" / ") || t.album_title}
                  </div>
                </>
              ) : (
                <div className="font-sans text-xs text-muted">未在播放</div>
              )}
            </div>
          </div>

          <div className="col-span-2 row-start-2 flex flex-col items-center gap-0.5 md:col-span-1 md:col-start-2 md:row-start-1">
            <div className="flex items-center gap-5">
              <button className="text-ink-2 hover:text-ink disabled:opacity-30" onClick={() => player.prev()} disabled={!t} aria-label="上一首">
                <PrevIcon />
              </button>
              <button
                className="grid h-9 w-9 place-items-center rounded-full border border-ink text-ink hover:bg-ink hover:text-paper disabled:opacity-30"
                onClick={() => player.toggle()}
                disabled={!t}
                aria-label={s.playing ? "暂停" : "播放"}
              >
                {s.playing ? <PauseIcon /> : <PlayIcon className="ml-0.5 h-4 w-4" />}
              </button>
              <button
                className="text-ink-2 hover:text-ink disabled:opacity-30"
                onClick={() => player.next()}
                disabled={!t || s.index + 1 >= s.queue.length}
                aria-label="下一首"
              >
                <NextIcon />
              </button>
            </div>
            <Progress />
          </div>

          <div className="col-start-2 row-start-1 flex items-center justify-end gap-4 md:col-start-3">
            <div className="hidden text-right font-sans text-[11px] leading-tight text-muted lg:block">
              {s.error ? (
                <span className="text-accent">{s.error}</span>
              ) : s.buffering && s.playing ? (
                "缓冲中…"
              ) : t ? (
                fmtTech(t.format, t.sample_rate, t.bit_depth)
              ) : null}
            </div>
            <div className="hidden items-center gap-2 md:flex">
              <button className="text-ink-2 hover:text-ink" onClick={() => player.toggleMute()} aria-label="静音">
                <VolumeIcon muted={s.muted || s.volume === 0} />
              </button>
              <input
                type="range"
                className="slider w-20"
                min={0}
                max={1}
                step={0.01}
                value={s.muted ? 0 : s.volume}
                style={{ ["--fill" as string]: `${volPct}%` }}
                onChange={(e) => player.setVolume(Number(e.target.value))}
                aria-label="音量"
              />
            </div>
            <button
              className={`relative hover:text-ink ${queueOpen ? "text-accent" : "text-ink-2"}`}
              onClick={() => setQueueOpen((v) => !v)}
              aria-label="播放队列"
            >
              <QueueIcon className="h-5 w-5" />
              {s.queue.length > 0 && (
                <span className="absolute -right-2 -top-1.5 font-sans text-[9px] tabular text-muted">{s.queue.length}</span>
              )}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
