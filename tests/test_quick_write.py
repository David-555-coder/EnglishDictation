import sqlite3
import json
from pathlib import Path

import pytest

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.database.repository import Repository
from src.services.quick_write import copy_text, load_quick_write, save_quick_write
from src.services.quick_write_defaults import DEFAULT_STEPS
from src.ui.quick_write_dialogs import QuickWriteEditor, QuickWritePanel
from test_library_ui import ui


def test_default_prompts_and_optional_single_filename(tmp_path):
    repository = Repository(tmp_path / "learning.db")
    config = load_quick_write(repository)
    assert len(config["steps"]) == 2
    assert config["filename"] == ""
    second = config["steps"][1]["prompt"]
    assert second.count("{{文件名}}") == 1
    assert "kcyj.docx" not in second
    assert "happy≠happily" in config["steps"][0]["prompt"]
    assert "所在句" in config["steps"][0]["prompt"]
    assert "真实可下载" in second
    assert "【本次输出文件名】\n故事甲.docx\n" in copy_text(second, "故事甲.docx")
    assert copy_text(second, "故事甲.docx").count("故事甲.docx") == 1
    assert "未指定" in copy_text(second, "")
    assert DEFAULT_STEPS == config["steps"]


def test_new_two_step_prompts_preserve_supplied_document_except_filename_adaptation():
    source = json.loads((Path(__file__).parent / 'fixtures/quick_write_two_step_source.json').read_text(encoding='utf-8'))
    paragraphs = source['paragraphs']
    assert DEFAULT_STEPS[0]['prompt'] == '\n'.join(paragraphs[2:112])
    second = paragraphs[112:]
    second[156-112] = '文件名：统一采用本轮顶部【本次输出文件名】。'
    second[157-112] = '未指定时按英文故事标题命名；已指定时不得改名。扩展名为 .docx，只保留一次。'
    second[188-112] = '1. 文件名是否符合本轮顶部【本次输出文件名】的要求，标题和正文是否完整。'
    assert DEFAULT_STEPS[1]['prompt'] == '【本次输出文件名】\n{{文件名}}\n\n' + '\n'.join(second)
    assert 'djci.docx' not in DEFAULT_STEPS[1]['prompt']
    assert '无论目标词数量多少，只生成一个英文标题、一篇完整故事' in DEFAULT_STEPS[0]['prompt']
    assert '同形异义' in DEFAULT_STEPS[0]['prompt']
    assert 'take 和 take part in' in DEFAULT_STEPS[0]['prompt']
    assert '重新读取文件并核查' in DEFAULT_STEPS[1]['prompt']


@pytest.mark.parametrize('filename,expected', [('故事甲', '故事甲.docx'), ('故事甲.docx', '故事甲.docx'),
                                             ('故事甲.DOCX', '故事甲.docx'), ('', '未指定；请根据文章标题命名')])
def test_new_prompts_copy_filename_only_into_second_round(tmp_path, ui, filename, expected):
    repository = Repository(tmp_path / 'learning.db')
    panel = QuickWritePanel(repository, ui(repository))
    panel.filename.setText(filename)
    panel.copy_step(0)
    assert QApplication.clipboard().text() == DEFAULT_STEPS[0]['prompt']
    panel.copy_step(1)
    copied = QApplication.clipboard().text()
    assert expected in copied
    assert '{{文件名}}' not in copied and 'djci.docx' not in copied
    if filename:
        assert copied.count(expected) == 1
    panel.close(); panel.deleteLater()


def test_existing_custom_steps_and_filename_are_not_silently_replaced(tmp_path):
    repository = Repository(tmp_path / 'learning.db')
    custom = {'filename': '自己的命名', 'steps': [{'name': '我的第一步', 'prompt': '写故事'},
              {'name': '我的第二步', 'prompt': '生成 {{文件名}}'}, {'name': '我的第三步', 'prompt': '再检查'}]}
    save_quick_write(repository, custom)
    assert load_quick_write(repository) == custom


def test_copy_panel_saves_filename_and_copies_steps(tmp_path, ui):
    repository = Repository(tmp_path / "learning.db")
    window = ui(repository)
    assert "快速成文" in [action.text() for action in window.main_toolbar.actions()]
    panel = QuickWritePanel(repository, window)
    panel.filename.setText("我的故事.docx")
    panel.copy_step(1, panel.copy_buttons[1])
    assert panel.filename.text() == "我的故事"
    assert load_quick_write(repository)["filename"] == "我的故事"
    assert QApplication.clipboard().text().count("我的故事.docx") == 1
    assert panel.copy_buttons[1].text() == "已复制"
    assert not panel.manage_requested
    panel.filename.setText("下次使用")
    panel.close()
    assert load_quick_write(repository)["filename"] == "下次使用"
    panel.deleteLater()


def test_steps_can_be_renamed_added_reordered_and_cancelled(tmp_path, ui, monkeypatch):
    repository = Repository(tmp_path / "learning.db")
    window = ui(repository)
    editor = QuickWriteEditor(repository, window)
    editor.step_list.setCurrentRow(1)
    editor.add_step()
    editor.name_edit.setText("第三轮自查")
    editor.prompt_edit.setPlainText("再次核对，并使用 {{文件名}}。")
    editor.move_step(-1)
    editor.save()
    assert editor.result() == QDialog.Accepted
    saved = load_quick_write(repository)["steps"]
    assert [step["name"] for step in saved] == ["生成故事并核验", "第三轮自查", "中文标注并生成 Word"]
    assert saved[1]["prompt"] == "再次核对，并使用 {{文件名}}。"
    editor.deleteLater()

    cancelled = QuickWriteEditor(repository, window)
    cancelled.name_edit.setText("临时名称")
    cancelled.reject()
    assert load_quick_write(repository)["steps"] == saved
    cancelled.deleteLater()

    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    deleting = QuickWriteEditor(repository, window)
    deleting.step_list.setCurrentRow(1)
    deleting.remove_step()
    deleting.save()
    assert len(load_quick_write(repository)["steps"]) == 2
    deleting.deleteLater()


def test_copy_stops_when_filename_cannot_be_saved(tmp_path, ui, monkeypatch):
    repository = Repository(tmp_path / "learning.db")
    window = ui(repository)
    panel = QuickWritePanel(repository, window)
    panel.filename.setText("新名字")
    QApplication.clipboard().setText("原剪贴板内容")

    def fail(*args):
        raise sqlite3.OperationalError("database locked")

    original = repository.set_setting
    monkeypatch.setattr(repository, "set_setting", fail)
    panel.copy_step(1)
    assert QApplication.clipboard().text() == "原剪贴板内容"
    monkeypatch.setattr(repository, "set_setting", original)
    panel.close()
    panel.deleteLater()
