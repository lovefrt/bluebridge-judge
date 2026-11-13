# -*- coding: utf-8 -*-
"""样例清单与报告生成测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bbjudge.judge import SubmissionResult, TestResult
from bbjudge.models import load_contest
from bbjudge.report import write_reports

ROOT = Path(__file__).resolve().parent.parent


class SampleContestTests(unittest.TestCase):
    def test_load_sample_contest(self) -> None:
        contest = load_contest(ROOT / "samples" / "contest.json")
        # 样例题必须存在；老师也可能在 Web 端追加自己的题目
        for pid in ("p1001", "p1002", "p1003"):
            self.assertIn(pid, contest.problems)
        self.assertEqual(len(contest.submissions), 11)
        self.assertEqual(contest.problems["p1001"].max_score, 100)
        self.assertTrue(contest.problems["p1001"].statement)


class ReportTests(unittest.TestCase):
    def _result(self, student: str, verdict: str, score: float) -> SubmissionResult:
        return SubmissionResult(
            student=student,
            problem_id="p1001",
            problem_title="A + B",
            language="cpp",
            verdict=verdict,
            score=score,
            max_score=100,
            time_ms=12.0,
            memory_kb=1024,
            tests=[
                TestResult(
                    index=1,
                    name="测试点 1",
                    verdict=verdict,
                    score=score,
                    max_score=100,
                    time_ms=12.0,
                    memory_kb=1024,
                    message="" if verdict == "AC" else "答案不对",
                )
            ],
        )

    def test_write_reports(self) -> None:
        contest = load_contest(ROOT / "samples" / "contest.json")
        results = [
            self._result("张三", "AC", 100),
            self._result("李四", "WA", 40),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(Path(tmp), contest, results)
            for path in paths.values():
                self.assertTrue(path.is_file(), path)
            html = paths["html"].read_text(encoding="utf-8")
            self.assertIn("张三", html)
            self.assertIn("李四", html)
            self.assertIn("badge WA", html)


if __name__ == "__main__":
    unittest.main()
