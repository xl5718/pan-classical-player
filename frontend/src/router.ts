import { useSyncExternalStore } from "react";

export type Route =
  | { name: "albums"; params: URLSearchParams }
  | { name: "album"; id: number }
  | { name: "composers" }
  | { name: "composer"; composer: string; work?: string };

function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "album" && parts[1]) return { name: "album", id: Number(parts[1]) };
  if (parts[0] === "composers") return { name: "composers" };
  if (parts[0] === "composer" && parts[1]) return { name: "composer", composer: parts[1], work: parts[2] };
  return { name: "albums", params: new URLSearchParams(query) };
}

const subscribe = (fn: () => void) => {
  window.addEventListener("hashchange", fn);
  return () => window.removeEventListener("hashchange", fn);
};
const snapshot = () => location.hash;

let lastAlbumsHash = "#/";

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, snapshot);
  const route = parse(hash);
  if (route.name === "albums") lastAlbumsHash = hash || "#/";
  return route;
}

/** 回到专辑墙，并保留离开时的搜索与筛选条件 */
export const albumsBackHref = () => lastAlbumsHash;

export const href = {
  albums: (params?: Record<string, string>) => {
    const q = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v)).toString() : "";
    return `#/${q ? `?${q}` : ""}`;
  },
  album: (id: number) => `#/album/${id}`,
  composers: () => "#/composers",
  composer: (name: string, work?: string) => `#/composer/${encodeURIComponent(name)}${work ? `/${encodeURIComponent(work)}` : ""}`,
};

/** 改查询参数但不新增历史记录（筛选、搜索输入时用） */
export function replaceAlbumParams(params: Record<string, string>) {
  const h = href.albums(params);
  history.replaceState(null, "", h);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}
