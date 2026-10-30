"""Device-side project discovery. Read-only: lists directories, reads Git metadata."""

import os
import subprocess
from pathlib import Path

# Skipped when estimating a project's most recent activity (mirrors the Try .stignore).
_SKIP = {
    ".git", "node_modules", ".venv", "venv", ".tox", ".nox", ".direnv", ".gradle", "target",
    ".next", ".nuxt", ".svelte-kit", "dist", "build", "coverage", ".cache", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".turbo",
}
_MTIME_DEPTH = 3
_MTIME_BUDGET = 5000
_COMMITS_PER_REPO = 2000
_SEP = "\x1f"


def git_root_commit(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    roots = sorted(line for line in out.stdout.split() if line)
    return roots[0] if out.returncode == 0 and roots else None


def commits(path: Path) -> list[dict]:
    """All commits reachable from any ref (read-only `git log`), newest first, capped."""
    fmt = _SEP.join(["%H", "%at", "%ct", "%ae", "%s"])
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "log", "--all", f"--format={fmt}",
             f"-n{_COMMITS_PER_REPO}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    result = []
    for line in out.stdout.splitlines() if out.returncode == 0 else []:
        parts = line.split(_SEP)
        if len(parts) == 5:
            sha, authored, committed, email, subject = parts
            result.append({"sha": sha, "authored_at": float(authored),
                           "committed_at": float(committed), "author_email": email,
                           "subject": subject})
    return result


def _repo(path: Path, **extra) -> dict:
    return {"path": str(path), "name": path.name.removesuffix(".git"),
            "root_commit": git_root_commit(path), "commits": commits(path), **extra}


def newest_mtime(path: Path) -> float | None:
    newest, seen = None, 0
    for dirpath, dirnames, filenames in os.walk(path):
        depth = Path(dirpath).relative_to(path).parts
        dirnames[:] = [] if len(depth) >= _MTIME_DEPTH else [
            d for d in dirnames if d not in _SKIP
        ]
        for name in filenames:
            seen += 1
            try:
                mtime = os.stat(os.path.join(dirpath, name), follow_symlinks=False).st_mtime
            except OSError:
                continue
            newest = mtime if newest is None else max(newest, mtime)
            if seen >= _MTIME_BUDGET:
                return newest
    return newest


def entry_for(workspace: str, base: Path, path: Path, kind: str) -> dict:
    has_git = (path / ".git").exists()
    return {
        "workspace": workspace,
        "relpath": str(path.relative_to(base)),
        "path": str(path),
        "kind": kind,
        "has_git": has_git,
        "git_root_commit": git_root_commit(path) if has_git else None,
        "newest_mtime": newest_mtime(path),
    }


def scan(workspaces: list[dict]) -> tuple[list[dict], list[dict], list[dict], bool]:
    """Every non-hidden directory in a Try root or directly under a workspace base is a
    project, Git or not; Git only adds commit evidence. Symlinks are never projects
    themselves; a symlink in a Try root (which graduation leaves behind) is reported as a
    link so its old key becomes an alias.
    Git repos (working copies, plus bare mirrors under a workspace's mirror_root) are
    returned separately with their commits as evidence.
    Returns (entries, links, repos, complete); complete is False if a root was unreadable."""
    entries, links, repos, complete = [], [], [], True
    for ws in workspaces:
        base, try_root = Path(ws["base"]), Path(ws["try_root"])
        for root, kind in ((try_root, "try"), (base, "graduated")):
            try:
                children = sorted(root.iterdir())
            except OSError:
                complete = False
                continue
            for child in children:
                if child.name.startswith(".") or child == try_root:
                    continue
                if child.is_symlink():
                    target = child.resolve()
                    if kind == "try" and target.is_dir():
                        links.append({
                            "workspace": ws["name"],
                            "relpath": str(child.relative_to(base)),
                            "target_path": str(target),
                        })
                    continue
                if not child.is_dir():
                    continue
                entry = entry_for(ws["name"], base, child, kind)
                entries.append(entry)
                if entry["has_git"]:
                    repos.append(_repo(child))
        mirror_root = Path(ws["mirror_root"]) if ws.get("mirror_root") else None
        if mirror_root and mirror_root.is_dir():
            for mirror in sorted(mirror_root.glob("*.git")):
                repos.append(_repo(mirror, mirror_workspace=ws["name"]))
    return entries, links, repos, complete
