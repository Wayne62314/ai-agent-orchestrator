from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
STATUSES = {"passed", "failed", "blocked", "not-tested"}
REQUIRED_CHECKS = {
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
}
TARGETS = {"windows-10-22h2", "windows-11"}
SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_ -]?key|access[_ -]?token|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
)


class AcceptanceError(ValueError):
    pass


def load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"{path}: cannot read acceptance JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise AcceptanceError(f"{path}: report root must be an object")
    return report


def _required_text(container: dict[str, Any], key: str, prefix: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceError(f"{prefix}.{key} must be non-empty text")
    return value.strip()


def _reject_sensitive_data(report: dict[str, Any]) -> None:
    serialized = json.dumps(report, ensure_ascii=False)
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(serialized):
            raise AcceptanceError(
                "report appears to contain a credential or authorization secret"
            )


def validate_report(
    report: dict[str, Any], *, require_complete: bool = False
) -> list[str]:
    if report.get("schemaVersion") != SCHEMA_VERSION:
        raise AcceptanceError(
            f"schemaVersion must be {SCHEMA_VERSION}, got "
            f"{report.get('schemaVersion')!r}"
        )

    target = _required_text(report, "target", "report")
    if target not in TARGETS:
        raise AcceptanceError(f"target must be one of {sorted(TARGETS)}")

    candidate = report.get("candidate")
    if not isinstance(candidate, dict):
        raise AcceptanceError("candidate must be an object")
    _required_text(candidate, "version", "candidate")
    commit = _required_text(candidate, "commit", "candidate")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise AcceptanceError("candidate.commit must be a full lowercase Git SHA")
    _required_text(candidate, "installerFile", "candidate")
    digest = _required_text(candidate, "installerSha256", "candidate")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AcceptanceError(
            "candidate.installerSha256 must be a lowercase SHA-256 digest"
        )

    environment = report.get("environment")
    if not isinstance(environment, dict):
        raise AcceptanceError("environment must be an object")
    for key in ("productName", "displayVersion", "build", "architecture"):
        _required_text(environment, key, "environment")

    checks = report.get("checks")
    if not isinstance(checks, list):
        raise AcceptanceError("checks must be an array")
    seen: set[str] = set()
    incomplete: list[str] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise AcceptanceError(f"checks[{index}] must be an object")
        check_id = _required_text(check, "id", f"checks[{index}]")
        if check_id in seen:
            raise AcceptanceError(f"duplicate check id: {check_id}")
        seen.add(check_id)
        status = _required_text(check, "status", f"checks[{index}]")
        if status not in STATUSES:
            raise AcceptanceError(
                f"checks[{index}].status must be one of {sorted(STATUSES)}"
            )
        notes = check.get("notes", "")
        if not isinstance(notes, str):
            raise AcceptanceError(f"checks[{index}].notes must be text")
        if status in {"failed", "blocked"} and not notes.strip():
            raise AcceptanceError(
                f"{check_id}: failed or blocked checks require explanatory notes"
            )
        if status != "passed":
            incomplete.append(check_id)

    missing = sorted(REQUIRED_CHECKS - seen)
    if missing:
        raise AcceptanceError(f"missing required checks: {', '.join(missing)}")
    _reject_sensitive_data(report)
    if require_complete and incomplete:
        raise AcceptanceError(
            "acceptance is incomplete: " + ", ".join(sorted(incomplete))
        )
    return incomplete


def validate_matrix(paths: list[Path]) -> None:
    if not paths:
        raise AcceptanceError("at least one report path is required")
    reports = [(path, load_report(path)) for path in paths]
    candidates: set[tuple[str, str, str]] = set()
    targets: set[str] = set()
    for path, report in reports:
        try:
            validate_report(report, require_complete=True)
        except AcceptanceError as exc:
            raise AcceptanceError(f"{path}: {exc}") from exc
        candidate = report["candidate"]
        candidates.add(
            (
                candidate["version"],
                candidate["commit"],
                candidate["installerSha256"],
            )
        )
        targets.add(report["target"])
    if len(candidates) != 1:
        raise AcceptanceError(
            "matrix reports must describe the same version, commit, and installer hash"
        )
    missing_targets = sorted(TARGETS - targets)
    if missing_targets:
        raise AcceptanceError(
            "matrix is incomplete; missing targets: " + ", ".join(missing_targets)
        )


def validate_windows11_recovery(path: Path) -> None:
    report = load_report(path)
    validate_report(report, require_complete=True)
    if report["target"] != "windows-11":
        raise AcceptanceError(
            "Windows 11 recovery requires a windows-11 acceptance report"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Stage 11 Windows client acceptance evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("path", type=Path)
    report_parser.add_argument("--require-complete", action="store_true")
    matrix_parser = subparsers.add_parser("matrix")
    matrix_parser.add_argument("paths", nargs="+", type=Path)
    recovery_parser = subparsers.add_parser("windows11-recovery")
    recovery_parser.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "report":
            report = load_report(args.path)
            incomplete = validate_report(report, require_complete=args.require_complete)
            if incomplete:
                print(
                    "Valid report; incomplete checks: " + ", ".join(sorted(incomplete))
                )
            else:
                print("Valid and complete acceptance report.")
        elif args.command == "matrix":
            validate_matrix(args.paths)
            print("Valid and complete Windows 10/11 acceptance matrix.")
        else:
            validate_windows11_recovery(args.path)
            print("Valid and complete Windows 11 recovery acceptance.")
    except AcceptanceError as exc:
        print(f"Acceptance validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
