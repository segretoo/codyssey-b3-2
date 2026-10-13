import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitcollect import GitError, collect_changes, parse_branch  # noqa: E402


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True,
    )


class GitCollectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        git(self.d, "init", "-q", "-b", "main")
        with open(os.path.join(self.d, "a.txt"), "w", encoding="utf-8") as f:
            f.write("one\n")
        git(self.d, "add", ".")
        git(self.d, "commit", "-q", "-m", "init")

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_repo_has_no_changes(self):
        c = collect_changes(self.d)
        self.assertEqual(c.status_lines, [])
        self.assertEqual(c.branch, "main")

    def test_modified_file_collected(self):
        with open(os.path.join(self.d, "a.txt"), "a", encoding="utf-8") as f:
            f.write("two\n")
        c = collect_changes(self.d)
        self.assertEqual(c.file_count, 1)
        self.assertIn("+two", c.diff)

    def test_staged_and_unstaged_combined_and_staged_only(self):
        with open(os.path.join(self.d, "a.txt"), "a", encoding="utf-8") as f:
            f.write("staged\n")
        git(self.d, "add", "a.txt")
        with open(os.path.join(self.d, "a.txt"), "a", encoding="utf-8") as f:
            f.write("unstaged\n")
        both = collect_changes(self.d)
        self.assertIn("+staged", both.diff)
        self.assertIn("+unstaged", both.diff)
        only = collect_changes(self.d, staged_only=True)
        self.assertIn("+staged", only.diff)
        self.assertNotIn("+unstaged", only.diff)

    def test_untracked_only_has_status_but_empty_diff(self):
        with open(os.path.join(self.d, "new.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        c = collect_changes(self.d)
        self.assertEqual(c.file_count, 1)
        self.assertEqual(c.diff, "")

    def test_non_ascii_filename(self):
        with open(os.path.join(self.d, "한글.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        c = collect_changes(self.d)
        self.assertIn("한글.txt", c.status_lines[0])

    def test_not_a_repo(self):
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaises(GitError) as cm:
                collect_changes(plain)
            self.assertIn("Git 저장소가 아닙니다", str(cm.exception))

    def test_parse_branch(self):
        self.assertEqual(parse_branch("## main...origin/main [ahead 1]"), "main")
        self.assertEqual(parse_branch("## feature/x"), "feature/x")
        self.assertEqual(parse_branch("## No commits yet on dev"), "dev")
        self.assertEqual(parse_branch("## HEAD (no branch)"), "(detached HEAD)")


if __name__ == "__main__":
    unittest.main()
