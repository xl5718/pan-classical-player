import { useEffect, useLayoutEffect, useRef } from "react";
import { PlayerBar } from "./components/PlayerBar";
import { AlbumPage } from "./pages/AlbumPage";
import { AlbumWall } from "./pages/AlbumWall";
import { ComposerPage } from "./pages/ComposerPage";
import { ComposersPage } from "./pages/ComposersPage";
import { href, useRoute } from "./router";
import { useTheme, type ThemePref } from "./theme";

const THEMES: [ThemePref, string][] = [
  ["system", "跟随系统"],
  ["light", "浅色"],
  ["dark", "深色"],
];

export default function App() {
  const route = useRoute();
  const [theme, setTheme] = useTheme();
  const scrolls = useRef(new Map<string, number>());
  const key = route.name === "album" ? `a${route.id}` : route.name === "composer" ? `c${route.composer}` : route.name;

  // 返回专辑墙时回到原来的滚动位置；进入其他页面从顶部开始
  const prevKey = useRef(key);
  useLayoutEffect(() => {
    if (prevKey.current !== key) {
      scrolls.current.set(prevKey.current, window.scrollY);
      prevKey.current = key;
      const saved = route.name === "albums" ? scrolls.current.get(key) : undefined;
      requestAnimationFrame(() => window.scrollTo(0, saved ?? 0));
    }
  }, [key, route.name]);

  useEffect(() => {
    document.title = "唱片目录";
  }, []);

  const nav = (active: boolean) => `hover:text-ink ${active ? "text-ink" : "text-muted"}`;
  return (
    <div className="min-h-screen pb-36">
      <header className="sticky top-0 z-20 border-b border-rule bg-paper/95 backdrop-blur supports-[backdrop-filter]:bg-paper/85">
        <div className="mx-auto flex max-w-[1400px] items-baseline gap-8 px-4 py-3.5 md:px-8">
          <a href={href.albums()} className="text-xl tracking-tight">
            唱片目录
          </a>
          <nav className="flex gap-5 font-sans text-sm">
            <a href={href.albums()} className={nav(route.name === "albums" || route.name === "album")}>
              专辑
            </a>
            <a href={href.composers()} className={nav(route.name === "composers" || route.name === "composer")}>
              作曲家
            </a>
          </nav>
          <div className="ml-auto font-sans text-xs">
            <select className="plain" value={theme} onChange={(e) => setTheme(e.target.value as ThemePref)} aria-label="外观">
              {THEMES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-4 pt-8 md:px-8">
        {route.name === "albums" && <AlbumWall params={route.params} />}
        {route.name === "album" && <AlbumPage id={route.id} />}
        {route.name === "composers" && <ComposersPage />}
        {route.name === "composer" && <ComposerPage name={route.composer} work={route.work} />}
      </main>
      <PlayerBar />
    </div>
  );
}
