from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from test_daily_review import clock, seed, daily, submit
from src.database.repository import Repository


def setup_pools(repo):
    seed(repo, 180)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-01',last_error_at='2026-08-20' WHERE id<=100")
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-10',last_error_at='2026-08-21',review_stage=3,review_relearning=1 WHERE id BETWEEN 101 AND 120")
        conn.execute("UPDATE wrong_words SET last_error_at='2026-09-14' WHERE id BETWEEN 121 AND 150")
        conn.execute("UPDATE wrong_words SET last_error_at='2026-09-15',review_stage=2 WHERE id>150")


@pytest.mark.parametrize('limit', [20,40,60,80])
def test_timely_capacity_is_protected_from_old_absolute_priority(clock, tmp_path, limit):
    repo=Repository(tmp_path/'db'); setup_pools(repo)
    repo.set_review_limit(limit)
    with repo.connection() as conn:
        before=[tuple(r) for r in conn.execute('SELECT * FROM wrong_words ORDER BY id')]
    ids=repo.today_review_ids()
    assert len(ids)==len(set(ids))==limit
    assert sum(i > 120 for i in ids) == min(60, limit-limit//5)
    assert sum(i <= 100 for i in ids) == limit-min(60, limit-limit//5)
    with repo.connection() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM wrong_words ORDER BY id')]==before


def test_timely_appointments_precede_old_errors_and_skip_future_words(clock, tmp_path):
    repo=Repository(tmp_path/'db'); setup_pools(repo)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_relearning=0,review_due_date='2026-09-16' WHERE id BETWEEN 101 AND 120")
    ids=repo.today_review_ids()
    assert sum(i > 120 for i in ids) == 32
    assert sum(i <= 100 for i in ids) == 8
    assert not set(ids)&set(range(101,121))  # Not yet due.


def test_fallback_and_invalid_confirmation_are_retained(clock, tmp_path):
    repo=Repository(tmp_path/'db'); setup_pools(repo)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-16' WHERE id BETWEEN 101 AND 116 OR id BETWEEN 121 AND 146")
        conn.execute("UPDATE word_examples SET sentence='missing answer' WHERE wrong_word_id=120")
    ids=repo.today_review_ids()
    assert len(ids)==40 and sum(i > 120 for i in ids)==32
    assert 120 not in ids
    with repo.connection() as conn:
        row=conn.execute('SELECT review_relearning,review_due_date FROM wrong_words WHERE id=120').fetchone()
        assert tuple(row)==(1,'2026-09-10')


def test_existing_started_plan_stays_fixed_until_next_day(clock, tmp_path):
    repo=Repository(tmp_path/'db'); setup_pools(repo)
    ids=repo.today_review_ids(start=True)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_relearning=1,review_stage=4 WHERE id=180")
    assert Repository(repo.database_path).today_review_ids()==ids
    submit(repo,daily(repo))
    assert repo.today_review_ids()==[]
    clock[0]+=timedelta(days=1)
    next_ids=repo.today_review_ids()
    assert not set(next_ids) & set(ids)
    assert sum(i>120 for i in next_ids)==28
    assert len(next_ids)==40


def test_real_snapshot_plans_and_learning_rows_preserved(tmp_path):
    source_path=Path('backups/before-confirm-consolidation-20260927/english_dictation.db')
    if not source_path.exists():
        pytest.skip('owner database snapshot unavailable')
    path=tmp_path/'db'
    with sqlite3.connect(source_path.resolve().as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(path) as target:
            source.backup(target)
        original={t:source.execute(f'SELECT * FROM {t} ORDER BY rowid').fetchall()
                  for t in ('wrong_words','practice_sessions','practice_answers','daily_review_days','daily_review_items')}
    repo=Repository(path)
    with repo.connection() as conn:
        for table,rows in original.items():
            width = len(rows[0]) if rows else 0
            assert [tuple(r)[:width] for r in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]==rows
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
