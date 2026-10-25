import { useMemo, useState } from "react";
import { api } from "../api";
import { useFetch } from "../data";
import { href } from "../router";

const fold = (s: string) => s.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();

export function ComposersPage() {
  const { data, error } = useFetch(() => api.composers(), []);
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const m = new Map<string, NonNullable<typeof data>>();
    for (const c of data ?? []) {
      if (q && !fold(c.name).includes(fold(q))) continue;
      const first = c.sort[0]?.toUpperCase() ?? "#";
      const key = /[A-Z]/.test(first) ? first : "#";
      m.set(key, [...(m.get(key) ?? []), c]);
    }
    return [...m.entries()].sort((a, b) => (a[0] === "#" ? 1 : b[0] === "#" ? -1 : a[0].localeCompare(b[0])));
  }, [data, q]);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-6 border-b border-rule pb-5">
        <div>
          <h1 className="text-3xl tracking-tight">作曲家</h1>
          <p className="label mt-1">{data ? `${data.length} 位 · 按姓氏排列` : error ? `加载失败：${error}` : "加载中…"}</p>
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="筛选"
          className="w-56 border-0 border-b border-rule bg-transparent pb-1 outline-none focus:border-ink"
        />
      </div>
      <nav className="sticky top-[57px] z-10 -mx-1 flex flex-wrap gap-x-2 bg-paper/95 py-2 font-sans text-xs text-muted">
        {groups.map(([k]) => (
          <a key={k} href={`#/composers`} onClick={(e) => { e.preventDefault(); document.getElementById(`c-${k}`)?.scrollIntoView({ behavior: "smooth" }); }} className="px-1 hover:text-ink">
            {k}
          </a>
        ))}
      </nav>
      <div className="mt-4 columns-1 gap-12 sm:columns-2 lg:columns-3">
        {groups.map(([k, list]) => (
          <section key={k} id={`c-${k}`} className="mb-8 break-inside-avoid">
            <h2 className="mb-2 border-b border-rule pb-1 text-2xl text-muted">{k}</h2>
            <ul>
              {list.map((c) => (
                <li key={c.name} className="flex items-baseline justify-between gap-3 py-0.5">
                  <a href={href.composer(c.name)} className={`truncate hover:underline ${c.known ? "" : "text-ink-2"}`}>
                    {c.name}
                  </a>
                  <span className="shrink-0 font-sans text-[11px] tabular text-muted">
                    {c.recordings} · {c.albums} 张
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
