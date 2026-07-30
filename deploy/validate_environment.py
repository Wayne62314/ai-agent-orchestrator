"""Validate production deployment inputs without printing secret values."""

from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path

IMAGE_PATTERN = re.compile(
    r"^ghcr\.io/wayne62314/ai-agent-orchestrator@sha256:[0-9a-f]{64}$"
)


def load_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Line {number} must use NAME=VALUE.")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or name in values:
            raise ValueError(f"Line {number} has an invalid or duplicate name.")
        values[name] = value.strip()
    return values


def validate(path: Path) -> None:
    values = load_environment(path)
    image = values.get("ORCHESTRATOR_IMAGE", "")
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError(
            "ORCHESTRATOR_IMAGE must pin the expected GHCR image by sha256 digest."
        )
    secret = values.get("ORCHESTRATOR_GITHUB_WEBHOOK_SECRET", "")
    if len(secret) < 32 or "REPLACE" in secret.upper():
        raise ValueError(
            "ORCHESTRATOR_GITHUB_WEBHOOK_SECRET must be a non-placeholder "
            "value of at least 32 characters."
        )
    try:
        port = int(values.get("ORCHESTRATOR_PORT", "8080"))
    except ValueError as exc:
        raise ValueError("ORCHESTRATOR_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("ORCHESTRATOR_PORT must be between 1 and 65535.")

    if os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError(
                "Production environment file must not be accessible by group or others."
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("environment_file", type=Path)
    arguments = parser.parse_args()
    try:
        validate(arguments.environment_file)
    except (OSError, ValueError) as exc:
        print(f"Invalid production environment: {exc}")
        return 1
    print("Production environment is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
