import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    fs_id        INTEGER PRIMARY KEY,
    path         TEXT NOT NULL,
    dir          TEXT NOT NULL,
    name         TEXT NOT NULL,
    ext          TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- audio / cue / image / zip / other
    size         INTEGER NOT NULL,
    server_mtime INTEGER NOT NULL,
    md5          TEXT,
    meta_json    TEXT,                   -- 解析结果缓存
    meta_mtime   INTEGER,                -- 解析时对应的 server_mtime
    error        TEXT
);
CREATE INDEX IF NOT EXISTS ix_files_dir ON files(dir);
CREATE INDEX IF NOT EXISTS ix_files_kind ON files(kind);

CREATE TABLE IF NOT EXISTS albums (
    id            INTEGER PRIMARY KEY,
    folder_path   TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    album_artist  TEXT,
    label         TEXT,
    year          INTEGER,
    cover_path    TEXT,
    cover_src     TEXT,
    disc_count    INTEGER NOT NULL DEFAULT 1,
    incomplete    INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS works (
    id         INTEGER PRIMARY KEY,
    album_id   INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    composer   TEXT,
    title      TEXT NOT NULL,
    catalogue  TEXT,
    seq        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_works_album ON works(album_id);
CREATE INDEX IF NOT EXISTS ix_works_composer ON works(composer);

CREATE TABLE IF NOT EXISTS tracks (
    id             INTEGER PRIMARY KEY,
    album_id       INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    work_id        INTEGER REFERENCES works(id) ON DELETE SET NULL,
    fsid           INTEGER NOT NULL,
    path           TEXT NOT NULL,
    disc_no        INTEGER NOT NULL DEFAULT 1,
    track_no       INTEGER,
    title          TEXT NOT NULL,
    movement_name  TEXT,
    movement_no    INTEGER,
    composer       TEXT,
    performer      TEXT,
    conductor      TEXT,
    duration       REAL,
    format         TEXT,
    sample_rate    INTEGER,
    bit_depth      INTEGER,
    size           INTEGER,
    md5            TEXT,
    server_mtime   INTEGER,
    start_offset   REAL,                 -- CUE 分轨：在整轨文件中的起点（秒）
    end_offset     REAL,                 -- CUE 分轨：终点（秒），NULL 表示到文件末尾
    cue_path       TEXT,
    incomplete     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tracks_album ON tracks(album_id);
CREATE INDEX IF NOT EXISTS ix_tracks_work ON tracks(work_id);
CREATE INDEX IF NOT EXISTS ix_tracks_fsid ON tracks(fsid);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


_initialized: set[str] = set()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    if str(path) not in _initialized:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _initialized.add(str(path))
    return conn
