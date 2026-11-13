# -*- coding: utf-8 -*-
"""Web 集成测试：账号、试卷、考试、提交、成绩榜与教师管理。"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from bbjudge.webapp import _scoreboard, create_app

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "pass123"


class ScoreboardLogicTests(unittest.TestCase):
    def _record(self, student, sid, paper, problem, score, verdict, created_at):
        return {
            "exam_id": "e1",
            "student": student,
            "student_id": sid,
            "paper_id": paper,
            "problem_id": problem,
            "status": "done",
            "score": score,
            "verdict": verdict,
            "created_at": created_at,
        }

    def _participants(self, *items):
        return [
            {"student_id": sid, "student": name, "paper_id": paper, "joined_at": "2026-01-01 08:00:00"}
            for name, sid, paper in items
        ]

    def test_ac_count_is_primary_sort_key(self) -> None:
        records = [
            self._record("学生A", "a", "P1", "p1", 50, "AC", "2026-01-01 10:00:00"),
            self._record("学生B", "b", "P1", "p2", 50, "WA", "2026-01-01 09:00:00"),
            self._record("学生B", "b", "P1", "p3", 50, "WA", "2026-01-01 09:01:00"),
        ]
        ranking = _scoreboard(
            records, "e1", self._participants(("学生A", "a", "P1"), ("学生B", "b", "P1")), {"P1": 100}
        )["ranking"]
        self.assertEqual([row["student"] for row in ranking], ["学生A", "学生B"])
        self.assertEqual(ranking[0]["ac"], 1)
        self.assertEqual(ranking[1]["total"], 100.0)

    def test_total_score_breaks_ac_tie(self) -> None:
        records = [
            self._record("学生A", "a", "P1", "p1", 50, "AC", "2026-01-01 10:00:00"),
            self._record("学生B", "b", "P1", "p1", 100, "AC", "2026-01-01 09:00:00"),
        ]
        ranking = _scoreboard(
            records, "e1", self._participants(("学生A", "a", "P1"), ("学生B", "b", "P1")), {"P1": 100}
        )["ranking"]
        self.assertEqual(ranking[0]["student"], "学生B")
        self.assertGreater(ranking[0]["total"], ranking[1]["total"])

    def test_earlier_submission_breaks_full_tie(self) -> None:
        records = [
            self._record("学生A", "a", "P1", "p1", 100, "AC", "2026-01-01 10:00:00"),
            self._record("学生B", "b", "P1", "p1", 100, "AC", "2026-01-01 09:00:00"),
        ]
        ranking = _scoreboard(
            records, "e1", self._participants(("学生A", "a", "P1"), ("学生B", "b", "P1")), {"P1": 100}
        )["ranking"]
        self.assertEqual([row["student"] for row in ranking], ["学生B", "学生A"])

    def test_papers_are_normalized_by_average_max_score(self) -> None:
        records = [
            self._record("A卷学生", "a", "P1", "p1", 100, "AC", "2026-01-01 10:00:00"),
            self._record("B卷学生", "b", "P2", "p2", 100, "AC", "2026-01-01 10:00:01"),
        ]
        ranking = _scoreboard(
            records,
            "e1",
            self._participants(("A卷学生", "a", "P1"), ("B卷学生", "b", "P2")),
            {"P1": 100, "P2": 200},
        )["ranking"]
        self.assertEqual(ranking[0]["student"], "A卷学生")
        self.assertEqual(ranking[0]["total"], 150.0)
        self.assertEqual(ranking[1]["total"], 75.0)
        self.assertEqual(ranking[0]["raw_total"], 100.0)
        self.assertEqual(ranking[0]["paper_max"], 100.0)


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(ROOT / "samples", self.root / "samples")
        self.contest_path = self.root / "samples" / "contest.json"
        self.app = create_app(self.contest_path, self.root / "data", start_worker=False)
        self.worker = self.app.extensions["bbjudge"].worker
        self.store = self.app.extensions["bbjudge"].store
        self.registry = self.app.extensions["bbjudge"].registry
        self.problems_dir = self.root / "samples" / "problems"
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---------- helpers ----------

    def _register(
        self,
        client=None,
        username: str = "stu001",
        real_name: str = "张三",
        password: str = PASSWORD,
        confirm: str | None = None,
    ):
        client = client or self.client
        return client.post(
            "/student/register",
            data={
                "username": username,
                "real_name": real_name,
                "password": password,
                "confirm": confirm if confirm is not None else password,
            },
            follow_redirects=False,
        )

    def _login(self, username: str, password: str = PASSWORD, client=None):
        client = client or self.client
        return client.post(
            "/student/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )

    def _create_paper(self, problem_ids: list[str], title: str = "A卷"):
        response = self.client.post(
            "/api/papers", json={"title": title, "problem_ids": problem_ids}
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["paper"]["id"]

    def _create_exam(
        self,
        paper_ids: list[str],
        title: str = "第一次考试",
        duration: int = 60,
        scoreboard: bool = True,
        start: bool = True,
    ) -> str:
        response = self.client.post(
            "/api/exams",
            json={
                "title": title,
                "paper_ids": paper_ids,
                "duration_minutes": duration,
                "scoreboard_enabled": scoreboard,
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        exam_id = response.get_json()["exam"]["id"]
        if start:
            started = self.client.post(f"/api/exams/{exam_id}/start")
            self.assertEqual(started.status_code, 200, started.get_json())
        return exam_id

    def _submit(
        self,
        exam_id: str,
        paper_id: str,
        problem: str,
        content: bytes | None = None,
        text: str | None = None,
        client=None,
    ):
        client = client or self.client
        data = {"exam_id": exam_id, "paper_id": paper_id, "problem": problem}
        if content is not None:
            data["file"] = (io.BytesIO(content), "main.cpp")
        if text is not None:
            data["text"] = text
        return client.post(
            "/student/submit",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    def _process(self, sid: str, client=None) -> dict:
        self.worker.process(sid)
        client = client or self.client
        return client.get(f"/api/submission/{sid}").get_json()

    def _create_problem(self, **overrides):
        payload = {
            "id": "p2001",
            "title": "新建题目",
            "type": "program",
            "score": 100,
            "time_limit_ms": 1000,
            "memory_limit_mb": 256,
            "comparator": "token",
            "statement": "# 题面",
            "answers": "",
        }
        payload.update(overrides)
        return self.client.post("/api/problems", json=payload)

    def _add_test(self, pid: str, score: float, input_text: str, answer_text: str):
        return self.client.post(
            f"/api/problems/{pid}/tests",
            data={"name": "测试", "score": str(score), "input_text": input_text, "answer_text": answer_text},
            content_type="multipart/form-data",
        )

    # ---------- 账号与入口 ----------

    def test_auth_pages_and_portal_separation(self) -> None:
        root = self.client.get("/")
        self.assertEqual(root.status_code, 302)
        self.assertTrue(root.headers["Location"].endswith("/student"))

        anonymous = self.client.get("/student")
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/student/login", anonymous.headers["Location"])
        self.assertEqual(self.client.get("/student/login").status_code, 200)
        self.assertEqual(self.client.get("/student/register").status_code, 200)

        registered = self._register()
        self.assertEqual(registered.status_code, 302)
        self.assertTrue(registered.headers["Location"].endswith("/student"))

        student_page = self.client.get("/student").get_data(as_text=True)
        self.assertIn("张三", student_page)
        self.assertIn("Love-Coding", student_page)
        self.assertIn("智航AI协会考试测验", student_page)
        self.assertIn("智航AI协会", student_page)
        self.assertNotIn('href="/teacher"', student_page)
        self.assertNotIn("教师端", student_page)

        for path in ("/teacher", "/teacher/papers", "/teacher/exams", "/teacher/accounts"):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            text = page.get_data(as_text=True)
            self.assertNotIn('href="/student"', text)
            self.assertNotIn("学生提交", text)

        accounts = self.client.get("/teacher/accounts").get_data(as_text=True)
        self.assertIn("张三", accounts)
        self.assertIn("stu001", accounts)

        self.client.get("/student/logout")
        self.assertEqual(self.client.get("/student").status_code, 302)
        self.assertEqual(self._login("张三").status_code, 302)
        self.assertEqual(self.client.get("/student").status_code, 200)

    def test_registration_validation(self) -> None:
        bad = self._register(real_name="", username="stu002")
        self.assertEqual(bad.status_code, 400)
        bad = self._register(password="123", confirm="123", username="stu003")
        self.assertEqual(bad.status_code, 400)
        bad = self._register(password="123456", confirm="654321", username="stu004")
        self.assertEqual(bad.status_code, 400)

        self.assertEqual(self._register().status_code, 302)
        other = self.app.test_client()
        duplicate = self._register(client=other)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(len(self.registry.list_students()), 1)

    def test_web_starts_with_empty_problem_bank(self) -> None:
        problems_dir = self.root / "samples" / "problems"
        for child in problems_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
        app = create_app(self.contest_path, self.root / "empty_data", start_worker=False)
        client = app.test_client()
        self.assertEqual(client.get("/teacher").status_code, 200)
        self.assertEqual(client.get("/student/register").status_code, 200)
        self.assertEqual(client.get("/api/problems").get_json()["problems"], [])

    def test_register_without_login_name_uses_real_name(self) -> None:
        client = self.app.test_client()
        response = self._register(client=client, username="", real_name="王小明")
        self.assertEqual(response.status_code, 302)
        client.get("/student/logout")
        self.assertEqual(self._login("王小明", client=client).status_code, 302)

    def test_account_management_and_password_change(self) -> None:
        self._register()
        student = self.registry.list_students()[0]

        accounts_page = self.client.get("/teacher/accounts").get_data(as_text=True)
        self.assertNotIn("提交次数", accounts_page)
        self.assertIn(f'href="/teacher/accounts/{student["id"]}"', accounts_page)
        detail = self.client.get(f"/teacher/accounts/{student['id']}").get_data(as_text=True)
        self.assertIn("stu001", detail)
        self.assertIn("张三", detail)
        self.assertIn("注册时间", detail)
        self.assertIn(PASSWORD, detail)
        self.assertNotIn("提交次数", detail)

        student_page = self.client.get("/student").get_data(as_text=True)
        self.assertIn("账号管理：张三", student_page)
        self.assertIn('href="/student/account"', student_page)
        self.assertIn("修改密码", self.client.get("/student/account").get_data(as_text=True))

        wrong = self.client.post(
            "/student/account/password",
            data={"old_password": "wrong", "new_password": "newpass123", "confirm": "newpass123"},
            follow_redirects=True,
        )
        self.assertIn("原密码不正确", wrong.get_data(as_text=True))

        changed = self.client.post(
            "/student/account/password",
            data={"old_password": PASSWORD, "new_password": "newpass123", "confirm": "newpass123"},
            follow_redirects=False,
        )
        self.assertEqual(changed.status_code, 302)
        self.client.get("/student/logout")
        self.assertEqual(self._login("stu001", PASSWORD).status_code, 401)
        self.assertEqual(self._login("stu001", "newpass123").status_code, 302)

    def test_teacher_can_view_and_reset_password(self) -> None:
        self._register()
        student = self.registry.list_students()[0]
        detail = self.client.get(f"/teacher/accounts/{student['id']}").get_data(as_text=True)
        self.assertIn(PASSWORD, detail)

        reset = self.client.post(
            f"/teacher/accounts/{student['id']}/password",
            data={"new_password": "reset123", "confirm": "reset123"},
            follow_redirects=False,
        )
        self.assertEqual(reset.status_code, 302)
        detail = self.client.get(f"/teacher/accounts/{student['id']}").get_data(as_text=True)
        self.assertIn("reset123", detail)
        self.assertNotIn(PASSWORD, detail)

        self.client.get("/student/logout")
        self.assertEqual(self._login("stu001", PASSWORD).status_code, 401)
        self.assertEqual(self._login("stu001", "reset123").status_code, 302)

    def test_teacher_can_delete_student_account(self) -> None:
        self._register()
        student_id = self.registry.list_students()[0]["id"]
        response = self.client.delete(f"/api/students/{student_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.registry.list_students(), [])
        self.assertEqual(self._login("stu001", PASSWORD).status_code, 401)

    # ---------- 试卷 / 考试管理 ----------

    def test_teacher_can_delete_test_case_from_edit_page(self) -> None:
        self._create_problem(id="p2012", title="可删除测试点")
        self.assertEqual(self._add_test("p2012", 40, "1 1\n", "2\n").status_code, 201)
        self.assertEqual(self._add_test("p2012", 60, "2 2\n", "4\n").status_code, 201)

        page = self.client.get("/teacher/problems/p2012").get_data(as_text=True)
        self.assertIn('data-delete-test="1"', page)
        self.assertIn('data-delete-test="2"', page)

        deleted = self.client.delete("/api/problems/p2012/tests/1")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["problem"]["test_count"], 1)
        detail = self.client.get("/teacher/problems/p2012").get_data(as_text=True)
        self.assertNotIn('data-delete-test="2"', detail)
        self.assertIn('data-delete-test="1"', detail)

        self.assertEqual(self.client.delete("/api/problems/p2012/tests/9").status_code, 404)

    def test_teacher_edit_shows_configured_score_before_tests(self) -> None:
        created = self._create_problem(id="p2011", title="草稿分值", score=80)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["problem"]["max_score"], 80.0)

        detail = self.client.get("/teacher/problems/p2011").get_data(as_text=True)
        self.assertIn('value="80.0"', detail)
        self.assertNotIn('value="0"', detail)

        updated = self.client.put(
            "/api/problems/p2011",
            json={
                "title": "草稿分值",
                "score": 120,
                "time_limit_ms": 1000,
                "memory_limit_mb": 256,
                "comparator": "token",
            },
        )
        self.assertEqual(updated.status_code, 200)
        detail = self.client.get("/teacher/problems/p2011").get_data(as_text=True)
        self.assertIn('value="120.0"', detail)

    def test_teacher_problem_statement_and_samples_can_be_edited(self) -> None:
        created = self._create_problem(
            id="p2010",
            title="带题面的题",
            description='<p>这是题目描述</p><img src="data:image/png;base64,abc">'
            "<script>alert(1)</script>",
            input_description="第一行两个整数",
            output_description="输出它们的和",
            samples=[{"input": "1 2\n", "output": "3\n"}],
        )
        self.assertEqual(created.status_code, 201)
        problem = created.get_json()["problem"]
        self.assertIn("这是题目描述", problem["description"])
        self.assertIn("data:image/png", problem["description"])
        self.assertNotIn("<script>", problem["description"])
        self.assertEqual(problem["samples"][0]["input"], "1 2\n")

        detail = self.client.get("/teacher/problems/p2010").get_data(as_text=True)
        self.assertIn("题目编辑", detail)
        self.assertIn("这是题目描述", detail)
        self.assertIn("data:image/png", detail)
        self.assertIn("第一行两个整数", detail)
        self.assertIn("1 2", detail)

        updated = self.client.put(
            "/api/problems/p2010",
            json={
                "title": "修改后的题面",
                "score": 100,
                "time_limit_ms": 1500,
                "memory_limit_mb": 128,
                "comparator": "token",
                "description": "<p>修改后的描述</p>",
                "input_description": "新的输入说明",
                "output_description": "新的输出说明",
                "samples": [{"input": "5 6", "output": "11"}],
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["problem"]["title"], "修改后的题面")
        self.assertTrue((self.problems_dir / "p2010" / "problem.json").is_file())

        self.assertEqual(self._add_test("p2010", 100, "1 2\n", "3\n").status_code, 201)
        paper_id = self._create_paper(["p2010"], title="题面卷")
        exam_id = self._create_exam([paper_id], title="题面考试")
        self._register()
        student_page = self.client.get(
            f"/student/exam/{exam_id}?view=submit"
        ).get_data(as_text=True)
        self.assertIn("题目信息", student_page)
        self.assertIn('id="problem-data"', student_page)
        self.assertIn('"p2010"', student_page)
        self.assertIn("5 6", student_page)

    def test_teacher_add_test_page_preserves_selected_problem(self) -> None:
        self._create_problem(id="p2001", title="第一题")
        self._create_problem(id="p2002", title="第二题")
        page = self.client.get("/teacher?problem=p2002").get_data(as_text=True)
        self.assertIn('value="p2002" selected', page)
        self.assertNotIn('value="p2001" selected', page)

    def test_paper_and_exam_delete_dependencies(self) -> None:
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id], start=False)

        blocked = self.client.delete(f"/api/papers/{paper_id}")
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("考试", blocked.get_json()["error"])

        forced = self.client.delete(f"/api/papers/{paper_id}?force=1")
        self.assertEqual(forced.status_code, 200)
        self.assertIsNone(self.registry.get_paper(paper_id))
        self.assertEqual(self.registry.get_exam(exam_id)["paper_ids"], [])

        self.assertEqual(self.client.delete(f"/api/exams/{exam_id}").status_code, 200)

    def test_teacher_delete_problem_dependency(self) -> None:
        created = self._create_problem(id="p2003", title="临时题")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self._add_test("p2003", 50, "1 2\n", "3\n").status_code, 201)

        paper_id = self._create_paper(["p2003"], title="临时卷")
        exam_id = self._create_exam([paper_id], title="临时考试", start=False)

        blocked = self.client.delete("/api/problems/p2003")
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue((self.problems_dir / "p2003").is_dir())

        forced = self.client.delete("/api/problems/p2003?force=1")
        self.assertEqual(forced.status_code, 200)
        self.assertFalse((self.problems_dir / "p2003").exists())
        self.assertIsNone(self.registry.get_paper(paper_id))
        self.assertEqual(self.registry.get_exam(exam_id)["paper_ids"], [])

        self.client.delete(f"/api/exams/{exam_id}")

    # ---------- 考试流程 ----------

    def test_exam_flow_and_scoreboard(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001", "p1002"])
        exam_id = self._create_exam([paper_id], title="阶段测验")

        student_exams = self.client.get("/student").get_data(as_text=True)
        self.assertIn("阶段测验", student_exams)
        self.assertIn("进入考试", student_exams)

        workspace = self.client.get(f"/student/exam/{exam_id}?view=submit").get_data(as_text=True)
        self.assertIn("提交说明", workspace)
        self.assertIn("选择试卷", workspace)
        self.assertNotIn("返回提交页", workspace)
        self.assertNotIn('name="student"', workspace)
        self.assertNotIn('href="/teacher"', workspace)
        self.assertIn("Love-Coding", workspace)

        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        submitted = self._submit(exam_id, paper_id, "p1001", content=source)
        self.assertEqual(submitted.status_code, 302)
        sid = submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

        record = self._process(sid)
        self.assertEqual(record["verdict"], "AC")
        self.assertEqual(record["score"], 100.0)

        status = self.client.get(f"/student/status/{sid}").get_data(as_text=True)
        self.assertIn("通过", status)
        self.assertIn("阶段测验", status)

        scoreboard = self.client.get(f"/api/scoreboard?exam_id={exam_id}").get_json()
        self.assertTrue(scoreboard["enabled"])
        self.assertEqual(scoreboard["ranking"][0]["student"], "张三")
        self.assertEqual(scoreboard["ranking"][0]["total"], 100.0)
        self.assertEqual(len(scoreboard["feed"]), 1)

        for path in ("/teacher", "/teacher/papers", "/teacher/exams"):
            text = self.client.get(path).get_data(as_text=True)
            self.assertNotIn("张三", text)
        self.assertIn("张三", self.client.get("/teacher/accounts").get_data(as_text=True))

    def test_exam_not_started_and_timeout_block_submission(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id], start=False)

        page = self.client.get(f"/student/exam/{exam_id}").get_data(as_text=True)
        self.assertIn("等待老师点击", page)
        self.assertNotIn('id="submit-form"', page)

        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        blocked = self._submit(exam_id, paper_id, "p1001", content=source)
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(self.store.all(), [])

        self.client.post(f"/api/exams/{exam_id}/start")
        self.assertIn("data-remaining", self.client.get(f"/student/exam/{exam_id}").get_data(as_text=True))
        # 直接把结束时间改到过去，模拟“到时间自动关闭”
        with self.registry.lock:
            for exam in self.registry.data["exams"]:
                if exam["id"] == exam_id:
                    exam["ends_at"] = "2000-01-01 00:00:00"
        ended_page = self.client.get(f"/student/exam/{exam_id}").get_data(as_text=True)
        self.assertNotIn("data-remaining", ended_page)
        blocked = self._submit(exam_id, paper_id, "p1001", content=source)
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(self.store.all(), [])

        self.client.get("/teacher/exams")
        self.assertIsNotNone(self.registry.get_exam(exam_id).get("final_ranking"))

    def test_final_health_check_waits_for_pending_submission(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id])
        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        submitted = self._submit(exam_id, paper_id, "p1001", content=source)
        sid = submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

        self.assertEqual(self.client.post(f"/api/exams/{exam_id}/end").status_code, 200)
        pending = self.client.get(f"/api/exams/{exam_id}/final")
        self.assertEqual(pending.status_code, 409)
        self.assertIn("评测中", pending.get_json()["error"])

        self.worker.process(sid)
        final = self.client.get(f"/api/exams/{exam_id}/final")
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.get_json()["final_ranking"][0]["total"], 100.0)

    def test_exam_final_ranking_snapshot(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id], title="最终成绩考试")
        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        submitted = self._submit(exam_id, paper_id, "p1001", content=source)
        sid = submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        self._process(sid)

        running = self.client.get(f"/api/exams/{exam_id}/final")
        self.assertEqual(running.status_code, 409)
        self.assertEqual(self.client.get(f"/teacher/exams/{exam_id}/final").status_code, 302)

        ended = self.client.post(f"/api/exams/{exam_id}/end")
        self.assertEqual(ended.status_code, 200)
        final = self.client.get(f"/api/exams/{exam_id}/final")
        self.assertEqual(final.status_code, 200)
        payload = final.get_json()
        self.assertEqual(payload["final_ranking"][0]["student"], "张三")
        self.assertEqual(payload["final_ranking"][0]["total"], 100.0)
        self.assertTrue(payload["finalized_at"])

        final_page = self.client.get(f"/teacher/exams/{exam_id}/final").get_data(as_text=True)
        self.assertIn("最终排名", final_page)
        self.assertIn("张三", final_page)
        self.assertIn("查看最终成绩", self.client.get("/teacher/exams").get_data(as_text=True))

        # 快照生成后，即使底层提交数据被改动，最终成绩也不变
        self.store.update(sid, score=0.0)
        again = self.client.get(f"/api/exams/{exam_id}/final").get_json()
        self.assertEqual(again["final_ranking"][0]["total"], 100.0)

    def test_scoreboard_can_be_toggled(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id], scoreboard=False)
        self.assertIn(
            "未开启实时排名",
            self.client.get(f"/student/exam/{exam_id}").get_data(as_text=True),
        )
        state = self.client.get(f"/api/scoreboard?exam_id={exam_id}").get_json()
        self.assertFalse(state["enabled"])

        toggled = self.client.post(
            f"/api/exams/{exam_id}/scoreboard", json={"enabled": True}
        )
        self.assertEqual(toggled.status_code, 200)
        self.assertIn(
            "实时成绩",
            self.client.get(f"/student/exam/{exam_id}").get_data(as_text=True),
        )
        self.assertTrue(
            self.client.get(f"/api/scoreboard?exam_id={exam_id}").get_json()["enabled"]
        )

    def test_exam_defaults_to_rank_and_can_switch_to_submit(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id])
        rank_page = self.client.get(f"/student/exam/{exam_id}").get_data(as_text=True)
        self.assertIn("实时排名", rank_page)
        self.assertIn("提交源码 / 答案", rank_page)
        self.assertIn("实时成绩", rank_page)
        self.assertNotIn('id="submit-form"', rank_page)

        submit_page = self.client.get(
            f"/student/exam/{exam_id}?view=submit"
        ).get_data(as_text=True)
        self.assertIn('id="submit-form"', submit_page)
        self.assertIn("提交说明", submit_page)

    def test_multi_paper_boards(self) -> None:
        self._register()
        paper_a = self._create_paper(["p1001"], title="A卷")
        paper_b = self._create_paper(["p1002"], title="B卷")
        exam_id = self._create_exam([paper_a, paper_b], title="多卷榜单")

        self.client.get(f"/student/exam/{exam_id}?paper={paper_a}")

        other = self.app.test_client()
        self.assertEqual(
            self._register(client=other, username="stu002", real_name="李四").status_code,
            302,
        )
        other.get(f"/student/exam/{exam_id}?paper={paper_b}")

        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        submitted = self._submit(exam_id, paper_a, "p1001", content=source)
        self._process(
            submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        )

        total = self.client.get(f"/student/exam/{exam_id}").get_data(as_text=True)
        self.assertIn("总榜", total)
        self.assertIn("A卷", total)
        self.assertIn("B卷", total)
        self.assertIn("张三", total)
        self.assertIn("李四", total)
        self.assertIn("归一化", total)

        board_a = self.client.get(
            f"/student/exam/{exam_id}?board={paper_a}"
        ).get_data(as_text=True)
        self.assertIn("<strong>张三</strong>", board_a)
        self.assertNotIn("<strong>李四</strong>", board_a)
        self.assertIn("分榜", board_a)

        board_b = other.get(
            f"/student/exam/{exam_id}?board={paper_b}"
        ).get_data(as_text=True)
        self.assertNotIn("<strong>李四</strong>", board_b)
        self.assertIn("还没有成绩", board_b)

    def test_scoreboard_is_scoped_to_exam(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_one = self._create_exam([paper_id], title="考试一")
        exam_two = self._create_exam([paper_id], title="考试二")
        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        submitted = self._submit(exam_one, paper_id, "p1001", content=source)
        self._process(submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1])

        one = self.client.get(f"/api/scoreboard?exam_id={exam_one}").get_json()
        two = self.client.get(f"/api/scoreboard?exam_id={exam_two}").get_json()
        self.assertEqual(len(one["ranking"]), 1)
        self.assertEqual(two["ranking"], [])
        self.assertEqual(two["feed"], [])

    def test_scoreboard_lists_all_participants_even_without_submission(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id], title="全员榜")
        self.client.get(f"/student/exam/{exam_id}")

        other = self.app.test_client()
        self.assertEqual(
            self._register(client=other, username="stu002", real_name="李四").status_code,
            302,
        )
        self.assertEqual(other.get(f"/student/exam/{exam_id}").status_code, 200)

        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        submitted = self._submit(exam_id, paper_id, "p1001", content=source)
        self._process(submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1])

        scoreboard = self.client.get(f"/api/scoreboard?exam_id={exam_id}").get_json()
        self.assertEqual(len(scoreboard["ranking"]), 2)
        by_name = {row["student"]: row for row in scoreboard["ranking"]}
        self.assertEqual(by_name["张三"]["total"], 100.0)
        self.assertEqual(by_name["李四"]["total"], 0.0)
        self.assertEqual(by_name["李四"]["submissions"], 0)
        exam = self.registry.get_exam(exam_id)
        self.assertEqual(len(exam["participants"]), 2)

        self.client.post(f"/api/exams/{exam_id}/end")
        final = self.client.get(f"/api/exams/{exam_id}/final").get_json()
        final_names = {row["student"] for row in final["final_ranking"]}
        self.assertEqual(final_names, {"张三", "李四"})
        final_li = next(row for row in final["final_ranking"] if row["student"] == "李四")
        self.assertEqual(final_li["total"], 0.0)

    def test_multiple_papers_selection(self) -> None:
        self._register()
        paper_a = self._create_paper(["p1001"], title="A卷")
        paper_b = self._create_paper(["p1002"], title="B卷")
        exam_id = self._create_exam([paper_a, paper_b], title="多卷考试")

        page_a = self.client.get(f"/student/exam/{exam_id}?view=submit").get_data(as_text=True)
        self.assertIn("p1001", page_a)
        self.assertNotIn("p1002", page_a)

        page_b = self.client.get(
            f"/student/exam/{exam_id}?view=submit&paper={paper_b}"
        ).get_data(as_text=True)
        self.assertIn("p1002", page_b)
        self.assertNotIn("p1001", page_b)

        submitted = self._submit(exam_id, paper_b, "p1002", text="5050")
        self.assertEqual(submitted.status_code, 302)
        record = self._process(
            submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        )
        self.assertEqual(record["verdict"], "AC")
        self.assertEqual(record["score"], 10.0)
        self.assertEqual(record["paper_title"], "B卷")

    def test_teacher_created_problem_can_be_examined(self) -> None:
        self._register()
        created = self._create_problem(id="p2001", title="加法")
        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.get_json()["problem"]["ready"])
        self.assertEqual(self._add_test("p2001", 50, "1 2\n", "3\n").status_code, 201)
        self.assertEqual(self._add_test("p2001", 50, "5 7\n", "12\n").status_code, 201)

        paper_id = self._create_paper(["p2001"], title="新题卷")
        exam_id = self._create_exam([paper_id], title="新题考试")
        self.assertIn("add-test-form", self.client.get("/teacher").get_data(as_text=True))
        source = (
            "#include <iostream>\n"
            "int main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<std::endl;}\n"
        ).encode("utf-8")
        submitted = self._submit(exam_id, paper_id, "p2001", content=source)
        record = self._process(
            submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        )
        self.assertEqual(record["verdict"], "AC")
        self.assertEqual(record["score"], 100.0)

    def test_fill_result_problem_in_exam(self) -> None:
        self._register()
        created = self._create_problem(
            id="p2002", title="填空", type="fill_result", score=30, answers="42\n43"
        )
        self.assertEqual(created.status_code, 201)
        paper_id = self._create_paper(["p2002"], title="填空卷")
        exam_id = self._create_exam([paper_id], title="填空考试")
        submitted = self._submit(exam_id, paper_id, "p2002", text="42")
        record = self._process(
            submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        )
        self.assertEqual(record["verdict"], "AC")
        self.assertEqual(record["score"], 30.0)

    def test_other_student_cannot_view_status(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id])
        source = (ROOT / "samples" / "submissions" / "zhangsan_p1001.cpp").read_bytes()
        submitted = self._submit(exam_id, paper_id, "p1001", content=source)
        sid = submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        self._process(sid)

        other = self.app.test_client()
        self.assertEqual(self._register(client=other, username="stu002", real_name="李四").status_code, 302)
        self.assertEqual(other.get(f"/student/status/{sid}").status_code, 404)
        self.assertEqual(other.get(f"/api/submission/{sid}").status_code, 404)

    def test_student_status_masks_expected_answer(self) -> None:
        self._register()
        self._create_problem(
            id="p2002", title="填空", type="fill_result", score=30, answers="42"
        )
        paper_id = self._create_paper(["p2002"], title="填空卷")
        exam_id = self._create_exam([paper_id], title="填空考试")
        submitted = self._submit(exam_id, paper_id, "p2002", text="41")
        sid = submitted.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        self._process(sid)

        raw = self.store.get(sid)
        self.assertIn("期望", raw["message"])
        self.assertIn("42", raw["message"])

        page = self.client.get(f"/student/status/{sid}").get_data(as_text=True)
        self.assertIn("答案不正确", page)
        self.assertNotIn("期望", page)

        api = self.client.get(f"/api/submission/{sid}").get_json()
        self.assertEqual(api["message"], "答案不正确")
        self.assertEqual(api["result"]["tests"][0]["message"], "答案不正确")

    def test_blank_submission_stays_on_submit_page(self) -> None:
        self._register()
        paper_id = self._create_paper(["p1001"])
        exam_id = self._create_exam([paper_id])
        response = self.client.post(
            "/student/submit",
            data={
                "exam_id": exam_id,
                "paper_id": paper_id,
                "problem": "p1001",
                "text": "",
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("view=submit", location)
        self.assertIn(f"paper={paper_id}", location)
        self.assertIn("problem=p1001", location)

        page = self.client.get(location).get_data(as_text=True)
        self.assertIn('id="submit-form"', page)
        self.assertIn("请上传源码/答案文件，或直接粘贴内容", page)
        self.assertIn("提交源码 / 答案", page)

    def test_submission_requires_exam(self) -> None:
        self._register()
        response = self.client.post(
            "/student/submit",
            data={"text": "int main(){}"},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.store.all(), [])

    def test_paper_requires_ready_problem(self) -> None:
        created = self._create_problem(id="p2004", title="草稿")
        self.assertEqual(created.status_code, 201)
        response = self.client.post(
            "/api/papers", json={"title": "草稿卷", "problem_ids": ["p2004"]}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("测试点", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
