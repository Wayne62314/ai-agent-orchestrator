from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_orchestrator.adapters.codex_sdk import CodexSdkExecutionAdapter
from agent_orchestrator.desktop_rpc import main


class DesktopPackagingStage10Tests(unittest.TestCase):
    def test_runtime_status_reports_pinned_sdk_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "codex.exe"
            runtime.write_bytes(b"runtime")
            adapter = CodexSdkExecutionAdapter()
            fake_module = SimpleNamespace(bundled_codex_path=lambda: runtime)

            with (
                patch.object(adapter, "validate_environment"),
                patch(
                    "agent_orchestrator.adapters.codex_sdk.importlib.metadata.version",
                    side_effect=lambda package: {
                        "openai-codex": "0.144.4",
                        "openai-codex-cli-bin": "0.144.4",
                    }[package],
                ),
                patch(
                    "agent_orchestrator.adapters.codex_sdk.importlib.import_module",
                    return_value=fake_module,
                ),
            ):
                status = adapter.runtime_status()

        self.assertEqual(status["sdkVersion"], "0.144.4")
        self.assertEqual(status["runtimeVersion"], "0.144.4")
        self.assertEqual(status["runtimeFile"], "codex.exe")

    def test_self_check_does_not_require_a_database(self) -> None:
        output = io.StringIO()
        with (
            patch.object(
                CodexSdkExecutionAdapter,
                "runtime_status",
                return_value={
                    "sdkVersion": "0.144.4",
                    "runtimeVersion": "0.144.4",
                    "runtimeFile": "codex.exe",
                },
            ),
            redirect_stdout(output),
        ):
            result = main(["--self-check"])

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["healthy"])
        self.assertEqual(payload["codexRuntime"]["file"], "codex.exe")

    def test_tauri_config_declares_sidecar_and_private_runtime(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        config = json.loads(
            (repository / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            config["bundle"]["externalBin"],
            ["binaries/agent-orchestrator-sidecar"],
        )
        self.assertEqual(
            config["bundle"]["resources"][
                "binaries/agent-orchestrator-sidecar-runtime/"
            ],
            "agent-orchestrator-sidecar-runtime/",
        )
        self.assertTrue(config["bundle"]["active"])


if __name__ == "__main__":
    unittest.main()
