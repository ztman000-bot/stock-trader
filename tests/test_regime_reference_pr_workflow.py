"""Exercise publication against disposable local git repos; GitHub is always mocked."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/propose_regime_reference.py"
spec = importlib.util.spec_from_file_location("regime_pr", SCRIPT)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class RegimeReferencePRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.remote = Path(self.tmp.name) / "origin.git"
        self.work.mkdir()
        self.git("init", "--bare", str(self.remote))
        self.git("init")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("config", "user.name", "Local safety test")
        self.git("config", "user.email", "safety@example.invalid")
        for name in publisher.ARTIFACTS:
            target = self.work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("baseline\n", encoding="utf-8")
        self.git("add", "--", *publisher.ARTIFACTS)
        self.git("commit", "-m", "test baseline")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")
        self.base = self.git("rev-parse", "HEAD")
        self.calls = []
        self.pending = []
        self.fail_gh = None
        self.env = {
            "GITHUB_REPOSITORY": publisher.REPOSITORY,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
        }

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.work, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def invoke(self, *args):
        self.calls.append(args)
        if args[0] == "git":
            return self.git(*args[1:])
        if args[:3] == ("gh", "pr", self.fail_gh):
            raise subprocess.CalledProcessError(1, args)
        if args[:3] == ("gh", "pr", "list"):
            return json.dumps(self.pending)
        if args[:3] == ("gh", "pr", "create"):
            return "https://github.com/ztman000-bot/stock-trader/pull/999"
        self.fail(f"Unexpected external command: {args}")

    def propose(self):
        with contextlib.chdir(self.work), patch.dict(os.environ, self.env):
            with patch.object(publisher, "run", side_effect=self.invoke), contextlib.redirect_stdout(io.StringIO()):
                return publisher.propose()

    def change_aggregate(self):
        (self.work / publisher.ARTIFACTS[0]).write_text("updated aggregate\n", encoding="utf-8")

    def assert_main_unchanged(self):
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-parse", "main"), self.base)

    def assert_no_publish(self):
        self.assertFalse(any(c[:2] == ("git", "push") or c[:3] == ("gh", "pr", "create") for c in self.calls))
        self.assert_main_unchanged()

    def test_changed_aggregate_creates_only_new_branch_and_pr(self):
        self.change_aggregate()
        self.assertTrue(self.propose().endswith("/999"))
        branch = publisher.BRANCH_PREFIX + "12345-1"
        self.assertEqual(self.git("branch", "--show-current"), branch)
        self.assertEqual(
            self.git("--git-dir", str(self.remote), "diff", "--name-only", self.base, branch),
            publisher.ARTIFACTS[0],
        )
        self.assertNotIn("[skip ci]", self.git("log", "-1", "--format=%B"))
        self.assert_main_unchanged()
        create = next(c for c in self.calls if c[:3] == ("gh", "pr", "create"))
        self.assertIn("main", create)
        self.assertIn(branch, create)
        self.assertFalse(any("--force" in c or "merge" in c for c in self.calls))

    def test_no_changes_creates_nothing(self):
        self.assertIsNone(self.propose())
        self.assert_no_publish()

    def test_pending_pr_is_not_overwritten(self):
        self.change_aggregate()
        self.pending = [{"headRefName": publisher.BRANCH_PREFIX + "old", "number": 12, "url": "pending-pr"}]
        self.assertIsNone(self.propose())
        self.assert_no_publish()

    def test_truncated_pr_list_fails_closed(self):
        self.change_aggregate()
        self.pending = [{}] * 1000
        with self.assertRaisesRegex(ValueError, "complete open-PR list"):
            self.propose()
        self.assert_no_publish()

    def test_unexpected_untracked_raw_file_is_blocked(self):
        self.change_aggregate()
        (self.work / "research/regime/raw.parquet").write_bytes(b"not allowed")
        with self.assertRaisesRegex(ValueError, "outside the two aggregate"):
            self.propose()
        self.assert_no_publish()

    def test_staged_files_are_blocked(self):
        self.change_aggregate()
        self.git("add", "--", publisher.ARTIFACTS[0])
        with self.assertRaisesRegex(ValueError, "staged"):
            self.propose()
        self.assert_no_publish()

    def test_tracked_non_aggregate_change_is_blocked(self):
        (self.work / "control.py").write_text("LOCKED = True\n", encoding="utf-8")
        self.git("add", "control.py")
        self.git("commit", "-m", "test fixture adds protected code")
        self.git("push", "origin", "main")
        self.base = self.git("rev-parse", "HEAD")
        (self.work / "control.py").write_text("LOCKED = False\n", encoding="utf-8")
        self.change_aggregate()
        with self.assertRaisesRegex(ValueError, "outside the two aggregate"):
            self.propose()
        self.assert_no_publish()

    def test_missing_output_is_blocked(self):
        (self.work / publisher.ARTIFACTS[0]).unlink()
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.propose()
        self.assert_no_publish()

    def test_invalid_branch_suffix_is_blocked(self):
        self.env["GITHUB_RUN_ID"] = "main --force"
        with self.assertRaisesRegex(ValueError, "Numeric GitHub"):
            self.propose()
        self.assert_no_publish()

    def test_non_main_dispatch_is_blocked(self):
        self.env["GITHUB_REF"] = "refs/heads/unreviewed"
        with self.assertRaisesRegex(ValueError, "main only"):
            self.propose()
        self.assert_no_publish()

    def test_other_repository_is_blocked(self):
        self.env["GITHUB_REPOSITORY"] = "someone/another-repo"
        with self.assertRaisesRegex(ValueError, "configured Stock Trader"):
            self.propose()
        self.assert_no_publish()

    def test_changed_main_base_is_not_rebased_or_pushed(self):
        self.git("commit", "--allow-empty", "-m", "unreviewed local commit")
        with self.assertRaisesRegex(ValueError, "main changed"):
            self.propose()
        self.assert_no_publish()

    def test_existing_remote_branch_is_not_overwritten(self):
        branch = publisher.BRANCH_PREFIX + "12345-1"
        self.git("push", "origin", f"HEAD:refs/heads/{branch}")
        self.change_aggregate()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.propose()
        self.assert_no_publish()

    def test_permission_error_listing_prs_has_no_publish_fallback(self):
        self.change_aggregate()
        self.fail_gh = "list"
        with self.assertRaises(subprocess.CalledProcessError):
            self.propose()
        self.assert_no_publish()

    def test_pr_creation_denied_leaves_main_unchanged(self):
        self.change_aggregate()
        self.fail_gh = "create"
        with self.assertRaises(subprocess.CalledProcessError):
            self.propose()
        self.assert_main_unchanged()
        pushes = [c for c in self.calls if c[:2] == ("git", "push")]
        self.assertEqual(pushes, [("git", "push", "origin", f"HEAD:refs/heads/{publisher.BRANCH_PREFIX}12345-1")])


class RegimeWorkflowContractTests(unittest.TestCase):
    def test_workflow_requires_pr_and_prepublication_tests(self):
        workflow = (ROOT / ".github/workflows/marcap-regime-reference.yml").read_text(encoding="utf-8")
        self.assertIn("ref: main", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("pull-requests: write", workflow)
        self.assertIn("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", workflow)
        self.assertIn("python .github/scripts/propose_regime_reference.py", workflow)
        self.assertLess(workflow.index("python -m unittest discover"), workflow.index("run: python .github/scripts"))
        for forbidden in ("[skip ci]", "HEAD:main", "git push", "--force", "gh pr merge", "continue-on-error", "secrets.PAT", "actions: write"):
            self.assertNotIn(forbidden, workflow)

    def test_committed_summary_still_passes_existing_validator(self):
        import sys
        with patch.object(sys, "path", [str(ROOT / "server"), *sys.path]):
            import marcap_regime_builder
        report = marcap_regime_builder.validate_summary(ROOT / publisher.ARTIFACTS[0])
        self.assertTrue(report["ok"])
        self.assertTrue(report["researchOnly"])
        self.assertFalse(report["realOrderEnabled"])


if __name__ == "__main__":
    unittest.main()
