"""命令行入口：python -m bbjudge judge / list / doctor。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .judge import SubmissionResult, judge_submission
from .models import ConfigError, Contest, load_contest
from .report import print_console, write_reports
from .runner import IS_WINDOWS, probe_sandbox


def _version_of(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        return "未安装"
    last_code: int | None = None
    last_text = ""
    for flag in ("--version", "-version"):
        try:
            proc = subprocess.run(
                [path, flag],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.SubprocessError):
            continue
        last_code = proc.returncode
        text = (proc.stdout or proc.stderr).strip()
        if proc.returncode == 0 and text:
            return text.splitlines()[0]
        if text:
            last_text = text.splitlines()[0]
    if last_text:
        return f"已安装但无法运行：{last_text}"
    if last_code is not None:
        code = last_code & 0xFFFFFFFF
        suffix = f"（退出码 0x{code:08X}）" if last_code < 0 or last_code >= 0x80000000 else ""
        return f"已安装但无法运行{suffix}"
    return "已安装（无法获取版本）"


def _load(path: Path) -> Contest:
    return load_contest(path)


def cmd_list(args: argparse.Namespace) -> int:
    contest = _load(Path(args.contest))
    print(f"比赛：{contest.name}")
    print(f"题目目录：{contest.base_dir}")
    print(f"\n题目（{len(contest.problems)} 道）：")
    for pid in contest.problem_order:
        problem = contest.problems[pid]
        if problem.is_fill_result:
            kind = "结果填空"
        else:
            kind = "编程"
        if problem.is_fill_result:
            detail = f"满分 {problem.max_score:g}"
        else:
            detail = (
                f"满分 {problem.max_score:g}，时限 {problem.time_limit_ms} ms，"
                f"内存 {problem.memory_limit_mb} MB，{len(problem.tests)} 个测试点"
            )
            if not problem.is_ready:
                detail += "（待添加测试点）"
        print(f"  {pid:<10} {problem.title}  [{kind}]  {detail}")
    print(f"\n提交（{len(contest.submissions)} 份）：")
    for submission in contest.submissions:
        print(
            f"  {submission.student:<10} {submission.problem_id:<10} "
            f"{submission.file.name}  ({submission.language or '按扩展名识别'})"
        )
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    print(f"bbjudge {__version__}")
    print(f"Python：{sys.version.split()[0]}（{sys.executable}）")
    for tool in ("g++", "gcc", "javac", "java"):
        print(f"{tool:<6}：{_version_of(tool)}")

    try:
        from importlib.metadata import version

        flask_version = version("flask")
    except Exception:  # noqa: BLE001 - 只是环境检查
        flask_version = "未安装（Web 界面需要：pip install flask）"
    print(f"Web   ：Flask {flask_version}")

    available, description = probe_sandbox()
    print(f"沙箱：{description}")
    if IS_WINDOWS:
        print("提示：Windows 本地模式只提供进程级限制，不能作为对抗恶意代码的强沙箱。")
    return 0 if available or not IS_WINDOWS else 1


def cmd_web(args: argparse.Namespace) -> int:
    try:
        from .webapp import create_app
    except ModuleNotFoundError as exc:
        if exc.name == "flask":
            print("Web 界面需要 Flask：pip install flask", file=sys.stderr)
            return 1
        raise

    try:
        app = create_app(Path(args.contest), Path(args.data_dir), start_worker=True)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1

    host = args.host
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print()
    print("=" * 60)
    print("Love-Coding · 本地 Web 界面已启动")
    print(f"  学生入口：http://{display_host}:{args.port}/student")
    print(f"  教师端  ：http://{display_host}:{args.port}/teacher")
    print(f"  数据目录：{Path(args.data_dir).resolve()}")
    print("  按 Ctrl+C 停止")
    print("=" * 60)
    print()
    try:
        app.run(host=host, port=args.port, debug=False, use_reloader=False, threaded=True)
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    contest_path = Path(args.contest)
    try:
        contest = _load(contest_path)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1

    submissions = list(contest.submissions)
    if args.student:
        wanted = set(args.student)
        submissions = [s for s in submissions if s.student in wanted]
    if args.problem:
        wanted = set(args.problem)
        submissions = [s for s in submissions if s.problem_id in wanted]
    if not submissions:
        print("没有匹配的提交，请检查 --student / --problem 过滤条件。", file=sys.stderr)
        return 1

    temp_root = Path(tempfile.mkdtemp(prefix="bbjudge-"))
    results: list[SubmissionResult] = []
    try:
        total = len(submissions)
        for index, submission in enumerate(submissions, start=1):
            problem = contest.problems[submission.problem_id]
            if not args.quiet:
                print(
                    f"[{index:>2}/{total}] {submission.student} / {submission.problem_id} …",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                result = judge_submission(
                    problem,
                    submission,
                    temp_root,
                    keep_temp=args.keep_temp,
                )
            except Exception as exc:  # noqa: BLE001 - 单份提交失败不应中断整场评测
                result = SubmissionResult(
                    student=submission.student,
                    problem_id=submission.problem_id,
                    problem_title=problem.title,
                    language=submission.language or "unknown",
                    verdict="SE",
                    score=0.0,
                    max_score=problem.max_score,
                    time_ms=0.0,
                    memory_kb=0,
                    message=f"判题程序内部错误：{exc}",
                    source_file=str(submission.file),
                )
            results.append(result)
            if not args.quiet:
                print(
                    f"         → {result.verdict:<4} {result.score:g}/{result.max_score:g}",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        if args.keep_temp:
            print(f"临时目录保留在：{temp_root}", file=sys.stderr)
        else:
            shutil.rmtree(temp_root, ignore_errors=True)

    out_dir = Path(args.out)
    paths = write_reports(out_dir, contest, results)
    print_console(contest, results)
    print(f"报告已生成：{paths['html']}")
    print(f"          {paths['json']}")
    print(f"          {paths['csv']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbjudge",
        description="仿蓝桥杯学生程序判题系统：批量编译、运行、比对并生成成绩报告。",
    )
    parser.add_argument("--version", action="version", version=f"bbjudge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    judge_parser = sub.add_parser("judge", help="评测比赛清单中的全部提交")
    judge_parser.add_argument("--contest", default="contest.json", help="比赛清单路径（默认 contest.json）")
    judge_parser.add_argument("--out", default="reports", help="报告输出目录（默认 reports）")
    judge_parser.add_argument("--student", action="append", help="只评测指定学生，可重复")
    judge_parser.add_argument("--problem", action="append", help="只评测指定题目，可重复")
    judge_parser.add_argument("--keep-temp", action="store_true", help="保留临时构建目录，便于排查")
    judge_parser.add_argument("--quiet", action="store_true", help="不显示逐条评测进度")
    judge_parser.set_defaults(func=cmd_judge)

    list_parser = sub.add_parser("list", help="查看题目与提交清单")
    list_parser.add_argument("--contest", default="contest.json", help="比赛清单路径")
    list_parser.set_defaults(func=cmd_list)

    doctor_parser = sub.add_parser("doctor", help="检查编译器与沙箱环境")
    doctor_parser.set_defaults(func=cmd_doctor)

    web_parser = sub.add_parser("web", help="启动本地 Web 界面：学生上传、自动评测、教师看分")
    web_parser.add_argument(
        "--contest", default="samples/contest.json", help="比赛清单路径（默认 samples/contest.json）"
    )
    web_parser.add_argument("--data-dir", default="webdata", help="上传与评测数据目录（默认 webdata）")
    web_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    web_parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    web_parser.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
