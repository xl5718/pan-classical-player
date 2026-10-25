import { useEffect, useMemo, useState } from "react";
import { api, type AlbumSummary } from "../api";
import { Cover } from "../components/Cover";
import { useAlbums } from "../data";
import { surname } from "../format";
import { href, replaceAlbumParams } from "../router";

type Sort = "title" | "composer" | "artist" | "year" | "added";
const SORTS: [Sort, string][] = [
  ["title", "标题"],
  ["composer", "作曲家"],
  ["artist", "演奏者"],
  ["year", "年份"],
  ["added", "最近加入"],
];

function counts(albums: AlbumSummary[], pick: (a: AlbumSummary) => (string | null | undefined)[]) {
  const m = new Map<string, number>();
  for (const a of albums) for (const v of new Set(pick(a))) if (v) m.set(v, (m.get(v) ?? 0) + 1);
  return [...m.entries()].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
}

const cmp = (a: string, b: string) => a.localeCompare(b, ["en", "zh-Hans-CN"], { sensitivity: "base" });

export function AlbumWall({ params }: { params: URLSearchParams }) {
  const { albums, error } = useAlbums();
  const q = params.get("q") ?? "";
  const composer = params.get("composer") ?? "";
  const performer = params.get("performer") ?? "";
  const label = params.get("label") ?? "";
  const decade = params.get("decade") ?? "";
  const sort = (params.get("sort") as Sort) || "title";
  const [input, setInput] = useState(q);
  const [hits, setHits] = useState<Set<number> | null>(null);
  const [showFilters, setShowFilters] = useState(false);

  const update = (patch: Record<string, string>) =>
    replaceAlbumParams({ q, composer, performer, label, decade, sort: sort === "title" ? "" : sort, ...patch });

  useEffect(() => {
    const id = setTimeout(() => {
      if (input !== q) update({ q: input });
    }, 250);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  useEffect(() => {
    if (!q.trim()) {
      setHits(null);
      return;
    }
    let alive = true;
    api.search(q).then((r) => alive && setHits(new Set(r.album_ids)));
    return () => {
      alive = false;
    };
  }, [q]);

  const options = useMemo(() => {
    if (!albums) return null;
    return {
      composers: counts(albums, (a) => a.composers),
      performers: counts(albums, (a) => [a.album_artist, ...a.performers]).filter(([, n]) => n > 1 || albums.length < 200),
      labels: counts(albums, (a) => [a.label]),
      decades: counts(albums, (a) => [a.year ? `${Math.floor(a.year / 10) * 10}` : null]).sort((x, y) => +x[0] - +y[0]),
    };
  }, [albums]);

  const shown = useMemo(() => {
    if (!albums) return [];
    let list = albums.filter(
      (a) =>
        (!hits || hits.has(a.id)) &&
        (!composer || a.composers.includes(composer)) &&
        (!performer || a.album_artist === performer || a.performers.includes(performer)) &&
        (!label || a.label === label) &&
        (!decade || (a.year && Math.floor(a.year / 10) * 10 === +decade)),
    );
    const key: Record<Sort, (a: AlbumSummary, b: AlbumSummary) => number> = {
      title: (a, b) => cmp(a.title, b.title),
      composer: (a, b) => cmp(surname(a.composers[0] ?? "~"), surname(b.composers[0] ?? "~")) || cmp(a.title, b.title),
      artist: (a, b) => cmp(a.album_artist ?? "~", b.album_artist ?? "~") || cmp(a.title, b.title),
      year: (a, b) => (a.year ?? 9999) - (b.year ?? 9999) || cmp(a.title, b.title),
      added: (a, b) => b.added - a.added,
    };
    list = [...list].sort(key[sort]);
    return list;
  }, [albums, hits, composer, performer, label, decade, sort]);

  const active = [
    composer && ["composer", composer],
    performer && ["performer", performer],
    label && ["label", label],
    decade && ["decade", `${decade} 年代`],
  ].filter(Boolean) as [string, string][];

  return (
    <div>
      <div className="flex flex-wrap items-end gap-x-8 gap-y-4 border-b border-rule pb-5">
        <div className="min-w-[16rem] flex-1">
          <label className="label" htmlFor="q">
            搜索
          </label>
          <input
            id="q"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="作曲家、作品、演奏者、厂牌……"
            className="mt-1 w-full border-0 border-b border-rule bg-transparent pb-1 text-xl outline-none placeholder:text-muted/60 focus:border-ink"
          />
        </div>
        <button className="font-sans text-xs text-ink-2 underline md:hidden" onClick={() => setShowFilters((v) => !v)}>
          {showFilters ? "收起筛选" : "筛选与排序"}
        </button>
        {options && (
          <div className={`${showFilters ? "flex" : "hidden"} flex-wrap items-end gap-x-6 gap-y-3 md:flex`}>
            <Filter title="作曲家" value={composer} onChange={(v) => update({ composer: v })} options={options.composers} />
            <Filter title="演奏者" value={performer} onChange={(v) => update({ performer: v })} options={options.performers} />
            <Filter title="厂牌" value={label} onChange={(v) => update({ label: v })} options={options.labels} />
            <Filter
              title="年代"
              value={decade}
              onChange={(v) => update({ decade: v })}
              options={options.decades}
              render={(v) => `${v}s`}
            />
            <div>
              <div className="label">排序</div>
              <select className="plain mt-1" value={sort} onChange={(e) => update({ sort: e.target.value === "title" ? "" : e.target.value })}>
                {SORTS.map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2 font-sans text-xs text-muted">
        <span className="tabular">
          {albums ? `${shown.length} / ${albums.length} 张` : error ? `加载失败：${error}` : "加载中…"}
        </span>
        {active.map(([k, v]) => (
          <button key={k} onClick={() => update({ [k]: "" })} className="border border-rule px-2 py-0.5 text-ink-2 hover:border-ink">
            {v} ×
          </button>
        ))}
        {(active.length > 0 || q) && (
          <button
            className="underline"
            onClick={() => {
              setInput("");
              replaceAlbumParams({ sort: sort === "title" ? "" : sort });
            }}
          >
            清除全部
          </button>
        )}
      </div>

      <ul className="mt-6 grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-x-6 gap-y-9 md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]">
        {shown.map((a) => (
          <li key={a.id} className="card-auto">
            <a href={href.album(a.id)} className="group block">
              <Cover path={a.cover_path} className="shadow-[0_1px_0_var(--rule)] transition-opacity group-hover:opacity-90" />
              <div className="mt-2.5">
                {a.composers.length > 0 && (
                  <div className="label truncate">
                    {a.composers.slice(0, 2).map(surname).join(" · ")}
                    {a.composers.length > 2 ? " …" : ""}
                  </div>
                )}
                <div className="mt-0.5 line-clamp-2 text-[15px] leading-snug group-hover:underline">{a.title}</div>
                <div className="mt-0.5 truncate font-sans text-xs text-ink-2">{a.album_artist}</div>
                <div className="truncate font-sans text-[11px] text-muted">
                  {[a.year, a.label].filter(Boolean).join(" · ")}
                </div>
              </div>
            </a>
          </li>
        ))}
      </ul>
      {albums && !shown.length && <p className="py-20 text-center font-sans text-sm text-muted">没有符合条件的专辑。</p>}
    </div>
  );
}

function Filter({
  title,
  value,
  onChange,
  options,
  render = (v) => v,
}: {
  title: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, number][];
  render?: (v: string) => string;
}) {
  return (
    <div>
      <div className="label">{title}</div>
      <select className="plain mt-1" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">全部</option>
        {options.map(([v, n]) => (
          <option key={v} value={v}>
            {render(v)}（{n}）
          </option>
        ))}
      </select>
    </div>
  );
}
