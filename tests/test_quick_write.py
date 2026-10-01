import sqlite3

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.database.repository import Repository
from src.services.quick_write import copy_text, load_quick_write
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
    assert "happily" not in config["steps"][0]["prompt"]
    assert "所在句" in config["steps"][0]["prompt"]
    assert "真实可下载" in second
    assert "故事标题" not in copy_text(second, "故事甲.docx")
    assert copy_text(second, "故事甲.docx").count("故事甲.docx") == 1
    assert "未指定" in copy_text(second, "")
    assert DEFAULT_STEPS == config["steps"]


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
