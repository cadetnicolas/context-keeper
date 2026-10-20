"""Read-only Git capture. Generated context is excluded from code fingerprints."""
import hashlib
import os
import subprocess
from pathlib import Path
from _store import DATA_DIR, Error, digest

EXCLUDE = ["--", ".", ":(top,exclude)" + DATA_DIR, ":(top,exclude)" + DATA_DIR + "/**"]


def git(root, *arguments, optional=False):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    try:
        result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
                                capture_output=True, timeout=20, env=environment)
    except (OSError, subprocess.TimeoutExpired) as error:
        if optional:
            return None
        raise Error("Git inspection is unavailable or timed out.") from error
    if result.returncode:
        if optional:
            return None
        raise Error("Git inspection failed: " + " ".join(arguments[:2]))
    return result.stdout


def locate(path):
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise Error("Workspace directory does not exist.")
    top = git(root, "rev-parse", "--show-toplevel", optional=True)
    return Path(os.fsdecode(top).strip()).resolve() if top else root


def artifact(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise Error("Artifacts require workspace-relative paths.")
    root = Path(root).resolve()
    raw = root / relative
    try:
        safe = raw.resolve().relative_to(root)
    except ValueError:
        raise Error("Artifact escapes the workspace (including through symlinks).")
    if any(part in (".git", ".hg", ".svn", DATA_DIR) for part in safe.parts):
        raise Error("Context metadata and VCS internals cannot be artifacts.")
    if not raw.is_file():
        return {"path": relative, "available": False, "sha256": None}
    if raw.stat().st_size > 256 * 1024 * 1024:
        raise Error("Artifact exceeds the 256 MiB hashing limit.")
    hasher = hashlib.sha256()
    with raw.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            hasher.update(block)
    return {"path": relative, "available": True, "sha256": hasher.hexdigest()}


def capture(root, project_id):
    root = Path(root).resolve()
    inside = git(root, "rev-parse", "--is-inside-work-tree", optional=True)
    if not inside:
        return {"project_id": project_id, "mode": "directory", "branch": None, "commit": None,
                "fingerprint": None, "limitations": ["Git is unavailable; only explicitly listed artifacts can be checked."]}
    commit = git(root, "rev-parse", "--verify", "HEAD", optional=True)
    branch = git(root, "symbolic-ref", "--short", "-q", "HEAD", optional=True)
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", *EXCLUDE)
    staged = git(root, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--binary", *EXCLUDE)
    unstaged = git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", *EXCLUDE)
    names = git(root, "ls-files", "--others", "--exclude-standard", "-z", *EXCLUDE).split(b"\0")
    if len(names) > 10001:
        raise Error("More than 10,000 untracked files; narrow the workspace or configure Git ignores first.")
    files, limitations = [], []
    for raw in names:
        if not raw:
            continue
        name = os.fsdecode(raw)
        try:
            files.append(artifact(root, name))
        except (Error, OSError):
            files.append({"path": name, "available": False, "sha256": None})
            limitations.append("Untracked file could not be hashed: " + name)
    tree = git(root, "ls-tree", "-r", "-z", "HEAD", optional=True)
    code_tree = None
    if tree is not None:
        entries = [entry for entry in tree.split(b"\0") if entry and
                   entry.split(b"\t", 1)[-1] != DATA_DIR.encode() and
                   not entry.split(b"\t", 1)[-1].startswith(DATA_DIR.encode() + b"/")]
        code_tree = digest(b"\0".join(entries))
    result = {"code_tree": code_tree, "project_id": project_id, "mode": "git", "branch": os.fsdecode(branch).strip() if branch else "(detached)",
              "commit": os.fsdecode(commit).strip() if commit else None, "dirty": bool(status),
              "changes_sha256": digest({"status": digest(status), "staged": digest(staged),
                                        "unstaged": digest(unstaged), "untracked": files}), "limitations": limitations}
    result["fingerprint"] = digest({key: result[key] for key in ("branch", "code_tree", "changes_sha256")})
    return result
