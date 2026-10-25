export type Format = { format: string | null; sample_rate: number | null; bit_depth: number | null; mixed?: boolean };

export type AlbumSummary = {
  id: number;
  folder_path: string;
  title: string;
  album_artist: string | null;
  label: string | null;
  year: number | null;
  cover_path: string | null;
  incomplete: number;
  disc_count: number;
  track_count: number;
  duration: number;
  added: number;
  composers: string[];
  performers: string[];
  format: Format;
};

export type Track = {
  id: number;
  album_id: number;
  work_id: number | null;
  fsid: number;
  path: string;
  disc_no: number;
  track_no: number | null;
  title: string;
  movement_name: string | null;
  movement_no: number | null;
  composer: string | null;
  performer: string | null;
  conductor: string | null;
  duration: number | null;
  format: string;
  sample_rate: number | null;
  bit_depth: number | null;
  start_offset: number | null;
  end_offset: number | null;
  incomplete: number;
};

export type Work = { id: number; album_id: number; composer: string | null; title: string; catalogue: string | null; seq: number };

export type AlbumDetail = Omit<AlbumSummary, "composers" | "performers" | "format" | "track_count" | "duration" | "added"> & {
  cover_large: string | null;
  works: Work[];
  tracks: Track[];
};

export type ComposerSummary = { name: string; recordings: number; albums: number; sort: string; known: boolean };

export type Recording = {
  work_id: number;
  album_id: number;
  album_title: string;
  album_artist: string | null;
  year: number | null;
  label: string | null;
  cover_path: string | null;
  performer: string | null;
  conductor: string | null;
  duration: number;
  tracks: Track[];
};

export type WorkGroup = { key: string; title: string; catalogue: string | null; recordings: Recording[] };
export type ComposerDetail = { name: string; works: WorkGroup[] };

/** 放进播放队列的曲目：带上显示所需的专辑与作品信息 */
export type QTrack = Track & { album_title: string; cover_path: string | null; work_title: string | null };

export type MediaItem = { fsid: number; name: string; path: string; size: number; kind: "image" | "pdf" };

export type DeletePlan = {
  album_id: number;
  title: string;
  folder: string;
  mode: "folder" | "files";
  paths: string[];
  file_count: number;
  total_size: number;
  files: string[];
  nested_albums: string[];
  token: string;
};
export type DeleteResult = { deleted: number; failed: { path: string; errno: number }[]; album_removed: boolean; mode: string };

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`.trim());
  return r.json();
}

export const api = {
  albums: () => get<AlbumSummary[]>("/api/albums"),
  album: (id: number) => get<AlbumDetail>(`/api/albums/${id}`),
  search: (q: string) => get<{ album_ids: number[] }>(`/api/search?q=${encodeURIComponent(q)}`),
  composers: () => get<ComposerSummary[]>("/api/composers"),
  composer: (name: string) => get<ComposerDetail>(`/api/composer?name=${encodeURIComponent(name)}`),
  media: (id: number) => get<MediaItem[]>(`/api/albums/${id}/media`),
  deletePlan: (id: number) => get<DeletePlan>(`/api/albums/${id}/delete-plan`),
  deleteAlbum: async (id: number, token: string): Promise<DeleteResult> => {
    const r = await fetch(`/api/albums/${id}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail ?? `HTTP ${r.status}`);
    return body;
  },
  prefetch: (trackId: number) => fetch(`/api/prefetch/${trackId}`, { method: "POST" }).catch(() => undefined),
};

export const coverUrl = (p: string | null) => (p ? `/covers/${p}` : null);

export function toQueue(tracks: Track[], album: { title: string; cover_path: string | null }, works: Map<number, Work> | null): QTrack[] {
  return tracks.map((t) => ({
    ...t,
    album_title: album.title,
    cover_path: album.cover_path,
    work_title: t.work_id != null ? (works?.get(t.work_id)?.title ?? null) : null,
  }));
}
