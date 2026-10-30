-- Foundation schema. Times are UTC unix seconds (REAL) unless noted.
-- Raw observations are immutable; everything derived lives in its own table
-- and records the rule version that produced it.

CREATE TABLE devices (
    id          TEXT PRIMARY KEY,               -- slug from config, e.g. 'omphalos', 'macbook'
    os          TEXT NOT NULL CHECK (os IN ('linux', 'macos')),
    home        TEXT NOT NULL,                  -- absolute home dir, used for path mapping
    created_at  REAL NOT NULL
);

-- Hostnames reported by clients (WakaTime machine name, collector hostname).
CREATE TABLE device_machine_names (
    machine_name TEXT PRIMARY KEY,
    device_id    TEXT NOT NULL REFERENCES devices(id)
);

CREATE TABLE api_tokens (
    id          TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,           -- sha256 of the token; the token itself is never stored
    kind        TEXT NOT NULL CHECK (kind IN ('user', 'collector', 'agent')),
    device_id   TEXT REFERENCES devices(id),
    label       TEXT,
    created_at  REAL NOT NULL,
    revoked_at  REAL
);

-- Projects ------------------------------------------------------------------

CREATE TABLE projects (
    id              TEXT PRIMARY KEY,           -- stable 'prj_…'
    key             TEXT NOT NULL UNIQUE,       -- device-independent: '<workspace>/<path under workspace base>'
    workspace       TEXT NOT NULL,              -- 'personal' | 'work' (from config)
    name            TEXT NOT NULL,              -- directory name
    kind            TEXT NOT NULL CHECK (kind IN ('try', 'graduated', 'legacy', 'manual')),
    lifecycle       TEXT NOT NULL CHECK (lifecycle IN ('active', 'paused', 'archived')),
    git_root_commit TEXT,                       -- supporting identity evidence
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX projects_name ON projects(name);
CREATE INDEX projects_root_commit ON projects(git_root_commit);

-- Old keys that resolve to a project (moves, graduation, legacy roots).
CREATE TABLE project_aliases (
    alias_key   TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    reason      TEXT NOT NULL,                  -- 'moved:root-commit' | 'moved:name' | 'merge' | 'legacy'
    created_at  REAL NOT NULL
);

-- Where each project lives on each device, as last reported by that device's scan.
CREATE TABLE project_locations (
    device_id   TEXT NOT NULL REFERENCES devices(id),
    path        TEXT NOT NULL,                  -- absolute path on that device
    project_id  TEXT NOT NULL REFERENCES projects(id),
    has_git     INTEGER NOT NULL,
    newest_mtime REAL,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    gone_at     REAL,                           -- set when a later full scan no longer sees it
    PRIMARY KEY (device_id, path)
);
CREATE INDEX project_locations_project ON project_locations(project_id);

-- Ambiguous moves awaiting the user's decision (shown in the TUI).
CREATE TABLE move_suggestions (
    id              INTEGER PRIMARY KEY,
    from_project_id TEXT NOT NULL REFERENCES projects(id),
    to_project_id   TEXT NOT NULL REFERENCES projects(id),
    evidence        TEXT NOT NULL,
    created_at      REAL NOT NULL,
    resolved_at     REAL,
    resolution      TEXT
);

-- Tasks, audit, selection --------------------------------------------------

CREATE TABLE tasks (
    id          TEXT PRIMARY KEY,               -- stable 'tsk_…'
    project_id  TEXT NOT NULL REFERENCES projects(id),
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    column_name TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    archived_at REAL
);
CREATE INDEX tasks_project ON tasks(project_id);

CREATE TABLE task_notes (
    id          TEXT PRIMARY KEY,               -- 'note_…'
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    body        TEXT NOT NULL,
    audit_id    INTEGER NOT NULL REFERENCES audit_log(id),
    created_at  REAL NOT NULL
);

-- Every user/agent/system change to editable records.
CREATE TABLE audit_log (
    id            INTEGER PRIMARY KEY,
    at            REAL NOT NULL,
    actor_kind    TEXT NOT NULL CHECK (actor_kind IN ('user', 'agent', 'system')),
    actor_device  TEXT,
    actor_harness TEXT,
    actor_session TEXT,
    op            TEXT NOT NULL,                -- e.g. 'project.create', 'task.move'
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    before_json   TEXT,
    after_json    TEXT
);
CREATE INDEX audit_entity ON audit_log(entity_type, entity_id);

-- Append-only per-device selection history; attribution replays it.
CREATE TABLE selection_events (
    id          INTEGER PRIMARY KEY,
    device_id   TEXT NOT NULL REFERENCES devices(id),
    at          REAL NOT NULL,
    project_id  TEXT REFERENCES projects(id),   -- NULL + NULL task = cleared
    task_id     TEXT REFERENCES tasks(id),
    reason      TEXT NOT NULL                   -- 'user' | 'lock' | 'sleep' | 'idle' | 'clear'
);
CREATE INDEX selection_device_at ON selection_events(device_id, at);

-- Raw observations: Wakapi heartbeats ----------------------------------------

CREATE TABLE raw_heartbeats (
    id            INTEGER PRIMARY KEY,
    source_id     TEXT NOT NULL UNIQUE,         -- content fingerprint; survives Wakapi rebuilds
    wakapi_id     TEXT,
    time          REAL NOT NULL,
    machine_name  TEXT,
    project_name  TEXT,
    entity        TEXT,
    entity_type   TEXT,
    category      TEXT,
    language      TEXT,
    branch        TEXT,
    is_write      INTEGER,
    user_agent    TEXT,
    payload_json  TEXT NOT NULL,
    ingested_at   REAL NOT NULL
);
CREATE INDEX raw_heartbeats_time ON raw_heartbeats(time);

-- Derived: origin tag, device and project match for each heartbeat.
CREATE TABLE heartbeat_derivations (
    heartbeat_id  INTEGER PRIMARY KEY REFERENCES raw_heartbeats(id),
    device_id     TEXT REFERENCES devices(id),
    origin        TEXT NOT NULL CHECK (origin IN ('agent', 'editor')),
    plugin        TEXT,
    project_id    TEXT REFERENCES projects(id),  -- NULL = unmatched, still preserved
    match_rule    TEXT NOT NULL,                 -- 'path' | 'legacy-path' | 'name' | 'unmatched'
    rule_version  TEXT NOT NULL,
    derived_at    REAL NOT NULL
);
CREATE INDEX heartbeat_derivations_project ON heartbeat_derivations(project_id);

-- Importer checkpoints ------------------------------------------------------

CREATE TABLE import_cursors (
    source      TEXT PRIMARY KEY,               -- 'wakapi', later 'agent:claude-code@macbook', …
    cursor_json TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE import_runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL,
    fetched     INTEGER NOT NULL DEFAULT 0,
    inserted    INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
