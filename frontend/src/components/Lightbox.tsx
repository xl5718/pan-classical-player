import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { MediaItem } from "../api";
import { CloseIcon } from "./icons";

export type Slide = { key: string; name: string; kind: "image" | "pdf"; src: string; thumb: string };

export function slidesFor(media: MediaItem[], coverLarge: string | null): Slide[] {
  const slides: Slide[] = media.map((m) => ({
    key: String(m.fsid),
    name: m.path,
    kind: m.kind,
    src: `/api/media/${m.fsid}`,
    thumb: m.kind === "image" ? `/api/media/${m.fsid}?thumb=true` : "",
  }));
  const firstIsCover = media[0]?.kind === "image" && /cover|front|folder|封面/i.test(media[0].name);
  if (coverLarge && !firstIsCover) {
    slides.unshift({ key: "cover", name: "封面", kind: "image", src: `/covers/${coverLarge}`, thumb: `/covers/${coverLarge}` });
  }
  return slides;
}

/** 专辑图片浏览：← → 切换，Esc 关闭，点击图片在适合窗口与原始大小之间切换 */
export function Lightbox({ slides, start = 0, onClose }: { slides: Slide[]; start?: number; onClose: () => void }) {
  const [i, setI] = useState(start);
  const [zoom, setZoom] = useState(false);
  const [loading, setLoading] = useState(true);
  const stripRef = useRef<HTMLDivElement>(null);
  const s = slides[i];
  const go = (d: number) => setI((x) => Math.min(slides.length - 1, Math.max(0, x + d)));

  useEffect(() => {
    // 捕获阶段拦截按键，避免触发播放器的空格/方向键快捷键
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
      else if (e.key !== " ") return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", k, true);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", k, true);
      document.body.style.overflow = prev;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setZoom(false);
    setLoading(s?.kind === "image");
    stripRef.current?.querySelector(`[data-i="${i}"]`)?.scrollIntoView({ inline: "center", block: "nearest" });
    const n = slides[i + 1];
    if (n?.kind === "image") new Image().src = n.src; // 预先加载下一张
  }, [i, s, slides]);

  if (!s) return null;
  // 挂到 body 上，避免被页面顶栏和播放条的层级压住
  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-[#11100e]/[0.98] text-[#ece7dc]" onClick={onClose}>
      <div className="flex items-center gap-4 px-5 py-3 font-sans text-xs" onClick={(e) => e.stopPropagation()}>
        <span className="tabular text-[#8d8578]">
          {i + 1} / {slides.length}
        </span>
        <span className="min-w-0 flex-1 truncate">{s.name}</span>
        {s.kind === "pdf" && (
          <a href={s.src} target="_blank" rel="noreferrer" className="underline">
            在新窗口打开
          </a>
        )}
        <button className="text-[#8d8578] hover:text-white" onClick={onClose} aria-label="关闭">
          <CloseIcon className="h-4 w-4" />
        </button>
      </div>

      <div className="relative min-h-0 flex-1" onClick={(e) => e.stopPropagation()}>
        {s.kind === "pdf" ? (
          <iframe src={s.src} title={s.name} className="h-full w-full bg-white" />
        ) : (
          <div className={`h-full w-full ${zoom ? "overflow-auto" : "flex items-center justify-center overflow-hidden"}`}>
            <img
              key={s.key}
              src={s.src}
              alt={s.name}
              onLoad={() => setLoading(false)}
              onError={() => setLoading(false)}
              onClick={() => setZoom((z) => !z)}
              className={zoom ? "mx-auto max-w-none cursor-zoom-out" : "max-h-full max-w-full cursor-zoom-in object-contain px-14"}
            />
          </div>
        )}
        {loading && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center font-sans text-xs text-[#8d8578]">
            正在从网盘读取…
          </div>
        )}
        {i > 0 && (
          <button className="absolute left-2 top-1/2 -translate-y-1/2 px-3 py-6 text-3xl text-[#8d8578] hover:text-white" onClick={() => go(-1)} aria-label="上一张">
            ‹
          </button>
        )}
        {i < slides.length - 1 && (
          <button className="absolute right-2 top-1/2 -translate-y-1/2 px-3 py-6 text-3xl text-[#8d8578] hover:text-white" onClick={() => go(1)} aria-label="下一张">
            ›
          </button>
        )}
      </div>

      {slides.length > 1 && (
        <div ref={stripRef} className="flex gap-2 overflow-x-auto px-5 py-3" onClick={(e) => e.stopPropagation()}>
          {slides.map((x, j) => (
            <button
              key={x.key}
              data-i={j}
              onClick={() => setI(j)}
              className={`h-16 w-16 shrink-0 overflow-hidden border ${j === i ? "border-[#ece7dc]" : "border-transparent opacity-60 hover:opacity-100"}`}
              title={x.name}
            >
              {x.kind === "image" ? (
                <img src={x.thumb} alt="" loading="lazy" className="h-full w-full object-cover" />
              ) : (
                <span className="grid h-full w-full place-items-center bg-[#2a2723] font-sans text-[10px]">PDF</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>,
    document.body,
  );
}
