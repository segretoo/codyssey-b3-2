import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from postprocess import (  # noqa: E402
    PR_TITLE_MAX, COMMIT_TITLE_MAX, normalize_commit, normalize_pr, validate_commit, validate_pr,
)


class CommitTest(unittest.TestCase):
    def test_title_only(self):
        t, b, notes = normalize_commit("feat: 로그인 기능 추가")
        self.assertEqual((t, b, notes), ("feat: 로그인 기능 추가", "", []))

    def test_code_fence_and_prefix_removed(self):
        t, b, _ = normalize_commit("```\nCommit Message: fix: 오타 수정\n\n- main.py 수정\n```")
        self.assertEqual(t, "fix: 오타 수정")
        self.assertEqual(b, "- main.py 수정")

    def test_long_title_truncated_to_72(self):
        t, _, notes = normalize_commit("feat: " + "가" * 100)
        self.assertEqual(len(t), COMMIT_TITLE_MAX)
        self.assertTrue(t.endswith("…"))
        self.assertTrue(notes)

    def test_between_50_and_72_only_warns(self):
        title = "feat: " + "가" * 55
        t, _, notes = normalize_commit(title)
        self.assertEqual(t, title)
        self.assertEqual(len(notes), 1)

    def test_plain_body_becomes_bullets(self):
        t, b, notes = normalize_commit("fix: 버그 수정\n\n파일 a.py 수정\n경계값 처리")
        self.assertEqual(b, "- 파일 a.py 수정\n- 경계값 처리")
        self.assertEqual(validate_commit(t, b), [])
        self.assertTrue(any("불릿" in n for n in notes))

    def test_empty_output_has_fallback_title(self):
        t, _, _ = normalize_commit("")
        self.assertTrue(t)


class PRTest(unittest.TestCase):
    GOOD = "feat: PR 생성 기능 추가\n\n## Why\n- 이유\n## What\n- 변경\n## How to Test\n- python main.py pr"

    def test_good_output_passes_unchanged_in_structure(self):
        t, b, notes = normalize_pr(self.GOOD)
        self.assertEqual(t, "feat: PR 생성 기능 추가")
        self.assertEqual(validate_pr(t, b), [])
        self.assertEqual(notes, [])
        self.assertTrue(b.startswith("## Why"))

    def test_missing_sections_filled(self):
        t, b, notes = normalize_pr("feat: x\n\n## What\n- 변경")
        self.assertEqual(validate_pr(t, b), [])
        self.assertEqual(len(notes), 2)  # Why, How to Test

    def test_section_without_bullets_converted(self):
        t, b, _ = normalize_pr("feat: x\n\n## Why\n이유 설명\n## What\n변경 설명\n## How to Test\n실행해보기")
        self.assertEqual(validate_pr(t, b), [])
        self.assertIn("- 이유 설명", b)

    def test_long_title_truncated(self):
        t, b, _ = normalize_pr("feat: " + "나" * 120 + "\n\n" + self.GOOD.split("\n\n", 1)[1])
        self.assertLessEqual(len(t), PR_TITLE_MAX)
        self.assertEqual(validate_pr(t, b), [])

    def test_header_variants_and_order(self):
        raw = "PR Title: fix: 수정\n\n### how to test\n- 실행\n# WHAT\n- 변경\n## why\n- 이유"
        t, b, _ = normalize_pr(raw)
        self.assertEqual(t, "fix: 수정")
        self.assertLess(b.index("## Why"), b.index("## What"))
        self.assertLess(b.index("## What"), b.index("## How to Test"))
        self.assertEqual(validate_pr(t, b), [])

    def test_missing_title_line(self):
        t, b, notes = normalize_pr("## Why\n- a\n## What\n- b\n## How to Test\n- c")
        self.assertIn("제목", t)
        self.assertEqual(validate_pr(t, b), [])
        self.assertTrue(notes)

    def test_validate_catches_problems(self):
        self.assertTrue(validate_pr("t", "## Why\n- a"))


if __name__ == "__main__":
    unittest.main()
