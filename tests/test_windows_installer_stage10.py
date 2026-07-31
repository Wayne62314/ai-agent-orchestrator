from __future__ import annotations

import json
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

        self.assertIn('Invoke-InstallerProcess $InstallerPath @("/S"', smoke)
        self.assertIn('"/AUTOSTART"', smoke)
        self.assertIn("Assert-UninstalledAndDataPreserved", smoke)
        self.assertIn("Assert-LoginStartupAbsent", smoke)
        self.assertIn("--self-check", smoke)


if __name__ == "__main__":
    unittest.main()
