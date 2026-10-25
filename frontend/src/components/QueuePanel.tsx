import { useEffect, useRef } from "react";
import { fmtDuration, surname, trackLabel } from "../format";
import { player, type GaplessScope } from "../player/engine";
import { usePlayer } from "../player/hooks";
import { CloseIcon } from "./icons";

export function QueuePanel({ onClose }: { onClose: () => void }) {
  const s = usePlayer();
  const curRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    curRef.current?.scrollIntoView({ block: "center" });
  }, []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const remaining = s.queue.slice(Math.max(s.index, 0)).reduce((a, t) => a + (t.duration ?? 0), 0);
  return (
    <aside className="fixed bottom-[76px] right-0 top-0 z-20 flex w-full max-w-md flex-col border-l border-rule bg-paper shadow-[0_0_40px_rgba(0,0,0,0.08)] md:bottom-[68px]">
      <header className="flex items-baseline justify-between border-b border-rule px-5 py-4">
        <div>
          <h2 className="text-lg">播放队列</h2>
          <div className="label mt-0.5">
            {s.queue.length} 首 · 剩余 {fmtDuration(remaining)}
          </div>
        </div>
        <div className="flex items-center gap-4 font-sans text-xs">
          {s.queue.length > 0 && (
            <button className="text-muted hover:text-ink" onClick={() => player.clear()}>
              清空
            </button>
          )}
          <button className="text-muted hover:text-ink" onClick={onClose} aria-label="关闭">
            <CloseIcon />
          </button>
        </div>
      </header>
      <div className="flex items-center gap-3 border-b border-rule px-5 py-2.5 font-sans text-xs text-muted">
        <span>无缝衔接</span>
        {([
          ["work", "同一作品内"],
          ["album", "整张专辑"],
          ["off", "关闭"],
        ] as [GaplessScope, string][]).map(([v, l]) => (
          <button key={v} onClick={() => player.setGapless(v)} className={s.gapless === v ? "text-ink underline" : "hover:text-ink"}>
            {l}
          </button>
        ))}
      </div>
      <ol className="flex-1 overflow-y-auto py-2">
        {s.queue.map((t, i) => {
          const prev = s.queue[i - 1];
          const newWork = !prev || prev.work_id !== t.work_id || prev.album_id !== t.album_id || t.work_id == null;
          const cur = i === s.index;
          return (
            <li key={`${t.id}-${i}`} ref={cur ? curRef : undefined}>
              {newWork && (t.work_title || !prev || prev.album_id !== t.album_id) && (
                <div className="px-5 pb-1 pt-3 text-[13px] text-ink-2">
                  {t.composer && <span className="label mr-2">{surname(t.composer)}</span>}
                  {t.work_title ?? t.album_title}
                </div>
              )}
              <div
                className={`group relative flex items-center gap-3 px-5 py-1 font-sans text-[13px] ${cur ? "bg-accent-soft" : "hover:bg-panel"} ${i < s.index ? "text-muted" : ""}`}
              >
                {(player.joinedWithNext(i) || player.joinedWithNext(i - 1)) && (
                  <span
                    className={`absolute left-2.5 w-px bg-accent/60 ${player.joinedWithNext(i - 1) ? "top-0" : "top-1/2"} ${player.joinedWithNext(i) ? "bottom-0" : "bottom-1/2"}`}
                    title="无缝衔接"
                  />
                )}
                <button className="min-w-0 flex-1 truncate text-left" onClick={() => player.playIndex(i)}>
                  {cur && <span className="mr-1.5 text-accent">▸</span>}
                  {trackLabel(t)}
                </button>
                <span className="tabular text-[11px] text-muted">{fmtDuration(t.duration)}</span>
                <button
                  className="text-muted opacity-0 hover:text-ink group-hover:opacity-100"
                  onClick={() => player.remove(i)}
                  aria-label="移出队列"
                >
                  <CloseIcon className="h-3 w-3" />
                </button>
              </div>
            </li>
          );
        })}
        {!s.queue.length && <li className="px-5 py-10 font-sans text-sm text-muted">队列为空。在专辑或作曲家页面点选曲目开始播放。</li>}
      </ol>
    </aside>
  );
}
