# -*- coding: utf-8 -*-
"""判题引擎集成测试：真实编译并运行 C++ 程序，覆盖各种评测状态。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bbjudge.judge import judge_submission
from bbjudge.models import Submission, load_problem


def _create_problem(
    root: Path,
    pid: str,
    *,
    tests: list[tuple[str, str]],
    time_limit_ms: int = 1000,
    memory_limit_mb: int = 256,
    score: float = 100,
    fill_answers: list[str] | None = None,
) -> Path:
    pdir = root / "problems" / pid
    (pdir / "tests").mkdir(parents=True, exist_ok=True)
    config: dict = {
        "id": pid,
        "title": pid,
        "type": "fill_result" if fill_answers is not None else "program",
        "time_limit_ms": time_limit_ms,
        "memory_limit_mb": memory_limit_mb,
        "score": score,
        "comparator": "token",
        "tests": [],
    }
    if fill_answers is not None:
        config["answers"] = fill_answers
    else:
        for index, (input_text, output_text) in enumerate(tests, start=1):
            inp = pdir / "tests" / f"{index:02d}.in"
            out = pdir / "tests" / f"{index:02d}.out"
            inp.write_text(input_text, encoding="utf-8")
            out.write_text(output_text, encoding="utf-8")
            config["tests"].append(
                {
                    "name": f"测试点 {index}",
                    "input": f"tests/{index:02d}.in",
                    "answer": f"tests/{index:02d}.out",
                    "score": score / len(tests),
                }
            )
    (pdir / "problem.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return pdir


SOURCES = {
    "ac.cpp": """
#include <iostream>
int main() { long long a, b; std::cin >> a >> b; std::cout << a + b << std::endl; }
""",
    "wa.cpp": """
#include <iostream>
int main() { long long a, b; std::cin >> a >> b; std::cout << a - b << std::endl; }
""",
    "ce.cpp": """
#include <iostream>
int main() { std::cout << 1 }
""",
    "re.cpp": """
#include <iostream>
int main() { int *p = nullptr; *p = 1; std::cout << 2 << std::endl; }
""",
    "tle.cpp": """
int main() { volatile long long x = 0; while (true) { x = x + 1; } }
""",
    "mle.cpp": """
#include <iostream>
int main() {
    volatile char *p = new char[100LL * 1024 * 1024];
    for (long long i = 0; i < 100LL * 1024 * 1024; i += 4096) p[i] = 1;
    std::cout << 3 << std::endl;
    delete[] p;
}
""",
}


class EngineTests(unittest.TestCase):
    def test_verdicts(self) -> None:
        if not __import__("shutil").which("g++"):
            self.skipTest("未安装 g++")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subs = root / "subs"
            subs.mkdir()
            for name, text in SOURCES.items():
                (subs / name).write_text(text, encoding="utf-8")
            (subs / "answer_ok.txt").write_text("42\n", encoding="utf-8")
            (subs / "answer_bad.txt").write_text("43\n", encoding="utf-8")

            add = load_problem(_create_problem(root, "p_add", tests=[("1 2", "3")]))
            fill = load_problem(
                _create_problem(root, "p_fill", tests=[], score=10, fill_answers=["42"])
            )
            mem = load_problem(
                _create_problem(root, "p_mem", tests=[("1 2", "3")], memory_limit_mb=64)
            )
            tle = load_problem(
                _create_problem(root, "p_tle", tests=[("", "1")], time_limit_ms=300)
            )

            cases = [
                ("AC", add, "ac.cpp", 100.0, "AC"),
                ("WA", add, "wa.cpp", 0.0, "WA"),
                ("CE", add, "ce.cpp", 0.0, "CE"),
                ("RE", add, "re.cpp", 0.0, "RE"),
                ("TLE", tle, "tle.cpp", 0.0, "TLE"),
                ("MLE", mem, "mle.cpp", 0.0, "MLE"),
                ("填空正确", fill, "answer_ok.txt", 10.0, "AC"),
                ("填空错误", fill, "answer_bad.txt", 0.0, "WA"),
            ]

            for label, problem, filename, expected_score, expected_verdict in cases:
                with self.subTest(label=label):
                    submission = Submission(
                        student="测试学生",
                        problem_id=problem.id,
                        file=subs / filename,
                    )
                    result = judge_submission(problem, submission, root)
                    self.assertEqual(result.verdict, expected_verdict, result.message)
                    self.assertAlmostEqual(result.score, expected_score)
                    if expected_verdict in ("WA", "RE"):
                        self.assertTrue(result.tests and result.tests[0].message)
                    if expected_verdict == "MLE":
                        self.assertGreater(result.memory_kb, 64 * 1024)
                    if expected_verdict == "CE":
                        self.assertIn("error", result.compile_output.lower())

    def test_fill_result_ignores_surrounding_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem = load_problem(
                _create_problem(root, "p_fill2", tests=[], score=5, fill_answers=["5050"])
            )
            answer = root / "a.txt"
            answer.write_text("  5050 \r\n", encoding="utf-8")
            result = judge_submission(
                problem,
                Submission(student="张三", problem_id=problem.id, file=answer),
                root,
            )
            self.assertEqual(result.verdict, "AC")
            self.assertEqual(result.score, 5)


if __name__ == "__main__":
    unittest.main()
