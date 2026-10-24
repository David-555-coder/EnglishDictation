"""Regression checks for restart-safe correction and bounded queue priority."""
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3

import pytest

from src.database.repository import Repository
from src.services.daily_review import select_review_ids
from src.services.fsrs7 import MODEL_ID, next_state
from src.services.grading import grade_session
from src.services.learning_evidence import expose
from src.services.practice_service import new_long_term_session, new_saved_wrong_session, new_wrong_session
from test_daily_review import seed
from test_library_ui import ui


def submit_at(repo, session, correct, at):
    session.view_state['memory']['began_at'] = (at - timedelta(minutes=5)).astimezone(timezone.utc).isoformat()
    session.answers = {i: session.article.item_map[i].answer if correct else 'wrong'
                       for i in session.target_ids}
    grade_session(session)
    session.submitted_at = at
    repo.save_submitted_session(session)


def first_failure(repo):
    seed(repo)
    session = new_long_term_session(repo.get_review_examples([1]))
    session.view_state['memory']['phase'] = 'study'
    submit_at(repo, session, False, datetime(2026, 10, 8, 13, 50))
    return session


def history_retry(repo, parent):
    session = new_saved_wrong_session(repo.get_practice_session(parent.database_id),
                                      repo.get_wrong_answers(parent.database_id))
    # The real preview preserves relearning and marks other history as study.
    if session.view_state['memory']['phase'] != 'relearning':
        session.view_state['memory']['phase'] = 'study'
    return session


def model_row(repo):
    return repo.list_wrong_words()[0]


def test_restart_history_correction_matches_direct_correction(tmp_path):
    results = []
    for route in ('direct', 'history'):
        repo = Repository(tmp_path / route / 'db')
        parent = first_failure(repo)
        if route == 'history':
            repo = Repository(repo.database_path)
            retry = history_retry(repo, parent)
        else:
            retry = new_wrong_session(parent)
        submit_at(repo, retry, True, datetime(2026, 10, 8, 21, 2))
        row = model_row(repo)
        with repo.connection() as conn:
            event = dict(conn.execute('SELECT * FROM memory_events ORDER BY id DESC LIMIT 1').fetchone())
        results.append((row['fsrs_reviews'], row['fsrs_stability'], row['fsrs_stability_fast'],
                        row['fsrs_difficulty'], event['kind'], event['rating'], event['elapsed_days']))
    assert results[0] == results[1]
    assert results[1][0] == 2 and results[1][4:6] == ('relearning', 3)


def test_repeated_restart_retains_original_episode_and_first_round(tmp_path):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    child = history_retry(Repository(repo.database_path), parent)
    submit_at(repo, child, False, datetime(2026, 10, 8, 16))
    grandchild = history_retry(Repository(repo.database_path), child)
    assert grandchild.view_state['memory']['episode'] == parent.session_key
    assert grandchild.view_state['memory']['parent_key'] == child.session_key
    assert grandchild.view_state['first_round_result'] == child.view_state['first_round_result']
    submit_at(repo, grandchild, True, datetime(2026, 10, 8, 21))
    assert model_row(repo)['fsrs_reviews'] == 3


@pytest.mark.parametrize('intervening', [False, True])
def test_old_or_superseded_history_does_not_create_recall_credit(tmp_path, intervening):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    if intervening:
        other = new_long_term_session(repo.get_review_examples([1]))
        submit_at(repo, other, True, datetime(2026, 10, 8, 15))
        when = datetime(2026, 10, 8, 21)
    else:
        when = datetime(2026, 10, 10, 21)
    before = model_row(repo)
    retry = history_retry(Repository(repo.database_path), parent)
    submit_at(repo, retry, True, when)
    after = model_row(repo)
    assert after['fsrs_reviews'] == before['fsrs_reviews']
    assert after['fsrs_stability'] == before['fsrs_stability']
    assert retry.correct_count == 1


@pytest.mark.parametrize('context', ['', '{}', '[]', 'null', 'not-json', '{"schema":2,"episode":"x"}'])
def test_legacy_or_damaged_context_is_conservative(tmp_path, context):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    record = repo.get_practice_session(parent.database_id)
    record['memory_context'] = context
    retry = new_saved_wrong_session(record, repo.get_wrong_answers(parent.database_id))
    assert retry.view_state['memory']['phase'] != 'relearning'


def test_context_saved_with_grading_and_idempotent(tmp_path):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    record = repo.get_practice_session(parent.database_id)
    context = json.loads(record['memory_context'])
    assert context['schema'] == 1 and context['episode'] == parent.session_key
    assert context['first_round_result'] == {'label': '阅读后答题', 'correct': 0, 'total': 1}
    repo.save_submitted_session(parent)
    assert repo.get_practice_session(parent.database_id)['memory_context'] == record['memory_context']
    assert model_row(repo)['fsrs_reviews'] == 1


def test_reveal_in_resumed_correction_is_not_independent_recall(tmp_path):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    retry = history_retry(Repository(repo.database_path), parent)
    expose(retry)
    before = model_row(repo)
    submit_at(repo, retry, True, datetime(2026, 10, 8, 21))
    assert model_row(repo)['fsrs_reviews'] == before['fsrs_reviews']
    with repo.connection() as conn:
        event = conn.execute('SELECT kind,rating FROM memory_events ORDER BY id DESC LIMIT 1').fetchone()
    assert tuple(event) == ('exposed', None)


def test_clock_reversal_in_resumed_correction_is_not_model_credit(tmp_path):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    retry = history_retry(Repository(repo.database_path), parent)
    before = model_row(repo)
    submit_at(repo, retry, True, datetime(2026, 10, 8, 12))
    assert model_row(repo)['fsrs_reviews'] == before['fsrs_reviews']


def test_history_ui_preserves_episode_after_repository_restart(tmp_path, ui):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    reopened = Repository(repo.database_path)
    window = ui(reopened)
    window.start_saved_wrong_practice(parent.database_id)
    assert window.pending_review_session.view_state['memory']['phase'] == 'relearning'
    assert window.pending_review_session.view_state['memory']['episode'] == parent.session_key
    window.start_full_practice()
    assert window.session.view_state['memory']['phase'] == 'relearning'
    submit_at(reopened, window.session, True, datetime(2026, 10, 8, 21))
    assert model_row(reopened)['fsrs_reviews'] == 2


def test_context_failure_rolls_back_scores_and_memory_then_allows_retry(tmp_path):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    before = model_row(repo)
    retry = history_retry(repo, parent)
    with repo.connection() as conn:
        conn.execute("""CREATE TRIGGER fail_context BEFORE UPDATE OF memory_context ON practice_sessions
            BEGIN SELECT RAISE(ABORT,'context failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        submit_at(repo, retry, True, datetime(2026, 10, 8, 21))
    assert retry.database_id is None
    assert model_row(repo) == before
    with repo.connection() as conn:
        assert conn.execute('SELECT count(*) FROM practice_sessions').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM memory_events').fetchone()[0] == 1
        conn.execute('DROP TRIGGER fail_context')
    repo.save_submitted_session(retry)
    assert model_row(repo)['fsrs_reviews'] == 2


def test_additive_context_upgrade_preserves_old_records_and_backs_up_once(tmp_path):
    repo = Repository(tmp_path / 'db')
    parent = first_failure(repo)
    with repo.connection() as conn:
        conn.execute('ALTER TABLE practice_sessions DROP COLUMN memory_context')
        before = dict(conn.execute('SELECT * FROM practice_sessions').fetchone())
        events = [tuple(r) for r in conn.execute('SELECT * FROM memory_events')]
    upgraded = Repository(repo.database_path)
    record = upgraded.get_practice_session(parent.database_id)
    assert record.pop('memory_context') == ''
    assert record == before
    with upgraded.connection() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM memory_events')] == events
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert len(list((tmp_path / 'migration_backups').glob('*.db'))) == 1
    Repository(repo.database_path)
    assert len(list((tmp_path / 'migration_backups').glob('*.db'))) == 1
    assert history_retry(upgraded, parent).view_state['memory']['phase'] == 'study'


def queue_word(word_id, *, due='2026-10-08', rating=3, confirmation=False, completed=None):
    state = next_state(None, rating)
    return dict(id=word_id, review_pool='maintenance', review_due_date=due,
                review_relearning=int(confirmation), error_count=1, last_error_at='2026-10-01',
                fsrs_model=MODEL_ID, fsrs_stability=state.stability,
                fsrs_difficulty=state.difficulty, fsrs_stability_fast=state.stability_fast,
                fsrs_last_at='2026-10-07T12:00:00+00:00',
                review_last_completed_day=completed)


def choose(rows, limit=1, today=date(2026, 10, 8)):
    return select_review_ids(rows, limit, today, lambda _: True,
                             now=datetime.combine(today, datetime.min.time(), timezone.utc)
                                 + timedelta(hours=12))


def test_confirmation_is_not_absolute_priority():
    rows = [queue_word(1, confirmation=True), queue_word(2, rating=1)]
    assert choose(rows) == [2]


def test_recovery_lane_reserves_some_capacity_without_absolute_old_priority():
    rows = [queue_word(i, rating=1, confirmation=True) for i in range(1, 42)]
    rows.append(queue_word(42, due='2026-10-02'))
    assert 42 in choose(rows, 40)


def test_completed_warm_attempt_resets_queue_wait_not_memory_due():
    rows = [queue_word(1, due='2026-09-01', rating=1, confirmation=True, completed='2026-10-07'),
            queue_word(2, due='2026-10-03')]
    assert choose(rows) == [2]
    assert rows[0]['review_due_date'] == '2026-09-01'


def test_incomplete_plan_does_not_reset_waiting():
    rows = [queue_word(1, due='2026-10-02'), queue_word(2, due='2026-10-03')]
    assert choose(rows) == [1]


def test_repository_uses_completed_plan_history_for_queue_protection(tmp_path, monkeypatch):
    monkeypatch.setattr(Repository, 'today', staticmethod(lambda: date(2026, 10, 8)))
    repo = Repository(tmp_path / 'db')
    seed(repo, 41, due='2026-10-02')
    with repo.connection() as conn:
        conn.execute("INSERT INTO daily_review_days(day) VALUES ('2026-10-07')")
        conn.executemany("INSERT INTO daily_review_items(day,word_id,completed,review_pool) VALUES ('2026-10-07',?,1,'maintenance')",
                         [(i,) for i in range(1, 41)])
    assert 41 in repo.today_review_ids()


def test_queue_rotation_handles_exposed_daily_attempts():
    rows = [queue_word(i, due='2026-09-01', rating=1, confirmation=True) for i in range(1, 5)]
    seen = set()
    for offset in range(4):
        day = date(2026, 10, 8) + timedelta(days=offset)
        ids = choose(rows, 1, day)
        seen.update(ids)
        # Warm daily submission completes a task but leaves its model due date unchanged.
        for row in rows:
            if row['id'] in ids:
                row['review_last_completed_day'] = day.isoformat()
    assert seen == {1, 2, 3, 4}


def test_waiting_does_not_turn_into_absolute_priority_after_three_days():
    old = queue_word(1, due='2026-10-06')
    difficult = queue_word(2, rating=1)
    assert choose([old, difficult]) == [1]  # Both recent; earlier appointment rotates first.
    assert choose([old, difficult], today=date(2026, 10, 9)) == [2]
    rows = [queue_word(i, rating=1) for i in range(2, 8)] + [old]
    assert 1 in choose(rows, 5, today=date(2026, 10, 9))


@pytest.mark.parametrize('completed', [None, '', 'bad-date', '2026-10-10'])
def test_invalid_or_future_completion_does_not_hide_waiting(completed):
    old = queue_word(1, due='2026-10-02', completed=completed)
    assert choose([old, queue_word(2, due='2026-10-03', rating=1)]) == [1]


def test_selection_never_admits_future_words_or_changes_input():
    from copy import deepcopy
    rows = [queue_word(1, due='2026-10-09'), queue_word(2, due='2026-10-02')]
    original = deepcopy(rows)
    assert choose(rows, 40) == [2]
    assert rows == original
