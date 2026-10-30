-- Milestone 3: ideas (which can become projects) and project descriptions.

-- An idea is a project that does not exist yet. Starting one links it to the project it
-- became (usually a new Try folder); dropping is soft and reversible.
CREATE TABLE ideas (
    id          TEXT PRIMARY KEY,               -- stable 'ida_…'
    workspace   TEXT NOT NULL,                  -- 'personal' | 'work' (from config)
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',       -- one line, shown on the idea's card
    body        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL CHECK (status IN ('open', 'started', 'dropped')),
    project_id  TEXT REFERENCES projects(id),   -- set when started
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    started_at  REAL,
    dropped_at  REAL
);
CREATE INDEX ideas_status ON ideas(status);
CREATE INDEX ideas_project ON ideas(project_id);

ALTER TABLE projects ADD COLUMN description TEXT NOT NULL DEFAULT '';
