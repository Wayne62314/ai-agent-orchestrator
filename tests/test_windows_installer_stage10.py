from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from agent_orchestrator import __version__


class WindowsInstallerStage10Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = Path(__file__).resolve().parents[1]
        cls.tauri_config = json.loads(
            (
                cls.repository / "desktop" / "src-tauri" / "tauri.conf.json"
            ).read_text(encoding="utf-8")
        )

    def test_desktop_versions_are_aligned(self) -> None:
        package = json.loads(
            (self.repository / "desktop" / "package.json").read_text(
                encoding="utf-8"
            )
        )
        with (self.repository / "desktop" / "src-tauri" / "Cargo.toml").open(
            "rb"
        ) as stream:
            cargo = tomllib.load(stream)
        with (self.repository / "pyproject.toml").open("rb") as stream:
            python_project = tomllib.load(stream)

        self.assertEqual(self.tauri_config["version"], "0.11.0")
        self.assertEqual(package["version"], self.tauri_config["version"])
        self.assertEqual(cargo["package"]["version"], self.tauri_config["version"])
        self.assertEqual(
            python_project["project"]["version"],
            self.tauri_config["version"],
        )
        self.assertEqual(__version__, self.tauri_config["version"])

    def test_release_binary_uses_the_windows_gui_subsystem(self) -> None:
        entrypoint = (
            self.repository / "desktop" / "src-tauri" / "src" / "main.rs"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]',
            entrypoint,
        )

    def test_nsis_is_a_per_user_non_downgrading_bundle(self) -> None:
        bundle = self.tauri_config["bundle"]
        windows = bundle["windows"]
        nsis = windows["nsis"]

        self.assertTrue(bundle["active"])
        self.assertEqual(bundle["targets"], "nsis")
        self.assertFalse(windows["allowDowngrades"])
        self.assertEqual(
            windows["webviewInstallMode"],
            {"type": "embedBootstrapper", "silent": True},
        )
        self.assertEqual(nsis["installMode"], "currentUser")
        self.assertEqual(nsis["compression"], "lzma")
        self.assertEqual(nsis["languages"], ["English", "SimpChinese"])
        self.assertEqual(nsis["startMenuFolder"], "AI Agent Orchestrator")
        self.assertEqual(nsis["installerHooks"], "./windows/installer-hooks.nsh")

    def test_login_start_is_explicit_and_defaults_to_no(self) -> None:
        hook = (
            self.repository
            / "desktop"
            / "src-tauri"
            / "windows"
            / "installer-hooks.nsh"
        ).read_text(encoding="utf-8")

        self.assertIn('GetOptions} $CMDLINE "/AUTOSTART"', hook)
        self.assertIn("MB_DEFBUTTON2", hook)
        self.assertIn(
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            hook,
        )
        self.assertIn("$UpdateMode = 0", hook)
        self.assertNotIn("$APPDATA", hook)
        self.assertNotIn("RMDir", hook)

    def test_ci_builds_and_uploads_a_checksummed_installer(self) -> None:
        workflow = (
            self.repository / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        collector = (
            self.repository / "packaging" / "collect-windows-installer.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("pnpm tauri build --bundles nsis", workflow)
        self.assertIn("test-windows-installer.ps1", workflow)
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            workflow,
        )
        self.assertIn("AI-Agent-Orchestrator-$($config.version)-x64-setup.exe", collector)
        self.assertIn("Get-FileHash", collector)
        self.assertIn("x86_64-pc-windows-msvc", collector)
        self.assertIn("sourceCommit = $sourceCommit", collector)
        self.assertIn("START-WINDOWS11-ACCEPTANCE.cmd", collector)
        self.assertIn("dist/windows-installer/**", workflow)

    def test_installer_smoke_matrix_covers_preservation_and_startup(self) -> None:
        smoke = (
            self.repository / "packaging" / "test-windows-installer.ps1"
        ).read_text(encoding="utf-8")
        native_shell = (
            self.repository / "desktop" / "src-tauri" / "src" / "lib.rs"
        ).read_text(encoding="utf-8")
        desktop = (
            self.repository / "desktop" / "src" / "App.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn('Invoke-InstallerProcess $InstallerPath @("/S"', smoke)
        self.assertIn('"/AUTOSTART"', smoke)
        self.assertIn("Assert-UninstalledAndDataPreserved", smoke)
        self.assertIn("Assert-LoginStartupAbsent", smoke)
        self.assertIn("--self-check", smoke)
        self.assertIn("desktop_state.warm_up();", native_shell)
        self.assertIn('"aiao-sidecar-warm-up"', native_shell)
        self.assertIn("refreshInFlight.current", desktop)
        self.assertIn("AddSeconds(60)", smoke)
        self.assertIn("First-launch diagnostics:", smoke)
        self.assertIn("sidecarCount=", smoke)

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_golden_journey_file_invocation_resolves_default_directory(self) -> None:
        script = (
            self.repository
            / "packaging"
            / "run-windows11-golden-journey.ps1"
        )

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=self.repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = result.stdout + result.stderr

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "must contain exactly one installer and one build manifest",
            output,
        )
        self.assertNotIn("parameter is an empty string", output.casefold())

    def test_golden_journey_launcher_distinguishes_errors_from_incomplete(self) -> None:
        launcher = (
            self.repository
            / "packaging"
            / "START-WINDOWS11-ACCEPTANCE.cmd"
        ).read_text(encoding="utf-8")

        self.assertIn('if "%AIAO_EXIT%"=="2"', launcher)
        self.assertIn("Acceptance could not start or encountered an error", launcher)

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell")
    def test_golden_journey_reads_utf8_evidence_with_chinese_text(self) -> None:
        source_script = (
            self.repository
            / "packaging"
            / "run-windows11-golden-journey.ps1"
        )
        check_ids = [
            "install.interactive",
            "launch.first",
            "launch.no-console",
            "auth.codex",
            "account.plan-truthful",
            "repository.select",
            "repository.changeable",
            "task.fields-empty",
            "task.create-feedback",
            "task.real",
            "acceptance.no-commands",
            "acceptance.evidence-separated",
            "accessibility.zoom-200",
            "accessibility.keyboard",
            "notification.local",
            "uninstall.copy",
            "feedback.recorded",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            script = candidate / source_script.name
            installer = candidate / "candidate.exe"
            evidence = candidate / "windows11-acceptance.json"
            shutil.copy2(source_script, script)
            installer.write_bytes(b"acceptance candidate")
            digest = hashlib.sha256(installer.read_bytes()).hexdigest()
            commit = "a" * 40
            (candidate / "candidate.build.json").write_text(
                json.dumps(
                    {
                        "file": installer.name,
                        "sourceCommit": commit,
                        "sha256": digest,
                        "appVersion": "test",
                    }
                ),
                encoding="utf-8",
            )
            evidence.write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "target": "windows-11",
                        "candidate": {
                            "version": "test",
                            "commit": commit,
                            "installerFile": installer.name,
                            "installerSha256": digest,
                        },
                        "environment": {
                            "productName": "Microsoft Windows 11 专业版",
                        },
                        "checks": [
                            {"id": check_id, "status": "passed", "notes": ""}
                            for check_id in check_ids
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ],
                cwd=candidate,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("The Windows 11 golden journey passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
