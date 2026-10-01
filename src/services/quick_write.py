"""Local, editable prompts for the optional two-step writing workflow."""

from __future__ import annotations

from copy import deepcopy

from src.services.quick_write_defaults import DEFAULT_STEPS


SETTING_KEY = "quick_write"
FILENAME_TOKEN = "{{文件名}}"


def load_quick_write(repository):
    saved = repository.get_setting(SETTING_KEY, None)
    if not isinstance(saved, dict):
        return {"steps": deepcopy(DEFAULT_STEPS), "filename": ""}
    steps = saved.get("steps")
    if not isinstance(steps, list) or not steps or any(
        not isinstance(step, dict)
        or not isinstance(step.get("name"), str)
        or not isinstance(step.get("prompt"), str)
        for step in steps
    ):
        steps = DEFAULT_STEPS
    filename = saved.get("filename", "")
    return {"steps": deepcopy(steps), "filename": filename if isinstance(filename, str) else ""}


def save_quick_write(repository, config):
    repository.set_setting(SETTING_KEY, config)


def normalized_filename(value):
    filename = value.strip()
    if filename.lower().endswith(".docx"):
        filename = filename[:-5].rstrip()
    if filename and (filename in {".", ".."} or any(char in filename for char in '<>:"/\\|?*')):
        raise ValueError("文件名不能包含路径或 Windows 不允许的符号。")
    return filename


def copy_text(prompt, filename):
    name = normalized_filename(filename)
    replacement = (
        f"{name}.docx" if name else "未指定；请根据文章标题命名，并使用 .docx 扩展名。"
    )
    return prompt.replace(FILENAME_TOKEN, replacement)
