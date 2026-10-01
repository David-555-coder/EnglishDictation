from copy import deepcopy
from datetime import date, timedelta
import sqlite3
from pathlib import Path

import pytest

from test_daily_review import clock, seed, daily, submit, word
from test_library_ui import ui
from src.database.repository import Repository
from src.models.practice import PracticeMode
from src.services.daily_review import review_transition, review_status_text
from src.services.practice_service import new_long_term_session


@pytest.mark.parametrize("stage", range(5))
@pytest.mark.parametrize("correct", [False, True])
def test_normal_transition(stage, correct):
    new_stage, confirmation, interval = review_transition(stage, False, correct)
    if correct:
        assert (new_stage, confirmation, interval) == (min(stage+1,4), False, (1,3,7,14,30)[min(stage+1,4)])
    elif stage >= 2:
        assert (new_stage, confirmation, interval) == (stage, True, 1)
    else:
        assert (new_stage, confirmation, interval) == (0, False, 1)


@pytest.mark.parametrize("stage", [2,3,4])
@pytest.mark.parametrize("correct", [False,True])
def test_confirmation_persists_and_uses_actual_result(clock, tmp_path, stage, correct):
    repo = Repository(tmp_path / 'db'); seed(repo)
    with repo.connection() as conn:
        conn.execute('UPDATE wrong_words SET review_stage=?', (stage,))
    submit(repo, daily(repo), correct=False)
    assert word(repo)['review_stage'] == stage
    assert word(repo)['review_relearning'] == 1
    for _ in range(3):
        manual = new_long_term_session(repo.get_review_examples([1]))
        submit(repo, manual)
    assert word(repo)['review_relearning'] == 1
    assert word(repo)['review_stage'] == stage
    clock[0] += timedelta(days=1)
    repo = Repository(repo.database_path)
    session = daily(repo); session.viewed_answer = True
    stale = deepcopy(session); stale.session_key = 'second-submission'
    submit(repo, session, correct=correct)
    expected = stage-1 if correct else 0
    assert word(repo)['review_stage'] == expected
    assert word(repo)['review_relearning'] == 0
    assert word(repo)['review_due_date'] == (clock[0]+timedelta(days=(1,3,7,14,30)[expected])).isoformat()
    snapshot = word(repo)
    repo.save_submitted_session(session)
    submit(repo, stale, correct=correct)
    for key in ('review_stage','review_relearning','review_due_date','review_correct_count','review_wrong_count'):
        assert word(repo)[key] == snapshot[key]


def test_manual_errors_request_but_do_not_consume_confirmation(clock, tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_stage=4, review_due_date='2026-10-01'")
    for _ in range(3):
        session = new_long_term_session(repo.get_review_examples([1]))
        submit(repo, session, correct=False)
    assert word(repo)['review_stage'] == 4 and word(repo)['review_relearning'] == 1
    assert word(repo)['review_due_date'] == '2026-09-16'
    assert word(repo)['review_wrong_count'] == 0


@pytest.mark.parametrize('limit', [20,40,60,80])
def test_mixed_allocation_and_cap(clock, tmp_path, limit):
    repo = Repository(tmp_path/'db'); seed(repo, 200)
    repo.set_review_limit(limit)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-01',last_error_at='2026-09-01' WHERE id<=100")
    ids = repo.today_review_ids(start=True)
    assert len(ids) == len(set(ids)) == limit
    assert sum(i<=100 for i in ids) == limit*7//10
    assert sum(i>100 for i in ids) == limit-limit*7//10
    submit(repo, daily(repo))
    assert repo.today_review_ids() == []
    assert repo.review_status()['completed'] == limit
    assert f'今日上限{limit}词' in review_status_text(repo.review_status(), repo.review_limit(today=True))


def test_no_recent_errors_fills_oldest_and_skips_invalid(clock, tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo, 90)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET last_error_at='2026-08-01'")
        conn.execute("UPDATE word_examples SET sentence='invalid' WHERE wrong_word_id>85")
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-16' WHERE id=85")
    assert repo.today_review_ids() == list(range(84,44,-1))
    assert len(repo.list_wrong_words()) == 90


def test_recent_pool_excludes_resolved_errors_and_outside_seven_dates(clock, tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo, 100)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-01',last_error_at='2026-09-01' WHERE id<=80")
        conn.execute("UPDATE wrong_words SET last_error_at='2026-09-08' WHERE id BETWEEN 81 AND 90")
        conn.execute("UPDATE wrong_words SET last_error_at='2026-09-14',review_last_date='2026-09-15' WHERE id>90")
    assert all(i<=80 for i in repo.today_review_ids())


def test_confirmation_update_rolls_back_on_failed_save(clock, tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo)
    with repo.connection() as conn:
        conn.execute('UPDATE wrong_words SET review_stage=4,review_relearning=1')
        conn.execute("CREATE TRIGGER fail_confirm BEFORE UPDATE ON daily_review_items BEGIN SELECT RAISE(ABORT,'fail'); END")
    session = daily(repo)
    with pytest.raises(sqlite3.IntegrityError):
        submit(repo, session)
    assert word(repo)['review_stage']==4 and word(repo)['review_relearning']==1
    assert word(repo)['review_due_date']=='2026-09-15'
    assert repo.list_history()==[]
    with repo.connection() as conn:
        conn.execute('DROP TRIGGER fail_confirm')
    repo.save_submitted_session(session)
    assert word(repo)['review_stage']==3 and word(repo)['review_relearning']==0


def test_limit_change_transaction_rollback(clock, tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo, 70)
    ids = repo.today_review_ids()
    with repo.connection() as conn:
        conn.execute("CREATE TRIGGER fail_limit BEFORE DELETE ON daily_review_items BEGIN SELECT RAISE(ABORT,'fail'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_review_limit(60)
    assert repo.today_review_ids()==ids and repo.review_limit()==40


def test_limit_preview_change_then_freeze_restart_and_next_day(clock, tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo, 120)
    assert len(repo.today_review_ids()) == 40
    assert repo.set_review_limit(60)
    assert len(repo.today_review_ids()) == 60
    assert repo.set_review_limit(20)
    selected = repo.today_review_ids(start=True)
    assert len(selected) == 20
    assert not repo.set_review_limit(80)
    repo = Repository(repo.database_path)
    assert repo.today_review_ids() == selected
    assert repo.review_limit(today=True) == 20 and repo.review_limit() == 80
    submit(repo, daily(repo))
    assert not repo.set_review_limit(60)
    assert repo.today_review_ids() == []
    clock[0] += timedelta(days=1)
    assert len(repo.today_review_ids()) == 60


def test_draft_also_freezes_preview(clock, tmp_path):
    repo = Repository(tmp_path/'db'); seed(repo, 70)
    session = daily(repo); repo.save_draft(session)
    assert not repo.set_review_limit(20)
    assert len(repo.today_review_ids()) == 40


@pytest.mark.parametrize('invalid', [0, 41, '60', None, True])
def test_invalid_limit_does_not_modify_plan(clock, tmp_path, invalid):
    repo = Repository(tmp_path/'db'); seed(repo)
    ids = repo.today_review_ids()
    with pytest.raises(ValueError):
        repo.set_review_limit(invalid)
    assert repo.today_review_ids() == ids and repo.review_limit() == 40


def test_settings_ui_before_and_after_start(clock, tmp_path, ui, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: messages.append(a[2]))
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('60', True))
    repo = Repository(tmp_path/'db'); seed(repo, 100)
    window = ui(repo)
    window.configure_review_limit()
    assert window.actions['今日复习'].text() == '今日复习（60）'
    assert '今天生效' in messages[-1]
    window.start_daily_review()
    first = window.session.target_ids[0]
    window.answer_edits[first].setText('kept input')
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('20', True))
    window.configure_review_limit()
    assert '从明天生效' in messages[-1]
    assert window.session.total == 60 and window.answer_edits[first].text() == 'kept input'
    assert len(repo.today_review_ids()) == 60


def test_current_upgrade_preserves_old_data_and_freezes_old_plan(clock, tmp_path):
    source_path = Path('backups/before-review-balance-20260926/english_dictation.db')
    if not source_path.exists():
        pytest.skip('owner database snapshot unavailable')
    path = tmp_path/'db'
    with sqlite3.connect(source_path.resolve().as_uri()+'?mode=ro', uri=True) as source:
        with sqlite3.connect(path) as target:
            source.backup(target)
        tables = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        original = {t: source.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}
        columns = {t: ','.join('"'+r[1]+'"' for r in source.execute(f'PRAGMA table_info("{t}")')) for t in tables}
        last_day = source.execute('SELECT max(day) FROM daily_review_days').fetchone()[0]
    clock[0] = date.fromisoformat(last_day)
    repo = Repository(path)
    with repo.connection() as conn:
        for table, rows in original.items():
            assert [tuple(r) for r in conn.execute(f'SELECT {columns[table]} FROM "{table}" ORDER BY rowid')] == rows
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert len(list((tmp_path/'migration_backups').glob('*.db'))) == 1
    ids = repo.today_review_ids()
    assert not repo.set_review_limit(80)
    assert repo.today_review_ids() == ids
    repo = Repository(path)
    assert len(list((tmp_path/'migration_backups').glob('*.db'))) == 1
    clock[0] += timedelta(days=1)
    session = daily(repo)
    assert 0 < session.total <= 80
    submit(repo, session)
    assert Repository(path).today_review_ids() == []
