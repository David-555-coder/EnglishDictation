from copy import deepcopy
import hashlib

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from src.database.repository import Repository
from src.models.practice import PracticeMode
from src.services.grading import grade_session
from src.services.library import ArticleLibrary, backup_library
from src.services.practice_service import new_full_session
from src.ui.unfinished_dialog import UnfinishedPracticeDialog
from test_library_ui import ui, sample_word


def session_for(repository, tmp_path, index):
    article = ArticleLibrary(repository).import_file(sample_word(tmp_path))
    article = deepcopy(article)
    article.source_path = str(tmp_path / f"article{index}.docx")
    article.filename = f"article{index}.docx"
    repository.save_article(article)
    return new_full_session(article)


def test_ten_distinct_articles_preserve_same_article_rounds_and_review(tmp_path):
    repo = Repository(tmp_path / "data" / "db")
    first = session_for(repo, tmp_path, 0)
    repo.save_draft(first)
    second = new_full_session(first.article)
    second.answers[second.target_ids[0]] = "latest"
    repo.save_draft(second)
    review = new_full_session(first.article)
    review.mode = PracticeMode.DAILY_REVIEW
    repo.save_draft(review)
    for index in range(1, 10):
        repo.save_draft(session_for(repo, tmp_path, index))
    records = repo.unfinished_articles()
    assert len(records) == 10
    assert next(s for s, _ in records if s.article.source_path == first.article.source_path).session_key == second.session_key
    assert {first.session_key, second.session_key, review.session_key} <= repo.draft_keys()
    repo.save_draft(session_for(repo, tmp_path, 10))
    assert len(repo.unfinished_articles()) == 10
    assert first.session_key not in repo.draft_keys() and second.session_key not in repo.draft_keys()
    assert review.session_key in repo.draft_keys()
    assert first.article.source_path in repo.list_article_paths()
    assert len(Repository(repo.database_path).unfinished_articles()) == 10


def test_newest_activity_keeps_an_older_article_and_submission_removes_its_round(tmp_path):
    repo = Repository(tmp_path / "db")
    sessions = [session_for(repo, tmp_path, index) for index in range(10)]
    for session in sessions:
        repo.save_draft(session)
    sessions[0].answers[sessions[0].target_ids[0]] = "revisited"
    repo.save_draft(sessions[0])
    repo.save_draft(session_for(repo, tmp_path, 10))
    assert sessions[0].session_key in repo.draft_keys()
    assert sessions[1].session_key not in repo.draft_keys()
    grade_session(sessions[0])
    repo.save_submitted_session(sessions[0])
    assert sessions[0].session_key not in repo.draft_keys()
    assert len(repo.list_history()) == 1 and len(repo.list_wrong_words()) == 1


def test_cleanup_preserves_failed_grade_save_and_unreadable_old_draft(tmp_path):
    repo = Repository(tmp_path / "db")
    pending = session_for(repo, tmp_path, 0)
    grade_session(pending)
    repo.save_draft(pending)  # Graded but not yet persisted as a formal result.
    with repo.connection() as connection:
        connection.execute("INSERT INTO practice_drafts VALUES ('bad','legacy','{broken','2099')")
    for index in range(1, 12):
        repo.save_draft(session_for(repo, tmp_path, index))
    reopened = Repository(repo.database_path)
    assert {pending.session_key, "bad"} <= reopened.draft_keys()
    assert len(reopened.unfinished_articles()) == 10


def test_simple_start_and_explicit_resume_cancel_and_accept(tmp_path, ui, monkeypatch):
    window = ui(Repository(tmp_path / "db"))
    window._import_paths([str(sample_word(tmp_path))])
    assert not window.repository.unfinished_articles()  # preview alone is not a draft
    window.primary_action_button.click()
    original = window.session
    item_id = original.target_ids[0]
    window.answer_edits[item_id].setText("draft answer")
    window.show_preview()
    assert window.primary_action_button.text() == "开始默写"
    assert not hasattr(window, "restart_action_button")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    window.primary_action_button.click()
    assert window.session is None
    monkeypatch.setattr(UnfinishedPracticeDialog, "exec", lambda _: QDialog.Rejected)
    window.actions["未完成练习…"].trigger()
    assert window.session is None
    monkeypatch.setattr(UnfinishedPracticeDialog, "exec", lambda _: QDialog.Accepted)
    window.actions["未完成练习…"].trigger()
    assert window.session.session_key == original.session_key
    assert window.answer_edits[item_id].text() == "draft answer"
    window.show_preview()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    window.actions["开始默写"].trigger()
    assert window.session.session_key != original.session_key and window.session.filled == 0
    assert original.session_key in window.repository.draft_keys()


def test_backup_menu_and_cancelled_restore_leave_current_data(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / "data" / "english_dictation.db")
    window = ui(repo)
    assert window.export_button.isEnabled()
    assert not window.actions["导出默写纸"].isEnabled()
    assert window.actions["导出学习备份…"].isEnabled()
    assert "备份全部学习数据" not in window.actions
    backup = backup_library(repo, tmp_path / "backup.zip")
    before = hashlib.sha256(repo.database_path.read_bytes()).hexdigest()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(backup), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    window.restore_data()
    assert window.pending_library_restore is None
    assert hashlib.sha256(repo.database_path.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob(".ed_restore_*"))


def test_export_backup_captures_current_zoom_and_layout(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / "data" / "english_dictation.db")
    window = ui(repo)
    window.set_zoom(150)
    window._set_library_collapsed(True)
    window.main_toolbar.hide()
    backup = tmp_path / "export.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(backup), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: QMessageBox.Ok)
    window.backup_data()
    assert backup.exists()
    assert repo.get_setting("zoom") == 150
    assert repo.get_setting("library_collapsed") and not repo.get_setting("toolbar_visible")


def test_confirmed_restore_closes_before_replacement_and_blocks_stale_autosave(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / "data" / "english_dictation.db")
    window = ui(repo)
    window._import_paths([str(sample_word(tmp_path))])
    window.start_full_practice()
    window.answer_edits[window.session.target_ids[0]].setText("before restore")
    window._save_active_draft()
    backup = backup_library(repo, tmp_path / "backup.zip")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(backup), ""))
    window.restore_data()
    assert window.pending_library_restore is not None
    assert window._closed_for_restore
    assert not window._autosave.isActive() and not window._daily_timer.isActive()
    monkeypatch.setattr(repo, "save_draft", lambda *_: (_ for _ in ()).throw(AssertionError("stale write")))
    assert window._save_active_draft()
    window.close()
    window.pending_library_restore.close()


def test_failed_close_cancels_restore_and_keeps_draft(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / "data" / "english_dictation.db")
    window = ui(repo)
    window._import_paths([str(sample_word(tmp_path))])
    window.start_full_practice()
    backup = backup_library(repo, tmp_path / "backup.zip")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(backup), ""))
    monkeypatch.setattr(window, "_save_active_draft", lambda: False)
    window.restore_data()
    assert window.pending_library_restore is None and not window._closed_for_restore
    assert not list(tmp_path.glob(".ed_restore_*"))


def test_dialog_uses_record_identity_not_duplicate_filename(tmp_path, ui):
    repo = Repository(tmp_path / "db")
    records = [(session_for(repo, tmp_path, index), "2026-09-30T22:00:00") for index in range(2)]
    for session, _ in records:
        session.article.filename = "same.docx"
    ui(repo)
    dialog = UnfinishedPracticeDialog(records)
    dialog.list.setCurrentRow(1)
    assert dialog.selected_session is records[1][0]
    assert "已填写 0 / 1" in dialog.list.item(1).text()
    dialog.close()
