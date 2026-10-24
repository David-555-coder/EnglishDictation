from datetime import date

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QStyle, QStyleOptionSlider

from test_daily_review import seed, daily, submit
from test_library_ui import ui, sample_word
from src.database.repository import Repository
from src.services.daily_review import new_daily_session
from src.ui.unfinished_dialog import UnfinishedPracticeDialog


@pytest.fixture
def review(tmp_path, monkeypatch):
    monkeypatch.setattr(Repository, 'today', staticmethod(lambda: date(2026, 10, 17)))
    repo = Repository(tmp_path / 'db')
    repo.enable_review_continuity()
    seed(repo, 80, '2026-10-17')
    repo.configure_review_capacity(80, 20)
    current = daily(repo)
    old = new_daily_session(repo.get_review_examples(repo.today_review_ids()[:40]), '2026-10-15')
    return repo, current, old


def payload(repo, session):
    with repo.connection() as db:
        return db.execute('SELECT payload FROM practice_drafts WHERE session_key=?', (session.session_key,)).fetchone()[0]


def learning_tables(repo):
    with repo.connection() as db:
        return {name: [tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                for name in ('practice_sessions', 'practice_answers', 'wrong_words', 'daily_review_items', 'memory_events')}


def memory_states(repo):
    with repo.connection() as db:
        names = [r[1] for r in db.execute('PRAGMA table_info(wrong_words)')
                 if r[1].startswith(('fsrs_', 'review_'))]
        return [tuple(r) for r in db.execute('SELECT '+','.join(names)+' FROM wrong_words ORDER BY id')]


def test_completed_today_does_not_reopen_old_40_even_with_article_drafts(review, tmp_path, ui):
    repo, current, old = review
    window = ui(repo)
    window._import_paths([str(sample_word(tmp_path))])
    window.start_full_practice()
    window._save_active_draft()
    window.close()
    submit(repo, current, False)
    repo.save_draft(old)  # Most recently saved, but belongs to October 15.
    old_payload, before = payload(repo, old), learning_tables(repo)
    reopened = ui(Repository(repo.database_path))
    reopened._restore_article_library()
    assert reopened.session is None and reopened.pending_review_session is None
    assert reopened.mode_title.text().startswith('原文模式')
    assert reopened.progress.isHidden() and reopened.score_text.isHidden()
    assert reopened.actions['今日复习'].text() == '今日复习（0）'
    assert not reopened.answer_edits
    assert payload(repo, old) == old_payload and learning_tables(repo) == before
    reopened._restore_article_library()  # Delayed catalogue/import callback must keep the home view.
    assert reopened.session is None and reopened.mode_title.text().startswith('原文模式')


def test_today_button_explicitly_resumes_instead_of_more_recent_old_draft(review, ui):
    repo, current, old = review
    item_id = current.target_ids[0]
    current.answers[item_id] = 'keep this answer'
    repo.save_draft(current)
    repo.save_draft(old)
    original = payload(repo, old)
    window = ui(repo)
    window._restore_article_library()
    assert window.session is None
    window.start_daily_review()
    assert window.session.session_key == current.session_key
    assert window.answer_edits[item_id].text() == 'keep this answer'
    assert window.session.total == 80
    assert payload(repo, old) == original


@pytest.mark.parametrize('stored_day', ['2026-10-15', '2026-10-18', None])
def test_non_today_or_undated_daily_draft_stays_manual(review, ui, stored_day):
    repo, _, old = review
    if stored_day is None:
        old.view_state.pop('daily_review_day')
    else:
        old.view_state['daily_review_day'] = stored_day
    repo.save_draft(old)
    before = payload(repo, old)
    window = ui(repo)
    window._restore_article_library()
    assert window.session is None and not window.answer_edits
    assert window.mode_title.text() == '今日复习'
    assert window.primary_action_button.text() == '开始今日复习'
    assert window.primary_action_button.isEnabled()
    assert payload(repo, old) == before
    window.primary_action_button.click()
    assert window.session.view_state['daily_review_day'] == '2026-10-17'
    assert window.session.total == 80


def test_completed_duplicate_today_draft_does_not_auto_resume(review, ui):
    repo, current, _ = review
    repo.save_draft(current)
    other = daily(repo)
    submit(repo, other, False)
    before = payload(repo, current)
    window = ui(repo)
    window._restore_article_library()
    assert window.session is None
    assert window.mode_title.text() == '今日复习已完成'
    assert payload(repo, current) == before


def test_old_daily_draft_can_be_explicitly_opened_with_original_date(review, ui, monkeypatch):
    repo, current, old = review
    submit(repo, current, False)
    repo.save_draft(old)
    window = ui(repo)
    window._restore_article_library()
    seen = []

    def choose(dialog):
        for index, (session, _) in enumerate(dialog.records):
            if session.session_key == old.session_key:
                dialog.list.setCurrentRow(index)
                seen.append(dialog.list.item(index).text())
                return QDialog.Accepted
        raise AssertionError('Old daily draft missing from the manual resume list')

    monkeypatch.setattr(UnfinishedPracticeDialog, 'exec', choose)
    window.resume_draft()
    assert '2026-10-15' in seen[0]
    assert window.session.session_key == old.session_key
    assert '2026-10-15' in window.mode_title.text()
    assert '草稿' in window.mode_title.text()
    before = learning_tables(repo)
    states = memory_states(repo)
    window._commit_submission()
    with repo.connection() as db:
        latest = db.execute('SELECT target_count FROM practice_sessions ORDER BY id DESC LIMIT 1').fetchone()[0]
    assert latest == 40
    after = learning_tables(repo)
    assert memory_states(repo) == states
    assert after['daily_review_items'] == before['daily_review_items']
    with repo.connection() as db:
        assert {tuple(r) for r in db.execute('SELECT kind,rating FROM memory_events WHERE session_key=?',
                                           (old.session_key,))} == {('stale_draft', None)}


def test_failed_grade_save_stays_manual_and_recoverable_even_for_old_daily_draft(review, ui, monkeypatch):
    from src.services.grading import grade_session
    repo, _, old = review
    grade_session(old)
    repo.save_draft(old)
    window = ui(repo)
    window._restore_article_library()
    assert window.session is None
    assert any(s.session_key == old.session_key for s, _ in repo.unfinished_practices())
    monkeypatch.setattr(UnfinishedPracticeDialog, 'exec', lambda dialog: QDialog.Accepted)
    window.resume_draft()
    assert window.session.session_key == old.session_key
    assert window.session.submitted and not window.session.database_id
    assert window.primary_action_button.text() == '重试保存成绩'
    assert all(edit.isReadOnly() for edit in window.answer_edits.values())


def test_today_button_syncs_draft_with_words_completed_elsewhere(review, ui):
    repo, current, _ = review
    item_id = current.target_ids[-1]
    current.answers[item_id] = 'keep pending answer'
    repo.save_draft(current)
    other = new_daily_session(repo.get_review_examples(repo.today_review_ids()[:20]), '2026-10-17')
    # Pick a word known to remain, regardless of the shuffled sentence order.
    completed = {item.normalized_answer for item in other.article.word_items}
    item_id = next(i for i in current.target_ids if current.article.item_map[i].normalized_answer not in completed)
    current.answers[item_id] = 'keep pending answer'
    repo.save_draft(current)
    submit(repo, other, False)
    window = ui(repo)
    window._restore_article_library()
    assert window.session is None
    window.start_daily_review()
    assert window.session.session_key == current.session_key
    assert window.session.total == 60
    assert window.answer_edits[item_id].text() == 'keep pending answer'


def test_scrollbar_rounded_style_keeps_wheel_and_drag_working(review, ui):
    repo, current, _ = review
    repo.save_draft(current)
    window = ui(repo)
    window._restore_article_library()
    window.start_daily_review()
    window.resize(980, 650)
    window.show()
    QApplication.processEvents()
    bar = window.scroll.verticalScrollBar()
    assert bar.width() == 10
    assert bar.maximum() > 0
    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    style = bar.style()
    assert style.subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarAddLine, bar).isEmpty()
    assert style.subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSubLine, bar).isEmpty()
    slider = style.subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
    QTest.mousePress(bar, Qt.LeftButton, pos=slider.center())
    QTest.mouseMove(bar, QPoint(slider.center().x(), bar.height() - 20))
    QTest.mouseRelease(bar, Qt.LeftButton, pos=QPoint(slider.center().x(), bar.height() - 20))
    assert bar.value() > 0
    bar.setValue(0)
    from PySide6.QtGui import QWheelEvent
    pos = window.scroll.viewport().rect().center()
    event = QWheelEvent(pos, window.scroll.viewport().mapToGlobal(pos), QPoint(), QPoint(0, -120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(window.scroll.viewport(), event)
    assert bar.value() > 0
    assert window.session.session_key == current.session_key and not window.session.submitted
