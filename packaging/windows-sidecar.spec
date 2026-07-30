"""Reproducible PyInstaller recipe for the Windows desktop sidecar."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPEC).resolve().parent.parent

sdk_datas, sdk_binaries, sdk_hiddenimports = collect_all("openai_codex")
runtime_datas, runtime_binaries, runtime_hiddenimports = collect_all("codex_cli_bin")

datas = [
    *sdk_datas,
    *runtime_datas,
    *copy_metadata("openai-codex"),
    *copy_metadata("openai-codex-cli-bin"),
]
binaries = [*sdk_binaries, *runtime_binaries]
hiddenimports = [*sdk_hiddenimports, *runtime_hiddenimports]

analysis = Analysis(
    [str(ROOT / "packaging" / "desktop_sidecar.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
archive = PYZ(analysis.pure)

executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="agent-orchestrator-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="agent-orchestrator-sidecar-runtime",
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="agent-orchestrator-sidecar",
)
