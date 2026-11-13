"""输出比对：支持忽略行末空白、按 token、按行、精确、浮点误差与自定义 checker。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable


class CheckerError(Exception):
    """自定义比对脚本自身出错（不是学生答案错误）。"""


def decode_bytes(data: bytes) -> str:
    """尽量把学生输出解码为文本：UTF-8 优先，其次 GB18030。"""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _clip(text: str, limit: int = 60) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


def _normalized_lines(text: str) -> list[str]:
    lines = normalize_newlines(text).split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _tokens(text: str) -> list[str]:
    return normalize_newlines(text).split()


def _token_message(expected: list[str], actual: list[str]) -> str:
    for i, (want, got) in enumerate(zip(expected, actual), start=1):
        if want != got:
            return f"第 {i} 个数据不一致：期望 {_clip(want)!r}，实际 {_clip(got)!r}"
    if len(actual) < len(expected):
        return (
            f"输出数据过少：期望 {len(expected)} 个，实际 {len(actual)} 个，"
            f"缺少 {_clip(expected[len(actual)])!r} 等"
        )
    return (
        f"输出数据过多：期望 {len(expected)} 个，实际 {len(actual)} 个，"
        f"多出 {_clip(actual[len(expected)])!r} 等"
    )


def _line_message(expected: list[str], actual: list[str]) -> str:
    for i, (want, got) in enumerate(zip(expected, actual), start=1):
        if want != got:
            return f"第 {i} 行不一致：期望 {_clip(want)!r}，实际 {_clip(got)!r}"
    if len(actual) < len(expected):
        return f"输出行数过少：期望 {len(expected)} 行，实际 {len(actual)} 行"
    return f"输出行数过多：期望 {len(expected)} 行，实际 {len(actual)} 行"


def _compare_float(expected: str, actual: str, tolerance: float) -> tuple[bool, str]:
    want_tokens = _tokens(expected)
    got_tokens = _tokens(actual)
    if len(want_tokens) != len(got_tokens):
        return False, _token_message(want_tokens, got_tokens)
    for i, (want, got) in enumerate(zip(want_tokens, got_tokens), start=1):
        try:
            w = float(want)
            g = float(got)
        except ValueError:
            return False, f"第 {i} 个数据不是数值：期望 {_clip(want)!r}，实际 {_clip(got)!r}"
        diff = abs(w - g)
        if diff <= tolerance or diff <= tolerance * max(abs(w), abs(g)):
            continue
        return (
            False,
            f"第 {i} 个浮点数误差过大：期望 {want}，实际 {got}，"
            f"绝对误差 {diff:.3g}（容差 {tolerance:g}）",
        )
    return True, ""


def _load_checker(path: Path) -> Callable[..., Any]:
    spec = importlib.util.spec_from_file_location(f"bbjudge_checker_{id(path)}", path)
    if spec is None or spec.loader is None:
        raise CheckerError(f"无法加载自定义比对脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 脚本错误统一上报
        raise CheckerError(f"加载 {path.name} 失败：{exc}") from exc
    func = getattr(module, "check", None)
    if not callable(func):
        raise CheckerError(f"{path.name} 中必须定义 check(input_text, expected_text, actual_text)")
    return func


def compare_output(
    mode: str,
    expected: str,
    actual: str,
    tolerance: float = 1e-6,
    checker: Path | None = None,
    input_text: str = "",
) -> tuple[bool, str]:
    """比对一次输出，返回 (是否通过, 说明)。"""
    if mode == "checker":
        if checker is None:
            raise CheckerError("comparator 为 checker，但没有配置 checker 脚本")
        func = _load_checker(checker)
        try:
            result = func(input_text, expected, actual)
        except Exception as exc:  # noqa: BLE001 - 学生输入可能触发脚本异常
            raise CheckerError(f"自定义比对脚本运行出错：{exc}") from exc
        if isinstance(result, tuple):
            ok = bool(result[0]) if result else False
            message = str(result[1]) if len(result) > 1 else ""
            return ok, message
        if isinstance(result, bool):
            return result, "" if result else "自定义比对未通过"
        raise CheckerError("自定义 check 的返回值必须是 bool 或 (bool, message)")

    if mode == "exact":
        want = normalize_newlines(expected)
        got = normalize_newlines(actual)
        if want == got:
            return True, ""
        return False, "输出与标准答案不完全一致（精确比对）"

    if mode == "float":
        return _compare_float(expected, actual, tolerance)

    want_tokens = _tokens(expected)
    got_tokens = _tokens(actual)
    if mode == "token":
        if want_tokens == got_tokens:
            return True, ""
        return False, _token_message(want_tokens, got_tokens)

    # 默认按行比对：忽略每行行末空白和文件末尾空行
    want_lines = _normalized_lines(expected)
    got_lines = _normalized_lines(actual)
    if want_lines == got_lines:
        return True, ""
    return False, _line_message(want_lines, got_lines)
