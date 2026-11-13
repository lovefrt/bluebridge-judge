# -*- coding: utf-8 -*-
"""输出比对单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bbjudge.compare import (
    CheckerError,
    compare_output,
    decode_bytes,
    normalize_newlines,
)


class DecodeTests(unittest.TestCase):
    def test_utf8(self) -> None:
        self.assertEqual(decode_bytes("你好".encode("utf-8")), "你好")

    def test_gbk(self) -> None:
        self.assertEqual(decode_bytes("你好".encode("gbk")), "你好")

    def test_bom(self) -> None:
        self.assertEqual(decode_bytes(b"\xef\xbb\xbf42"), "42")

    def test_newlines(self) -> None:
        self.assertEqual(normalize_newlines("a\r\nb\rc"), "a\nb\nc")


class TokenTests(unittest.TestCase):
    def test_ignores_all_whitespace(self) -> None:
        ok, _ = compare_output("token", "1 2\n3\n", "1   2 3")
        self.assertTrue(ok)

    def test_mismatch_message(self) -> None:
        ok, message = compare_output("token", "4000000000", "-294967296")
        self.assertFalse(ok)
        self.assertIn("4000000000", message)
        self.assertIn("-294967296", message)

    def test_too_few(self) -> None:
        ok, message = compare_output("token", "1 2 3", "1 2")
        self.assertFalse(ok)
        self.assertIn("过少", message)


class LineTests(unittest.TestCase):
    def test_trailing_spaces_and_blank_lines(self) -> None:
        ok, _ = compare_output("line", "a b\nc\n\n", "a b   \nc")
        self.assertTrue(ok)

    def test_internal_spacing_matters(self) -> None:
        ok, _ = compare_output("line", "a  b", "a b")
        self.assertFalse(ok)

    def test_line_number_in_message(self) -> None:
        ok, message = compare_output("line", "1\n2\n3", "1\n9\n3")
        self.assertFalse(ok)
        self.assertIn("第 2 行", message)


class ExactTests(unittest.TestCase):
    def test_exact_trailing_newline(self) -> None:
        ok, _ = compare_output("exact", "42\n", "42")
        self.assertFalse(ok)


class FloatTests(unittest.TestCase):
    def test_tolerance(self) -> None:
        ok, _ = compare_output("float", "3.1415926", "3.1415927", 1e-6)
        self.assertTrue(ok)

    def test_out_of_tolerance(self) -> None:
        ok, message = compare_output("float", "1.0", "1.01", 1e-3)
        self.assertFalse(ok)
        self.assertIn("误差", message)

    def test_non_numeric(self) -> None:
        ok, message = compare_output("float", "1.0", "abc", 1e-6)
        self.assertFalse(ok)
        self.assertIn("不是数值", message)


class CheckerTests(unittest.TestCase):
    def test_custom_checker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checker = Path(tmp) / "checker.py"
            checker.write_text(
                "def check(inp, expected, actual):\n"
                "    return (actual.strip().lower() == expected.strip().lower(), '忽略大小写')\n",
                encoding="utf-8",
            )
            ok, _ = compare_output("checker", "YES", "yes", checker=checker)
            self.assertTrue(ok)
            ok, message = compare_output("checker", "YES", "no", checker=checker)
            self.assertFalse(ok)
            self.assertEqual(message, "忽略大小写")

    def test_broken_checker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checker = Path(tmp) / "checker.py"
            checker.write_text("def check(a, b, c):\n    raise RuntimeError('boom')\n", encoding="utf-8")
            with self.assertRaises(CheckerError):
                compare_output("checker", "1", "1", checker=checker)


if __name__ == "__main__":
    unittest.main()
