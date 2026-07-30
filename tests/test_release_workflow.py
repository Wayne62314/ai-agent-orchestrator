from __future__ import annotations

import re
import unittest
from pathlib import Path


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.workflow = (
            cls.root / ".github" / "workflows" / "release-image.yml"
        ).read_text(encoding="utf-8")
        cls.ci_workflow = (
            cls.root / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        cls.production_compose = (
            cls.root / "deploy" / "compose.production.yaml"
        ).read_text(encoding="utf-8")
        cls.dockerfile = (cls.root / "Dockerfile").read_text(encoding="utf-8")

    def test_release_requires_semver_tag_from_main_and_matching_package(self) -> None:
        self.assertIn('tags:\n      - "v*.*.*"', self.workflow)
        self.assertNotIn("pull_request:", self.workflow)
        self.assertIn("merge-base --is-ancestor", self.workflow)
        self.assertIn('test "$version" = "$package_version"', self.workflow)

    def test_actions_are_commit_pinned_and_permissions_are_explicit(self) -> None:
        uses = re.findall(r"^\s*uses:\s*(\S+)", self.workflow, re.MULTILINE)
        self.assertEqual(len(uses), 2)
        for action in uses:
            self.assertRegex(action, r"^actions/[^@]+@[0-9a-f]{40}$")
        for permission in (
            "contents: read",
            "packages: write",
            "attestations: write",
            "id-token: write",
        ):
            self.assertIn(permission, self.workflow)

    def test_release_uses_immutable_tags_digest_and_attestation(self) -> None:
        self.assertIn('--tag "$IMAGE:$VERSION"', self.workflow)
        self.assertIn('--tag "$IMAGE:sha-$GITHUB_SHA"', self.workflow)
        self.assertNotIn("$IMAGE:latest", self.workflow)
        self.assertIn("containerimage.digest", self.workflow)
        self.assertIn("push-to-registry: true", self.workflow)
        self.assertIn('docker pull "$IMAGE@$DIGEST"', self.workflow)

    def test_registry_token_is_passed_on_stdin(self) -> None:
        self.assertIn("secrets.GITHUB_TOKEN", self.workflow)
        self.assertIn("--password-stdin", self.workflow)
        self.assertNotIn("docker login ghcr.io -p", self.workflow)

    def test_release_checks_volume_write_and_runtime_identity(self) -> None:
        self.assertIn("ENTRYPOINT", self.dockerfile)
        self.assertIn("container_entrypoint.py", self.dockerfile)
        self.assertIn('docker run --rm "$IMAGE@$DIGEST" id -un', self.workflow)
        self.assertIn("--user 0", self.workflow)
        self.assertIn('--volume "$volume:/data"', self.workflow)
        self.assertIn(')\" = \"10001\"', self.workflow)
        self.assertIn(
            "Verify container volume permissions and runtime identity",
            self.ci_workflow,
        )
        self.assertIn("ai-agent-orchestrator:ci id -un", self.ci_workflow)
        self.assertIn("--user 0", self.ci_workflow)
        self.assertIn(')\" = \"10001\"', self.ci_workflow)

    def test_production_compose_is_digest_pinned_and_hardened(self) -> None:
        self.assertIn("pin an image digest", self.production_compose)
        self.assertNotIn("build:", self.production_compose)
        self.assertIn("127.0.0.1:", self.production_compose)
        self.assertIn("no-new-privileges:true", self.production_compose)
        self.assertIn("cap_drop:\n      - ALL", self.production_compose)
        self.assertIn("read_only: true", self.production_compose)


if __name__ == "__main__":
    unittest.main()
