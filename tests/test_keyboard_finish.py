from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from test_library_ui import ui
from test_enhanced_services import document
from src.database.repository import Repository


def practice(tmp_path, ui):
    window = ui(Repository(tmp_path / "db"))
    window._import_paths([str(document(tmp_path))])
    window.start_full_practice(); window.show(); window.activateWindow()
    QApplication.processEvents()
    return window


def test_shift_backspace_clears_only_current_and_preserves_lock(tmp_path, ui):
    window = practice(tmp_path, ui)
    first, last = window.answer_edits.values()
    first.setText("keep me"); last.setText("rewrite this phrase"); last.setFocus()
    QTest.keyClick(last, Qt.Key_Backspace, Qt.ShiftModifier)
    assert first.text() == "keep me" and last.text() == ""
    assert QApplication.focusWidget() is last
    assert window.session.answers[last.item_id] == ""
    last.setText("abc"); QTest.keyClick(last, Qt.Key_Backspace)
    assert last.text() == "ab"
    window.submit()
    QTest.keyClick(last, Qt.Key_Backspace, Qt.ShiftModifier)
    assert last.text() == "ab" and last.isReadOnly()


def test_last_enter_finds_blank_without_grading(tmp_path, ui):
    window = practice(tmp_path, ui)
    first, last = window.answer_edits.values()
    last.setText("momentum"); last.setFocus()
    QTest.keyClick(last, Qt.Key_Return)
    assert QApplication.focusWidget() is first
    assert not window.session.submitted and not window.repository.list_history()


def test_last_enter_confirms_once_default_check_then_submits(tmp_path, ui, monkeypatch):
    window = practice(tmp_path, ui)
    from src.ui.main_window import MainWindow
    monkeypatch.setattr(window, "_confirm_finished_submission", MainWindow._confirm_finished_submission.__get__(window))
    for edit in window.answer_edits.values(): edit.setText("wrong")
    last = list(window.answer_edits.values())[-1]
    last.setFocus(); last.deselect()
    timer = QTimer(); timer.setSingleShot(True)
    seen = []

    def choose():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert dialog.defaultButton().text() == "继续检查"
        assert dialog.escapeButton().text() == "继续检查"
        seen.append(dialog.windowTitle())
        button = next(b for b in dialog.buttons() if b.text() == ("继续检查" if len(seen) == 1 else "提交批改"))
        button.click()

    timer.timeout.connect(choose)
    timer.start(50); QTest.keyClick(last, Qt.Key_Return)
    assert not window.session.submitted and not last.hasSelectedText()
    assert not window.repository.list_history()
    timer.start(50); QTest.keyClick(last, Qt.Key_Return)
    assert window.session.submitted and len(window.repository.list_history()) == 1
    QTest.keyClick(last, Qt.Key_Return)
    assert len(seen) == 2 and len(window.repository.list_history()) == 1


def test_collapsed_sidebar_keeps_draft_and_restores_setting(tmp_path, ui, monkeypatch):
    window = practice(tmp_path, ui)
    edit = next(iter(window.answer_edits.values())); edit.setText("unfinished")
    before = window.scroll.width()
    window.actions['显示文章列表'].trigger(); QApplication.processEvents()
    assert window.library_collapsed and window.article_panel.isHidden()
    assert window.scroll.width() > before
    assert next(iter(window.answer_edits.values())) is edit
    window.close()
    reopened = ui(Repository(tmp_path / "db")); reopened._restore_article_library()
    assert reopened.library_collapsed and reopened.article_panel.isHidden()
    assert reopened.session is None
    from src.ui.unfinished_dialog import UnfinishedPracticeDialog
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(UnfinishedPracticeDialog, 'exec', lambda dialog: QDialog.Accepted)
    reopened.resume_draft()
    assert next(iter(reopened.answer_edits.values())).text() == "unfinished"
    reopened.actions['显示文章列表'].trigger()
    assert not reopened.library_collapsed and not reopened.article_panel.isHidden()
