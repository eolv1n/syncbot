SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS spotify_tracks (
    spotify_track_id TEXT PRIMARY KEY,
    isrc TEXT,
    artists_raw TEXT NOT NULL,
    title_raw TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    added_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS soundeo_matches (
    spotify_track_id TEXT NOT NULL,
    soundeo_track_id TEXT,
    soundeo_url TEXT,
    match_score REAL NOT NULL,
    match_type TEXT NOT NULL,
    availability_status TEXT NOT NULL,
    downloaded_flag INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT NOT NULL,
    PRIMARY KEY (spotify_track_id, soundeo_track_id),
    FOREIGN KEY (spotify_track_id) REFERENCES spotify_tracks (spotify_track_id)
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_track_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_result TEXT NOT NULL,
    action_at TEXT NOT NULL,
    notes TEXT,
    FOREIGN KEY (spotify_track_id) REFERENCES spotify_tracks (spotify_track_id)
);

CREATE TABLE IF NOT EXISTS waitlist (
    spotify_track_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT NOT NULL,
    last_retry_at TEXT,
    FOREIGN KEY (spotify_track_id) REFERENCES spotify_tracks (spotify_track_id)
);

CREATE TABLE IF NOT EXISTS downloads_cache (
    soundeo_track_id TEXT PRIMARY KEY,
    normalized_track_key TEXT NOT NULL,
    downloaded_at TEXT,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

