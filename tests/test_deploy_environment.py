from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from deploy.validate_environment import validate


class ProductionEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / ".env"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write(self, *, image: str, secret: str, port: str = "8080") -> None:
        self.path.write_text(
            "\n".join(
                (
                    f"ORCHESTRATOR_IMAGE={image}",
                    f"ORCHESTRATOR_GITHUB_WEBHOOK_SECRET={secret}",
                    f"ORCHESTRATOR_PORT={port}",
                )
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_accepts_digest_pin_and_runtime_secret(self) -> None:
        self.write(
            image=(
                "ghcr.io/wayne62314/ai-agent-orchestrator@sha256:"
                + ("a" * 64)
            ),
            secret="runtime-secret-with-at-least-32-characters",
        )
        validate(self.path)

    def test_rejects_mutable_tag(self) -> None:
        self.write(
            image="ghcr.io/wayne62314/ai-agent-orchestrator:0.7.0",
            secret="runtime-secret-with-at-least-32-characters",
        )
        with self.assertRaisesRegex(ValueError, "sha256 digest"):
            validate(self.path)

    def test_rejects_placeholder_or_short_secret(self) -> None:
        image = (
            "ghcr.io/wayne62314/ai-agent-orchestrator@sha256:" + ("b" * 64)
        )
        for secret in ("short", "REPLACE_WITH_LONG_RANDOM_SECRET"):
            with self.subTest(secret=secret):
                self.write(image=image, secret=secret)
                with self.assertRaisesRegex(ValueError, "non-placeholder"):
                    validate(self.path)

    def test_rejects_invalid_port(self) -> None:
        self.write(
            image=(
                "ghcr.io/wayne62314/ai-agent-orchestrator@sha256:"
                + ("c" * 64)
            ),
            secret="runtime-secret-with-at-least-32-characters",
            port="70000",
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            validate(self.path)


if __name__ == "__main__":
    unittest.main()
