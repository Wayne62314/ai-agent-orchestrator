"""Prepare a mounted data directory, then run the application unprivileged."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import NamedTuple, NoReturn, Sequence

DATA_DIRECTORY = Path("/data")
APPLICATION_USER = "orchestrator"


class Account(NamedTuple):
    uid: int
    gid: int
    home: str


def resolve_account(name: str) -> Account:
    import pwd

    record = pwd.getpwnam(name)
    return Account(record.pw_uid, record.pw_gid, record.pw_dir)


def prepare_data_directory(path: Path, *, uid: int, gid: int) -> None:
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RuntimeError(f"{path} must be a directory.")
    os.chown(path, uid, gid)


def run(argv: Sequence[str]) -> NoReturn:
    if not argv:
        raise RuntimeError("A container command is required.")

    if os.geteuid() == 0:
        account = resolve_account(APPLICATION_USER)
        prepare_data_directory(DATA_DIRECTORY, uid=account.uid, gid=account.gid)
        os.initgroups(APPLICATION_USER, account.gid)
        os.setgid(account.gid)
        os.setuid(account.uid)
        os.environ["HOME"] = account.home

    os.execvp(argv[0], list(argv))


if __name__ == "__main__":
    run(sys.argv[1:])
