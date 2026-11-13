"""结果展示与导出：控制台表格、JSON、CSV、单文件 HTML 报告。"""

from __future__ import annotations

import csv
import html
import json
import unicodedata
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from .models import Contest
from .judge import SubmissionResult

VERDICT_LABELS = {
    "AC": "通过",
    "WA": "答案错误",
    "TLE": "超时",
    "MLE": "内存超限",
    "RE": "运行错误",
    "CE": "编译错误",
    "OLE": "输出超限",
    "SE": "系统错误",
}


def _display_width(text: str) -> int:
    width = 0
    for ch in str(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad(text: str, width: int, align: str = "left") -> str:
    text = str(text)
    padding = max(0, width - _display_width(text))
    if align == "right":
        return " " * padding + text
    if align == "center":
        left = padding // 2
        return " " * left + text + " " * (padding - left)
    return text + " " * padding


def _score_text(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def _best_results(results: list[SubmissionResult]) -> dict[tuple[str, str], SubmissionResult]:
    best: dict[tuple[str, str], SubmissionResult] = {}
    for result in results:
        key = (result.student, result.problem_id)
        current = best.get(key)
        if current is None or result.score >= current.score:
            best[key] = result
    return best


def _student_totals(results: list[SubmissionResult]) -> "OrderedDict[str, float]":
    """同一学生同一题多次提交时，只取最高分计入总分。"""
    totals: "OrderedDict[str, float]" = OrderedDict()
    for (student, _problem_id), result in _best_results(results).items():
        totals[student] = totals.get(student, 0.0) + result.score
    return totals


def print_console(contest: Contest, results: list[SubmissionResult]) -> None:
    """在控制台打印成绩单。"""
    line = "=" * 92
    print()
    print(line)
    print(f"比赛：{contest.name}")
    print(f"提交：{len(results)} 份    学生：{len({r.student for r in results})} 人")
    print("-" * 92)
    print(
        _pad("学生", 16)
        + _pad("题目", 10)
        + _pad("结果", 7)
        + _pad("得分", 8, "right")
        + "  "
        + _pad("用时(ms)", 10, "right")
        + "  "
        + _pad("内存(MB)", 10, "right")
    )
    print("-" * 92)
    for result in results:
        print(
            _pad(result.student, 16)
            + _pad(result.problem_id, 10)
            + _pad(result.verdict, 7)
            + _pad(_score_text(result.score), 8, "right")
            + "  "
            + _pad(f"{result.time_ms:.0f}", 10, "right")
            + "  "
            + _pad(f"{result.memory_kb / 1024:.1f}", 10, "right")
        )

    failures = [r for r in results if r.verdict != "AC"]
    if failures:
        print("-" * 92)
        print("未通过详情：")
        for result in failures:
            detail = result.message or ""
            print(f"  · {result.student} / {result.problem_id} [{result.verdict}] {detail}")

    totals = _student_totals(results)
    if totals:
        print("-" * 92)
        print("学生总分：")
        for student, total in sorted(totals.items(), key=lambda item: item[1], reverse=True):
            print(f"  {_pad(student, 16)} {_score_text(total):>8}")

    print("=" * 92)
    print()


def write_json(out_dir: Path, contest: Contest, results: list[SubmissionResult]) -> Path:
    path = out_dir / "results.json"
    totals = _student_totals(results)
    payload = {
        "contest": contest.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "submissions": len(results),
            "students": len(totals),
            "average_score": round(sum(r.score for r in results) / len(results), 2) if results else 0,
            "max_score": max((r.max_score for r in results), default=0),
        },
        "students": [
            {"student": student, "total_score": total}
            for student, total in sorted(totals.items(), key=lambda item: item[1], reverse=True)
        ],
        "results": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_csv(out_dir: Path, results: list[SubmissionResult]) -> Path:
    path = out_dir / "summary.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "学生",
                "题目编号",
                "题目名称",
                "语言",
                "结果",
                "得分",
                "满分",
                "用时(ms)",
                "CPU(ms)",
                "内存(MB)",
                "通过测试点",
                "测试点总数",
                "说明",
            ]
        )
        for result in results:
            passed = sum(1 for t in result.tests if t.verdict == "AC")
            writer.writerow(
                [
                    result.student,
                    result.problem_id,
                    result.problem_title,
                    result.to_dict()["language_name"],
                    result.verdict,
                    _score_text(result.score),
                    _score_text(result.max_score),
                    f"{result.time_ms:.0f}",
                    f"{result.cpu_ms:.0f}",
                    f"{result.memory_kb / 1024:.1f}",
                    passed,
                    len(result.tests),
                    result.message.replace("\n", " "),
                ]
            )
    return path


def _verdict_badge(verdict: str) -> str:
    label = VERDICT_LABELS.get(verdict, verdict)
    return (
        f'<span class="badge {html.escape(verdict)}" title="{html.escape(label)}">'
        f"{html.escape(verdict)}</span>"
    )


def _render_test_rows(result: SubmissionResult) -> str:
    if not result.tests:
        return ""
    rows = []
    for test in result.tests:
        message = html.escape(test.message or "")
        rows.append(
            "<tr>"
            f"<td>{test.index}</td>"
            f"<td>{html.escape(test.name)}</td>"
            f"<td>{_verdict_badge(test.verdict)}</td>"
            f"<td>{_score_text(test.score)} / {_score_text(test.max_score)}</td>"
            f"<td>{test.time_ms:.0f}</td>"
            f"<td>{test.memory_kb / 1024:.1f}</td>"
            f"<td class='msg'>{message}</td>"
            "</tr>"
        )
    return (
        "<table class='tests'>"
        "<thead><tr><th>#</th><th>测试点</th><th>结果</th><th>得分</th>"
        "<th>用时(ms)</th><th>内存(MB)</th><th>说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def write_html(
    out_dir: Path,
    contest: Contest,
    results: list[SubmissionResult],
) -> Path:
    path = out_dir / "index.html"
    problem_order = contest.problem_order
    best = _best_results(results)
    totals = _student_totals(results)
    students = sorted(totals, key=lambda s: totals[s], reverse=True)

    matrix_rows = []
    for student in students:
        cells = []
        for pid in problem_order:
            result = best.get((student, pid))
            if result is None:
                cells.append("<td class='empty'>—</td>")
            else:
                cells.append(
                    "<td>"
                    f"<div class='cell'><b>{_score_text(result.score)}</b>"
                    f"{_verdict_badge(result.verdict)}</div>"
                    f"<div class='fraction'>/ {_score_text(result.max_score)}</div>"
                    "</td>"
                )
        matrix_rows.append(
            "<tr>"
            f"<td class='student'>{html.escape(student)}</td>"
            f"{''.join(cells)}"
            f"<td class='total'>{_score_text(totals[student])}</td>"
            "</tr>"
        )

    header_cells = "".join(
        f"<th>{html.escape(pid)}<div class='ptitle'>{html.escape(contest.problems[pid].title)}</div></th>"
        for pid in problem_order
    )

    detail_rows = []
    for result in results:
        passed = sum(1 for t in result.tests if t.verdict == "AC")
        compile_block = ""
        if result.compile_output:
            compile_block = (
                f"<details class='compile'><summary>编译输出</summary>"
                f"<pre>{html.escape(result.compile_output)}</pre></details>"
            )
        detail_rows.append(
            "<tbody class='submission'>"
            "<tr>"
            f"<td>{html.escape(result.student)}</td>"
            f"<td>{html.escape(result.problem_id)}</td>"
            f"<td>{html.escape(result.problem_title)}</td>"
            f"<td>{_verdict_badge(result.verdict)}</td>"
            f"<td>{_score_text(result.score)} / {_score_text(result.max_score)}</td>"
            f"<td>{passed} / {len(result.tests)}</td>"
            f"<td>{result.time_ms:.0f}</td>"
            f"<td>{result.memory_kb / 1024:.1f}</td>"
            f"<td>{html.escape(result.to_dict()['language_name'])}</td>"
            "<td>"
            f"{compile_block}"
            f"<details><summary>查看测试点</summary>{_render_test_rows(result)}</details>"
            "</td>"
            "</tr>"
            "</tbody>"
        )

    problem_stats = []
    for pid in problem_order:
        rows = [r for r in results if r.problem_id == pid]
        if not rows:
            continue
        ac = sum(1 for r in rows if r.verdict == "AC")
        avg = sum(r.score for r in rows) / len(rows)
        problem_stats.append(
            f"<li><b>{html.escape(pid)}</b> {html.escape(contest.problems[pid].title)}："
            f"{len(rows)} 次提交，通过率 {ac / len(rows) * 100:.0f}%，平均分 {avg:.1f}</li>"
        )

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    average = sum(r.score for r in results) / len(results) if results else 0

    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(contest.name)} · 判题报告</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px; background: #f8fafc; color: #0f172a;
         font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif; }}
  h1 {{ margin: 0 0 6px; font-size: 26px; }}
  .meta {{ color: #64748b; margin-bottom: 22px; }}
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 26px; }}
  .card {{ flex: 1 1 150px; background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
           padding: 14px 18px; }}
  .card .num {{ font-size: 26px; font-weight: 700; }}
  .card .label {{ color: #64748b; font-size: 13px; margin-top: 2px; }}
  h2 {{ font-size: 19px; margin: 30px 0 10px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; }}
  th, td {{ border-bottom: 1px solid #eef2f7; padding: 9px 11px; text-align: left;
            vertical-align: top; font-size: 14px; }}
  th {{ background: #f1f5f9; font-weight: 600; white-space: nowrap; }}
  tr:last-child td {{ border-bottom: none; }}
  .matrix td {{ text-align: left; }}
  .matrix .total {{ font-weight: 700; }}
  .student {{ font-weight: 600; white-space: nowrap; }}
  .ptitle {{ font-weight: 400; color: #64748b; font-size: 12px; margin-top: 2px; }}
  .cell {{ display: flex; align-items: center; gap: 6px; }}
  .fraction {{ color: #94a3b8; font-size: 11px; }}
  .badge {{ display: inline-block; padding: 2px 7px; border-radius: 999px;
            font-size: 11px; font-weight: 700; letter-spacing: .3px; }}
  .AC {{ background: #dcfce7; color: #166534; }}
  .WA {{ background: #fee2e2; color: #991b1b; }}
  .TLE {{ background: #ffedd5; color: #9a3412; }}
  .MLE {{ background: #f3e8ff; color: #6b21a8; }}
  .RE {{ background: #fef3c7; color: #92400e; }}
  .CE {{ background: #e2e8f0; color: #334155; }}
  .OLE {{ background: #cffafe; color: #155e75; }}
  .SE {{ background: #fecaca; color: #7f1d1d; }}
  .empty {{ color: #cbd5e1; }}
  details summary {{ cursor: pointer; color: #2563eb; }}
  table.tests {{ margin-top: 8px; }}
  table.tests th, table.tests td {{ padding: 5px 8px; font-size: 13px; }}
  .msg {{ color: #b91c1c; max-width: 420px; }}
  pre {{ background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px;
         overflow: auto; font-size: 12px; }}
  ul {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
        padding: 14px 22px 14px 34px; }}
  li {{ margin: 4px 0; }}
  .compile summary {{ margin-bottom: 6px; }}
</style>
</head>
<body>
  <h1>{html.escape(contest.name)}</h1>
  <div class="meta">判题报告 · 生成于 {generated} · 仿蓝桥杯本地判题系统</div>

  <div class="cards">
    <div class="card"><div class="num">{len(results)}</div><div class="label">提交份数</div></div>
    <div class="card"><div class="num">{len(totals)}</div><div class="label">学生人数</div></div>
    <div class="card"><div class="num">{average:.1f}</div><div class="label">提交平均分</div></div>
    <div class="card"><div class="num">{sum(1 for r in results if r.verdict == "AC")}</div><div class="label">通过（AC）提交</div></div>
  </div>

  <h2>学生成绩矩阵</h2>
  <table class="matrix">
    <thead><tr><th>学生</th>{header_cells}<th>总分</th></tr></thead>
    <tbody>{''.join(matrix_rows)}</tbody>
  </table>

  <h2>提交明细</h2>
  <table class="details">
    <thead><tr><th>学生</th><th>题目</th><th>名称</th><th>结果</th><th>得分</th>
      <th>测试点</th><th>用时(ms)</th><th>内存(MB)</th><th>语言</th><th>详情</th></tr></thead>
    {''.join(detail_rows)}
  </table>

  <h2>题目统计</h2>
  <ul>{''.join(problem_stats) or '<li>暂无题目统计</li>'}</ul>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")
    return path


def write_reports(out_dir: Path, contest: Contest, results: list[SubmissionResult]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "json": write_json(out_dir, contest, results),
        "csv": write_csv(out_dir, results),
        "html": write_html(out_dir, contest, results),
    }
