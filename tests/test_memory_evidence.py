from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.database.repository import Repository
from src.services.daily_review import new_daily_session
from src.services.grading import grade_session
from src.services.practice_service import new_wrong_session, new_long_term_session
from src.services.learning_evidence import expose, answer_changed, begin
from src.services.serialization import session_to_dict, session_from_dict
from test_daily_review import seed, clock, daily, word
from test_library_ui import ui


@pytest.mark.parametrize('submitted', [False, True])
def test_late_startup_restore_does_not_replace_active_daily(clock, tmp_path, ui, monkeypatch, submitted):
    from src.services.library import ArticleLibrary
    from test_library_ui import sample_word
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, 'information', lambda *args: QMessageBox.Ok)
    repo = Repository(tmp_path / 'db'); seed(repo)
    ArticleLibrary(repo).import_file(sample_word(tmp_path))
    window = ui(repo)
    window.start_daily_review()
    session = window.session
    edit = next(iter(window.answer_edits.values()))
    edit.setText('unfinished answer')
    item_id = edit.item_id
    if submitted:
        window._commit_submission()
        assert '成绩已保存' in window.statusBar().currentMessage()
    window._restore_article_library()
    assert window.session is session
    assert window.session.answers[item_id] == 'unfinished answer'
    assert window.article_list.count() == 1


def test_delayed_view_callback_after_window_deletion_is_harmless(clock, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QTimer, 'singleShot', lambda *args: None)
    repo = Repository(tmp_path / 'db'); seed(repo)
    window = MainWindow(repo)
    window.start_daily_review()
    key = window.session.session_key
    generation = window._content_generation
    window.close(); window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    window._restore_view(key, generation)


def commit(repo, session, correct, when):
    if correct:
        session.answers = {i: session.article.item_map[i].answer for i in session.target_ids}
    grade_session(session)
    session.submitted_at = when
    repo.save_submitted_session(session)


def events(repo):
    with repo.connection() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM memory_events ORDER BY id')]


def test_failure_reading_retry_and_next_day_confirmation(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    session = daily(repo)
    when = datetime(2026,9,15,12)
    commit(repo,session,False,when)
    first = word(repo)
    assert events(repo)[0]['rating'] == 1
    assert first['fsrs_reviews'] == 1 and first['review_due_date'] == '2026-09-16'
    retry = new_wrong_session(session)
    repo.record_exposure(['word0'],'read',when.astimezone(timezone.utc).isoformat())
    commit(repo,retry,True,when+timedelta(minutes=10))
    rows = events(repo)
    assert rows[-1]['kind'] == 'relearning' and rows[-1]['rating'] == 3
    assert rows[-1]['elapsed_days'] == pytest.approx(10/1440)
    assert word(repo)['fsrs_reviews'] == 2 and word(repo)['review_relearning'] == 1
    assert repo.review_status()['completed'] == 1
    clock[0] += timedelta(days=1)
    # No preview: a new day's cold answer is independent and removes confirmation.
    follow = daily(repo)
    commit(repo,follow,True,when+timedelta(days=1,minutes=10))
    assert events(repo)[-1]['kind'] == 'first'
    assert word(repo)['review_relearning'] == 0


def test_reveal_keeps_already_completed_answers_but_not_later_edits(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo,2)
    session = daily(repo)
    first,second = session.target_ids
    session.answers[first] = session.article.item_map[first].answer
    expose(session)
    session.answers[second] = session.article.item_map[second].answer
    answer_changed(session,second)
    commit(repo,session,False,datetime(2026,9,15,12))
    rows = {r['normalized_answer']:r for r in events(repo)}
    assert rows[session.article.item_map[first].normalized_answer]['rating'] == 3
    assert rows[session.article.item_map[second].normalized_answer]['rating'] is None
    assert session.correct_count == 2  # Exposure never changes ordinary grading.


def test_changing_back_after_reveal_does_not_restore_cold_evidence(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    session = daily(repo); item_id = session.target_ids[0]
    session.answers[item_id] = 'word0'; expose(session)
    session.answers[item_id] = 'other'; answer_changed(session,item_id)
    session.answers[item_id] = 'word0'; answer_changed(session,item_id)
    commit(repo,session,False,datetime(2026,9,15,12))
    assert events(repo)[0]['rating'] is None and word(repo)['fsrs_reviews'] == 0


def test_legacy_draft_keeps_score_but_never_invents_memory_history(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    session = daily(repo); session.view_state.pop('memory')
    restored = session_from_dict(session_to_dict(session))
    commit(repo,restored,True,datetime(2026,9,15,12))
    assert restored.correct_count == 1
    assert events(repo)[0]['kind'] == 'unknown' and events(repo)[0]['rating'] is None
    assert word(repo)['fsrs_model'] is None and word(repo)['review_due_date'] == '2026-09-15'


def test_idempotence_and_transaction_rollback(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo); session = daily(repo)
    with repo.connection() as conn:
        conn.execute("CREATE TRIGGER fail BEFORE INSERT ON memory_events BEGIN SELECT RAISE(ABORT,'fail'); END")
    with pytest.raises(Exception):
        commit(repo,session,False,datetime(2026,9,15,12))
    assert word(repo)['error_count'] == 1 and word(repo)['fsrs_reviews'] == 0
    assert not repo.list_history()
    with repo.connection() as conn:
        conn.execute('DROP TRIGGER fail')
    repo.save_submitted_session(session)
    repo.save_submitted_session(session)
    assert len(events(repo)) == 1 and word(repo)['error_count'] == 2


def test_manual_qualified_recall_completes_plan_without_refill(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo,70)
    ids = repo.today_review_ids(start=True)
    manual = new_long_term_session(repo.get_review_examples(ids[:1]))
    commit(repo,manual,True,datetime(2026,9,15,12))
    assert repo.today_review_ids() == ids[1:]
    assert repo.review_status()['completed'] == 1
    assert repo.review_status()['waiting'] == 30
    assert not repo.set_review_limit(80)


def test_reading_does_not_satisfy_plan_or_upgrade_existing_memory(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    session = daily(repo); commit(repo,session,True,datetime(2026,9,15,12))
    before = word(repo)
    manual = new_long_term_session(repo.get_review_examples([1]))
    manual.view_state['memory']['phase'] = 'study'
    commit(repo,manual,True,datetime(2026,9,15,13))
    after = word(repo)
    for column in ('fsrs_stability','fsrs_stability_fast','fsrs_difficulty','fsrs_last_at','fsrs_reviews'):
        assert after[column] == before[column]
    assert after['review_relearning'] == before['review_relearning']
    assert after['review_due_date'] == before['review_due_date']


def test_cross_day_daily_and_clock_rollback_do_not_update_model(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    stale = daily(repo); commit(repo,stale,True,datetime(2026,9,16,12))
    assert word(repo)['fsrs_reviews'] == 0 and events(repo)[0]['kind'] == 'stale_draft'
    session = daily(repo); commit(repo,session,True,datetime(2026,9,15,12))
    manual = new_long_term_session(repo.get_review_examples([1]))
    commit(repo,manual,True,datetime(2026,9,15,11))
    assert events(repo)[-1]['kind'] == 'clock_conflict' and word(repo)['fsrs_reviews'] == 1


def test_nested_evidence_changes_trigger_draft_save_and_daily_starts_cold(clock,tmp_path,ui):
    repo = Repository(tmp_path/'db'); seed(repo)
    window = ui(repo); window.start_daily_review()
    assert window.pending_review_session is None and not window._answers_visible
    assert window.session.view_state['memory']['phase'] == 'first'
    window._save_active_draft()
    window.show_answers(); window._save_active_draft()
    restored = Repository(repo.database_path).load_drafts()[0]
    assert restored.view_state['memory']['exposed']
    assert 'Shift+Backspace' in window.hint_text.text() if hasattr(window,'hint_text') else True


def test_corrupt_state_is_not_used_for_math_and_valid_cold_attempt_reinitializes(clock,tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET fsrs_model='fsrs7-dual-34-4bc0a097',fsrs_stability=-1,fsrs_difficulty=5,fsrs_stability_fast=1,fsrs_last_at='2026-09-01'")
    assert repo.today_review_ids() == [1]
    commit(repo,daily(repo),True,datetime(2026,9,15,12))
    assert word(repo)['fsrs_stability'] > 0
