-- Milestone 2: commit evidence and agent session context.

-- Raw commits as observed by any device's scan (working copy or bare mirror).
-- Commits are evidence only: never a clock for time worked.
CREATE TABLE raw_commits (
    sha            TEXT PRIMARY KEY,
    root_commit    TEXT,                        -- repository identity
    authored_at    REAL NOT NULL,
    committed_at   REAL NOT NULL,
    author_email   TEXT,
    subject        TEXT,
    first_device   TEXT REFERENCES devices(id),
    first_path     TEXT,
    observed_at    REAL NOT NULL
);
CREATE INDEX raw_commits_committed ON raw_commits(committed_at);

-- Derived: which project a commit belongs to (a commit can be seen in several places).
CREATE TABLE commit_projects (
    sha           TEXT NOT NULL REFERENCES raw_commits(sha),
    project_id    TEXT NOT NULL REFERENCES projects(id),
    rule_version  TEXT NOT NULL,
    PRIMARY KEY (sha, project_id)
);
CREATE INDEX commit_projects_project ON commit_projects(project_id);

-- An agent's task context is scoped to its session and never changes a device selection.
-- Append-only; the latest row per (harness, session_id) is the current link.
CREATE TABLE session_task_links (
    id          INTEGER PRIMARY KEY,
    harness     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    device_id   TEXT REFERENCES devices(id),
    project_id  TEXT REFERENCES projects(id),
    task_id     TEXT REFERENCES tasks(id),      -- NULL = unlinked
    at          REAL NOT NULL,
    actor_kind  TEXT NOT NULL CHECK (actor_kind IN ('user', 'agent')),
    audit_id    INTEGER REFERENCES audit_log(id)
);
CREATE INDEX session_task_links_session ON session_task_links(harness, session_id, at);

CREATE INDEX tasks_column ON tasks(project_id, column_name);
