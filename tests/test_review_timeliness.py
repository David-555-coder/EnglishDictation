"""Timely maintenance and evidence-backed warm-failure correction regressions."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from src.database.repository import Repository
from src.services.daily_review import select_review_ids
from src.services.fsrs7 import next_state
from src.services.learning_evidence import expose
from src.services.practice_service import new_long_term_session, new_wrong_session
from test_daily_review import seed
from test_learning_flow import history_retry, model_row, queue_word, submit_at
from test_library_ui import ui


def choose(rows, limit=40, day=date(2026, 10, 12), usable=lambda _: True):
    return select_review_ids(rows, limit, day, usable,
                             now=datetime.combine(day, datetime.min.time(), timezone.utc)
                                 + timedelta(hours=12))


def existing_memory(repo):
    seed(repo)
    original = new_long_term_session(repo.get_review_examples([1]))
    submit_at(repo, original, True, datetime(2026, 10, 1, 12))
    return original


def warm_failure(repo, at=datetime(2026, 10, 12, 13, 50)):
    parent = new_long_term_session(repo.get_review_examples([1]))
    parent.view_state['memory']['phase'] = 'study'
    submit_at(repo, parent, False, at)
    return parent


@pytest.mark.parametrize('route', ['direct', 'history', 'draft'])
@pytest.mark.parametrize('correct', [True, False])
def test_existing_warm_failure_bridges_only_actual_closed_book_correction(tmp_path, route, correct):
    from src.services.serialization import session_from_dict, session_to_dict
    repo = Repository(tmp_path / 'db')
    existing_memory(repo)
    old = model_row(repo)
    parent = warm_failure(repo)
    assert model_row(repo)['fsrs_reviews'] == old['fsrs_reviews']
    with repo.connection() as conn:
        parent_event = dict(conn.execute('SELECT * FROM memory_events ORDER BY id DESC LIMIT 1').fetchone())
    if route == 'history':
        repo = Repository(repo.database_path)
        retry = history_retry(repo, parent)
    else:
        retry = new_wrong_session(parent)
        if route == 'draft':
            retry = session_from_dict(session_to_dict(retry))
    submit_at(repo, retry, correct, datetime(2026, 10, 12, 21, 2))
    row = model_row(repo)
    assert row['fsrs_reviews'] == old['fsrs_reviews'] + 1
    # The recorded warm failure starts short-term relearning. A later success
    # must not get the much larger gain of an eleven-day independent recall.
    from src.services.memory_scheduler import state_from_row
    prior = next_state(state_from_row(old), 1, (datetime(2026, 10, 12, 13, 50)
                       - datetime(2026, 10, 1, 12)).total_seconds() / 86400)
    expected = next_state(prior, 3 if correct else 1, (7 * 60 + 12) / 1440)
    assert row['fsrs_stability'] == pytest.approx(expected.stability)
    assert row['fsrs_stability_fast'] == pytest.approx(expected.stability_fast)
    assert row['review_relearning'] == 1 and row['review_due_date'] == '2026-10-13'
    with repo.connection() as conn:
        event = dict(conn.execute('SELECT * FROM memory_events ORDER BY id DESC LIMIT 1').fetchone())
        assert dict(conn.execute('SELECT * FROM memory_events WHERE id=?', (parent_event['id'],)).fetchone()) == parent_event
    assert event['kind'] == 'relearning' and event['rating'] == (3 if correct else 1)
    assert event['elapsed_days'] == pytest.approx((7 * 60 + 12) / 1440)
    context = json.loads(repo.get_practice_session(retry.database_id)['memory_context'])
    assert context['relearning_anchors']['word0']['parent_key'] == parent.session_key
    assert context['first_round_result']['label'] == '阅读后答题'
    repo.save_submitted_session(retry)
    assert model_row(repo)['fsrs_reviews'] == row['fsrs_reviews']


@pytest.mark.parametrize('invalid', ['exposed', 'expired', 'reversed', 'intervening',
                                    'superseded_warm', 'missing_parent', 'corrupt_context'])
def test_warm_bridge_rejects_untrusted_or_superseded_evidence(tmp_path, invalid):
    repo = Repository(tmp_path / 'db')
    existing_memory(repo)
    parent = warm_failure(repo)
    retry = new_wrong_session(parent)
    at = datetime(2026, 10, 12, 21, 2)
    if invalid == 'exposed':
        expose(retry)
    elif invalid == 'expired':
        at += timedelta(days=1)
    elif invalid == 'reversed':
        at = datetime(2026, 10, 12, 13)
    elif invalid == 'intervening':
        other = new_long_term_session(repo.get_review_examples([1]))
        submit_at(repo, other, True, datetime(2026, 10, 12, 15))
    elif invalid == 'superseded_warm':
        warm_failure(repo, datetime(2026, 10, 12, 15))
    elif invalid == 'missing_parent':
        repo.delete_practice_session(parent.database_id)
    elif invalid == 'corrupt_context':
        with repo.connection() as conn:
            conn.execute("UPDATE practice_sessions SET memory_context='{}' WHERE id=?", (parent.database_id,))
    before = model_row(repo)
    submit_at(repo, retry, True, at)
    assert model_row(repo)['fsrs_reviews'] == before['fsrs_reviews']
    assert model_row(repo)['fsrs_stability'] == before['fsrs_stability']
    assert retry.correct_count == 1


def test_warm_success_cannot_be_forged_into_failed_parent(tmp_path):
    repo = Repository(tmp_path / 'db')
    existing_memory(repo)
    parent = new_long_term_session(repo.get_review_examples([1]))
    parent.view_state['memory']['phase'] = 'study'
    submit_at(repo, parent, True, datetime(2026, 10, 12, 13))
    retry = new_wrong_session(parent)
    retry.target_ids = parent.target_ids
    before = model_row(repo)
    submit_at(repo, retry, True, datetime(2026, 10, 12, 21))
    assert model_row(repo)['fsrs_reviews'] == before['fsrs_reviews']


def test_warm_bridge_detects_revelation_in_another_view_during_correction(tmp_path):
    repo = Repository(tmp_path / 'db')
    existing_memory(repo)
    parent = warm_failure(repo)
    retry = new_wrong_session(parent)
    at = datetime(2026, 10, 12, 21)
    repo.record_exposure(['word0'], 'another-view',
                         (at - timedelta(minutes=1)).astimezone(timezone.utc).isoformat())
    before = model_row(repo)
    submit_at(repo, retry, True, at)
    assert model_row(repo)['fsrs_reviews'] == before['fsrs_reviews']


def test_preview_is_before_closed_book_round_not_reveal_during_it(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db')
    existing_memory(repo)
    parent = warm_failure(repo)
    window = ui(repo)
    monkeypatch.setattr('src.services.learning_evidence.utc_now', lambda: '2026-10-12T06:00:00+00:00')
    window.start_saved_wrong_practice(parent.database_id)
    monkeypatch.setattr('src.services.learning_evidence.utc_now', lambda: '2026-10-12T06:05:00+00:00')
    window.start_full_practice()
    assert window.session.view_state['memory']['began_at'] == '2026-10-12T06:05:00+00:00'


@pytest.mark.parametrize('another_reveal', [False, True])
@pytest.mark.parametrize('restore_draft', [False, True])
def test_same_clock_tick_preview_is_distinguished_from_another_reveal(tmp_path, ui, monkeypatch,
                                                                      another_reveal, restore_draft):
    repo = Repository(tmp_path / 'db')
    existing_memory(repo)
    parent = warm_failure(repo)
    window = ui(repo)
    # Windows' wall clock may give the preview and the immediately following
    # start action the same timestamp. Only this round's known preview is safe.
    at = '2026-10-12T06:00:00+00:00'
    monkeypatch.setattr('src.services.learning_evidence.utc_now', lambda: at)
    window.start_saved_wrong_practice(parent.database_id)
    window.start_full_practice()
    if another_reveal:
        repo.record_exposure(['word0'], 'another-view-at-start', at)
    before = model_row(repo)
    from src.services.grading import grade_session
    session = window.session
    if restore_draft:
        from src.services.serialization import session_from_dict, session_to_dict
        session = session_from_dict(session_to_dict(session))
    session.answers = {item_id: session.article.item_map[item_id].answer for item_id in session.target_ids}
    grade_session(session)
    session.submitted_at = datetime(2026, 10, 12, 14, 2)
    repo.save_submitted_session(session)
    assert model_row(repo)['fsrs_reviews'] == before['fsrs_reviews'] + int(not another_reveal)


def test_warm_bridge_and_audit_link_roll_back_atomically(tmp_path):
    import sqlite3
    repo = Repository(tmp_path / 'db')
    existing_memory(repo)
    parent = warm_failure(repo)
    retry = new_wrong_session(parent)
    before = model_row(repo)
    with repo.connection() as conn:
        conn.execute("""CREATE TRIGGER fail_anchor BEFORE UPDATE OF memory_context ON practice_sessions
            BEGIN SELECT RAISE(ABORT,'anchor failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        submit_at(repo, retry, True, datetime(2026, 10, 12, 21))
    assert model_row(repo) == before and retry.database_id is None
    with repo.connection() as conn:
        assert conn.execute('SELECT count(*) FROM memory_events').fetchone()[0] == 2
        conn.execute('DROP TRIGGER fail_anchor')
    repo.save_submitted_session(retry)
    assert model_row(repo)['fsrs_reviews'] == before['fsrs_reviews'] + 1


def test_timely_due_words_are_not_displaced_by_large_old_maintenance_pool():
    old = [queue_word(i, due='2026-09-01') for i in range(1, 501)]
    timely = [queue_word(i, due='2026-10-12', rating=1, confirmation=True)
              for i in range(501, 541)]
    selected = choose(old + timely)
    assert len(selected) == len(set(selected)) == 40
    assert len(set(selected) & set(range(501, 541))) == 32
    assert len(set(selected) & set(range(1, 501))) == 8


@pytest.mark.parametrize('old_count,timely_count', [(0, 80), (80, 0), (2, 80), (80, 2), (2, 2)])
def test_unused_recovery_or_timely_slots_fill_without_growing_limit(old_count, timely_count):
    rows = [queue_word(i, due='2026-09-01') for i in range(1, old_count + 1)]
    rows += [queue_word(1000+i, due='2026-10-12') for i in range(timely_count)]
    assert len(choose(rows)) == min(40, len(rows))


def test_one_timely_word_is_selected_immediately_despite_500_waiting_words():
    rows = [queue_word(i, due='2026-09-01') for i in range(1, 501)]
    rows += [queue_word(501, due='2026-10-12')]
    assert 501 in choose(rows)


def test_repeated_failures_cannot_monopolize_the_timely_lane():
    repeated = [queue_word(i, due='2026-10-12', rating=1, completed='2026-10-11') for i in range(1, 41)]
    fresh = [queue_word(i, due='2026-10-12') for i in range(41, 49)]
    assert set(range(41, 49)).issubset(choose(repeated + fresh))


def test_recent_learning_gets_grace_after_missing_a_few_due_days():
    old = [queue_word(i, due='2026-09-01') for i in range(1, 501)]
    recent = queue_word(501, due='2026-10-08')
    assert 501 in choose(old + [recent])


def test_qualified_manual_practice_counts_for_timely_rotation():
    manual = dict(queue_word(1, due='2026-10-12', rating=1),
                  review_last_independent_at='2026-10-11T04:00:00+00:00')
    fresh = queue_word(2, due='2026-10-12')
    selected = [choose([manual, fresh], 1, date(2026, 10, 12) + timedelta(days=i))[0] for i in range(2)]
    assert selected == [2, 2]


def test_stress_after_four_new_articles_does_not_leave_new_words_unvisited():
    from tools.verify_review_timeliness import stress
    result = stress(select_review_ids, [50, 50, 50, 50])
    assert result['new_covered'] == 200 and result['new_never_selected'] == 0
    assert result['maximum_new_first_wait'] <= 3


def test_small_custom_limits_preserve_timely_priority_and_some_old_rotation():
    rows = [queue_word(1, due='2026-09-01'), queue_word(2, due='2026-10-12')]
    selected = []
    for offset in range(5):
        day = date(2026, 10, 12) + timedelta(days=offset)
        rows[1]['review_due_date'] = day.isoformat()
        ids = choose(rows, 1, day)
        assert len(ids) == 1
        selected += ids
    assert selected.count(1) == 1 and selected.count(2) == 4


def test_unusable_words_do_not_waste_slots_or_mutate_rows():
    rows = [queue_word(i, due='2026-09-01' if i < 50 else '2026-10-12') for i in range(1, 100)]
    original = deepcopy(rows)
    checked = []
    def usable(word_id):
        checked.append(word_id)
        return word_id % 2 == 0
    selected = choose(rows, usable=usable)
    assert len(selected) == 40 and all(word_id % 2 == 0 for word_id in selected)
    assert len(checked) == len(set(checked))
    assert rows == original


def test_timeliness_does_not_reclassify_persistent_streams():
    old = queue_word(1, due='2026-09-01')
    backlog = dict(queue_word(2, due='2026-10-12'), review_pool='backlog')
    future = queue_word(3, due='2026-10-13')
    rows = [old, backlog, future]
    original = deepcopy(rows)
    assert choose(rows) == [1]
    assert rows == original


@pytest.mark.parametrize('limit', [1, 2, 3, 4, 5, 7, 20, 40, 53, 80])
def test_capacity_split_respects_custom_total_and_keeps_main_lane_larger(limit):
    old = [queue_word(i, due='2026-09-01') for i in range(1, 101)]
    timely = [queue_word(i, due='2026-10-12') for i in range(101, 201)]
    selected = choose(old + timely, limit)
    recovery = sum(word_id <= 100 for word_id in selected)
    assert len(selected) == limit and len(set(selected)) == limit
    assert recovery in (limit // 5, (limit + 4) // 5)
    if limit >= 3:
        assert recovery < limit - recovery
