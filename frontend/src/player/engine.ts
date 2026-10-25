import { api, coverUrl, type QTrack } from "../api";
import { surname, trackLabel } from "../format";

export type GaplessScope = "work" | "album" | "off";

export type PlayerState = {
  queue: QTrack[];
  index: number;
  playing: boolean;
  buffering: boolean;
  volume: number;
  muted: boolean;
  error: string | null;
  gapless: GaplessScope;
};

/** 播放单元：浏览器里一个 <audio> 源。可能是一个文件（含 CUE 分轨），也可能是无缝拼接的一组分轨文件 */
type Unit = {
  key: string;
  url: string;
  pcm: boolean;
  start: Map<number, number>; // 队列下标 -> 该曲在单元内的起点（秒）
  end: Map<number, number | null>; // 队列下标 -> 终点；null 表示到单元末尾
};

const STORE_KEY = "player.v1";
const probe = typeof document !== "undefined" ? document.createElement("audio") : null;

/** 浏览器不能直接播放的格式交给后端用 ffmpeg 转成 FLAC */
export function needsTranscode(format: string): boolean {
  if (!probe) return false;
  switch (format) {
    case "APE":
    case "WavPack":
      return true;
    case "ALAC":
      return probe.canPlayType('audio/mp4; codecs="alac"') === "";
    default:
      return false;
  }
}

const trackStart = (t: QTrack) => t.start_offset ?? 0;
const trackEnd = (t: QTrack): number | null =>
  t.end_offset ?? (t.start_offset != null && t.duration ? t.start_offset + t.duration : null);

/** 相邻两首能否在服务器端拼成一条无缝的流 */
function joinable(a: QTrack, b: QTrack, scope: GaplessScope): boolean {
  if (scope === "off") return false;
  if (a.fsid === b.fsid || a.start_offset != null || b.start_offset != null) return false;
  if (!a.duration || !b.duration || a.album_id !== b.album_id || a.disc_no !== b.disc_no) return false;
  if (a.sample_rate !== b.sample_rate) return false;
  if (scope === "work") return a.work_id != null && a.work_id === b.work_id;
  return true;
}

export function buildUnits(queue: QTrack[], scope: GaplessScope, noJoin: Set<number>): Unit[] {
  const units: Unit[] = new Array(queue.length);
  let i = 0;
  while (i < queue.length) {
    let j = i;
    while (j + 1 < queue.length && !noJoin.has(queue[j].id) && !noJoin.has(queue[j + 1].id) && joinable(queue[j], queue[j + 1], scope)) j++;
    if (j > i) {
      const ids = queue.slice(i, j + 1).map((t) => t.id);
      const u: Unit = { key: `g:${ids.join(",")}`, url: `/stream/pcm?ids=${ids.join(",")}`, pcm: true, start: new Map(), end: new Map() };
      let acc = 0;
      for (let k = i; k <= j; k++) {
        u.start.set(k, acc);
        acc += queue[k].duration!;
        u.end.set(k, k === j ? null : acc);
        units[k] = u;
      }
      i = j + 1;
      continue;
    }
    // 单个文件：同一文件的相邻 CUE 分轨共用一个单元
    const t = queue[i];
    const pcm = needsTranscode(t.format);
    const key = `${pcm ? "p" : "d"}:${t.fsid}`;
    const prev = i > 0 ? units[i - 1] : undefined;
    const u: Unit =
      prev && prev.key === key
        ? prev
        : { key, url: pcm ? `/stream/pcm?ids=${t.id}` : `/stream/${t.id}`, pcm, start: new Map(), end: new Map() };
    u.start.set(i, trackStart(t));
    u.end.set(i, trackEnd(t));
    units[i] = u;
    i++;
  }
  return units;
}

export class PlayerEngine {
  readonly el: HTMLAudioElement;
  private state: PlayerState;
  private listeners = new Set<() => void>();
  private posListeners = new Set<() => void>();
  private loaded: Unit | null = null;
  private units: Unit[] = [];
  private noJoin = new Set<number>(); // 拼接失败过的曲目，之后单独播放
  private pendingSeek: number | null = null;
  private ticker: number | null = null;
  private prefetched = new Set<number>();
  private restorePos = 0;

  constructor() {
    this.el = new Audio();
    this.el.preload = "auto";
    const saved = this.load();
    this.state = {
      queue: saved?.queue ?? [],
      index: saved?.index ?? -1,
      playing: false,
      buffering: false,
      volume: saved?.volume ?? 1,
      muted: false,
      error: null,
      gapless: saved?.gapless ?? "work",
    };
    this.units = buildUnits(this.state.queue, this.state.gapless, this.noJoin);
    this.restorePos = saved?.position ?? 0;
    this.el.volume = this.state.volume;

    const el = this.el;
    el.addEventListener("play", () => this.set({ playing: true, error: null }));
    el.addEventListener("pause", () => this.set({ playing: false }));
    el.addEventListener("waiting", () => this.set({ buffering: true }));
    el.addEventListener("playing", () => this.set({ buffering: false }));
    el.addEventListener("canplay", () => this.set({ buffering: false }));
    el.addEventListener("ended", () => this.onEnded());
    el.addEventListener("error", () => {
      const code = el.error?.code;
      if (!el.getAttribute("src")) return;
      const u = this.loaded;
      if (u && u.key.startsWith("g:")) {
        // 无缝拼接失败（例如声道数不同）：这几首改为单独播放
        for (const k of u.start.keys()) this.noJoin.add(this.state.queue[k]?.id);
        this.rebuild();
        this.loaded = null;
        this.playIndex(this.state.index, this.position);
        return;
      }
      this.set({ playing: false, buffering: false, error: code === 4 ? "浏览器无法播放此格式" : "加载失败，请重试" });
    });
    el.addEventListener("loadedmetadata", () => {
      if (this.pendingSeek != null) {
        el.currentTime = this.pendingSeek;
        this.pendingSeek = null;
      }
    });
    el.addEventListener("timeupdate", () => this.emitPos());
    el.addEventListener("volumechange", () => this.set({ volume: el.volume, muted: el.muted }));
    this.setupMediaSession();
    window.addEventListener("pywebviewready", () => this.syncNative());
    window.addEventListener("beforeunload", () => this.save());
    window.setInterval(() => this.save(), 5000);
  }

  // ---------- 订阅 ----------
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  subscribePos = (fn: () => void) => {
    this.posListeners.add(fn);
    return () => this.posListeners.delete(fn);
  };
  getState = () => this.state;
  // useSyncExternalStore 要求快照在两次通知之间保持不变；WebKit 的 currentTime 是实时变化的，
  // 直接返回会被 React 当成状态一直在变而无限重绘（React #185），所以只在通知时更新快照
  private posSnap = 0;
  getPosition = () => this.posSnap;

  private set(patch: Partial<PlayerState>) {
    this.state = { ...this.state, ...patch };
    if ("queue" in patch || "gapless" in patch) this.rebuild();
    this.listeners.forEach((f) => f());
    if ("index" in patch || "queue" in patch) this.updateMediaMetadata();
    if ("playing" in patch && navigator.mediaSession) navigator.mediaSession.playbackState = this.state.playing ? "playing" : "paused";
    if ("index" in patch || "queue" in patch || "playing" in patch) this.syncNative();
    if ("index" in patch || "queue" in patch) this.emitPos();
  }
  private rebuild() {
    this.units = buildUnits(this.state.queue, this.state.gapless, this.noJoin);
    // 当前已加载的源若仍是同一单元，保留对象以免误判为需要重新加载
    const L = this.loaded;
    if (!L) return;
    const cur = this.units[this.state.index];
    if (cur && cur.key === L.key) {
      this.loaded = cur;
      return;
    }
    // 正在播放的流因队列变化（如追加了同一作品的后续乐章）而改变了分组：让当前这条流照常播完
    const q = this.state.queue;
    const ids = L.key.startsWith("g:") ? L.key.slice(2).split(",").map(Number) : null;
    const keep = [...L.start.keys()].every((k, n) => (ids ? q[k]?.id === ids[n] : true));
    if (keep && L.start.has(this.state.index)) for (const k of L.start.keys()) this.units[k] = L;
  }
  /** 队列中第 i 首是否与下一首无缝衔接（用于在队列里画连线） */
  joinedWithNext(i: number): boolean {
    const a = this.units[i];
    const b = this.units[i + 1];
    return !!a && a === b;
  }
  setGapless(scope: GaplessScope) {
    this.set({ gapless: scope });
  }
  private emitPos() {
    this.posSnap = this.position;
    this.posListeners.forEach((f) => f());
    this.updatePositionState();
  }

  // ---------- 位置 ----------
  get current(): QTrack | null {
    return this.state.queue[this.state.index] ?? null;
  }
  private get unitTime(): number {
    return this.pendingSeek ?? this.el.currentTime;
  }
  private get unit(): Unit | null {
    return this.units[this.state.index] ?? null;
  }
  private get isLoaded(): boolean {
    return !!this.loaded && this.loaded.key === this.unit?.key;
  }
  /** 当前曲目内的播放位置（秒） */
  get position(): number {
    const u = this.unit;
    if (!u || !this.isLoaded) return this.restorePos;
    return Math.max(0, this.unitTime - (u.start.get(this.state.index) ?? 0));
  }
  get duration(): number {
    const t = this.current;
    if (!t) return 0;
    if (t.duration) return t.duration;
    const u = this.unit;
    return isFinite(this.el.duration) && u ? this.el.duration - (u.start.get(this.state.index) ?? 0) : 0;
  }

  // ---------- 队列 ----------
  playQueue(tracks: QTrack[], index = 0) {
    this.prefetched.clear();
    this.set({ queue: tracks, index: -1 });
    this.playIndex(index);
  }
  append(tracks: QTrack[]) {
    const queue = [...this.state.queue, ...tracks];
    this.set({ queue });
    if (this.state.index < 0) this.playIndex(0);
    else this.prefetchNext();
  }
  playNext(tracks: QTrack[]) {
    const q = [...this.state.queue];
    q.splice(this.state.index + 1, 0, ...tracks);
    this.set({ queue: q });
    if (this.state.index < 0) this.playIndex(0);
    else this.prefetchNext();
  }
  remove(i: number) {
    const q = this.state.queue.filter((_, j) => j !== i);
    let index = this.state.index;
    if (i < index) index -= 1;
    else if (i === index) {
      this.set({ queue: q, index: Math.min(index, q.length - 1) });
      if (q.length) this.playIndex(this.state.index, 0, this.state.playing);
      else this.stop();
      return;
    }
    this.set({ queue: q, index });
  }
  /** 从队列中去掉某张专辑的所有曲目（专辑被删除时） */
  removeAlbum(albumId: number) {
    const cur = this.current;
    const q = this.state.queue.filter((t) => t.album_id !== albumId);
    if (q.length === this.state.queue.length) return;
    if (cur && cur.album_id === albumId) {
      this.stop();
      this.set({ queue: q, index: q.length ? 0 : -1, playing: false });
      this.restorePos = 0;
    } else {
      this.set({ queue: q, index: cur ? q.indexOf(cur) : -1 });
    }
  }
  clear() {
    this.stop();
    this.set({ queue: [], index: -1 });
  }
  private stop() {
    this.el.pause();
    this.el.removeAttribute("src");
    this.el.load();
    this.loaded = null;
  }

  // ---------- 播放控制 ----------
  playIndex(i: number, startAt = 0, autoplay = true) {
    const t = this.state.queue[i];
    if (!t) return;
    this.restorePos = 0;
    this.set({ index: i, error: null });
    const u = this.units[i];
    const target = (u.start.get(i) ?? 0) + startAt;
    if (this.loaded && this.loaded.key === u.key) {
      this.loaded = u;
      this.el.currentTime = target;
    } else {
      this.openSource(u, target);
    }
    if (autoplay) this.el.play().catch(() => this.set({ playing: false }));
    this.startTicker();
    this.prefetchNext();
  }

  private openSource(u: Unit, at: number) {
    this.loaded = u;
    this.pendingSeek = at > 0 ? at : null;
    this.el.src = u.url;
    this.set({ buffering: true });
  }

  toggle() {
    if (this.state.playing) this.el.pause();
    else this.play();
  }
  play() {
    const t = this.current;
    if (!t) return;
    if (!this.isLoaded) {
      this.playIndex(this.state.index, this.restorePos);
      return;
    }
    this.el.play().catch(() => undefined);
    this.startTicker();
  }
  pause() {
    this.el.pause();
  }
  next() {
    if (this.state.index + 1 < this.state.queue.length) this.playIndex(this.state.index + 1, 0, true);
  }
  prev() {
    if (this.position > 3 || this.state.index === 0) this.seek(0);
    else this.playIndex(this.state.index - 1, 0, true);
  }
  seek(pos: number) {
    const t = this.current;
    if (!t) return;
    pos = Math.max(0, Math.min(pos, this.duration || pos));
    if (!this.isLoaded) {
      this.restorePos = pos;
      this.emitPos();
      return;
    }
    this.el.currentTime = (this.unit!.start.get(this.state.index) ?? 0) + pos;
    this.emitPos();
    setTimeout(() => this.syncNative(), 300);
  }
  setVolume(v: number) {
    this.el.volume = Math.max(0, Math.min(1, v));
    if (this.el.muted && v > 0) this.el.muted = false;
  }
  toggleMute() {
    this.el.muted = !this.el.muted;
  }

  // ---------- 曲目边界 ----------
  private startTicker() {
    if (this.ticker != null) return;
    this.ticker = window.setInterval(() => this.tick(), 100);
  }
  private tick() {
    const t = this.current;
    const u = this.unit;
    if (!t || !u || !this.state.playing || !this.isLoaded) return;
    const end = u.end.get(this.state.index);
    if (end == null || this.unitTime < end - 0.03) return;
    const i = this.state.index + 1;
    const nu = this.units[i];
    if (nu && nu.key === u.key && Math.abs((nu.start.get(i) ?? 0) - end) < 0.5) {
      // 下一首在同一条流里紧接着（CUE 分轨或无缝拼接组）：只切换显示，声音不中断
      this.set({ index: i });
      this.prefetchNext();
    } else if (nu) {
      this.playIndex(i);
    } else {
      this.el.pause();
      this.seek(0);
    }
  }
  private onEnded() {
    if (this.state.index + 1 < this.state.queue.length) this.playIndex(this.state.index + 1);
    else this.set({ playing: false });
  }

  private prefetchNext() {
    const t = this.current;
    if (!t) return;
    const u = this.unit;
    const rest = this.state.queue.slice(this.state.index + 1);
    const n = rest.find((x, k) => x.fsid !== t.fsid && this.units[this.state.index + 1 + k] !== u);
    if (n && !this.prefetched.has(n.fsid)) {
      this.prefetched.add(n.fsid);
      api.prefetch(n.id);
    }
  }

  // ---------- Media Session（锁屏、耳机按键） ----------
  private setupMediaSession() {
    const ms = navigator.mediaSession;
    if (!ms) return;
    const h = (a: MediaSessionAction, fn: MediaSessionActionHandler) => {
      try {
        ms.setActionHandler(a, fn);
      } catch {
        /* 部分浏览器不支持某些动作 */
      }
    };
    h("play", () => this.play());
    h("pause", () => this.pause());
    h("previoustrack", () => this.prev());
    h("nexttrack", () => this.next());
    h("seekbackward", (d) => this.seek(this.position - (d.seekOffset ?? 10)));
    h("seekforward", (d) => this.seek(this.position + (d.seekOffset ?? 10)));
    h("seekto", (d) => d.seekTime != null && this.seek(d.seekTime));
    h("stop", () => this.pause());
  }
  private updateMediaMetadata() {
    const ms = navigator.mediaSession;
    const t = this.current;
    if (!ms || typeof MediaMetadata === "undefined") return;
    if (!t) {
      ms.metadata = null;
      return;
    }
    const cover = coverUrl(t.cover_path);
    const who = [t.composer ? surname(t.composer) : null, t.performer].filter(Boolean).join(" — ");
    ms.metadata = new MediaMetadata({
      title: trackLabel(t),
      artist: who,
      album: t.work_title ?? t.album_title,
      artwork: cover ? [{ src: new URL(cover, location.href).href, sizes: "600x600", type: "image/jpeg" }] : [],
    });
  }
  /** 桌面版：把播放状态告诉 macOS 的「正在播放」（网页版没有 pywebview，什么也不做） */
  private syncNative() {
    try {
      this.syncNativeUnsafe();
    } catch (e) {
      console.warn("同步「正在播放」失败", e);
    }
  }
  private syncNativeUnsafe() {
    const api = (window as unknown as { pywebview?: { api?: { now_playing?: (i: object) => unknown } } }).pywebview?.api;
    if (typeof api?.now_playing !== "function") return;
    const t = this.current;
    const info = t
      ? {
          title: trackLabel(t),
          artist: [t.composer ? surname(t.composer) : null, t.performer].filter(Boolean).join(" — "),
          album: t.work_title ?? t.album_title,
          duration: this.duration,
          position: this.position,
          playing: this.state.playing,
          cover: t.cover_path,
        }
      : {};
    const r = api.now_playing(info) as Promise<unknown> | undefined;
    if (r && typeof r.catch === "function") r.catch(() => undefined);
  }

  private updatePositionState() {
    const ms = navigator.mediaSession;
    if (!ms?.setPositionState) return;
    const d = this.duration;
    if (!d) return;
    try {
      ms.setPositionState({ duration: d, position: Math.min(this.position, d), playbackRate: this.el.playbackRate || 1 });
    } catch {
      /* 位置暂时不合法时忽略 */
    }
  }

  // ---------- 持久化 ----------
  private save() {
    try {
      localStorage.setItem(
        STORE_KEY,
        JSON.stringify({
          queue: this.state.queue,
          index: this.state.index,
          position: this.position,
          volume: this.state.volume,
          gapless: this.state.gapless,
        }),
      );
    } catch {
      /* 存储不可用时忽略 */
    }
  }
  private load(): { queue: QTrack[]; index: number; position: number; volume: number; gapless?: GaplessScope } | null {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }
}

export const player = new PlayerEngine();
// 供桌面版（媒体键）与调试使用
(window as unknown as { __player: PlayerEngine }).__player = player;

// 开发时热更新会重建这个模块：先停掉旧的播放器，避免它在页面外继续出声
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    player.el.pause();
    player.el.removeAttribute("src");
    player.el.load();
  });
}
