import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safety import MASK, apply_safe_mode, count_sensitive, mask_text, split_diff  # noqa: E402


def make_diff(path: str, added: list[str]) -> str:
    body = "\n".join(f"+{ln}" for ln in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n{body}"


class MaskTextTest(unittest.TestCase):
    def test_api_key_formats(self):
        for secret in [
            "sk-" + "abcdefghijklmnopqrstuvwx",
            "AKIA" + "ABCDEFGHIJKLMNOP",
            "ghp_" + "a" * 36,
            "AIza" + "B" * 35,
        ]:
            out, n = mask_text(f"+token here {secret} end")
            self.assertNotIn(secret, out)
            self.assertGreaterEqual(n, 1)

    def test_email_phone_bearer_jwt(self):
        text = "mail me@example.com tel 010-1234-5678 Authorization: Bearer abcdefghijklmnop1234 jwt " + "eyJ" + "hbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4f"
        out, n = mask_text(text)
        for leaked in ["me@example.com", "010-1234-5678", "abcdefghijklmnop1234", "SflKxwRJSMeKKF2QT4f"]:
            self.assertNotIn(leaked, out)
        self.assertGreaterEqual(n, 4)

    def test_quoted_and_env_style_secrets(self):
        out, _ = mask_text('+DB_PASSWORD = "hunter2hunter2"\n+SECRET_KEY=abcd1234efgh')
        self.assertNotIn("hunter2hunter2", out)
        self.assertNotIn("abcd1234efgh", out)
        self.assertIn("DB_PASSWORD", out)  # 키 이름은 남겨서 AI 가 맥락을 이해할 수 있게 한다

    def test_normal_code_is_untouched(self):
        code = '+api_key = os.environ["AI_API_KEY"]\n+token = get_token()\n+password = None'
        out, n = mask_text(code)
        self.assertEqual(out, code)
        self.assertEqual(n, 0)

    def test_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\ndef\n-----END RSA PRIVATE KEY-----"
        out, n = mask_text("+" + pem)
        self.assertNotIn("MIIEabc", out)
        self.assertEqual(n, 1)

    def test_custom_pattern(self):
        out, n = mask_text("order EMP-12345 done", extra_patterns=[r"EMP-\d+"])
        self.assertNotIn("EMP-12345", out)
        self.assertEqual(n, 1)

    def test_count_sensitive_does_not_modify(self):
        self.assertEqual(count_sensitive("x me@example.com y"), 1)


class SafeModeTest(unittest.TestCase):
    def test_file_limit(self):
        diff = "\n".join(make_diff(f"f{i}.py", ["x = 1"]) for i in range(15))
        res = apply_safe_mode(diff, max_files=10, max_lines=1000)
        self.assertEqual((res.files_total, res.files_kept), (15, 10))
        self.assertNotIn("f12.py", res.text)
        self.assertIn("5개 파일 생략", res.text)

    def test_line_limit(self):
        diff = make_diff("big.py", [f"line{i} = {i}" for i in range(500)])
        res = apply_safe_mode(diff, max_files=10, max_lines=200)
        self.assertEqual(res.lines_kept, 200)
        self.assertIn("줄 수 제한", res.text)
        self.assertLessEqual(len(res.text.splitlines()), 201)

    def test_sensitive_file_body_not_sent(self):
        diff = make_diff(".env", ["API_KEY=supersecretvalue"]) + "\n" + make_diff("app.py", ["print(1)"])
        res = apply_safe_mode(diff)
        self.assertNotIn("supersecretvalue", res.text)
        self.assertEqual(res.redacted_files, [".env"])
        self.assertIn("print(1)", res.text)

    def test_env_example_is_not_redacted(self):
        res = apply_safe_mode(make_diff(".env.example", ["AI_API_KEY=your_key_here"]))
        self.assertEqual(res.redacted_files, [])

    def test_pem_in_subdir_redacted(self):
        res = apply_safe_mode(make_diff("certs/server.pem", ["abc"]))
        self.assertEqual(res.redacted_files, ["certs/server.pem"])

    def test_private_key_not_leaked_by_truncation(self):
        lines = ["x"] * 10 + ["-----BEGIN PRIVATE KEY-----", "SECRETBODY", "-----END PRIVATE KEY-----"]
        res = apply_safe_mode(make_diff("k.txt", lines), max_lines=9)
        self.assertNotIn("SECRETBODY", res.text)

    def test_split_diff(self):
        diff = make_diff("a.py", ["1"]) + "\n" + make_diff("b.py", ["2"])
        self.assertEqual(len(split_diff(diff)), 2)

    def test_mask_constant_used(self):
        res = apply_safe_mode(make_diff("a.py", ["e = 'a@b.com'"]))
        self.assertIn(MASK, res.text)
        self.assertEqual(res.masked_count, 1)


if __name__ == "__main__":
    unittest.main()
