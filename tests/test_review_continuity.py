"""Production continuity policy, distinct from legacy stream compatibility."""
from datetime import date, datetime, timedelta

import pytest

from src.database.repository import Repository
from test_daily_review import seed, submit, daily
from test_memory_evidence import commit, events
from src.services.practice_service import new_wrong_session, new_long_term_session
from src.services.serialization import session_to_dict, session_from_dict


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clock = [date(2026, 10, 16)]
    monkeypatch.setattr(Repository, "today", staticmethod(lambda: clock[0]))
    repo = Repository(tmp_path / "db")
    repo.enable_review_continuity()
    return repo, clock


def enroll(repo, count, due="2026-10-16"):
    seed(repo, count, due)
    with repo.connection() as db:
        db.execute("UPDATE wrong_words SET review_active=1")


@pytest.mark.parametrize("total", [40, 80, 120])
def test_yesterday_failures_return_before_unenrolled_old_words(setup, total):
    repo, _ = setup
    enroll(repo, 37)
    add_old(repo, 200)
    repo.configure_review_capacity(total, 20)
    ids = repo.today_review_ids()
    assert set(range(1, 38)).issubset(ids)
    assert len(ids) <= total


def test_total_cap_contains_old_intake_and_persists(setup):
    repo, _ = setup
    enroll(repo, 74)
    add_old(repo, 50)
    repo.configure_review_capacity(80, 20)
    ids = repo.today_review_ids(start=True)
    assert len(ids) == 80
    assert set(range(1, 75)).issubset(ids)
    with repo.connection() as db:
        assert db.execute("SELECT count(*) FROM daily_review_items WHERE selection_reason='old_intake'").fetchone()[0] == 6
        assert db.execute("SELECT count(*) FROM daily_review_items WHERE original_due_date IS NULL").fetchone()[0] == 0
    restarted = Repository(repo.database_path)
    assert restarted.today_review_ids() == ids
    assert restarted.review_capacity(today=True) == (80, 20)
    restarted.configure_review_capacity(40, 0)
    assert restarted.today_review_ids() == ids


def add_old(repo, count):
    with repo.connection() as db:
        for i in range(count):
            name = f"old{i}"
            wid = db.execute("""INSERT INTO wrong_words(answer,normalized_answer,first_error_at,last_error_at,
                review_due_date,review_pool) VALUES (?,?,'2026-09-01','2026-09-01','2026-10-01','backlog')""",
                (name, name)).lastrowid
            db.execute("INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence) VALUES (?,'old','old',?)",
                       (wid, f"Learn {name} now."))


def test_future_work_is_advisory_and_never_borrows_future_words(setup):
    repo, _ = setup
    enroll(repo, 79, "2026-10-17")
    add_old(repo, 50)
    repo.configure_review_capacity(80, 20)
    ids = repo.today_review_ids()
    assert len(ids) == 20
    assert not set(range(1, 80)).intersection(ids)
    assert repo.continuity_status()["future_reserved"] == 79


def test_cold_failure_and_correction_share_slot_then_return_tomorrow(setup):
    repo, clock = setup
    enroll(repo, 1)
    repo.configure_review_capacity(80, 20)
    session = daily(repo)
    submit(repo, session, correct=False)
    assert repo.today_review_ids() == []
    clock[0] += timedelta(days=1)
    assert repo.today_review_ids() == [1]
    with repo.connection() as db:
        assert db.execute("SELECT review_active FROM wrong_words").fetchone()[0] == 1


def test_active_overload_admits_no_old_and_reports_real_shortage(setup):
    repo, _ = setup
    enroll(repo, 95)
    add_old(repo, 10)
    repo.configure_review_capacity(80, 20)
    assert len(repo.today_review_ids()) == 80
    status = repo.continuity_status()
    assert status["return_waiting"] == 15
    assert status["old_waiting"] == 10


def test_cross_midnight_reading_is_not_cold_recall_but_normal_overnight_is(setup):
    repo, clock = setup
    enroll(repo, 1)
    first = daily(repo)
    first.view_state['memory']['began_at'] = '2026-10-16T10:00:00+08:00'
    commit(repo, first, False, datetime.fromisoformat('2026-10-16T12:00:00+08:00'))
    repo.record_exposure(['word0'], 'reading', '2026-10-16T15:58:00+00:00')
    clock[0] += timedelta(days=1)
    warm = daily(repo)
    warm.view_state['memory']['began_at'] = '2026-10-17T00:01:00+08:00'
    commit(repo, warm, True, datetime.fromisoformat('2026-10-17T00:02:00+08:00'))
    assert events(repo)[-1]['rating'] is None
    assert events(repo)[-1]['kind'] == 'study'
    cold = new_long_term_session(repo.get_review_examples([1]))
    cold.view_state['memory']['began_at'] = '2026-10-17T10:00:00+08:00'
    commit(repo, cold, True, datetime.fromisoformat('2026-10-17T10:01:00+08:00'))
    assert events(repo)[-1]['kind'] == 'first'
    assert events(repo)[-1]['rating'] == 3


def test_another_view_reveal_during_ordinary_correction_invalidates_credit(setup):
    repo, _ = setup
    enroll(repo, 1)
    first = daily(repo)
    first.view_state['memory']['began_at'] = '2026-10-16T10:00:00+08:00'
    commit(repo, first, False, datetime.fromisoformat('2026-10-16T12:00:00+08:00'))
    retry = new_wrong_session(first)
    retry.view_state['memory']['began_at'] = '2026-10-16T12:05:00+08:00'
    repo.record_exposure(['word0'], 'another-view', '2026-10-16T04:06:00+00:00')
    commit(repo, retry, True, datetime.fromisoformat('2026-10-16T12:10:00+08:00'))
    assert events(repo)[-1]['rating'] is None


def test_consolidation_is_small_resumable_and_bounded_per_word(setup):
    repo, _ = setup
    enroll(repo, 13)
    first = daily(repo)
    commit(repo, first, False, datetime(2026,10,16,10))
    retry = new_wrong_session(first)
    commit(repo, retry, False, datetime(2026,10,16,10,10))
    extra = repo.create_consolidation(retry)
    assert extra.total == 10 and extra.view_state['consolidation']
    assert not extra.view_state['memory']['exposed']
    assert extra.view_state['memory']['parent_key'] == retry.session_key
    restarted = Repository(repo.database_path)
    restored = next(s for s in restarted.load_drafts() if s.session_key == extra.session_key)
    assert restored.target_ids == extra.target_ids and restored.view_state['consolidation']
    second_group = restarted.create_consolidation(retry)
    assert second_group.total == 3
    assert restarted.create_consolidation(retry) is None
    commit(repo, extra, False, datetime(2026,10,16,10,20))
    assert repo.create_consolidation(extra) is None
    assert repo.review_status()['completed'] == 13
    assert repo.today_review_ids() == []


def test_enabled_policy_is_idempotent_and_never_changes_original_memory_dates(setup):
    repo, _ = setup
    enroll(repo, 3)
    with repo.connection() as db:
        before = [tuple(r) for r in db.execute('SELECT id,review_due_date,fsrs_model,fsrs_stability,fsrs_last_at FROM wrong_words')]
    repo.enable_review_continuity()
    with repo.connection() as db:
        assert before == [tuple(r) for r in db.execute('SELECT id,review_due_date,fsrs_model,fsrs_stability,fsrs_last_at FROM wrong_words')]


def test_midnight_short_recall_is_relearning_and_does_not_finish_next_day_plan(setup):
    repo, clock = setup
    enroll(repo, 1)
    first = daily(repo)
    first.view_state['memory']['began_at'] = '2026-10-16T23:50:00+08:00'
    commit(repo, first, False, datetime.fromisoformat('2026-10-16T23:58:00+08:00'))
    clock[0] += timedelta(days=1)
    assert repo.today_review_ids() == [1]
    short = new_long_term_session(repo.get_review_examples([1]))
    short.view_state['memory']['began_at'] = '2026-10-17T00:01:00+08:00'
    commit(repo, short, True, datetime.fromisoformat('2026-10-17T00:02:00+08:00'))
    assert events(repo)[-1]['kind'] == 'relearning'
    assert repo.review_status()['remaining'] == 1


def test_append_today_only_and_finished_slots_do_not_refill(setup):
    repo, _ = setup
    enroll(repo, 100)
    repo.configure_review_capacity(40, 0)
    ids = repo.today_review_ids(start=True)
    assert repo.append_review_capacity(20, 0) == 20
    assert repo.today_review_ids()[:40] == ids
    assert repo.review_capacity() == (40, 0)
    assert repo.review_capacity(today=True) == (60, 0)
    submit(repo, daily(repo), correct=False)
    assert repo.today_review_ids() == []
    repo.configure_review_capacity(60, 0, today_only=True)
    assert repo.today_review_ids() == []


def test_invalid_new_capacity_changes_are_atomic(setup):
    repo, _ = setup
    enroll(repo, 3)
    before = repo.review_capacity()
    for total, old in ((0,0), (True,0), (80,81), (80,-1), (10001,0)):
        with pytest.raises(ValueError): repo.configure_review_capacity(total, old)
        assert repo.review_capacity() == before


def test_same_day_future_read_does_not_erase_an_answer_entered_before_it(setup):
    repo, _ = setup
    enroll(repo, 1)
    session = daily(repo)
    item_id = session.target_ids[0]
    session.view_state['memory'].update(began_at='2026-10-16T10:00:00+08:00',
        answered_at={item_id: '2026-10-16T10:01:00+08:00'})
    repo.record_exposure(['word0'], 'later-other-view', '2026-10-16T02:02:00+00:00')
    commit(repo,session,True,datetime.fromisoformat('2026-10-16T10:03:00+08:00'))
    assert events(repo)[-1]['kind'] == 'first'
    assert events(repo)[-1]['rating'] == 3
