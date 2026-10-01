from __future__ import annotations

import re

# 英文词、短语、连字符与撇号；短语内部允许多个空格。
ENGLISH_TARGET_RE = re.compile(r"[A-Za-z]+(?:[’'][A-Za-z]+|[-‑][A-Za-z]+)*(?:[ \t]+[A-Za-z]+(?:[’'][A-Za-z]+|[-‑][A-Za-z]+)*)*")
CHINESE_NOTE_RE = re.compile(r"^[ \t]*[（(\[【]([^）)\]】\r\n]*[\u3400-\u9fff][^）)\]】\r\n]*)[）)\]】]")


def normalize_answer(value: str) -> str:
    return " ".join((value or "").strip().casefold().replace("’", "'").split())


def natural_sort_key(value: str) -> list[tuple[int, object]]:
    """Case-insensitive natural order that is also safe for names starting with digits."""
    return [(1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r"(\d+)", value)]


def extract_chinese_note(text: str, position: int) -> str:
    match = CHINESE_NOTE_RE.match(text[position:])
    if match:
        return match.group(1).strip()
    match = re.match(r"^[ \t]*(?:[:：—][ \t]*)?([\u3400-\u9fff][\u3400-\u9fff、，； \t]*)", text[position:])
    return match.group(1).strip(" ，；") if match else ""


def sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    boundaries = [0]
    depth = 0
    for index, char in enumerate(text):
        if char in "（([【": depth += 1
        elif char in "）)]】": depth = max(0, depth - 1)
        if char == "\n":
            boundaries.append(index + 1)
        elif not depth and char in ".!?。！？":
            if char == ".":
                token = text[:index].split()[-1:] or [""]
                if token[0].lower().rstrip(".") in {"mr", "mrs", "ms", "dr", "prof", "e.g", "i.e", "etc"}:
                    continue
                if index + 1 < len(text) and (text[index + 1].isalnum() or text[index + 1] == "."):
                    continue
            stop = index + 1
            while stop < len(text) and text[stop] in '\"”’!?。！？': stop += 1
            boundaries.append(stop)
    left = max((pos for pos in boundaries if pos <= start), default=0)
    right = min((pos for pos in boundaries if pos >= end), default=len(text))
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1] in "\r\n":
        right -= 1
    return left, right


def underline_for_answer(answer: str) -> str:
    visible = max(5, min(30, round(len(answer) * 1.25)))
    return "_" * visible
