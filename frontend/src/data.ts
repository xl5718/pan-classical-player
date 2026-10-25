import { useEffect, useState } from "react";
import { api, type AlbumSummary } from "./api";

let albumsPromise: Promise<AlbumSummary[]> | null = null;

export function invalidateAlbums() {
  albumsPromise = null;
}

/** 专辑列表在会话内共享；扫描在后台更新后，切回专辑墙时重新拉取 */
export function useAlbums(): { albums: AlbumSummary[] | null; error: string | null } {
  const [albums, setAlbums] = useState<AlbumSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const p = albumsPromise ?? (albumsPromise = api.albums());
    p.then((a) => alive && setAlbums(a)).catch((e: Error) => {
      albumsPromise = null;
      if (alive) setError(e.message);
    });
    api
      .albums()
      .then((fresh) => {
        albumsPromise = Promise.resolve(fresh);
        if (alive) setAlbums(fresh);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return { albums, error };
}

export function useFetch<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    fn()
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}
