"""账号、试卷、考试的数据存储（JSON 文件，线程安全，原子写入）。"""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    return datetime.now().strftime(DATETIME_FORMAT)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, DATETIME_FORMAT)
    except (TypeError, ValueError):
        return None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def exam_state(exam: dict, now: datetime | None = None) -> dict:
    """根据开始/结束时间计算考试状态。"""
    now = now or datetime.now()
    started = parse_time(exam.get("started_at"))
    ends = parse_time(exam.get("ends_at"))
    if started is None or ends is None:
        return {"status": "upcoming", "label": "未开始", "remaining_seconds": None}
    if now >= ends:
        return {"status": "ended", "label": "已结束", "remaining_seconds": 0}
    return {
        "status": "running",
        "label": "进行中",
        "remaining_seconds": max(0, int((ends - now).total_seconds())),
    }


class Registry:
    """students / papers / exams 三个集合。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data = {"students": [], "papers": [], "exams": []}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            try:
                self.path.replace(self.path.with_suffix(".broken"))
            except OSError:
                pass
            return
        for key in ("students", "papers", "exams"):
            value = raw.get(key)
            if isinstance(value, list):
                self.data[key] = [item for item in value if isinstance(item, dict) and item.get("id")]

    def _save_locked(self) -> None:
        payload = {"version": 1, **self.data}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _list(self, key: str) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self.data[key])

    def _get(self, key: str, item_id: str) -> dict | None:
        with self.lock:
            for item in self.data[key]:
                if item.get("id") == item_id:
                    return copy.deepcopy(item)
        return None

    def _delete(self, key: str, item_id: str) -> bool:
        with self.lock:
            for index, item in enumerate(self.data[key]):
                if item.get("id") == item_id:
                    del self.data[key][index]
                    self._save_locked()
                    return True
        return False

    # ---------- 学生账号 ----------

    def find_student_by_username(self, username: str) -> dict | None:
        with self.lock:
            for student in self.data["students"]:
                if student.get("username") == username:
                    return copy.deepcopy(student)
        return None

    def find_student_by_real_name(self, real_name: str) -> dict | None:
        """仅当真实姓名唯一时返回，避免同名误登录。"""
        with self.lock:
            matches = [s for s in self.data["students"] if s.get("real_name") == real_name]
            if len(matches) == 1:
                return copy.deepcopy(matches[0])
        return None

    def get_student(self, student_id: str) -> dict | None:
        return self._get("students", student_id)

    def list_students(self) -> list[dict]:
        return self._list("students")

    def create_student(
        self,
        username: str,
        real_name: str,
        password_hash: str,
        password_plain: str | None = None,
    ) -> dict:
        with self.lock:
            if any(s.get("username") == username for s in self.data["students"]):
                raise ValueError("登录名已存在")
            student = {
                "id": new_id("stu"),
                "username": username,
                "real_name": real_name,
                "password_hash": password_hash,
                "password": password_plain,
                "created_at": now_str(),
            }
            self.data["students"].append(student)
            self._save_locked()
            return copy.deepcopy(student)

    def delete_student(self, student_id: str) -> bool:
        return self._delete("students", student_id)

    def update_student_password(
        self,
        student_id: str,
        password_hash: str,
        password_plain: str | None = None,
    ) -> dict | None:
        with self.lock:
            for student in self.data["students"]:
                if student.get("id") == student_id:
                    student["password_hash"] = password_hash
                    if password_plain is not None:
                        student["password"] = password_plain
                    self._save_locked()
                    return copy.deepcopy(student)
        return None

    # ---------- 试卷 ----------

    def list_papers(self) -> list[dict]:
        return self._list("papers")

    def get_paper(self, paper_id: str) -> dict | None:
        return self._get("papers", paper_id)

    def create_paper(self, title: str, problem_ids: list[str]) -> dict:
        with self.lock:
            paper = {
                "id": new_id("paper"),
                "title": title,
                "problem_ids": list(dict.fromkeys(problem_ids)),
                "created_at": now_str(),
            }
            self.data["papers"].append(paper)
            self._save_locked()
            return copy.deepcopy(paper)

    def delete_paper(self, paper_id: str) -> bool:
        return self._delete("papers", paper_id)

    def papers_using_problem(self, problem_id: str) -> list[dict]:
        return [p for p in self.list_papers() if problem_id in p.get("problem_ids", [])]

    def exams_using_paper(self, paper_id: str) -> list[dict]:
        return [e for e in self.list_exams() if paper_id in e.get("paper_ids", [])]

    def detach_problem_from_papers(self, problem_id: str) -> list[str]:
        """强制删除题目时，从试卷中移除它；返回因此变空而被删除的试卷 id。"""
        with self.lock:
            kept_papers = []
            removed_papers = []
            for paper in self.data["papers"]:
                if problem_id in paper.get("problem_ids", []):
                    paper["problem_ids"] = [
                        pid for pid in paper.get("problem_ids", []) if pid != problem_id
                    ]
                    if not paper["problem_ids"]:
                        removed_papers.append(paper["id"])
                        continue
                kept_papers.append(paper)
            if removed_papers:
                self.data["papers"] = kept_papers
                for exam in self.data["exams"]:
                    exam["paper_ids"] = [
                        pid for pid in exam.get("paper_ids", []) if pid not in removed_papers
                    ]
            self._save_locked()
            return removed_papers

    def detach_paper_from_exams(self, paper_id: str) -> list[str]:
        """强制删除试卷时，从考试中移除它；返回受影响的考试 id。"""
        with self.lock:
            affected = []
            for exam in self.data["exams"]:
                if paper_id in exam.get("paper_ids", []):
                    exam["paper_ids"] = [
                        pid for pid in exam.get("paper_ids", []) if pid != paper_id
                    ]
                    affected.append(exam["id"])
            self._save_locked()
            return affected

    # ---------- 考试 ----------

    def list_exams(self) -> list[dict]:
        return self._list("exams")

    def get_exam(self, exam_id: str) -> dict | None:
        return self._get("exams", exam_id)

    def create_exam(
        self,
        title: str,
        paper_ids: list[str],
        duration_minutes: int,
        scoreboard_enabled: bool,
    ) -> dict:
        with self.lock:
            exam = {
                "id": new_id("exam"),
                "title": title,
                "paper_ids": list(dict.fromkeys(paper_ids)),
                "duration_minutes": duration_minutes,
                "scoreboard_enabled": bool(scoreboard_enabled),
                "created_at": now_str(),
                "started_at": None,
                "ends_at": None,
            }
            self.data["exams"].append(exam)
            self._save_locked()
            return copy.deepcopy(exam)

    def start_exam(self, exam_id: str) -> dict | None:
        with self.lock:
            for exam in self.data["exams"]:
                if exam.get("id") != exam_id:
                    continue
                state = exam_state(exam)
                if state["status"] == "running":
                    raise ValueError("考试已经开始")
                if state["status"] == "ended":
                    raise ValueError("考试已经结束，不能重新开始")
                started = datetime.now()
                ends = started + timedelta(minutes=int(exam.get("duration_minutes", 60)))
                exam["started_at"] = started.strftime(DATETIME_FORMAT)
                exam["ends_at"] = ends.strftime(DATETIME_FORMAT)
                self._save_locked()
                return copy.deepcopy(exam)
        return None

    def end_exam(self, exam_id: str) -> dict | None:
        with self.lock:
            for exam in self.data["exams"]:
                if exam.get("id") != exam_id:
                    continue
                state = exam_state(exam)
                if state["status"] == "ended":
                    raise ValueError("考试已经结束")
                if state["status"] == "upcoming":
                    raise ValueError("考试还没有开始")
                exam["ends_at"] = now_str()
                self._save_locked()
                return copy.deepcopy(exam)
        return None

    def set_scoreboard(self, exam_id: str, enabled: bool) -> dict | None:
        with self.lock:
            for exam in self.data["exams"]:
                if exam.get("id") == exam_id:
                    exam["scoreboard_enabled"] = bool(enabled)
                    self._save_locked()
                    return copy.deepcopy(exam)
        return None

    def set_final_ranking(self, exam_id: str, ranking: list[dict]) -> dict | None:
        """保存考试结束时的最终排名快照，后续不再变动。"""
        with self.lock:
            for exam in self.data["exams"]:
                if exam.get("id") == exam_id:
                    exam["final_ranking"] = copy.deepcopy(ranking)
                    exam["finalized_at"] = now_str()
                    self._save_locked()
                    return copy.deepcopy(exam)
        return None

    def add_participant(
        self,
        exam_id: str,
        student_id: str,
        username: str,
        real_name: str,
        paper_id: str | None = None,
    ) -> dict | None:
        """记录进入考试的学生；未提交的学生也会出现在排名里（0 分）。"""
        with self.lock:
            for exam in self.data["exams"]:
                if exam.get("id") != exam_id:
                    continue
                participants = exam.get("participants")
                if not isinstance(participants, list):
                    participants = []
                    exam["participants"] = participants
                existing = next(
                    (p for p in participants if p.get("student_id") == student_id), None
                )
                if existing is None:
                    participants.append(
                        {
                            "student_id": student_id,
                            "username": username,
                            "student": real_name,
                            "paper_id": paper_id,
                            "joined_at": now_str(),
                        }
                    )
                    self._save_locked()
                elif (
                    existing.get("username") != username
                    or existing.get("student") != real_name
                    or (paper_id is not None and existing.get("paper_id") != paper_id)
                ):
                    existing["username"] = username
                    existing["student"] = real_name
                    if paper_id is not None:
                        existing["paper_id"] = paper_id
                    self._save_locked()
                return copy.deepcopy(exam)
        return None

    def delete_exam(self, exam_id: str) -> bool:
        return self._delete("exams", exam_id)
