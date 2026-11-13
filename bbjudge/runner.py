"""进程运行器：时限、内存上限、输出上限、进程树清理。

Windows 上使用作业对象（Job Object）：
- 限制提交内存，超过时分配失败或进程被终止；
- 限制活动进程数为 1，防止学生程序再开子进程；
- 关闭作业句柄时杀掉整个进程树；
- 关闭崩溃对话框，避免评测被系统弹窗卡住。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

CREATE_NO_WINDOW = 0x08000000

_ULONG_PTR = None
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes as _w

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    _SIZE_T = ctypes.c_size_t

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", _w.DWORD),
            ("MinimumWorkingSetSize", _SIZE_T),
            ("MaximumWorkingSetSize", _SIZE_T),
            ("ActiveProcessLimit", _w.DWORD),
            ("Affinity", _SIZE_T),
            ("PriorityClass", _w.DWORD),
            ("SchedulingClass", _w.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", _SIZE_T),
            ("JobMemoryLimit", _SIZE_T),
            ("PeakProcessMemoryUsed", _SIZE_T),
            ("PeakJobMemoryUsed", _SIZE_T),
        ]

    class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", _w.DWORD),
            ("PageFaultCount", _w.DWORD),
            ("PeakWorkingSetSize", _SIZE_T),
            ("WorkingSetSize", _SIZE_T),
            ("QuotaPeakPagedPoolUsage", _SIZE_T),
            ("QuotaPagedPoolUsage", _SIZE_T),
            ("QuotaPeakNonPagedPoolUsage", _SIZE_T),
            ("QuotaNonPagedPoolUsage", _SIZE_T),
            ("PagefileUsage", _SIZE_T),
            ("PeakPagefileUsage", _SIZE_T),
            ("PrivateUsage", _SIZE_T),
        ]

    _k32.CreateJobObjectW.restype = _w.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, _w.LPCWSTR]
    _k32.SetInformationJobObject.restype = _w.BOOL
    _k32.SetInformationJobObject.argtypes = [_w.HANDLE, ctypes.c_int, ctypes.c_void_p, _w.DWORD]
    _k32.AssignProcessToJobObject.restype = _w.BOOL
    _k32.AssignProcessToJobObject.argtypes = [_w.HANDLE, _w.HANDLE]
    _k32.TerminateJobObject.restype = _w.BOOL
    _k32.TerminateJobObject.argtypes = [_w.HANDLE, _w.UINT]
    _k32.CloseHandle.restype = _w.BOOL
    _k32.CloseHandle.argtypes = [_w.HANDLE]
    _k32.OpenProcess.restype = _w.HANDLE
    _k32.OpenProcess.argtypes = [_w.DWORD, _w.BOOL, _w.DWORD]
    _k32.GetProcessTimes.restype = _w.BOOL
    _k32.GetProcessTimes.argtypes = [_w.HANDLE] + [ctypes.POINTER(_w.FILETIME)] * 4
    _k32.SetErrorMode.restype = _w.UINT
    _k32.SetErrorMode.argtypes = [_w.UINT]
    _psapi.GetProcessMemoryInfo.restype = _w.BOOL
    _psapi.GetProcessMemoryInfo.argtypes = [
        _w.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
        _w.DWORD,
    ]

    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    _PROCESS_RIGHTS = 0x0400 | 0x1000 | 0x0100 | 0x0001 | 0x00100000

    _error_mode_ready = False

    def _prepare_windows() -> None:
        """关闭 Windows 错误对话框；子进程会继承该错误模式。"""
        global _error_mode_ready
        if _error_mode_ready:
            return
        _k32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        _error_mode_ready = True


@dataclass
class RunResult:
    exit_code: int | None = None
    timed_out: bool = False
    wall_ms: float = 0.0
    cpu_ms: float = 0.0
    peak_memory_kb: int = 0
    stdout: bytes = b""
    stderr: bytes = b""
    output_limit_exceeded: bool = False
    launch_error: str = ""
    sandboxed: bool = False


def _open_process(pid: int) -> int:
    return _k32.OpenProcess(_PROCESS_RIGHTS, False, pid)


def _create_job(memory_limit_mb: int, allow_children: bool) -> int:
    hard_mb = max(memory_limit_mb * 2, memory_limit_mb + 128)
    hard_mb = min(hard_mb, 8192)
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        return 0
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = (
        _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    if not allow_children:
        flags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    info.BasicLimitInformation.LimitFlags = flags
    if not allow_children:
        info.BasicLimitInformation.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = hard_mb * 1024 * 1024
    ok = _k32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        _k32.CloseHandle(job)
        return 0
    return job


def _assign_job(job: int, process_handle: int) -> bool:
    if not job or not process_handle:
        return False
    return bool(_k32.AssignProcessToJobObject(job, process_handle))


def _terminate_job(job: int) -> None:
    if job:
        _k32.TerminateJobObject(job, 9)


def _query_stats(process_handle: int) -> tuple[int, float]:
    """返回 (峰值提交内存 KB, CPU 时间 ms)。"""
    if not process_handle:
        return 0, 0.0

    peak_kb = 0
    counters = _PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(counters)
    if _psapi.GetProcessMemoryInfo(process_handle, ctypes.byref(counters), counters.cb):
        peak_kb = int(counters.PeakPagefileUsage // 1024)

    cpu_ms = 0.0
    creation = _w.FILETIME()
    exit_time = _w.FILETIME()
    kernel = _w.FILETIME()
    user = _w.FILETIME()
    if _k32.GetProcessTimes(
        process_handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        def ticks(ft: "_w.FILETIME") -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        cpu_ms = (ticks(kernel) + ticks(user)) / 10_000.0
    return peak_kb, cpu_ms


def probe_sandbox() -> tuple[bool, str]:
    """检查当前环境能否使用受限运行器。"""
    if not IS_WINDOWS:
        return False, "非 Windows 平台，使用 POSIX rlimit（功能有限）"
    try:
        _prepare_windows()
        job = _create_job(64, False)
        if not job:
            return False, f"作业对象创建失败（错误码 {ctypes.get_last_error()}）"
        _k32.CloseHandle(job)
        return True, "Windows 作业对象可用（内存上限、进程数限制、进程树清理）"
    except OSError as exc:
        return False, f"沙箱初始化失败：{exc}"


def _read_limited(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read(limit)
    except OSError:
        return b""


def _child_env(io_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["TEMP"] = str(io_dir)
    env["TMP"] = str(io_dir)
    return env


def _posix_preexec(memory_limit_mb: int, time_limit_ms: int, max_output_bytes: int):
    import math
    import resource

    def _limits() -> None:
        os.setsid()
        mem = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        cpu = max(1, math.ceil(time_limit_ms / 1000))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_output_bytes, max_output_bytes))

    return _limits


def _kill_posix_tree(proc: subprocess.Popen) -> None:
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_program(
    command: list[str],
    cwd: Path | str,
    stdin_path: Path | str | None = None,
    time_limit_ms: int = 1000,
    memory_limit_mb: int = 256,
    max_output_bytes: int = 16 * 1024 * 1024,
    env: dict[str, str] | None = None,
    allow_children: bool = False,
) -> RunResult:
    """在受限环境中运行一个命令，收集退出码、时间、内存和输出。"""
    cwd = Path(cwd)
    io_dir = Path(tempfile.mkdtemp(prefix=".io-", dir=str(cwd)))
    out_path = io_dir / "stdout.bin"
    err_path = io_dir / "stderr.bin"
    result = RunResult()

    job = 0
    process_handle = 0
    proc: subprocess.Popen | None = None
    stop = threading.Event()
    over_limit = [False]

    child_env = _child_env(io_dir)
    if env:
        child_env.update(env)

    if IS_WINDOWS:
        _prepare_windows()
        job = _create_job(memory_limit_mb, allow_children)

    creationflags = CREATE_NO_WINDOW if IS_WINDOWS else 0
    preexec = None
    if not IS_WINDOWS:
        preexec = _posix_preexec(memory_limit_mb, time_limit_ms, max_output_bytes)

    fin = None
    fout = None
    ferr = None
    try:
        if stdin_path is not None:
            fin = open(stdin_path, "rb")
        else:
            fin = open(os.devnull, "rb")
        fout = open(out_path, "wb")
        ferr = open(err_path, "wb")

        started = time.perf_counter()
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=fin,
                stdout=fout,
                stderr=ferr,
                env=child_env,
                creationflags=creationflags,
                preexec_fn=preexec,
            )
        except (OSError, ValueError) as exc:
            result.launch_error = str(exc)
            return result

        if IS_WINDOWS:
            process_handle = _open_process(proc.pid)
            result.sandboxed = _assign_job(job, process_handle)

        def watch() -> None:
            while not stop.wait(0.02):
                try:
                    size = out_path.stat().st_size + err_path.stat().st_size
                except OSError:
                    continue
                if size > max_output_bytes:
                    over_limit[0] = True
                    if job:
                        _terminate_job(job)
                    elif proc is not None:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()

        wait_seconds = time_limit_ms / 1000.0 + 0.35
        try:
            proc.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            result.timed_out = True
            if IS_WINDOWS:
                if job:
                    _terminate_job(job)
                else:
                    proc.kill()
            else:
                _kill_posix_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

        result.wall_ms = (time.perf_counter() - started) * 1000.0
        result.exit_code = proc.returncode
        stop.set()
        watcher.join(timeout=1.0)

        if IS_WINDOWS:
            result.peak_memory_kb, result.cpu_ms = _query_stats(process_handle)
        result.output_limit_exceeded = over_limit[0]

    finally:
        stop.set()
        for handle in (fout, ferr, fin):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        result.stdout = _read_limited(out_path, max_output_bytes + 1)
        result.stderr = _read_limited(err_path, max_output_bytes + 1)
        # 峰值内存可能略微超过上限，先把数据读回再放出硬上限
        if len(result.stdout) > max_output_bytes:
            result.output_limit_exceeded = True
        if process_handle and IS_WINDOWS:
            _k32.CloseHandle(process_handle)
        if job and IS_WINDOWS:
            _k32.CloseHandle(job)
        shutil.rmtree(io_dir, ignore_errors=True)

    return result
