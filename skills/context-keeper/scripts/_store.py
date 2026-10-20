"""Private file primitives for ContextKeeper. Python standard library only."""
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

VERSION = "0.5.0"
DATA_DIR = ".context-keeper"
SCHEMA = 1
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b"),
    re.compile(r'''(?i)\b(?:api[_-]?key|access[_-]?token|password|secret)["']?\s*[:=]\s*["']?[^\s,;"'}]+["']?'''),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}"),
]


class Error(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encoded(value)).hexdigest()


def redact(value):
    if isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.fullmatch(r"(?i)(api[_-]?key|access[_-]?token|password|secret)", key)
                      else redact(item)) for key, item in value.items()}
    return value


def identity(value):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise Error("Use a lowercase task/experience ID containing only letters, digits and hyphens.")
    return value


def object_fields(data, allowed, required=()):
    if not isinstance(data, dict):
        raise Error("Expected a JSON object.")
    if set(data) - set(allowed):
        raise Error("Unknown fields: " + ", ".join(sorted(set(data) - set(allowed))))
    if set(required) - set(data):
        raise Error("Missing fields: " + ", ".join(sorted(set(required) - set(data))))


def text(value, name, maximum=20000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise Error(name + " must be a nonempty string within its length limit.")
    return value.strip()


def texts(value, name, maximum=100):
    if not isinstance(value, list) or len(value) > maximum:
        raise Error(name + " must be a list of at most %d strings." % maximum)
    return [text(item, name) for item in value]


def integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Error(name + " must be an integer >= %d." % minimum)
    return value


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise Error("Context data must not be symlinked: " + path.name)
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise Error("JSON file exceeds 2 MiB.")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Error("Cannot read valid JSON from " + str(path)) from error


def confined(root, *parts):
    """Do not follow symlinks in generated state directories, even inside the root."""
    root = Path(root).resolve()
    result = root
    for part in parts:
        result = result / part
    try:
        relative = result.relative_to(root)
    except ValueError:
        raise Error("Context path escapes its root.")
    current = root
    for part in relative.parts:
        if part in ("..", "."):
            raise Error("Invalid context path.")
        current = current / part
        if current.is_symlink():
            raise Error("Context paths cannot contain symlinks.")
    return result


def atomic_write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise Error("Refusing to overwrite a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".ck-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = confined(directory, ".lock")
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a+b") as handle:
        deadline = time.monotonic() + 10
        if os.name == "nt":
            import msvcrt
            handle.write(b"0")
            handle.flush()
        else:
            import fcntl
        while True:
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Error("Another writer is active; retry after it finishes.")
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def seal(record):
    return {**record, "integrity": {"algorithm": "sha256", "payload": digest(record)}}


def verify(record):
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA:
        raise Error("Unsupported or missing context schema version.")
    body = {key: value for key, value in record.items() if key != "integrity"}
    if record.get("integrity") != {"algorithm": "sha256", "payload": digest(body)}:
        raise Error("Context integrity check failed; do not edit immutable checkpoint/version files.")
    return record


def immutable_directory(destination, files):
    destination = Path(destination)
    if destination.exists():
        raise Error("Immutable revision already exists; recover its pointer or use a new revision.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=str(destination.parent)))
    try:
        for name, content in files.items():
            atomic_write(staging / name, content)
        os.rename(str(staging), str(destination))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def quote(value):
    return "\n".join("> " + line for line in str(value).splitlines())
