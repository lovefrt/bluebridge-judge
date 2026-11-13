"""题目、比赛与提交的数据模型及加载逻辑。"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_LANGUAGES = {
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".c": "c",
    ".java": "java",
    ".py": "python",
}

COMPARE_MODES = ("token", "line", "exact", "float", "checker")


class ConfigError(Exception):
    """配置文件或目录结构有误。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"找不到配置文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON 格式错误：{path}（第 {exc.lineno} 行）") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是对象：{path}")
    return data


def _resolve(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p)


def _as_int(value: Any, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是整数，当前为 {value!r}") from exc
    if result <= 0:
        raise ConfigError(f"{name} 必须是正整数，当前为 {result}")
    return result


def infer_language(path: Path) -> str | None:
    """根据扩展名推断语言。"""
    return SUPPORTED_LANGUAGES.get(path.suffix.lower())


@dataclass(frozen=True)
class TestCase:
    """一个测试点。"""

    index: int
    name: str
    score: float
    input: Path | None
    answer: Path | None


@dataclass
class Problem:
    """一道题。"""

    id: str
    title: str
    type: str
    base_dir: Path
    time_limit_ms: int
    memory_limit_mb: int
    total_score: float
    comparator: str
    float_tolerance: float
    tests: list[TestCase] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    ignore_case: bool = False
    checker: Path | None = None
    statement: str = ""
    description: str = ""
    input_description: str = ""
    output_description: str = ""
    samples: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_fill_result(self) -> bool:
        return self.type == "fill_result"

    @property
    def max_score(self) -> float:
        if self.is_fill_result:
            return self.total_score
        return sum(t.score for t in self.tests)

    @property
    def display_score(self) -> float:
        """编辑/列表展示用：草稿编程题还没有测试点时显示配置总分。"""
        return self.max_score if self.tests else self.total_score

    @property
    def is_ready(self) -> bool:
        """题目是否已经可以让学生提交。"""
        if self.is_fill_result:
            return bool(self.answers)
        return bool(self.tests)


def _build_tests(
    problem_dir: Path,
    raw_tests: list[dict[str, Any]],
    total_score: float,
) -> list[TestCase]:
    if not raw_tests:
        return []

    explicit = [t.get("score") for t in raw_tests]
    if all(s is None for s in explicit):
        share = total_score / len(raw_tests)
        scores = [share] * len(raw_tests)
    else:
        scores = []
        for i, s in enumerate(explicit):
            if s is None:
                raise ConfigError(
                    f"{problem_dir.name} 的测试点 {i + 1} 缺少 score，"
                    "要么全部省略，要么全部写明"
                )
            scores.append(float(s))

    tests: list[TestCase] = []
    for i, raw in enumerate(raw_tests, start=1):
        input_value = raw.get("input")
        answer_value = raw.get("answer")
        input_path = _resolve(problem_dir, input_value) if input_value else None
        answer_path = _resolve(problem_dir, answer_value) if answer_value else None
        if input_path is not None and not input_path.is_file():
            raise ConfigError(f"测试点输入文件不存在：{input_path}")
        if answer_path is not None and not answer_path.is_file():
            raise ConfigError(f"测试点答案文件不存在：{answer_path}")
        tests.append(
            TestCase(
                index=i,
                name=str(raw.get("name") or f"测试点 {i}"),
                score=scores[i - 1],
                input=input_path,
                answer=answer_path,
            )
        )
    return tests


def load_problem(problem_dir: Path) -> Problem:
    """从目录中的 problem.json 加载一道题。"""
    config_path = problem_dir / "problem.json"
    data = _read_json(config_path)

    problem_type = str(data.get("type", "program")).lower()
    if problem_type not in ("program", "fill_result"):
        raise ConfigError(
            f"{config_path} 的 type 只能是 program 或 fill_result，"
            f"当前为 {problem_type!r}"
        )

    time_limit_ms = _as_int(data.get("time_limit_ms"), 1000, "time_limit_ms")
    memory_limit_mb = _as_int(data.get("memory_limit_mb"), 256, "memory_limit_mb")
    total_score = float(data.get("score", 100))

    comparator_raw = data.get("comparator", "token")
    tolerance = 1e-6
    if isinstance(comparator_raw, dict):
        comparator = str(comparator_raw.get("mode", "token")).lower()
        tolerance = float(comparator_raw.get("tolerance", 1e-6))
    else:
        comparator = str(comparator_raw).lower()
    if comparator not in COMPARE_MODES:
        raise ConfigError(
            f"{config_path} 的 comparator 不支持 {comparator!r}，"
            f"可选：{', '.join(COMPARE_MODES)}"
        )

    checker_raw = data.get("checker")
    checker = _resolve(problem_dir, checker_raw) if checker_raw else None
    if checker is not None:
        if not checker.is_file():
            raise ConfigError(f"自定义比对脚本不存在：{checker}")
        comparator = "checker"

    answers: list[str] = []
    for key in ("answer", "answers"):
        value = data.get(key)
        if isinstance(value, str):
            answers.append(value)
        elif isinstance(value, list):
            answers.extend(str(v) for v in value)
    answer_file = data.get("answer_file")
    if answer_file:
        answer_path = _resolve(problem_dir, answer_file)
        if not answer_path.is_file():
            raise ConfigError(f"答案文件不存在：{answer_path}")
        answers.append(answer_path.read_text(encoding="utf-8-sig"))
    # 去掉纯空白答案，避免误判
    answers = [a for a in answers if a.strip()]
    if problem_type == "fill_result" and not answers:
        raise ConfigError(f"{config_path} 是结果填空题，但缺少 answer/answers")

    statement = ""
    statement_path = problem_dir / "statement.md"
    if statement_path.is_file():
        statement = statement_path.read_text(encoding="utf-8-sig")

    description_raw = data.get("description")
    if isinstance(description_raw, str):
        description = description_raw.strip()
    elif statement.strip():
        description = (
            '<pre class="legacy-statement">' + html.escape(statement) + "</pre>"
        )
    else:
        description = ""

    input_description = str(data.get("input_description") or "").strip()
    output_description = str(data.get("output_description") or "").strip()
    samples: list[dict[str, str]] = []
    raw_samples = data.get("samples")
    if isinstance(raw_samples, list):
        for item in raw_samples[:50]:
            if not isinstance(item, dict):
                continue
            sample_input = str(item.get("input") or "")
            sample_output = str(item.get("output") or "")
            if not sample_input.strip() and not sample_output.strip():
                continue
            samples.append({"input": sample_input, "output": sample_output})

    tests = _build_tests(problem_dir, data.get("tests") or [], total_score)
    if problem_type == "program":
        for test in tests:
            if test.answer is None:
                raise ConfigError(
                    f"{config_path} 的测试点 {test.index} 缺少 answer（编程题必须有标准输出）"
                )

    return Problem(
        id=str(data.get("id") or problem_dir.name),
        title=str(data.get("title") or problem_dir.name),
        type=problem_type,
        base_dir=problem_dir,
        time_limit_ms=time_limit_ms,
        memory_limit_mb=memory_limit_mb,
        total_score=total_score,
        comparator=comparator,
        float_tolerance=tolerance,
        tests=tests,
        answers=answers,
        ignore_case=bool(data.get("ignore_case", False)),
        checker=checker,
        statement=statement,
        description=description,
        input_description=input_description,
        output_description=output_description,
        samples=samples,
    )


@dataclass(frozen=True)
class Submission:
    """一次学生提交。"""

    student: str
    problem_id: str
    file: Path
    language: str | None = None


@dataclass
class Contest:
    """一场比赛/一次作业。"""

    name: str
    base_dir: Path
    problems: dict[str, Problem]
    submissions: list[Submission]
    problem_order: list[str]
    problems_dir: Path | None = None


def load_contest(contest_path: Path, allow_empty: bool = False) -> Contest:
    """加载 contest.json 及其引用的题目。

    allow_empty=True 时允许题库为空（Web 端删光题目后仍能启动），
    并跳过引用了已删除题目的历史提交。
    """
    data = _read_json(contest_path)
    base = contest_path.parent

    problems_dir_raw = data.get("problems_dir", "problems")
    problems_dir = _resolve(base, str(problems_dir_raw))
    if not problems_dir.is_dir():
        if allow_empty:
            problems_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise ConfigError(f"题目目录不存在：{problems_dir}")

    problem_dirs: list[Path] = []
    for entry in sorted(problems_dir.iterdir()):
        if entry.is_dir() and (entry / "problem.json").is_file():
            problem_dirs.append(entry)
    if not problem_dirs and not allow_empty:
        raise ConfigError(f"题目目录中没有找到任何 problem.json：{problems_dir}")

    problems: dict[str, Problem] = {}
    order: list[str] = []
    for pdir in problem_dirs:
        problem = load_problem(pdir)
        if problem.id in problems:
            raise ConfigError(f"题目 id 重复：{problem.id}")
        problems[problem.id] = problem
        order.append(problem.id)

    raw_submissions = data.get("submissions", [])
    if not isinstance(raw_submissions, list):
        raise ConfigError(f"{contest_path} 的 submissions 必须是数组")

    submissions: list[Submission] = []
    for i, raw in enumerate(raw_submissions, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"第 {i} 条提交必须是对象")
        student = str(raw.get("student") or "").strip()
        problem_id = str(raw.get("problem") or "").strip()
        file_value = raw.get("file")
        if not student:
            raise ConfigError(f"第 {i} 条提交缺少 student")
        if problem_id not in problems:
            if allow_empty:
                continue
            raise ConfigError(f"第 {i} 条提交引用了未知题目：{problem_id!r}")
        if not file_value:
            raise ConfigError(f"第 {i} 条提交缺少 file")
        file_path = _resolve(base, str(file_value))
        if not file_path.is_file():
            raise ConfigError(f"提交文件不存在：{file_path}")
        language = raw.get("language")
        if language is None:
            language = infer_language(file_path)
        if language is not None:
            language = str(language).lower()
            if language not in ("cpp", "c", "java", "python"):
                raise ConfigError(f"不支持的语言：{language!r}")
        submissions.append(
            Submission(
                student=student,
                problem_id=problem_id,
                file=file_path,
                language=language,
            )
        )

    return Contest(
        name=str(data.get("name") or contest_path.stem),
        base_dir=base,
        problems=problems,
        submissions=submissions,
        problem_order=order,
        problems_dir=problems_dir,
    )
