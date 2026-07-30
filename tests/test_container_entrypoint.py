from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deploy import container_entrypoint


class ContainerEntrypointTests(unittest.TestCase):
    def test_root_prepares_volume_and_drops_privileges_before_exec(self) -> None:
        account = container_entrypoint.Account(10001, 10001, "/home/orchestrator")
        with (
            mock.patch.object(
                container_entrypoint.os,
                "geteuid",
                return_value=0,
                create=True,
            ),
            mock.patch.object(
                container_entrypoint,
                "resolve_account",
                return_value=account,
            ),
            mock.patch.object(container_entrypoint, "prepare_data_directory") as prepare,
            mock.patch.object(
                container_entrypoint.os,
                "initgroups",
                create=True,
            ) as initgroups,
            mock.patch.object(
                container_entrypoint.os,
                "setgid",
                create=True,
            ) as setgid,
            mock.patch.object(
                container_entrypoint.os,
                "setuid",
                create=True,
            ) as setuid,
            mock.patch.object(container_entrypoint.os, "execvp") as execvp,
            mock.patch.dict(container_entrypoint.os.environ, {}, clear=True),
        ):
            container_entrypoint.run(("agent-orchestrator", "--version"))

        prepare.assert_called_once_with(
            container_entrypoint.DATA_DIRECTORY,
            uid=10001,
            gid=10001,
        )
        initgroups.assert_called_once_with("orchestrator", 10001)
        setgid.assert_called_once_with(10001)
        setuid.assert_called_once_with(10001)
        execvp.assert_called_once_with(
            "agent-orchestrator",
            ["agent-orchestrator", "--version"],
        )

    def test_non_root_executes_without_changing_identity(self) -> None:
        with (
            mock.patch.object(
                container_entrypoint.os,
                "geteuid",
                return_value=10001,
                create=True,
            ),
            mock.patch.object(container_entrypoint, "resolve_account") as resolve,
            mock.patch.object(container_entrypoint.os, "execvp") as execvp,
        ):
            container_entrypoint.run(("python", "-V"))

        resolve.assert_not_called()
        execvp.assert_called_once_with("python", ["python", "-V"])

    def test_data_path_must_be_a_real_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data"
            path.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "must be a directory"):
                container_entrypoint.prepare_data_directory(
                    path,
                    uid=10001,
                    gid=10001,
                )

    @unittest.skipIf(
        not hasattr(Path, "symlink_to"),
        "symbolic links are unavailable",
    )
    def test_data_path_cannot_be_a_symbolic_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "data"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symbolic links require additional privileges")
            self.assertTrue(stat.S_ISLNK(link.lstat().st_mode))
            with self.assertRaisesRegex(RuntimeError, "must be a directory"):
                container_entrypoint.prepare_data_directory(
                    link,
                    uid=10001,
                    gid=10001,
                )


if __name__ == "__main__":
    unittest.main()
