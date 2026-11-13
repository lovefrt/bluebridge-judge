"""判题核心：读取提交、编译、逐测试点运行、比对并汇总成绩。"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import compare
from .models import Problem, Submission
from .runner import run_program

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
COMPILE_TIME_MS = 20_000
COMPILE_MEMORY_MB = 1024

LANGUAGE_NAMES = {
    "cpp": "C++",
    "c": "C",
    "java": "Java",
    "python": "Python",
    "text": "结果填空",
    "unknown": "未知",
}

LANGUAGE_SUFFIXES = {
    "cpp": (".cpp", ".cc", ".cxx", ".c++"),
    "c": (".c",),
    "java": (".java",),
    "python": (".py",),
}

# 解释器/虚拟机自身的启动时间与内存开销，判题时给予补偿。
LANGUAGE_TIME_OVERHEAD_MS = {"cpp": 0, "c": 0, "java": 300, "python": 120}
LANGUAGE_MEMORY_OVERHEAD_MB = {"cpp": 0, "c": 0, "java": 160, "python": 24}


@dataclass
class TestResult:
    index: int
    name: str
    verdict: str
    score: float
    max_score: float
    time_ms: float = 0.0
    cpu_ms: float = 0.0
    memory_kb: int = 0
    message: str = ""


@dataclass
class SubmissionResult:
    student: str
    problem_id: str
    problem_title: str
    language: str
    verdict: str
    score: float
    max_score: float
    time_ms: float
    cpu_ms: float = 0.0
    memory_kb: int = 0
    tests: list[TestResult] = field(default_factory=list)
    compile_output: str = ""
    message: str = ""
    source_file: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["language_name"] = LANGUAGE_NAMES.get(self.language, self.language)
        data["time_s"] = round(self.time_ms / 1000.0, 3)
        data["cpu_s"] = round(self.cpu_ms / 1000.0, 3)
        data["memory_mb"] = round(self.memory_kb / 1024.0, 2)
        return data


def _tool(name: str) -> str | None:
    return shutil.which(name)


def _exit_code_text(code: int | None) -> str:
    if code is None:
        return "未知"
    if code < 0:
        return f"{code}"
    if code >= 0x80000000:
        return f"0x{code:08X}"
    return str(code)


def _stderr_excerpt(data: bytes, limit: int = 240) -> str:
    text = compare.decode_bytes(data).strip()
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > limit:
        text = "…" + text[-limit:]
    return text


def _compile_error_line(text: str) -> str:
    """从编译器输出中挑一行最适合显示的错误信息。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        if "error" in lower or "错误" in line:
            return line
    return lines[-1] if lines else "编译失败"


def _prepare_source(source: bytes, lang: str, build_dir: Path) -> tuple[Path | None, str]:
    """把学生源码转成 UTF-8 并写到构建目录，返回 (源码路径, 类名)。"""
    text = compare.decode_bytes(source)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if lang == "java":
        match = re.search(r"public\s+(?:final\s+|abstract\s+)?class\s+(\w+)", text)
        class_name = match.group(1) if match else "Main"
        path = build_dir / f"{class_name}.java"
        path.write_text(text, encoding="utf-8")
        return path, class_name

    suffix = {
        "cpp": ".cpp",
        "c": ".c",
        "python": ".py",
    }[lang]
    path = build_dir / f"main{suffix}"
    path.write_text(text, encoding="utf-8")
    return path, ""


def _compile(
    lang: str,
    source_path: Path,
    build_dir: Path,
    class_name: str,
    problem: Problem,
) -> tuple[bool, str, list[str] | None]:
    """编译（或语法检查），返回 (是否成功, 输出, 运行命令)。"""
    exe_suffix = ".exe" if sys.platform == "win32" else ""

    if lang in ("cpp", "c"):
        compiler = _tool("g++" if lang == "cpp" else "gcc")
        if not compiler:
            return False, f"未找到 {('g++' if lang == 'cpp' else 'gcc')}，无法编译 {LANGUAGE_NAMES[lang]} 程序", None
        output = build_dir / f"main{exe_suffix}"
        if lang == "cpp":
            cmd = [compiler, "-O2", "-std=c++17", "-DONLINE_JUDGE", "-o", str(output), str(source_path)]
        else:
            cmd = [compiler, "-O2", "-std=c11", "-DONLINE_JUDGE", "-o", str(output), str(source_path)]
        result = run_program(
            cmd,
            cwd=build_dir,
            time_limit_ms=COMPILE_TIME_MS,
            memory_limit_mb=COMPILE_MEMORY_MB,
            max_output_bytes=1024 * 1024,
            allow_children=True,
        )
        if result.launch_error:
            return False, f"启动编译器失败：{result.launch_error}", None
        text = _stderr_excerpt(result.stderr, 4000)
        if result.timed_out:
            return False, "编译超时（超过 20 秒）\n" + text, None
        if result.exit_code != 0:
            return False, text or f"编译失败（退出码 {_exit_code_text(result.exit_code)}）", None
        return True, text, [str(output)]

    if lang == "java":
        javac = _tool("javac")
        if not javac:
            return False, "未找到 javac，无法编译 Java 程序", None
        cmd = [javac, "-encoding", "UTF-8", "-d", str(build_dir), str(source_path)]
        result = run_program(
            cmd,
            cwd=build_dir,
            time_limit_ms=COMPILE_TIME_MS,
            memory_limit_mb=COMPILE_MEMORY_MB,
            max_output_bytes=1024 * 1024,
            allow_children=True,
        )
        if result.launch_error:
            return False, f"启动 javac 失败：{result.launch_error}", None
        text = _stderr_excerpt(result.stderr, 4000)
        if result.timed_out:
            return False, "编译超时（超过 20 秒）\n" + text, None
        if result.exit_code != 0:
            return False, text or "编译失败", None
        java = _tool("java")
        if not java:
            return False, "未找到 java，无法运行 Java 程序", None
        run_cmd = [
            java,
            f"-Xmx{problem.memory_limit_mb}m",
            "-XX:MaxMetaspaceSize=128m",
            "-XX:+UseSerialGC",
            "-cp",
            str(build_dir),
            class_name,
        ]
        return True, text, run_cmd

    # Python：先做语法检查
    cmd = [sys.executable, "-I", "-B", "-m", "py_compile", str(source_path)]
    result = run_program(
        cmd,
        cwd=build_dir,
        time_limit_ms=COMPILE_TIME_MS,
        memory_limit_mb=COMPILE_MEMORY_MB,
        max_output_bytes=1024 * 1024,
        allow_children=True,
    )
    if result.launch_error:
        return False, f"启动 Python 解释器失败：{result.launch_error}", None
    text = _stderr_excerpt(result.stderr, 4000)
    if result.timed_out:
        return False, "语法检查超时", None
    if result.exit_code != 0:
        return False, text or "语法错误", None
    return True, text, [sys.executable, "-I", "-B", str(source_path)]


def _judge_program(
    problem: Problem,
    submission: Submission,
    lang: str,
    source: bytes,
    temp_root: Path,
    keep_temp: bool,
) -> SubmissionResult:
    result = SubmissionResult(
        student=submission.student,
        problem_id=problem.id,
        problem_title=problem.title,
        language=lang,
        verdict="SE",
        score=0.0,
        max_score=problem.max_score,
        time_ms=0.0,
        memory_kb=0,
        source_file=str(submission.file),
    )

    build_dir = Path(tempfile.mkdtemp(prefix="build-", dir=str(temp_root)))
    try:
        source_path, class_name = _prepare_source(source, lang, build_dir)
        assert source_path is not None
        ok, compile_output, run_cmd = _compile(lang, source_path, build_dir, class_name, problem)
        # 编译器输出里的临时目录对老师没有意义，替换成相对路径
        compile_output = compile_output.replace(str(build_dir) + "\\", "").replace(
            str(build_dir) + "/", ""
        )
        result.compile_output = compile_output
        if not ok or run_cmd is None:
            result.verdict = "CE"
            result.message = _compile_error_line(compile_output)
            return result

        time_limit = problem.time_limit_ms + LANGUAGE_TIME_OVERHEAD_MS[lang]
        memory_limit = problem.memory_limit_mb + LANGUAGE_MEMORY_OVERHEAD_MB[lang]

        for test in problem.tests:
            test_dir = build_dir / f"run-{test.index:02d}"
            test_dir.mkdir(parents=True, exist_ok=True)
            input_path = test_dir / "input.txt"
            if test.input is not None:
                shutil.copyfile(test.input, input_path)
            else:
                input_path.write_bytes(b"")

            run = run_program(
                run_cmd,
                cwd=test_dir,
                stdin_path=input_path,
                time_limit_ms=time_limit,
                memory_limit_mb=memory_limit,
                max_output_bytes=MAX_OUTPUT_BYTES,
            )

            verdict, message = _classify_run(run, time_limit, memory_limit)
            score = 0.0
            if verdict == "OK":
                expected_path = test.answer
                assert expected_path is not None
                expected = compare.decode_bytes(expected_path.read_bytes())
                actual = compare.decode_bytes(run.stdout)
                input_text = ""
                if problem.checker is not None:
                    input_text = compare.decode_bytes(input_path.read_bytes())
                try:
                    passed, message = compare.compare_output(
                        problem.comparator,
                        expected,
                        actual,
                        problem.float_tolerance,
                        problem.checker,
                        input_text,
                    )
                except compare.CheckerError as exc:
                    verdict, message = "SE", str(exc)
                else:
                    verdict = "OK" if passed else "WA"
                    if passed:
                        score = test.score

            if verdict == "OK":
                verdict = "AC"
                score = test.score

            result.tests.append(
                TestResult(
                    index=test.index,
                    name=test.name,
                    verdict=verdict,
                    score=score,
                    max_score=test.score,
                    time_ms=round(run.wall_ms, 1),
                    cpu_ms=round(run.cpu_ms, 1),
                    memory_kb=run.peak_memory_kb,
                    message=message,
                )
            )
            result.time_ms += run.wall_ms
            result.cpu_ms += run.cpu_ms
            result.memory_kb = max(result.memory_kb, run.peak_memory_kb)

        result.score = sum(t.score for t in result.tests)
        non_ac = [t for t in result.tests if t.verdict != "AC"]
        if not non_ac:
            result.verdict = "AC"
        else:
            priority = ["SE", "CE", "MLE", "OLE", "TLE", "RE", "WA"]
            result.verdict = min((t.verdict for t in non_ac), key=priority.index)
            result.message = next((t.message for t in non_ac if t.verdict == result.verdict), "")
        return result
    finally:
        if not keep_temp:
            shutil.rmtree(build_dir, ignore_errors=True)


def _classify_run(run, time_limit_ms: int, memory_limit_mb: int) -> tuple[str, str]:
    """把底层运行结果归类成评测状态（AC 之外）。"""
    if run.launch_error:
        return "SE", f"无法启动程序：{run.launch_error}"
    if run.peak_memory_kb > memory_limit_mb * 1024 * 1.01:
        peak_mb = run.peak_memory_kb / 1024.0
        return "MLE", f"超过内存限制：峰值 {peak_mb:.1f} MB，限制 {memory_limit_mb} MB"
    if run.timed_out or run.wall_ms > time_limit_ms + 50:
        return (
            "TLE",
            f"超过时间限制：用时 {run.wall_ms:.0f} ms，限制 {time_limit_ms} ms",
        )
    if run.output_limit_exceeded:
        return "OLE", f"输出超过限制（{MAX_OUTPUT_BYTES // (1024 * 1024)} MB）"
    if run.exit_code != 0:
        extra = _stderr_excerpt(run.stderr)
        message = f"程序异常退出（退出码 {_exit_code_text(run.exit_code)}）"
        if extra:
            message += "：" + extra
        return "RE", message
    return "OK", ""


def _judge_fill_result(
    problem: Problem,
    submission: Submission,
    source: bytes,
) -> SubmissionResult:
    text = compare.decode_bytes(source).strip()
    candidates = problem.answers
    if problem.ignore_case:
        matches = any(text.lower() == ans.strip().lower() for ans in candidates)
    else:
        matches = any(text == ans.strip() for ans in candidates)
    verdict = "AC" if matches else "WA"
    score = problem.total_score if matches else 0.0
    message = ""
    if not matches:
        shown = " / ".join(ans.strip()[:60] for ans in candidates[:3])
        message = f"答案不正确：期望 {shown!r}，实际 {text[:60]!r}"
    return SubmissionResult(
        student=submission.student,
        problem_id=problem.id,
        problem_title=problem.title,
        language="text",
        verdict=verdict,
        score=score,
        max_score=problem.total_score,
        time_ms=0.0,
        memory_kb=0,
        tests=[
            TestResult(
                index=1,
                name="答案比对",
                verdict=verdict,
                score=score,
                max_score=problem.total_score,
                message=message,
            )
        ],
        message=message,
        source_file=str(submission.file),
    )


def judge_submission(
    problem: Problem,
    submission: Submission,
    temp_root: Path,
    keep_temp: bool = False,
) -> SubmissionResult:
    """评测一次提交，返回结构化结果。"""
    if not problem.is_fill_result and not problem.tests:
        return SubmissionResult(
            student=submission.student,
            problem_id=problem.id,
            problem_title=problem.title,
            language=submission.language or "unknown",
            verdict="SE",
            score=0.0,
            max_score=problem.max_score,
            time_ms=0.0,
            memory_kb=0,
            message="题目还没有测试点，暂不能评测",
            source_file=str(submission.file),
        )

    source = submission.file.read_bytes()

    if problem.is_fill_result:
        return _judge_fill_result(problem, submission, source)

    lang = submission.language
    if lang is None:
        for key, suffixes in LANGUAGE_SUFFIXES.items():
            if submission.file.suffix.lower() in suffixes:
                lang = key
                break
    if lang is None:
        return SubmissionResult(
            student=submission.student,
            problem_id=problem.id,
            problem_title=problem.title,
            language="unknown",
            verdict="CE",
            score=0.0,
            max_score=problem.max_score,
            time_ms=0.0,
            memory_kb=0,
            message=f"无法根据扩展名识别语言：{submission.file.suffix}",
            source_file=str(submission.file),
        )

    return _judge_program(problem, submission, lang, source, temp_root, keep_temp)
