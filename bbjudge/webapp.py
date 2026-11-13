"""Love-Coding 本地判题 Web 应用。

- 学生端：注册/登录 → 选择考试 → 选择试卷 → 提交 → 实时成绩；
- 教师端：题目、试卷、考试、学生账号管理；
- 数据：state.json（提交）、registry.json（账号/试卷/考试）、uploads/（源码）。
"""

from __future__ import annotations

import copy
import html
import json
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from . import compare
from .judge import judge_submission
from .models import SUPPORTED_LANGUAGES, ConfigError, Submission, load_contest, load_problem
from .registry import Registry, exam_state, now_str

BRAND = "Love-Coding"
ORG = "智航AI协会"
SUBTITLE = "智航AI协会考试测验"

MAX_UPLOAD_BYTES = 1024 * 1024
MAX_UPLOAD_TOTAL = 3 * 1024 * 1024
PROBLEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

STATUS_LABELS = {
    "queued": "排队中",
    "judging": "评测中",
    "done": "已完成",
    "error": "系统错误",
}

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

LANGUAGE_EXTENSIONS = {
    "cpp": ".cpp",
    "c": ".c",
    "java": ".java",
    "python": ".py",
    "text": ".txt",
}


def _safe_name(text: str, limit: int = 24) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text).strip("._")
    return (cleaned or "student")[:limit]


class Store:
    """线程安全的提交记录存储，JSON 文件原子写入。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.records: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            records = data.get("submissions", [])
            if isinstance(records, list):
                self.records = [r for r in records if isinstance(r, dict) and r.get("id")]
        except (OSError, ValueError):
            broken = self.path.with_suffix(".broken")
            try:
                self.path.replace(broken)
            except OSError:
                pass
            self.records = []

    def _save_locked(self) -> None:
        payload = {"version": 1, "submissions": self.records}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, record: dict) -> None:
        with self.lock:
            self.records.insert(0, record)
            self._save_locked()

    def update(self, sid: str, **fields) -> dict | None:
        with self.lock:
            for record in self.records:
                if record.get("id") == sid:
                    record.update(fields)
                    self._save_locked()
                    return copy.deepcopy(record)
        return None

    def get(self, sid: str) -> dict | None:
        with self.lock:
            for record in self.records:
                if record.get("id") == sid:
                    return copy.deepcopy(record)
        return None

    def all(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self.records)


class JudgeWorker:
    """串行评测队列。"""

    def __init__(self, contest, store: Store, data_dir: Path, temp_root: Path) -> None:
        self.contest = contest
        self.store = store
        self.data_dir = data_dir
        self.temp_root = temp_root
        self.queue: "queue.Queue[str]" = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="bbjudge-worker", daemon=True)
        self._thread.start()

    def submit(self, sid: str) -> None:
        self.queue.put(sid)

    def _loop(self) -> None:
        while True:
            sid = self.queue.get()
            try:
                self.process(sid)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            finally:
                self.queue.task_done()

    def process(self, sid: str) -> None:
        record = self.store.get(sid)
        if not record or record.get("status") not in ("queued", "judging"):
            return

        self.store.update(sid, status="judging")
        problem = self.contest.problems.get(record.get("problem_id", ""))
        source = self.data_dir / record.get("source_path", "")
        if problem is None:
            self.store.update(sid, status="error", message="题目不存在，可能已被删除")
            return
        if not source.is_file():
            self.store.update(sid, status="error", message="源码文件丢失")
            return

        submission = Submission(
            student=record["student"],
            problem_id=problem.id,
            file=source,
            language=record.get("language"),
        )
        try:
            result = judge_submission(problem, submission, self.temp_root)
        except Exception as exc:  # noqa: BLE001
            self.store.update(
                sid,
                status="error",
                message=f"判题程序内部错误：{exc}",
                traceback=traceback.format_exc(),
            )
            return

        payload = result.to_dict()
        self.store.update(
            sid,
            status="done",
            result=payload,
            verdict=result.verdict,
            score=result.score,
            max_score=result.max_score,
            time_ms=round(result.time_ms, 1),
            memory_kb=result.memory_kb,
            message=result.message,
        )


def _student_safe_message(verdict: str, message: object) -> str:
    """学生端说明不包含期望输出/正确答案，避免测试点泄露答案。"""
    text = str(message or "").strip()
    if not text or verdict == "AC":
        return ""
    if verdict == "WA":
        if text.startswith("输出数据过少"):
            return "输出数据过少"
        if text.startswith("输出数据过多"):
            return "输出数据过多"
        if text.startswith("输出行数过少"):
            return "输出行数过少"
        if text.startswith("输出行数过多"):
            return "输出行数过多"
        if text.startswith("答案不正确"):
            return "答案不正确"
        if text.startswith("第 "):
            return text.split("：", 1)[0]
        return "输出与预期不一致"
    if verdict == "TLE":
        return "运行超时"
    if verdict == "RE":
        return "程序运行出错"
    if verdict == "CE":
        return "编译错误"
    if verdict == "SE":
        return "评测系统错误"
    return "未通过"


def _decorate(record: dict) -> dict:
    result = copy.deepcopy(record)
    status = result.get("status")
    if status == "done":
        verdict = result.get("verdict") or "AC"
        result["status_label"] = VERDICT_LABELS.get(verdict, verdict)
        result["badge"] = verdict
        result["badge_text"] = verdict
        result["score_text"] = f"{float(result.get('score', 0) or 0):g}"
        result["time_text"] = f"{float(result.get('time_ms', 0) or 0):.0f}"
        result["memory_text"] = f"{float(result.get('memory_kb', 0) or 0) / 1024:.1f}"
        result["message"] = _student_safe_message(verdict, result.get("message"))
        payload = result.get("result")
        if isinstance(payload, dict):
            payload["message"] = _student_safe_message(verdict, payload.get("message"))
            for test in payload.get("tests") or []:
                if isinstance(test, dict):
                    test["message"] = _student_safe_message(
                        str(test.get("verdict") or ""), test.get("message")
                    )
    else:
        result["status_label"] = STATUS_LABELS.get(status, status or "未知")
        result["badge"] = "SE" if status == "error" else (status or "queued")
        result["badge_text"] = result["status_label"]
        result["score_text"] = "—"
        result["time_text"] = "—"
        result["memory_text"] = "—"
    return result


def _sanitize_problem_html(value: object) -> str:
    """保留题面富文本，同时去掉脚本、事件处理器和 javascript: 链接。"""
    text = str(value or "")[:2_000_000]
    text = re.sub(
        r"<\s*(script|style|iframe|object|embed|link|meta)\b[^>]*>.*?<\s*/\s*\1\s*>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<\s*(script|style|iframe|object|embed|link|meta)\b[^>]*?/?>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s+on[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", text
    )
    text = re.sub(
        r"(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2",
        r'\1="#"',
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def _clean_samples(raw: object) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return samples
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        sample_input = str(item.get("input") or "")
        sample_output = str(item.get("output") or "")
        if not sample_input.strip() and not sample_output.strip():
            continue
        samples.append({"input": sample_input, "output": sample_output})
    return samples


def _problem_summary(problem) -> dict:
    return {
        "id": problem.id,
        "title": problem.title,
        "type": problem.type,
        "kind": "结果填空" if problem.is_fill_result else "编程题",
        "max_score": round(problem.display_score, 2),
        "time_limit_ms": problem.time_limit_ms,
        "memory_limit_mb": problem.memory_limit_mb,
        "test_count": len(problem.tests),
        "ready": problem.is_ready,
        "description": problem.description,
        "input_description": problem.input_description,
        "output_description": problem.output_description,
        "samples": problem.samples,
    }


def _paper_summary(paper: dict, contest) -> dict:
    problems = []
    for pid in paper.get("problem_ids", []):
        problem = contest.problems.get(pid)
        problems.append(
            {
                "id": pid,
                "title": problem.title if problem else "（题目已删除）",
                "missing": problem is None,
            }
        )
    result = dict(paper)
    result["problems"] = problems
    result["problem_count"] = len(problems)
    return result


def _format_remaining(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "已结束"
    minutes, sec = divmod(int(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分 {sec} 秒"


def _exam_summary(exam: dict, registry: Registry) -> dict:
    papers = []
    for paper_id in exam.get("paper_ids", []):
        paper = registry.get_paper(paper_id)
        if paper:
            papers.append(
                {
                    "id": paper["id"],
                    "title": paper["title"],
                    "problem_count": len(paper.get("problem_ids", [])),
                }
            )
    state = exam_state(exam)
    badge = {"running": "AC", "upcoming": "queued", "ended": "CE"}[state["status"]]
    result = dict(exam)
    result.update(
        {
            "papers": papers,
            "paper_count": len(papers),
            "paper_titles": [p["title"] for p in papers],
            "state": state["status"],
            "state_label": state["label"],
            "state_badge": badge,
            "remaining_seconds": state["remaining_seconds"],
            "remaining_text": _format_remaining(state["remaining_seconds"]),
            "has_final": exam.get("final_ranking") is not None,
            "finalized_at": exam.get("finalized_at"),
            "participant_count": len(exam.get("participants", [])),
        }
    )
    return result


def _paper_max_scores(registry: Registry, contest, exam: dict) -> dict[str, float]:
    result: dict[str, float] = {}
    for paper_id in exam.get("paper_ids", []):
        paper = registry.get_paper(paper_id)
        if paper is None:
            continue
        total = 0.0
        for problem_id in paper.get("problem_ids", []):
            problem = contest.problems.get(problem_id)
            if problem is not None:
                total += float(problem.max_score)
        result[paper_id] = total
    return result


def _scoreboard(
    records: list[dict],
    exam_id: str,
    participants: list[dict] | None = None,
    paper_max_by_id: dict[str, float] | None = None,
) -> dict:
    exam_records = [r for r in records if r.get("exam_id") == exam_id]
    done = [r for r in exam_records if r.get("status") == "done"]
    participants = participants or []
    paper_max_by_id = paper_max_by_id or {}

    valid_max = [float(v) for v in paper_max_by_id.values() if float(v) > 0]
    average_max = sum(valid_max) / len(valid_max) if valid_max else None

    participant_by_key: dict[str, dict] = {}
    student_keys: list[str] = []
    seen: set[str] = set()
    for participant in participants:
        key = (
            participant.get("student_id")
            or participant.get("username")
            or participant.get("student")
        )
        if not key or key in seen:
            continue
        seen.add(key)
        participant_by_key[key] = participant
        student_keys.append(key)

    for record in exam_records:
        key = record.get("student_id") or record.get("student")
        if not key or key in seen:
            continue
        seen.add(key)
        student_keys.append(key)

    ranking: list[dict] = []
    for key in student_keys:
        participant = participant_by_key.get(key, {})
        student_records = [
            r for r in exam_records if (r.get("student_id") or r.get("student")) == key
        ]
        student_name = (
            participant.get("student")
            or (student_records[0].get("student") if student_records else "")
            or key
        )

        paper_id = participant.get("paper_id")
        if not paper_id:
            for record in sorted(
                student_records, key=lambda item: item.get("created_at", ""), reverse=True
            ):
                if record.get("paper_id"):
                    paper_id = record["paper_id"]
                    break

        candidates = [
            record
            for record in done
            if (record.get("student_id") or record.get("student")) == key
            and (not paper_id or record.get("paper_id") == paper_id)
        ]

        best: dict[str, dict] = {}
        for record in candidates:
            problem_id = record.get("problem_id", "")
            current = best.get(problem_id)
            if current is None:
                best[problem_id] = record
                continue
            record_score = float(record.get("score", 0) or 0)
            current_score = float(current.get("score", 0) or 0)
            if record_score > current_score or (
                record_score == current_score and record.get("verdict") == "AC"
            ):
                best[problem_id] = record

        raw_total = sum(float(record.get("score", 0) or 0) for record in best.values())
        ac = sum(1 for record in best.values() if record.get("verdict") == "AC")
        submissions = len(candidates)

        submit_times = [
            record.get("created_at") for record in student_records if record.get("created_at")
        ]
        first_submit_at = min(submit_times) if submit_times else ""

        paper_max = paper_max_by_id.get(paper_id) if paper_id else None
        total = raw_total
        if average_max and paper_max and paper_max > 0:
            total = raw_total / paper_max * average_max

        ranking.append(
            {
                "student": student_name,
                "total": round(total, 2),
                "raw_total": round(raw_total, 2),
                "ac": ac,
                "problems": len(best),
                "submissions": submissions,
                "paper_id": paper_id,
                "paper_max": round(float(paper_max), 2) if paper_max else None,
                "first_submit_at": first_submit_at,
            }
        )

    ranking.sort(
        key=lambda item: (
            -item["ac"],
            -item["total"],
            item["first_submit_at"] or "9999-12-31 23:59:59",
            item["student"],
        )
    )
    for index, entry in enumerate(ranking, start=1):
        entry["rank"] = index

    feed = []
    for record in exam_records[:30]:
        feed.append(
            {
                "id": record.get("id"),
                "student": record.get("student"),
                "problem_id": record.get("problem_id"),
                "problem_title": record.get("problem_title"),
                "status": record.get("status"),
                "verdict": record.get("verdict"),
                "score": record.get("score", 0),
                "max_score": record.get("max_score", 0),
                "created_at": record.get("created_at"),
            }
        )

    return {"ranking": ranking, "feed": feed, "updated_at": now_str()}


def create_app(
    contest_path: str | Path,
    data_dir: str | Path,
    start_worker: bool = True,
) -> Flask:
    contest_path = Path(contest_path)
    contest = load_contest(contest_path, allow_empty=True)
    problems_dir = contest.problems_dir or (contest.base_dir / "problems")

    data_dir = Path(data_dir).resolve()
    uploads_dir = data_dir / "uploads"
    temp_root = data_dir / "tmp"
    for directory in (uploads_dir, temp_root):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)

    store = Store(data_dir / "state.json")
    registry = Registry(data_dir / "registry.json")
    worker = JudgeWorker(contest, store, data_dir, temp_root)
    problem_lock = threading.RLock()

    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.config["SECRET_KEY"] = os.environ.get("BBJUDGE_SECRET", "bbjudge-local-dev")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_TOTAL
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    context = SimpleNamespace(
        contest=contest,
        store=store,
        registry=registry,
        worker=worker,
        data_dir=data_dir,
        uploads_dir=uploads_dir,
        temp_root=temp_root,
        problems_dir=problems_dir,
        problem_lock=problem_lock,
    )
    app.extensions["bbjudge"] = context

    for record in store.all():
        if record.get("status") in ("queued", "judging"):
            store.update(record["id"], status="queued")
            if start_worker:
                worker.submit(record["id"])

    if start_worker:
        worker.start()

    def current_student() -> dict | None:
        student_id = session.get("student_id")
        if not student_id:
            return None
        student = registry.get_student(student_id)
        if student is None:
            session.pop("student_id", None)
        return student

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            student = current_student()
            if student is None:
                return redirect(url_for("student_login", next=request.path))
            g.student = student
            return view(*args, **kwargs)

        return wrapped

    @app.context_processor
    def _inject():
        is_teacher = request.path.startswith("/teacher")
        return {
            "brand": BRAND,
            "org": ORG,
            "subtitle": SUBTITLE,
            "portal": "teacher" if is_teacher else "student",
            "home_url": url_for("teacher") if is_teacher else url_for("student"),
            "current_student": current_student(),
        }

    @app.template_filter("num")
    def _format_number(value):
        try:
            return f"{float(value):g}"
        except (TypeError, ValueError):
            return value

    def list_problems():
        with problem_lock:
            return [contest.problems[pid] for pid in contest.problem_order if pid in contest.problems]

    def ready_problems():
        return [p for p in list_problems() if p.is_ready]

    def finalize_exam(exam_id: str) -> dict | None:
        """考试结束后生成/升级最终排名快照；已是最新格式则保持不变。"""
        exam = registry.get_exam(exam_id)
        if exam is None:
            return None
        final_ranking = exam.get("final_ranking")
        if final_ranking is not None and all(
            "first_submit_at" in entry for entry in final_ranking
        ):
            return exam
        if exam_state(exam)["status"] != "ended":
            return exam
        records = store.all()
        # 截止时间前提交、但还在评测队列里的先等它跑完，避免最终成绩漏人
        pending = any(
            record.get("exam_id") == exam_id
            and record.get("status") in ("queued", "judging")
            for record in records
        )
        if pending:
            return exam
        snapshot = _scoreboard(
            records,
            exam_id,
            exam.get("participants", []),
            _paper_max_scores(registry, contest, exam),
        )
        registry.set_final_ranking(exam_id, snapshot["ranking"])
        return registry.get_exam(exam_id)

    def finalize_ended_exams() -> None:
        for exam in registry.list_exams():
            if exam_state(exam)["status"] != "ended":
                continue
            final_ranking = exam.get("final_ranking")
            legacy = final_ranking is not None and any(
                "first_submit_at" not in entry for entry in final_ranking
            )
            if final_ranking is None or legacy:
                try:
                    finalize_exam(exam["id"])
                except Exception:  # noqa: BLE001 - 后台线程不能因单场考试退出
                    traceback.print_exc()

    if start_worker:
        finalize_ended_exams()

        def _finalizer_loop() -> None:
            while True:
                time.sleep(5)
                try:
                    finalize_ended_exams()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()

        threading.Thread(
            target=_finalizer_loop, name="bbjudge-finalizer", daemon=True
        ).start()

    def exam_papers(exam: dict) -> list[dict]:
        papers = []
        for paper in registry.list_papers():
            if paper["id"] in exam.get("paper_ids", []):
                papers.append(_paper_summary(paper, contest))
        return papers

    def build_board(exam: dict, board_id: str) -> dict | None:
        papers = exam_papers(exam)
        if board_id == "all":
            records = store.all()
            participants = exam.get("participants", [])
            paper_max = _paper_max_scores(registry, contest, exam)
            title = "总榜"
            normalized = len(papers) > 1
        else:
            paper = next((item for item in papers if item["id"] == board_id), None)
            if paper is None:
                return None
            records = [
                record
                for record in store.all()
                if record.get("exam_id") == exam["id"]
                and record.get("paper_id") == board_id
            ]
            # 分榜只展示实际提交过该卷的学生；进入考试但没提交的学生只出现在总榜。
            participants = []
            paper_max = None
            title = paper["title"]
            normalized = False
        data = _scoreboard(records, exam["id"], participants, paper_max)
        data.update({"id": board_id, "title": title, "normalized": normalized})
        return data

    def build_boards(exam: dict) -> list[dict]:
        papers = exam_papers(exam)
        boards = [build_board(exam, "all")]
        if len(papers) > 1:
            for paper in papers:
                board = build_board(exam, paper["id"])
                if board is not None:
                    boards.append(board)
        return [board for board in boards if board is not None]

    def read_field(file_key: str, text_key: str, required: bool) -> bytes:
        raw = b""
        uploaded = request.files.get(file_key)
        if uploaded is not None and uploaded.filename:
            raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError("文件超过 1 MB 上限")
        text = request.form.get(text_key) or ""
        if not raw and text:
            raw = text.encode("utf-8")
        if required and not raw.strip():
            raise ValueError("请填写标准输出/答案")
        return raw

    # ---------- 通用 ----------

    @app.get("/")
    def index():
        return redirect(url_for("student"))

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "brand": BRAND})

    # ---------- 学生账号 ----------

    @app.get("/student/login")
    def student_login():
        if current_student() is not None:
            return redirect(url_for("student"))
        return render_template("login.html")

    @app.post("/student/login")
    def student_login_post():
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        student = registry.find_student_by_username(username)
        if student is None:
            # 也允许用真实姓名登录（仅在姓名唯一时）
            student = registry.find_student_by_real_name(username)
        if student is None or not check_password_hash(student["password_hash"], password):
            flash("登录名或密码不正确", "err")
            return render_template("login.html", username=username), 401
        session["student_id"] = student["id"]
        flash(f"欢迎回来，{student['real_name']}", "ok")
        next_url = request.args.get("next") or request.form.get("next") or ""
        if next_url.startswith("/student"):
            return redirect(next_url)
        return redirect(url_for("student"))

    @app.get("/student/register")
    def student_register():
        if current_student() is not None:
            return redirect(url_for("student"))
        return render_template("register.html")

    @app.post("/student/register")
    def student_register_post():
        username = (request.form.get("username") or "").strip()
        real_name = (request.form.get("real_name") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        login_name = username or real_name

        errors = []
        if not real_name or len(real_name) > 40:
            errors.append("请填写真实姓名（最多 40 个字符）")
        if username and username != real_name and not USERNAME_RE.fullmatch(username):
            errors.append("登录名需为 3-32 位字母、数字、下划线或短横线")
        if not login_name:
            errors.append("请填写真实姓名或登录名")
        if len(password) < 6:
            errors.append("密码至少 6 位")
        if password != confirm:
            errors.append("两次输入的密码不一致")
        if not errors and registry.find_student_by_username(login_name) is not None:
            errors.append("登录名已被注册；如果真实姓名重名，请填写学号作为登录名")
        if errors:
            for message in errors:
                flash(message, "err")
            return render_template("register.html", username=username, real_name=real_name), 400

        student = registry.create_student(
            login_name, real_name, generate_password_hash(password), password
        )
        session["student_id"] = student["id"]
        flash("注册成功，已自动登录", "ok")
        return redirect(url_for("student"))

    @app.get("/student/logout")
    def student_logout():
        session.pop("student_id", None)
        flash("已退出登录", "ok")
        return redirect(url_for("student_login"))

    # ---------- 学生端 ----------

    @app.get("/student")
    @login_required
    def student():
        exams = [_exam_summary(e, registry) for e in registry.list_exams()]
        exams.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return render_template("student_exams.html", exams=exams)

    @app.get("/student/exam/<exam_id>")
    @login_required
    def student_exam(exam_id: str):
        exam = registry.get_exam(exam_id)
        if exam is None:
            abort(404)

        papers = exam_papers(exam)
        selected_id = request.args.get("paper") or (papers[0]["id"] if papers else "")
        selected = next((p for p in papers if p["id"] == selected_id), papers[0] if papers else None)
        view = request.args.get("view") or "rank"
        if view not in ("rank", "submit"):
            view = "rank"

        if exam_state(exam)["status"] == "running" and selected:
            registry.add_participant(
                exam_id,
                g.student["id"],
                g.student["username"],
                g.student["real_name"],
                paper_id=selected["id"],
            )
            exam = registry.get_exam(exam_id)

        summary = _exam_summary(exam, registry)
        remaining_seconds = summary["remaining_seconds"]

        if view == "submit":
            problems = []
            if selected:
                for pid in selected.get("problem_ids", []):
                    problem = contest.problems.get(pid)
                    if problem is not None and problem.is_ready:
                        problems.append(problem)
            selected_problem_id = request.args.get("problem") or (
                problems[0].id if problems else ""
            )
            return render_template(
                "student_submit.html",
                exam=summary,
                papers=papers,
                selected_paper=selected,
                problems=problems,
                selected_problem_id=selected_problem_id,
                problem_data=[_problem_summary(p) for p in problems],
                remaining_seconds=remaining_seconds,
                active_view="submit",
            )

        boards = build_boards(exam) if exam.get("scoreboard_enabled") else []
        selected_board = None
        if boards:
            board_id = request.args.get("board") or "all"
            selected_board = next(
                (board for board in boards if board["id"] == board_id), boards[0]
            )
        return render_template(
            "student_rank.html",
            exam=summary,
            papers=papers,
            selected_paper=selected,
            boards=boards,
            selected_board=selected_board,
            remaining_seconds=remaining_seconds,
            active_view="rank",
        )

    @app.post("/student/submit")
    @login_required
    def student_submit():
        student = g.student
        exam_id = (request.form.get("exam_id") or "").strip()
        paper_id = (request.form.get("paper_id") or "").strip()
        problem_id = (request.form.get("problem") or "").strip()

        def back_to_submit():
            params = {"exam_id": exam_id, "view": "submit"}
            if paper_id:
                params["paper"] = paper_id
            if problem_id:
                params["problem"] = problem_id
            return redirect(url_for("student_exam", **params))

        exam = registry.get_exam(exam_id)
        if exam is None:
            flash("请先选择考试", "err")
            return redirect(url_for("student"))
        state = exam_state(exam)
        if state["status"] != "running":
            flash("考试未开始或已经结束，不能提交", "err")
            return back_to_submit()

        paper = registry.get_paper(paper_id)
        if paper is None or paper_id not in exam.get("paper_ids", []):
            flash("请选择本场考试的有效试卷", "err")
            return back_to_submit()
        if problem_id not in paper.get("problem_ids", []):
            flash("该题目不属于所选试卷", "err")
            return back_to_submit()

        problem = contest.problems.get(problem_id)
        if problem is None or not problem.is_ready:
            flash("题目不存在或还未就绪", "err")
            return back_to_submit()

        registry.add_participant(
            exam_id,
            student["id"],
            student["username"],
            student["real_name"],
            paper_id=paper_id,
        )

        uploaded = request.files.get("file")
        raw = b""
        original_name = ""
        suffix = ""
        if uploaded is not None and uploaded.filename:
            original_name = uploaded.filename
            raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
            suffix = Path(original_name).suffix.lower()
            if len(raw) > MAX_UPLOAD_BYTES:
                flash("文件超过 1 MB 上限", "err")
                return back_to_submit()
        text = (request.form.get("text") or "").strip()
        if not raw and text:
            raw = text.encode("utf-8")
            original_name = original_name or "粘贴的内容"
        if not raw:
            flash("请上传源码/答案文件，或直接粘贴内容", "err")
            return back_to_submit()

        source_text = compare.decode_bytes(raw)
        if not source_text.strip():
            flash("提交内容是空的", "err")
            return back_to_submit()

        language_choice = (request.form.get("language") or "auto").lower()
        if problem.is_fill_result:
            language = "text"
            extension = ".txt"
        else:
            if suffix in SUPPORTED_LANGUAGES:
                language = SUPPORTED_LANGUAGES[suffix]
            elif language_choice in ("cpp", "c", "java", "python"):
                language = language_choice
            elif text:
                language = "cpp"
            else:
                flash("无法识别源码语言，请选择语言或改用 .cpp/.py/.java 文件", "err")
                return back_to_submit()
            extension = LANGUAGE_EXTENSIONS[language]

        sid = uuid.uuid4().hex[:12]
        filename = f"{sid}__{_safe_name(student['real_name'])}{extension}"
        source_path = uploads_dir / filename
        source_path.write_text(source_text, encoding="utf-8")

        record = {
            "id": sid,
            "student_id": student["id"],
            "username": student["username"],
            "student": student["real_name"],
            "exam_id": exam["id"],
            "exam_title": exam["title"],
            "paper_id": paper["id"],
            "paper_title": paper["title"],
            "problem_id": problem.id,
            "problem_title": problem.title,
            "filename": original_name or filename,
            "language": language,
            "created_at": now_str(),
            "status": "queued",
            "source_path": source_path.relative_to(data_dir).as_posix(),
            "verdict": "",
            "score": 0.0,
            "max_score": problem.max_score,
            "time_ms": 0.0,
            "memory_kb": 0,
            "message": "",
        }
        store.add(record)
        worker.submit(sid)
        flash("提交成功，已进入评测队列。", "ok")
        return redirect(url_for("student_status", sid=sid))

    @app.get("/student/status/<sid>")
    @login_required
    def student_status(sid: str):
        record = store.get(sid)
        if record is None or record.get("student_id") != g.student["id"]:
            abort(404)
        submission = _decorate(record)
        return render_template(
            "status.html",
            submission=submission,
            record=submission,
        )

    @app.get("/student/account")
    @login_required
    def student_account():
        return render_template("student_account.html", student=g.student)

    @app.post("/student/account/password")
    @login_required
    def student_account_password():
        old_password = request.form.get("old_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm") or ""
        errors = []
        if not check_password_hash(g.student["password_hash"], old_password):
            errors.append("原密码不正确")
        if len(new_password) < 6:
            errors.append("新密码至少 6 位")
        if new_password != confirm:
            errors.append("两次输入的新密码不一致")
        if errors:
            for message in errors:
                flash(message, "err")
            return redirect(url_for("student_account"))
        registry.update_student_password(
            g.student["id"], generate_password_hash(new_password), new_password
        )
        flash("密码已更新", "ok")
        return redirect(url_for("student_account"))

    # ---------- 教师端页面 ----------

    @app.get("/teacher")
    def teacher():
        problems = list_problems()
        program_problems = [p for p in problems if not p.is_fill_result]
        return render_template(
            "teacher.html",
            problems=problems,
            program_problems=program_problems,
            selected_problem=request.args.get("problem") or "",
        )

    @app.get("/teacher/papers")
    def teacher_papers():
        papers = [_paper_summary(p, contest) for p in registry.list_papers()]
        return render_template("teacher_papers.html", papers=papers, problems=ready_problems())

    @app.get("/teacher/problems/<pid>")
    def teacher_problem(pid: str):
        problem = contest.problems.get(pid)
        if problem is None:
            abort(404)
        return render_template("teacher_problem.html", problem=problem)

    @app.get("/teacher/exams")
    def teacher_exams():
        finalize_ended_exams()
        exams = [_exam_summary(e, registry) for e in registry.list_exams()]
        exams.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        papers = [_paper_summary(p, contest) for p in registry.list_papers()]
        return render_template("teacher_exams.html", exams=exams, papers=papers)

    @app.get("/teacher/exams/<exam_id>/final")
    def teacher_exam_final(exam_id: str):
        exam = finalize_exam(exam_id)
        if exam is None:
            abort(404)
        if exam_state(exam)["status"] != "ended":
            flash("考试还没有结束，最终成绩会在结束后生成。", "err")
            return redirect(url_for("teacher_exams"))
        if exam.get("final_ranking") is None:
            flash("考试已结束，但还有提交在评测中，请稍后刷新最终成绩。", "err")
            return redirect(url_for("teacher_exams"))
        summary = _exam_summary(exam, registry)
        return render_template(
            "exam_final.html",
            exam=summary,
            ranking=exam.get("final_ranking", []),
            finalized_at=exam.get("finalized_at"),
        )

    @app.get("/teacher/accounts")
    def teacher_accounts():
        return render_template("teacher_accounts.html", students=registry.list_students())

    @app.get("/teacher/accounts/<student_id>")
    def teacher_account(student_id: str):
        student = registry.get_student(student_id)
        if student is None:
            abort(404)
        return render_template("teacher_account.html", student=student)

    @app.post("/teacher/accounts/<student_id>/password")
    def teacher_reset_student_password(student_id: str):
        student = registry.get_student(student_id)
        if student is None:
            abort(404)
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm") or ""
        if len(new_password) < 6:
            flash("新密码至少 6 位", "err")
        elif new_password != confirm:
            flash("两次输入的新密码不一致", "err")
        else:
            registry.update_student_password(
                student_id, generate_password_hash(new_password), new_password
            )
            flash("密码已重置", "ok")
        return redirect(url_for("teacher_account", student_id=student_id))

    # ---------- 成绩榜 / 提交状态 API ----------

    @app.get("/api/scoreboard")
    def api_scoreboard():
        if current_student() is None:
            return jsonify({"error": "unauthorized"}), 401
        exam_id = request.args.get("exam_id") or ""
        exam = registry.get_exam(exam_id)
        if exam is None:
            return jsonify({"error": "考试不存在"}), 404
        if not exam.get("scoreboard_enabled"):
            return jsonify(
                {"enabled": False, "ranking": [], "feed": [], "updated_at": now_str()}
            )
        board_id = request.args.get("board") or "all"
        board = build_board(exam, board_id)
        if board is None:
            return jsonify({"error": "榜单不存在"}), 404
        board["enabled"] = True
        return jsonify(board)

    @app.get("/api/submission/<sid>")
    def api_submission(sid: str):
        student = current_student()
        if student is None:
            return jsonify({"error": "unauthorized"}), 401
        record = store.get(sid)
        if record is None or record.get("student_id") != student["id"]:
            return jsonify({"error": "not found"}), 404
        return jsonify(_decorate(record))

    # ---------- 题目 API ----------

    @app.get("/api/problems")
    def api_problems():
        return jsonify({"problems": [_problem_summary(p) for p in list_problems()]})

    @app.post("/api/problems")
    def api_create_problem():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "请求体必须是 JSON 对象"}), 400

        pid = str(data.get("id") or "").strip()
        title = str(data.get("title") or "").strip()
        problem_type = str(data.get("type") or "program").lower()
        if not PROBLEM_ID_RE.fullmatch(pid):
            return jsonify({"error": "题目编号只能包含字母、数字、下划线、短横线，且以字母/数字开头"}), 400
        if not title:
            return jsonify({"error": "请填写题目名称"}), 400
        if problem_type not in ("program", "fill_result"):
            return jsonify({"error": "题型只能是 program 或 fill_result"}), 400

        try:
            time_limit = int(data.get("time_limit_ms", 1000))
            memory_limit = int(data.get("memory_limit_mb", 256))
            score = float(data.get("score", 100))
        except (TypeError, ValueError):
            return jsonify({"error": "时限、内存、分值必须是数字"}), 400
        if time_limit <= 0 or memory_limit <= 0 or score <= 0:
            return jsonify({"error": "时限、内存、分值必须大于 0"}), 400

        comparator = str(data.get("comparator") or "token").lower()
        if comparator not in ("token", "line", "exact", "float"):
            comparator = "token"

        answers_raw = data.get("answers", [])
        if isinstance(answers_raw, str):
            answers = [line.strip() for line in answers_raw.splitlines() if line.strip()]
        elif isinstance(answers_raw, list):
            answers = [str(item).strip() for item in answers_raw if str(item).strip()]
        else:
            answers = []
        if problem_type == "fill_result" and not answers:
            return jsonify({"error": "结果填空题至少需要一个标准答案"}), 400

        final_dir = problems_dir / pid
        if final_dir.exists():
            return jsonify({"error": f"题目编号 {pid} 已存在"}), 409

        tmp_dir = problems_dir / f".{pid}.tmp"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        (tmp_dir / "tests").mkdir(parents=True)
        payload = {
            "id": pid,
            "title": title,
            "type": problem_type,
            "time_limit_ms": time_limit,
            "memory_limit_mb": memory_limit,
            "score": score,
            "comparator": comparator,
            "tests": [],
        }
        if problem_type == "fill_result":
            payload["answers"] = answers

        legacy_statement = str(data.get("statement") or "")
        description = _sanitize_problem_html(data.get("description"))
        if not description and legacy_statement.strip():
            description = (
                '<pre class="legacy-statement">'
                + html.escape(legacy_statement)
                + "</pre>"
            )
        payload["description"] = description
        payload["input_description"] = str(
            data.get("input_description") or ""
        ).strip()
        payload["output_description"] = str(
            data.get("output_description") or ""
        ).strip()
        payload["samples"] = _clean_samples(data.get("samples"))

        try:
            (tmp_dir / "problem.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if legacy_statement.strip():
                (tmp_dir / "statement.md").write_text(
                    legacy_statement, encoding="utf-8"
                )
            tmp_dir.rename(final_dir)
            problem = load_problem(final_dir)
        except (OSError, ConfigError) as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            return jsonify({"error": f"创建题目失败：{exc}"}), 400

        with problem_lock:
            contest.problems[pid] = problem
            contest.problem_order.append(pid)
        return jsonify({"problem": _problem_summary(problem)}), 201

    @app.put("/api/problems/<pid>")
    def api_update_problem(pid: str):
        problem = contest.problems.get(pid)
        if problem is None:
            return jsonify({"error": "题目不存在"}), 404
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "请求体必须是 JSON 对象"}), 400

        title = str(data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "请填写题目名称"}), 400
        try:
            time_limit = int(data.get("time_limit_ms", problem.time_limit_ms))
            memory_limit = int(data.get("memory_limit_mb", problem.memory_limit_mb))
            score = float(data.get("score", problem.total_score))
        except (TypeError, ValueError):
            return jsonify({"error": "时限、内存、分值必须是数字"}), 400
        if time_limit <= 0 or memory_limit <= 0 or score <= 0:
            return jsonify({"error": "时限、内存、分值必须大于 0"}), 400

        comparator = str(data.get("comparator") or problem.comparator).lower()
        if comparator not in ("token", "line", "exact", "float"):
            comparator = problem.comparator

        answers = list(problem.answers)
        if problem.is_fill_result:
            raw_answers = data.get("answers", [])
            if isinstance(raw_answers, str):
                answers = [line.strip() for line in raw_answers.splitlines() if line.strip()]
            elif isinstance(raw_answers, list):
                answers = [str(item).strip() for item in raw_answers if str(item).strip()]
            else:
                answers = []
            if not answers:
                return jsonify({"error": "结果填空题至少需要一个标准答案"}), 400

        config_path = problem.base_dir / "problem.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            return jsonify({"error": f"读取题目配置失败：{exc}"}), 500

        config["title"] = title
        config["time_limit_ms"] = time_limit
        config["memory_limit_mb"] = memory_limit
        config["score"] = score
        config["comparator"] = comparator
        raw_tests = config.get("tests")
        if (
            not problem.is_fill_result
            and isinstance(raw_tests, list)
            and raw_tests
            and all(isinstance(item, dict) and item.get("score") is not None for item in raw_tests)
        ):
            current_total = sum(float(item.get("score", 0) or 0) for item in raw_tests)
            if current_total > 0 and abs(current_total - score) > 1e-9:
                factor = score / current_total
                for item in raw_tests:
                    item["score"] = round(float(item.get("score", 0) or 0) * factor, 2)
        config["description"] = _sanitize_problem_html(data.get("description"))
        config["input_description"] = str(
            data.get("input_description") or ""
        ).strip()
        config["output_description"] = str(
            data.get("output_description") or ""
        ).strip()
        config["samples"] = _clean_samples(data.get("samples"))
        if problem.is_fill_result:
            config["answers"] = answers

        try:
            tmp_config = config_path.with_suffix(".tmp")
            tmp_config.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp_config, config_path)
            refreshed = load_problem(problem.base_dir)
        except (OSError, ValueError, ConfigError) as exc:
            return jsonify({"error": f"保存题目失败：{exc}"}), 500

        with problem_lock:
            contest.problems[pid] = refreshed
        return jsonify({"problem": _problem_summary(refreshed)})

    @app.delete("/api/problems/<pid>")
    def api_delete_problem(pid: str):
        problem = contest.problems.get(pid)
        if problem is None:
            return jsonify({"error": "题目不存在"}), 404
        force = request.args.get("force") == "1"
        used = registry.papers_using_problem(pid)
        if used and not force:
            titles = "、".join(p["title"] for p in used)
            return jsonify(
                {"error": f"题目已被试卷使用（{titles}），请先从试卷中移除"}
            ), 409
        removed_papers = registry.detach_problem_from_papers(pid) if used else []
        with problem_lock:
            shutil.rmtree(problem.base_dir, ignore_errors=True)
            contest.problems.pop(pid, None)
            if pid in contest.problem_order:
                contest.problem_order.remove(pid)
        return jsonify({"ok": True, "removed_papers": removed_papers})

    @app.post("/api/problems/<pid>/tests")
    def api_add_test(pid: str):
        problem = contest.problems.get(pid)
        if problem is None:
            return jsonify({"error": "题目不存在"}), 404
        if problem.is_fill_result:
            return jsonify({"error": "结果填空题不需要测试用例"}), 400
        try:
            score = float(request.form.get("score", "10"))
        except (TypeError, ValueError):
            return jsonify({"error": "测试点分值必须是数字"}), 400
        if score <= 0:
            return jsonify({"error": "测试点分值必须大于 0"}), 400

        name = (request.form.get("name") or "").strip() or f"测试点 {len(problem.tests) + 1}"
        try:
            input_bytes = read_field("input_file", "input_text", required=False)
            answer_bytes = read_field("answer_file", "answer_text", required=True)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        tests_dir = problem.base_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        index = 1
        while (tests_dir / f"case_{index:02d}.in").exists() or (
            tests_dir / f"case_{index:02d}.out"
        ).exists():
            index += 1
        input_name = f"tests/case_{index:02d}.in"
        answer_name = f"tests/case_{index:02d}.out"
        (tests_dir / f"case_{index:02d}.in").write_bytes(input_bytes)
        (tests_dir / f"case_{index:02d}.out").write_bytes(answer_bytes)

        config_path = problem.base_dir / "problem.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            tests = config.get("tests")
            if not isinstance(tests, list):
                tests = []
                config["tests"] = tests
            tests.append(
                {"name": name, "input": input_name, "answer": answer_name, "score": score}
            )
            tmp_config = config_path.with_suffix(".tmp")
            tmp_config.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp_config, config_path)
            refreshed = load_problem(problem.base_dir)
        except (OSError, ValueError, ConfigError) as exc:
            return jsonify({"error": f"写入测试点失败：{exc}"}), 500

        with problem_lock:
            contest.problems[pid] = refreshed
        return (
            jsonify(
                {
                    "problem": _problem_summary(refreshed),
                    "test": {
                        "name": name,
                        "score": score,
                        "input": input_name,
                        "answer": answer_name,
                    },
                }
            ),
            201,
        )

    @app.delete("/api/problems/<pid>/tests/<int:test_index>")
    def api_delete_test(pid: str, test_index: int):
        problem = contest.problems.get(pid)
        if problem is None:
            return jsonify({"error": "题目不存在"}), 404
        if problem.is_fill_result:
            return jsonify({"error": "结果填空题没有测试用例"}), 400

        config_path = problem.base_dir / "problem.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            return jsonify({"error": f"读取题目配置失败：{exc}"}), 500
        tests = config.get("tests")
        if not isinstance(tests, list) or test_index < 1 or test_index > len(tests):
            return jsonify({"error": "测试用例不存在"}), 404

        removed = tests.pop(test_index - 1)
        try:
            tmp_config = config_path.with_suffix(".tmp")
            tmp_config.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp_config, config_path)
            refreshed = load_problem(problem.base_dir)
        except (OSError, ValueError, ConfigError) as exc:
            return jsonify({"error": f"删除测试用例失败：{exc}"}), 500

        if isinstance(removed, dict):
            remaining = [item for item in tests if isinstance(item, dict)]
            for key in ("input", "answer"):
                value = removed.get(key)
                if not isinstance(value, str) or not value:
                    continue
                if any(item.get(key) == value for item in remaining):
                    continue
                path = (problem.base_dir / value).resolve()
                try:
                    if problem.base_dir.resolve() in path.parents and path.is_file():
                        path.unlink()
                except OSError:
                    pass

        with problem_lock:
            contest.problems[pid] = refreshed
        return jsonify({"ok": True, "problem": _problem_summary(refreshed), "removed": removed})

    # ---------- 试卷 API ----------

    @app.get("/api/papers")
    def api_papers():
        return jsonify({"papers": [_paper_summary(p, contest) for p in registry.list_papers()]})

    @app.post("/api/papers")
    def api_create_paper():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "请求体必须是 JSON 对象"}), 400
        title = str(data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "请填写试卷名称"}), 400
        problem_ids = data.get("problem_ids")
        if not isinstance(problem_ids, list) or not problem_ids:
            return jsonify({"error": "请至少选择一道题目"}), 400
        clean_ids = []
        for pid in problem_ids:
            problem = contest.problems.get(str(pid))
            if problem is None:
                return jsonify({"error": f"题目不存在：{pid}"}), 400
            if not problem.is_ready:
                return jsonify({"error": f"题目 {pid} 还没有测试点，不能加入试卷"}), 400
            clean_ids.append(problem.id)
        paper = registry.create_paper(title, clean_ids)
        return jsonify({"paper": _paper_summary(paper, contest)}), 201

    @app.delete("/api/papers/<paper_id>")
    def api_delete_paper(paper_id: str):
        paper = registry.get_paper(paper_id)
        if paper is None:
            return jsonify({"error": "试卷不存在"}), 404
        force = request.args.get("force") == "1"
        used = registry.exams_using_paper(paper_id)
        if used and not force:
            titles = "、".join(e["title"] for e in used)
            return jsonify(
                {"error": f"试卷已被考试使用（{titles}），请先删除对应考试"}
            ), 409
        affected = registry.detach_paper_from_exams(paper_id) if used else []
        registry.delete_paper(paper_id)
        return jsonify({"ok": True, "detached_from_exams": affected})

    # ---------- 考试 API ----------

    @app.get("/api/exams")
    def api_exams():
        finalize_ended_exams()
        return jsonify({"exams": [_exam_summary(e, registry) for e in registry.list_exams()]})

    @app.post("/api/exams")
    def api_create_exam():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "请求体必须是 JSON 对象"}), 400
        title = str(data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "请填写考试名称"}), 400
        paper_ids = data.get("paper_ids")
        if not isinstance(paper_ids, list) or not paper_ids:
            return jsonify({"error": "请至少选择一套试卷"}), 400
        clean_papers = []
        for paper_id in paper_ids:
            paper = registry.get_paper(str(paper_id))
            if paper is None:
                return jsonify({"error": f"试卷不存在：{paper_id}"}), 400
            clean_papers.append(paper["id"])
        try:
            duration = int(data.get("duration_minutes", 60))
        except (TypeError, ValueError):
            return jsonify({"error": "考试时长必须是整数分钟"}), 400
        if duration < 1 or duration > 1440:
            return jsonify({"error": "考试时长需在 1-1440 分钟之间"}), 400
        exam = registry.create_exam(
            title, clean_papers, duration, bool(data.get("scoreboard_enabled", False))
        )
        return jsonify({"exam": _exam_summary(exam, registry)}), 201

    @app.post("/api/exams/<exam_id>/start")
    def api_start_exam(exam_id: str):
        try:
            exam = registry.start_exam(exam_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        if exam is None:
            return jsonify({"error": "考试不存在"}), 404
        return jsonify({"exam": _exam_summary(exam, registry)})

    @app.post("/api/exams/<exam_id>/end")
    def api_end_exam(exam_id: str):
        try:
            exam = registry.end_exam(exam_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        if exam is None:
            return jsonify({"error": "考试不存在"}), 404
        exam = finalize_exam(exam_id)
        return jsonify({"exam": _exam_summary(exam, registry)})

    @app.get("/api/exams/<exam_id>/final")
    def api_exam_final(exam_id: str):
        exam = finalize_exam(exam_id)
        if exam is None:
            return jsonify({"error": "考试不存在"}), 404
        if exam_state(exam)["status"] != "ended":
            return jsonify({"error": "考试还没有结束，最终成绩尚未生成"}), 409
        if exam.get("final_ranking") is None:
            return jsonify({"error": "考试已结束，但还有提交在评测中，请稍后重试"}), 409
        return jsonify(
            {
                "exam": _exam_summary(exam, registry),
                "final_ranking": exam.get("final_ranking", []),
                "finalized_at": exam.get("finalized_at"),
            }
        )

    @app.post("/api/exams/<exam_id>/scoreboard")
    def api_exam_scoreboard(exam_id: str):
        data = request.get_json(silent=True) or {}
        exam = registry.set_scoreboard(exam_id, bool(data.get("enabled")))
        if exam is None:
            return jsonify({"error": "考试不存在"}), 404
        return jsonify({"exam": _exam_summary(exam, registry)})

    @app.delete("/api/exams/<exam_id>")
    def api_delete_exam(exam_id: str):
        if registry.get_exam(exam_id) is None:
            return jsonify({"error": "考试不存在"}), 404
        registry.delete_exam(exam_id)
        return jsonify({"ok": True})

    # ---------- 学生账号 API ----------

    @app.delete("/api/students/<student_id>")
    def api_delete_student(student_id: str):
        if registry.get_student(student_id) is None:
            return jsonify({"error": "账号不存在"}), 404
        registry.delete_student(student_id)
        return jsonify({"ok": True})

    # ---------- 错误处理 ----------

    @app.errorhandler(413)
    def too_large(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "请求体过大"}), 413
        flash("上传内容过大（源码/数据上限 1 MB）。", "err")
        return redirect(request.referrer or url_for("student"))

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return render_template("error.html", message="页面不存在或已失效。"), 404

    return app
