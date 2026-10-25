import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type DeletePlan } from "../api";
import { invalidateAlbums } from "../data";
import { player } from "../player/engine";
import { albumsBackHref } from "../router";
import { CloseIcon } from "./icons";

const MB = (n: number) => (n > 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${(n / 1e6).toFixed(0)} MB`);

export function DeleteAlbumButton({ albumId }: { albumId: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="mt-8 font-sans text-xs text-muted underline-offset-2 hover:text-accent hover:underline" onClick={() => setOpen(true)}>
        删除专辑…
      </button>
      {open && <DeleteDialog albumId={albumId} onClose={() => setOpen(false)} />}
    </>
  );
}

function DeleteDialog({ albumId, onClose }: { albumId: number; onClose: () => void }) {
  const [plan, setPlan] = useState<DeletePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    api.deletePlan(albumId).then(setPlan).catch((e: Error) => setError(e.message));
  }, [albumId]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [busy, onClose]);

  // 防止误触：计划出来 1.5 秒后才允许确认
  useEffect(() => {
    if (!plan) return;
    const id = setTimeout(() => setArmed(true), 1500);
    return () => clearTimeout(id);
  }, [plan]);

  const confirm = async () => {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.deleteAlbum(albumId, plan.token);
      if (r.failed.length) {
        setError(`有 ${r.failed.length} 项删除失败：${r.failed.map((f) => `${f.path}（errno ${f.errno}）`).join("；")}`);
        setBusy(false);
        return;
      }
      player.removeAlbum(albumId);
      invalidateAlbums();
      location.hash = albumsBackHref();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/30 p-4" onClick={() => !busy && onClose()}>
      <div className="w-full max-w-xl border border-rule bg-paper p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4">
          <h2 className="text-xl">从百度网盘删除这张专辑？</h2>
          <button className="text-muted hover:text-ink" onClick={onClose} disabled={busy} aria-label="关闭">
            <CloseIcon />
          </button>
        </div>
        {!plan && !error && <p className="mt-4 font-sans text-sm text-muted">正在核对网盘上的文件…</p>}
        {plan && (
          <div className="mt-4 font-sans text-sm leading-relaxed text-ink-2">
            {plan.mode === "folder" ? (
              <p>
                将删除整个文件夹（共 {plan.file_count} 个文件，{MB(plan.total_size)}）：
                <br />
                <code className="break-all text-[12px] text-ink">{plan.folder}</code>
              </p>
            ) : (
              <p>
                这个文件夹里还有其他专辑
                {plan.nested_albums.length ? `（${plan.nested_albums.length} 张）` : ""}，因此只删除属于本专辑的 {plan.file_count} 个文件（
                {MB(plan.total_size)}），文件夹本身保留。
              </p>
            )}
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-muted">查看文件清单</summary>
              <ul className="mt-2 max-h-48 overflow-y-auto border-l border-rule pl-3 text-[12px] text-muted">
                {plan.files.map((f) => (
                  <li key={f} className="break-all">
                    {f}
                  </li>
                ))}
                {plan.file_count > plan.files.length && <li>……另有 {plan.file_count - plan.files.length} 个</li>}
              </ul>
            </details>
            <p className="mt-3 text-xs text-muted">
              删除的文件应进入百度网盘回收站，保留期内可以恢复（第一次使用后建议去网盘回收站确认一下）。本地的索引、封面和缓存会一并清除。
            </p>
          </div>
        )}
        {error && <p className="mt-4 border-l-2 border-accent pl-3 font-sans text-sm text-accent">{error}</p>}
        <div className="mt-6 flex justify-end gap-3 font-sans text-sm">
          <button className="border border-rule px-4 py-1.5 hover:border-ink" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            className="bg-accent px-4 py-1.5 text-paper hover:opacity-90 disabled:opacity-40"
            onClick={confirm}
            disabled={!plan || !armed || busy}
          >
            {busy ? "正在删除…" : "删除"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
