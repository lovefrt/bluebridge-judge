# Love-Coding 判题器（bluebridge-judge）

一个仿蓝桥杯的本地评测系统：命令行可以批量判题，Web 端支持**学生注册登录 → 选择考试 → 选择试卷 → 提交 → 实时成绩**，教师端可以管理题目、试卷、考试和学生账号。

它保留了蓝桥杯的核心判题方式：

- **两种题型**：结果填空题、程序设计题；
- **按测试点独立给分**：每个测试点有自己的分值，做对一部分就拿部分分；
- **固定编译环境**：C++17 / C11 / Java / Python；
- **赛后/限时评测**：考试有开始时间和时长，到时间服务器自动拒绝提交；
- **实时成绩可开关**：教师可以决定某场考试是否向学生展示实时排名；进入考试的学生全部上榜，未提交也会显示 0 分。

> 功能需求的全量清单见 [docs/功能需求.md](docs/功能需求.md)。

![成绩报告预览](docs/report-preview.png)

---

## 快速开始

要求：Python 3.10+；判 C++ 需要 `g++`，判 Java 需要 JDK；Web 界面需要 Flask。

```powershell
cd bluebridge-judge
pip install -r requirements.txt
python -m bbjudge doctor
```

启动 Web 端：

```powershell
python -m bbjudge web --contest samples/contest.json --data-dir webdata_demo
```

### 一键启动（Windows）

双击项目根目录的 **`start.cmd`** 即可。脚本会自动：

- 进入项目目录（从任意位置双击都能用）；
- 检查 Python 是否可用；
- 启动 Web 服务；
- 等服务就绪后自动打开学生端页面。

默认配置在 `start.cmd` 顶部：

```bat
set "CONTEST=contest.json"
set "DATA_DIR=webdata_demo"
set "PORT=8765"
```

`contest.json` 指向实际运行题库 `problems/`；`samples/` 只作为自动化测试和演示数据，不会在一键启动时加载。需要换题库、数据目录或端口时改这三行即可；停止服务：在启动窗口按 `Ctrl+C`。

### 局域网使用

如果希望同 Wi-Fi/局域网里的其他电脑或手机访问：

1. 双击项目根目录的 **`start-lan.cmd`**；
2. 脚本会自动检测本机局域网 IPv4，并监听 `0.0.0.0:8765`；
3. 其他设备打开脚本显示的地址，例如：

```text
学生端：http://192.168.1.23:8765/student
教师端：http://192.168.1.23:8765/teacher
```

注意：

- 所有设备必须在同一局域网/同一 Wi-Fi；
- Windows 防火墙弹窗时，允许 Python 访问**专用网络**；
- 如果其他设备打不开，检查是否开启了 AP 隔离、是否连了不同网段，或用管理员 PowerShell 放行端口：

```powershell
New-NetFirewallRule -DisplayName "Love-Coding 8765" -Direction Inbound `
  -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

- 教师端没有登录鉴权，且账号详情可看密码，**只适合可信的课堂局域网**，不要直接映射到公网。

手动启动局域网模式：

```powershell
python -m bbjudge web --contest contest.json --data-dir webdata_demo --host 0.0.0.0 --port 8765
```

打开：

| 入口 | 地址 | 用途 |
| --- | --- | --- |
| 学生端 | <http://127.0.0.1:8765/student> | 注册/登录、选择考试和试卷、提交、查看实时成绩 |
| 教师端 | <http://127.0.0.1:8765/teacher> | 题目、试卷、考试、学生账号管理 |

根路径 `/` 会跳转到学生端；教师端地址需要老师直接访问或收藏。

> 即使老师把题库删空，Web 端也能正常启动（题库为空），直接在教师端重新创建题目即可；命令行版仍要求至少一道题。

## Web 端使用流程

### 学生

1. 打开学生端，点击“注册”；
2. 填写**真实姓名、密码**；登录名/学号可选（不填则用真实姓名登录，重名时填学号）；
3. 登录后进入“选择考试”，选择正在进行的考试；
4. 进入考试默认看到**实时排名**；多套试卷时可在榜单上方切换“总榜 / 各卷分榜”；
5. 点击“提交源码 / 答案”切换到提交界面，选择试卷、题目，上传或粘贴后提交；
6. 提交自动绑定当前账号，不需要再填姓名；成绩榜显示真实姓名；
7. 实时排名中**进入考试即上总榜**，提交前也会以 0 分出现；各卷分榜只显示在该卷实际提交过的学生；总榜按平均满分归一化，分榜显示原始分；
8. 点击顶部的“账号管理：姓名”，可以查看自己的账号信息、修改密码；
9. 考试倒计时结束后，提交按钮自动禁用，服务器也会拒绝提交。

![学生登录](docs/web-login.png)

![学生实时排名](docs/web-student.png)

![学生提交页](docs/web-submit-page.png)

![学生题目信息与样例](docs/student-problem-info.png)

![学生分榜](docs/web-board-a.png)

![学生选择考试](docs/web-exams.png)

### 教师

- **题目管理**：创建编程题/结果填空题，填写题目描述、输入描述、输出描述和多组输入输出样例，添加/删除测试用例，删除题目；点击题目名称进入编辑页，可随时修改全部题面信息；
- **试卷管理**：勾选已就绪的题目组成试卷，删除试卷；
- **考试管理**：选择一套或多套试卷、设置时长、创建考试、点击“开始作答”、提前结束、删除考试、开关实时排名；考试结束后自动生成最终排名快照，可在列表中“查看最终成绩”；
- **学生账号**：查看登录名、真实姓名、密码、注册时间，点击真实姓名进入账号详情，可重置密码或删除账号；列表不显示提交次数；

![教师端：考试管理](docs/web-teacher.png)

![教师端：最终成绩](docs/web-final.png)

![教师端：题目管理（含删除）](docs/web-problems.png)

![教师端：题目编辑](docs/teacher-problem-edit.png)

![教师端：试卷管理](docs/web-papers.png)

![教师端：学生账号](docs/web-accounts.png)

![教师端：账号详情](docs/web-account-detail.png)

![学生端：账号管理](docs/web-account-student.png)

### 考试规则

- 考试创建后是“未开始”，学生只能看到“等待老师开始”；
- 老师点击“开始作答”后，服务器记录开始时间，并按设定时长生成结束时间；
- 到结束时间，服务器端检查会拒绝任何新提交，学生端倒计时归零并禁用表单；
- 考试结束（手动结束或到点）时生成一次**最终排名快照**，之后不再变化；快照包含所有进入过考试的学生，未提交者 0 分；教师在考试列表点“查看最终成绩”查看；
- 一场考试可以选择多套试卷，学生在进入考试后选择其中一套；
- “实时排名”是每场考试的独立开关，关闭后学生端不显示成绩榜；开启时所有进入考试的学生都会出现在榜上。

### 排名规则

1. **AC 数从高到低**；
2. AC 数相同，**归一化总分从高到低**；
3. 仍然相同，**先提交的学生排在前面**（按该场考试最早的提交时间）。

多套试卷满分不同时，按所有试卷的**平均满分**做归一化：

```text
归一化总分 = 原始总分 ÷ 学生所选试卷满分 × 平均试卷满分
```

榜单显示归一化总分；发生归一化时，下方会显示“原始分 / 试卷满分”。

## 数据保存在哪里

默认在当前目录的 `webdata/`：

```text
webdata/
├─ state.json     # 提交记录与评测结果（重启后保留）
├─ registry.json  # 学生账号、试卷、考试
├─ uploads/       # 学生上传的源码/答案
└─ tmp/           # 判题临时目录（启动时自动清空）
```

- 为了教师查看/重置账号，密码哈希用于登录校验，同时在本地 `registry.json` 保存一份可查看的密码明文；这只适合课堂本地可信环境，公网部署应改为“只能重置、不展示明文”；
- 教师新建的题目写入 `contest.json` 的 `problems_dir`（样例里是 `samples/problems`），和命令行版共用题库；
- 想从零开始演示：换一个 `--data-dir`，例如 `--data-dir webdata_demo`；
- 想让同一局域网的学生访问：`--host 0.0.0.0`，并放行防火墙端口；**不要直接暴露到公网**。

## 教师端接口

除了网页表单，也可以直接调用接口（适合脚本建题或批量导入）。

### 题目

```bash
curl -X POST http://127.0.0.1:8765/api/problems \
  -H "Content-Type: application/json" \
  -d '{
    "id": "p2001",
    "title": "区间求和",
    "type": "program",
    "score": 100,
    "time_limit_ms": 1000,
    "memory_limit_mb": 256,
    "comparator": "token",
    "description": "<p>题目描述（可包含图片）</p>",
    "input_description": "第一行两个整数 n, m",
    "output_description": "输出一个整数",
    "samples": [
      {"input": "1 2", "output": "3"}
    ]
  }'
```

- `type`：`program`（编程题）或 `fill_result`（结果填空题）；
- 结果填空题用 `answers` 传标准答案（数组或换行分隔字符串）；
- 编程题创建后是草稿，添加测试点后才可加入试卷；
- 旧接口的 `statement` 仍然兼容，会作为题目描述保存。

修改已有题目（可随时改题面、样例、分值等）：

```bash
curl -X PUT http://127.0.0.1:8765/api/problems/p2001 \
  -H "Content-Type: application/json" \
  -d '{
    "title": "区间求和（更新）",
    "score": 100,
    "time_limit_ms": 1000,
    "memory_limit_mb": 256,
    "description": "<p>新的题目描述</p>",
    "input_description": "新的输入说明",
    "output_description": "新的输出说明",
    "samples": [{"input": "2 3", "output": "5"}]
  }'
```

```bash
# 添加测试点（multipart）
curl -X POST http://127.0.0.1:8765/api/problems/p2001/tests \
  -F "name=大数测试" -F "score=50" \
  -F "input_text=1000000000 1000000000" \
  -F "answer_text=2000000000"

# 删除题目（被试卷使用时会返回 409）
curl -X DELETE http://127.0.0.1:8765/api/problems/p2001

# 强制删除：同时从相关试卷移除；变空的试卷会一并删除
curl -X DELETE "http://127.0.0.1:8765/api/problems/p2001?force=1"
```

### 试卷

```bash
curl -X POST http://127.0.0.1:8765/api/papers \
  -H "Content-Type: application/json" \
  -d '{"title":"A卷","problem_ids":["p1001","p1002"]}'

curl -X DELETE http://127.0.0.1:8765/api/papers/<paper_id>
curl -X DELETE "http://127.0.0.1:8765/api/papers/<paper_id>?force=1"
```

试卷被考试使用时，网页会二次确认；确认后强制删除会同时把试卷从相关考试中移除。题目被试卷使用时同理，会从试卷移除，变空的试卷自动删除。

### 考试

```bash
# 创建考试：选择多套试卷、120 分钟、开启实时排名
curl -X POST http://127.0.0.1:8765/api/exams \
  -H "Content-Type: application/json" \
  -d '{
    "title":"2026 春季阶段测验",
    "paper_ids":["<paper_a>","<paper_b>"],
    "duration_minutes":120,
    "scoreboard_enabled":true
  }'

# 开始作答 / 提前结束
curl -X POST http://127.0.0.1:8765/api/exams/<exam_id>/start
curl -X POST http://127.0.0.1:8765/api/exams/<exam_id>/end

# 开关实时排名
curl -X POST http://127.0.0.1:8765/api/exams/<exam_id>/scoreboard \
  -H "Content-Type: application/json" -d '{"enabled":false}'

# 删除考试
curl -X DELETE http://127.0.0.1:8765/api/exams/<exam_id>
```

### 查询

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/problems` | 题目列表 |
| `GET` | `/api/papers` | 试卷列表 |
| `GET` | `/api/exams` | 考试列表与状态 |
| `GET` | `/api/scoreboard?exam_id=<id>` | 某场考试的成绩榜（需学生登录且该场开启排名） |
| `GET` | `/api/exams/<id>/final` | 考试结束后的最终排名快照 |
| `GET` | `/api/submission/<id>` | 自己的单份提交结果 |

## 命令行评测

```powershell
python -m bbjudge judge --contest samples/contest.json --out reports
```

在 `reports/` 下生成 HTML/JSON/CSV 成绩报告。常用参数：

```powershell
python -m bbjudge list --contest samples/contest.json
python -m bbjudge judge --contest samples/contest.json --student 张三 --problem p1001
python -m bbjudge judge --contest samples/contest.json --quiet
python -m bbjudge judge --contest samples/contest.json --keep-temp
```

运行测试（54 个用例，会真实编译运行 C++，覆盖账号、试卷、考试、提交、学生端结果脱敏、排名规则、归一化、全员成绩榜、最终快照和权限）：

```powershell
python -m unittest discover -s tests -v
```

## 目录结构

```text
bluebridge-judge/
├─ bbjudge/
│  ├─ models.py             # 题目 / 比赛 / 提交模型
│  ├─ registry.py           # 学生账号、试卷、考试存储
│  ├─ compare.py            # 输出比对
│  ├─ runner.py             # 时限、内存、输出上限、进程树清理
│  ├─ judge.py              # 编译、逐测试点判题、评分
│  ├─ report.py             # 命令行报告
│  ├─ webapp.py             # Flask：账号、考试、试卷、提交、成绩榜
│  ├─ web/                  # 模板、样式、轮询脚本
│  └─ __main__.py           # judge / list / doctor / web
├─ samples/                 # 样例比赛、题目、提交（仅测试/演示）
├─ contest.json             # 实际运行比赛清单
├─ problems/                # 实际运行题库（教师端新建题目写到这里）
├─ docs/功能需求.md          # 功能清单与优先级
├─ reports/                 # 命令行报告输出
├─ webdata/                 # Web 端数据（默认生成）
├─ start.cmd                # Windows 一键启动脚本（仅本机）
├─ start-lan.cmd            # Windows 局域网启动脚本
├─ requirements.txt         # Flask
└─ tests/
```

## 题目格式

每道题一个目录，至少包含 `problem.json`，测试数据放在 `tests/` 下。

### 编程题

```json
{
  "id": "p1001",
  "title": "A + B",
  "type": "program",
  "time_limit_ms": 1000,
  "memory_limit_mb": 256,
  "score": 100,
  "comparator": "token",
  "tests": [
    { "name": "样例 1", "input": "tests/01.in", "answer": "tests/01.out", "score": 40 },
    { "name": "大数测试", "input": "tests/02.in", "answer": "tests/02.out", "score": 60 }
  ]
}
```

- `comparator`：`token`（默认）、`line`、`exact`、`float`、`checker`；
- `float` 可写成 `{"mode": "float", "tolerance": 1e-6}`；
- `checker` 指向 Python 脚本，需定义 `check(input_text, expected_text, actual_text)`；
- 测试点不写 `score` 时按题目总分平均分配；
- 题面默认保存在 `problem.json` 的 `description`、`input_description`、`output_description`、`samples` 字段中；旧版 `statement.md` 仍可读取；

### 结果填空题

```json
{
  "id": "p1002",
  "title": "1 到 100 求和",
  "type": "fill_result",
  "score": 10,
  "answers": ["5050"]
}
```

学生提交文本，判题时去掉首尾空白后与 `answers` 比较；`ignore_case: true` 可忽略大小写。

## 比赛清单格式（命令行）

```json
{
  "name": "2026 春季 C++ 阶段测验",
  "problems_dir": "problems",
  "submissions": [
    { "student": "张三", "problem": "p1001", "file": "submissions/zhangsan_p1001.cpp" }
  ]
}
```

- `problems_dir` 下的每个子目录只要含 `problem.json` 就会被加载；
- Web 端账号、试卷、考试独立存放在 `webdata/registry.json`，不写回 `contest.json`；
- 同一学生同一题可以出现多条提交，成绩矩阵取最高分，明细保留全部记录。

## 判定状态

| 状态 | 含义 |
| --- | --- |
| `AC` | 通过 |
| `WA` | 答案错误 |
| `TLE` | 超时 |
| `MLE` | 内存超限 |
| `RE` | 运行错误 |
| `CE` | 编译错误 |
| `OLE` | 输出超限 |
| `SE` | 系统错误 |

得分按测试点累加，因此会出现“WA 但仍然得 40 分”这样的部分分结果。

## 安全边界（重要）

Windows 本地模式通过**作业对象（Job Object）**提供：

- 进程提交内存上限；
- 活动进程数限制；
- 关闭作业句柄时杀掉整个进程树；
- 关闭崩溃对话框，避免系统弹窗把评测卡死。

它**不是**真正的安全沙箱：学生程序仍以当前用户身份运行，理论上可以读写文件、访问网络。它适合课堂、校内、可信学生；公网 OJ 需要 Linux + cgroup/namespace/seccomp，或 Docker（`--network none --memory 256m --pids-limit 1 --read-only`）。

Web 端学生有账号，密码用于登录校验；但**教师端没有登录鉴权**，且教学本地版会在账号详情展示密码明文，任何能访问 `/teacher` 的人都能看到。默认只监听本机，不要直接暴露到公网。

## 已知限制

- 单机串行判题，没有多评测机；
- Web 端没有班级、场次归档、教师账号；教师端接口无鉴权；
- 学生程序仍在当前用户权限下运行，Windows 沙箱强度有限；
- Java 运行环境需要正确安装（本机 `java` 损坏时 `doctor` 会暴露）；
- 只支持填空题和编程题，不支持代码补全、子任务、交互题、代码相似度检测。

## 下一步可以做什么

1. 教师账号与权限、班级管理；
2. Linux 沙箱，变成可对外服务的在线评测；
3. 代码相似度检测和提交审计；
4. 考试归档、成绩导出 PDF。
