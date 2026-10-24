"""Behavioral checks for manual draft recovery and the main-pane reader."""
from copy import deepcopy
from datetime import timedelta
from docx import Document

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

from test_library_ui import ui, sample_word
from test_review_reading import correction, tables
from test_daily_review import seed, daily, submit
from src.database.repository import Repository
from src.services.library import ArticleLibrary
from src.services.serialization import session_to_dict
from src.ui.unfinished_dialog import UnfinishedPracticeDialog


def test_last_browsed_article_opens_as_original_and_draft_stays_manual(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'data' / 'db')
    first = sample_word(tmp_path)
    second = tmp_path / 'second.docx'; second.write_bytes(first.read_bytes())
    window = ui(repo); window._import_paths([str(first), str(second)])
    window.article_list.setCurrentRow(1)
    window.start_full_practice()
    key = window.session.session_key
    next(iter(window.answer_edits.values())).setText('my saved answer')
    window.close()
    before = tables(repo, ('app_settings',))
    reopened = ui(repo); reopened._restore_article_library()
    assert reopened.current_article.source_path == str(second.resolve())
    assert reopened.mode_title.text().startswith('原文模式')
    assert not reopened.answer_edits and reopened.session is None
    assert reopened.progress.isHidden() and reopened.score_text.isHidden()
    assert tables(repo, ('app_settings',)) == before
    monkeypatch.setattr(UnfinishedPracticeDialog, 'exec', lambda _: QDialog.Accepted)
    reopened.resume_draft()
    assert reopened.session.session_key == key
    assert next(iter(reopened.answer_edits.values())).text() == 'my saved answer'


def test_selecting_or_clicking_current_article_shows_original_without_deleting_answers(tmp_path, ui):
    repo = Repository(tmp_path / 'data' / 'db')
    window = ui(repo); window._import_paths([str(sample_word(tmp_path))]); window.start_full_practice()
    key = window.session.session_key
    next(iter(window.answer_edits.values())).setText('keep me')
    window._article_list_clicked(window.article_list.currentItem())
    assert window.session is None and window.mode_title.text().startswith('原文模式')
    assert next(s for s in repo.load_drafts() if s.session_key == key).filled == 1


def test_missing_last_article_falls_back_to_first_preview(tmp_path, ui):
    repo = Repository(tmp_path / 'data' / 'db')
    ArticleLibrary(repo).import_file(sample_word(tmp_path))
    repo.set_setting('last_article_path', 'missing')
    window = ui(repo); window._restore_article_library()
    assert window.current_article.source_path == window.articles[0].source_path
    assert window.session is None


def test_failed_full_article_grade_remains_recoverable_from_manual_list(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'data' / 'db')
    window = ui(repo); window._import_paths([str(sample_word(tmp_path))]); window.start_full_practice()
    original_save = repo.save_submitted_session
    def fail(_): raise OSError('disk unavailable')
    monkeypatch.setattr(repo, 'save_submitted_session', fail)
    window._commit_submission(); key = window.session.session_key
    window.close()
    monkeypatch.setattr(repo, 'save_submitted_session', original_save)
    reopened = ui(repo); reopened._restore_article_library()
    assert reopened.session is None
    assert '尚未保存' in reopened.statusBar().currentMessage()
    dialog = UnfinishedPracticeDialog(repo.unfinished_practices())
    assert '成绩待保存' in dialog.list.item(0).text()
    dialog.close()
    monkeypatch.setattr(UnfinishedPracticeDialog, 'exec', lambda _: QDialog.Accepted)
    reopened.resume_draft()
    assert reopened.session.session_key == key and reopened.primary_action_button.text() == '重试保存成绩'
    reopened.primary_action_button.click()
    assert reopened.session.database_id and len(repo.list_history()) == 1


def test_main_reader_returns_same_result_and_scroll_without_new_task(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); parent = correction(repo, 30)
    window = ui(repo); window.resize(980, 650); window.show(); window._activate_session(parent)
    QApplication.processEvents(); window._cancel_pending_follow()
    window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum() // 2)
    offset = window.scroll.verticalScrollBar().value(); assert offset > 0
    before = tables(repo, ('memory_exposures',)); snapshot = deepcopy(session_to_dict(parent))
    window.read_current_wrong(); QApplication.processEvents()
    assert window._wrong_reading and QApplication.activeModalWidget() is None
    assert not window.answer_edits and window.pending_review_session is None
    assert window.reading_back_button.isVisible() and window.progress.isHidden() and window.score_text.isHidden()
    assert session_to_dict(parent) == snapshot and tables(repo, ('memory_exposures',)) == before
    window.set_zoom(160); window.actions['专注模式'].trigger(); QApplication.processEvents()
    assert window._wrong_reading and window.status_panel.isVisible()
    window.actions['专注模式'].trigger(); window.set_zoom(100); QApplication.processEvents()
    window.reading_back_button.click(); QApplication.processEvents(); QTest.qWait(30)
    assert not window._wrong_reading and window.session is parent
    assert abs(window.scroll.verticalScrollBar().value() - offset) <= 3
    assert session_to_dict(parent) == snapshot
    assert tables(repo, ('memory_exposures',)) == before


def test_starting_next_from_reader_requires_click_and_respects_small_group_choice(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db'); parent = correction(repo, 12)
    window = ui(repo); window.resize(980, 650); window.show(); window._activate_session(parent)
    QApplication.processEvents(); window._cancel_pending_follow()
    window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum() // 2)
    offset = window.scroll.verticalScrollBar().value()
    window.read_current_wrong()
    before = tables(repo)
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a: (4, False))
    window.primary_action_button.click()
    assert window._wrong_reading and window.session is parent and tables(repo) == before
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a: (4, True))
    window.primary_action_button.click()
    assert not window._wrong_reading and not window.session.submitted
    assert window.session.total == 4 and window.session.view_state['consolidation_origin']['results'] == session_to_dict(parent)['results']
    assert window.session.view_state['consolidation_origin']['view_state']['scroll'] == offset


def test_first_round_reader_starts_correction_with_one_explicit_click(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); seed(repo, 3, repo.today().isoformat())
    first = daily(repo); submit(repo, first, False)
    window = ui(repo); window._activate_session(first); window.read_current_wrong()
    assert window.primary_action_button.text() == '开始错词默写'
    assert len(repo.list_history()) == 1
    window.primary_action_button.click()
    assert not window.session.submitted and window.session.total == 3
    assert window.pending_review_session is None and not window._wrong_reading
    assert window.session.view_state['memory']['phase'] == 'relearning'


def test_zoom_alignment_and_narrow_gutter_at_window_sizes(tmp_path, ui):
    window = ui(Repository(tmp_path / 'db'))
    path = sample_word(tmp_path); doc = Document(path)
    for _ in range(40): doc.add_paragraph('This long article keeps a real vertical scrollbar visible.')
    doc.save(path); window._import_paths([str(path)])
    window.show()
    for width in (980, 1100, 1280, 1480, 2400):
        window.resize(width, 760); QApplication.processEvents(); QTest.qWait(10)
        a = window.zoom_panel.mapTo(window, QPoint()).x()
        b = window.status_panel.mapTo(window, QPoint()).x()
        assert abs(a - b) <= 2, (width, a, b)
        assert window.zoom_panel.width() == window.status_panel.width()
        gutter = b - window.scroll.verticalScrollBar().mapTo(window, QPoint()).x() - window.scroll.verticalScrollBar().width()
        assert gutter <= 11
        assert window.zoom_label.isVisible()


def test_plan_explains_actual_daily_capacity_without_changing_selection(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); repo.enable_review_continuity()
    seed(repo, 100, repo.today().isoformat())
    with repo.connection() as db:
        db.execute('UPDATE wrong_words SET review_active=0')
        db.execute('UPDATE wrong_words SET review_active=1 WHERE id<=77')
        db.execute('UPDATE wrong_words SET review_due_date=? WHERE id BETWEEN 72 AND 77',
                   ((repo.today()+timedelta(days=2)).isoformat(),))
    repo.configure_review_capacity(80, 20)
    window = ui(repo); before = tables(repo)
    assert '今天安排80词' in window.actions['今日复习'].toolTip()
    assert tables(repo) == before
    window.start_daily_review()
    assert window.session.total == 80
    assert window.mode_detail.text() == '今日安排 80 词 · 每日最多 80 词'
    assert '到期复习：71' in window.progress_text.text() and '加入旧词：9' in window.progress_text.text()
    before = tables(repo); window._refresh_daily_review()
    assert tables(repo) == before
