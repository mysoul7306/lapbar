"""Timestamped, owner-only backups taken before lapbar overwrites anything."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterator

# Files lapbar touches (settings.json above all) may carry tokens: backups are
# owner-only, or narrower when the original already is.
BACKUP_MODE = 0o600


def _candidates(path: Path, now: datetime) -> Iterator[Path]:
    base = path.with_name(f"{path.name}.bak-{now.strftime('%Y%m%d-%H%M%S')}")
    yield base
    n = 1
    while True:
        yield base.with_name(f"{base.name}-{n}")
        n += 1


def write_backup(path: Path, now: datetime | None = None) -> Path:
    """Copy `path` (following symlinks) to `<path>.bak-<stamp>` next to it.

    O_EXCL guarantees an existing backup is never reused or overwritten.
    """
    payload = path.read_bytes()
    mode = BACKUP_MODE & (path.stat().st_mode & 0o777)
    for candidate in _candidates(path, now or datetime.now()):
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), mode)  # exact mode regardless of umask
            fh.write(payload)
        return candidate
    raise AssertionError("unreachable: candidate generator is infinite")
