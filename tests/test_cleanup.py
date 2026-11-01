import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pattyline.cleanup import clean
from pattyline.engine import Engine
from tests.support import Project, FakeRuntime, FakeSandbox, FakeGitHub


class CleanupTests(unittest.TestCase):
    def test_cleanup_keeps_dirty_inactive_and_terminal_identity(self):
        environment = {k: v for k, v in os.environ.items() if k not in {"CODEX_THREAD_ID", "PATTYLINE_WORKER"}}
        with patch.dict(os.environ, environment, clear=True):
            project = Project()
            self.addCleanup(project.close)
            project.approve_commit()
            engine = Engine(project.root, project.config, FakeRuntime(), FakeSandbox(), FakeGitHub(project.root, project.config))
            self.addCleanup(engine.state.close)
            result = engine.start(["feature/01"])
            worktree = Path(result["work"][0]["worktree"])
            (worktree / "untracked.txt").write_text("preserve me")
            preview = clean(project.root, project.config, True)
            self.assertEqual(preview[0]["action"], "preserve")
            self.assertTrue(worktree.exists())
            (worktree / "untracked.txt").unlink()
            self.assertEqual(clean(project.root, project.config)[0]["action"], "remove worktree")
            clean(project.root, project.config, True)
            self.assertFalse(worktree.exists())
            self.assertEqual(engine.state.get_work("feature/01")["state"], "integrated")
            self.assertTrue((project.root / ".pattyline/reports" / (result["run"]["id"] + ".json")).exists())
