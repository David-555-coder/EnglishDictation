from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from src.database.repository import Repository
from src.models.practice import PracticeMode
from src.services.daily_review import new_daily_session
from src.services.grading import grade_session
from src.services.practice_service import new_saved_wrong_session, new_long_term_session, new_wrong_session, new_full_session
from test_library_ui import ui


@pytest.fixture
def clock(monkeypatch):
    clock = [date(2026, 9, 15)]
    monkeypatch.setattr(Repository, "today", staticmethod(lambda: clock[0]))
    return clock


def seed(repo, count=1, due="2026-09-15"):
    # Synthetic due tasks are explicitly enrolled in maintenance. Legacy/backlog
    # migration and independent quotas have dedicated regression fixtures.
    with repo.connection() as conn:
        for i in range(count):
            answer = f"word{i}"
            cur = conn.execute("""INSERT INTO wrong_words
                (answer, normalized_answer, error_count, first_error_at, last_error_at, review_due_date, review_pool,review_active)
                VALUES (?,?,?, '2026-09-01', '2026-09-14', ?, 'maintenance',1)""", (answer, answer, i+1, due))
            conn.execute("""INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence)
                VALUES (?, ?, ?, ?)""", (cur.lastrowid, f"a{i%4}.docx", f"a{i%4}.docx", f"Learn {answer} now."))


def daily(repo):
    return new_daily_session(repo.get_review_examples(repo.today_review_ids()), repo.today().isoformat())


def submit(repo, session, correct=True, day=None):
    if correct:
        session.answers = {i: session.article.item_map[i].answer for i in session.target_ids}
    grade_session(session)
    session.submitted_at = datetime.combine(day or repo.today(), datetime.min.time())
    repo.save_submitted_session(session)


def word(repo):
    return repo.list_wrong_words()[0]


def test_intervals_and_restart(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    from src.services.fsrs7 import MemoryState, next_state
    previous = None
    last_day = None
    for _ in range(5):
        session = daily(repo)
        assert session.total == 1
        submit(repo, session)
        expected = next_state(previous,3,(clock[0]-last_day).days if last_day else 0)
        record = word(repo)
        assert record['fsrs_stability'] == pytest.approx(expected.stability)
        assert record['fsrs_stability_fast'] == pytest.approx(expected.stability_fast)
        assert record['review_due_date'] > clock[0].isoformat()
        assert repo.today_review_ids() == []
        repo = Repository(repo.database_path)
        assert word(repo)['fsrs_stability'] == record['fsrs_stability']
        previous, last_day = expected, clock[0]
        clock[0] = date.fromisoformat(record['review_due_date'])
    assert word(repo)["review_correct_count"] == 5


@pytest.mark.parametrize("viewed", [False, True])
def test_failure_returns_tomorrow_regardless_of_viewing(clock, tmp_path, viewed):
    repo = Repository(tmp_path / "db"); seed(repo)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_stage=3")
    session = daily(repo); session.viewed_answer = viewed
    submit(repo, session, correct=False)
    assert word(repo)["review_stage"] == 0
    assert word(repo)["review_relearning"] == 1
    assert word(repo)["review_due_date"] == "2026-09-16"
    assert word(repo)["review_wrong_count"] == 1
    assert repo.today_review_ids() == []


def test_viewed_answer_does_not_penalize_correct_review(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo)
    session = daily(repo)
    session.viewed_answer = True  # Legacy draft flag must also be ignored.
    submit(repo, session)
    assert word(repo)["review_stage"] == 1
    assert word(repo)["review_due_date"] == "2026-09-19"
    assert word(repo)["review_correct_count"] == 1
    assert word(repo)["review_wrong_count"] == 0
    assert word(repo)["consecutive_correct"] == 1
    assert repo.list_history()[0]["viewed_answer"] == 0
    repo.save_submitted_session(session)
    assert word(repo)["review_correct_count"] == 1


def test_73_due_fixed_40_no_refill_priority_and_next_day(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo, 73)
    ids = repo.today_review_ids()
    assert len(ids) == 40 and ids == list(range(73,33,-1))
    session = daily(repo)
    assert len({i.filename for i in session.article.word_items}) == 4
    submit(repo, session)
    assert repo.today_review_ids() == []
    assert Repository(repo.database_path).today_review_ids() == []
    clock[0] += timedelta(days=1)
    assert len(repo.today_review_ids()) == 33


@pytest.mark.parametrize("mode", list(PracticeMode))
def test_legacy_view_flag_uses_actual_mixed_results(clock, tmp_path, mode):
    repo = Repository(tmp_path / "db"); seed(repo, 2)
    session = daily(repo)
    session.mode = mode
    session.viewed_answer = True
    first = session.target_ids[0]
    correct_word = session.article.item_map[first].normalized_answer
    session.answers[first] = session.article.item_map[first].answer
    submit(repo, session, correct=False)
    assert session.correct_count == 1 and session.wrong_count == 1
    for row in repo.list_wrong_words():
        correct = row["normalized_answer"] == correct_word
        assert row["consecutive_correct"] == int(correct)
        assert row["review_stage"] == int(correct)
        assert row["review_correct_count"] == int(correct)
        assert row["review_wrong_count"] == int(not correct)


def test_current_database_copy_and_legacy_view_flag(clock, tmp_path):
    source_path = Path("backups/before-answer-visibility-only-20260920/english_dictation.db")
    if not source_path.exists():
        pytest.skip("owner's database backup is not available")
    path = tmp_path / "db"
    with sqlite3.connect(source_path.resolve().as_uri()+"?mode=ro", uri=True) as source:
        with sqlite3.connect(path) as target:
            source.backup(target)
        tables = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        original = {t: source.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}
        columns = {t: ','.join('"'+r[1]+'"' for r in source.execute(f'PRAGMA table_info("{t}")')) for t in tables}
    clock[0] = date(2026, 9, 21)
    repo = Repository(path)
    with repo.connection() as conn:
        for table, rows in original.items():
            assert [tuple(r) for r in conn.execute(f'SELECT {columns[table]} FROM "{table}" ORDER BY rowid')] == rows
    assert repo.load_articles()[0]
    repo.configure_review_budgets(40, 40)
    session = daily(repo)
    assert 0 < session.total <= 40
    session.viewed_answer = True
    submit(repo, session)
    reopened = Repository(path)
    assert reopened.today_review_ids() == []
    with reopened.connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT count(*) FROM wrong_words WHERE review_last_date='2026-09-21' AND review_due_date>'2026-09-22'").fetchone()[0] == session.total


def test_unselected_overdue_words_keep_state_and_precede_new_due_words(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 70)
    selected = repo.today_review_ids()
    unselected = set(range(1, 71)) - set(selected)
    assert len(selected) == 40 and len(unselected) == 30
    placeholders = ",".join("?" for _ in unselected)
    query = f"""SELECT id, review_stage, review_last_date, review_due_date,
        review_correct_count, review_wrong_count FROM wrong_words
        WHERE id IN ({placeholders}) ORDER BY id"""
    with repo.connection() as conn:
        before = {row["id"]: tuple(row) for row in conn.execute(query, sorted(unselected))}
    submit(repo, daily(repo))
    with repo.connection() as conn:
        after = {row["id"]: tuple(row) for row in conn.execute(query, sorted(unselected))}
    assert after == before

    clock[0] += timedelta(days=1)
    with repo.connection() as conn:
        for index in range(20):
            answer = f"new-word{index}"
            cursor = conn.execute("""INSERT INTO wrong_words
                (answer, normalized_answer, error_count, first_error_at, last_error_at, review_due_date, review_pool)
                VALUES (?,?,1000,'2026-09-01','2026-09-16',?,'maintenance')""",
                (answer, answer, clock[0].isoformat()))
            conn.execute("""INSERT INTO word_examples
                (wrong_word_id,source_article,source_path,sentence) VALUES (?,?,?,?)""",
                (cursor.lastrowid, "new.docx", "new.docx", f"Learn {answer} now."))
        conn.execute("UPDATE wrong_words SET error_count=1000 WHERE id>70")
    next_ids = repo.today_review_ids()
    assert len(next_ids) == 40
    assert unselected.issubset(set(next_ids))
    assert len(set(next_ids) - unselected) == 10
    with repo.connection() as conn:
        remaining = {row["id"]: tuple(row) for row in conn.execute(query, sorted(unselected))}
        for word_id in unselected - set(next_ids):
            assert remaining[word_id] == before[word_id]


def test_retries_manual_success_and_stale_drafts_do_not_advance(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo)
    session = daily(repo)
    stale = deepcopy(session)
    stale.session_key = "another-draft"
    repo.save_draft(session)
    assert repo.load_drafts()[0].view_state["daily_review_day"] == "2026-09-15"
    assert repo.load_drafts()[0].mode == PracticeMode.DAILY_REVIEW
    submit(repo, session)
    repo.save_submitted_session(session)
    submit(repo, stale)
    for _ in range(5):
        manual = new_long_term_session(repo.get_review_examples([word(repo)["id"]]))
        submit(repo, manual)
    assert word(repo)["review_stage"] == 1
    assert word(repo)["review_correct_count"] == 1
    assert word(repo)["review_due_date"] == "2026-09-19"
    clock[0] += timedelta(days=1)
    old = deepcopy(stale); old.session_key = "cross-day"; old.submitted = False
    submit(repo, old)
    assert word(repo)["review_correct_count"] == 1


def test_ordinary_wrong_enters_tomorrow_and_relearning_shortens(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    records = [dict(id=1, answer="word", paragraph_text="A word.", source_article="a.docx")]
    session = new_saved_wrong_session(dict(id=1), records)
    session.mode = PracticeMode.FULL_ARTICLE
    submit(repo, session, correct=False)
    assert word(repo)["review_due_date"] == "2026-09-16"
    assert repo.today_review_ids() == []
    clock[0] += timedelta(days=1)
    submit(repo, daily(repo))
    before = word(repo)["review_correct_count"]
    submit(repo, new_full_session(session.article))
    assert word(repo)["review_correct_count"] == before
    submit(repo, new_wrong_session(session), correct=False)
    assert word(repo)["review_relearning"] == 1
    assert word(repo)["review_due_date"] == "2026-09-17"


def test_legacy_migration_spreads_priority_and_preserves_tables(clock, tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE wrong_words (id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_answer TEXT NOT NULL UNIQUE, answer TEXT NOT NULL, chinese TEXT NOT NULL DEFAULT '',
            error_count INTEGER NOT NULL DEFAULT 0, occurrence_count INTEGER NOT NULL DEFAULT 0,
            first_error_at TEXT NOT NULL, last_error_at TEXT NOT NULL)""")
        for i in range(121):
            conn.execute("""INSERT INTO wrong_words (normalized_answer,answer,error_count,first_error_at,last_error_at)
                VALUES (?,?,?,'2026-09-01',?)""", (f"word{i}", f"word{i}", i, "2026-09-14" if i<50 else "2026-09-01"))
        original = conn.execute("SELECT * FROM wrong_words").fetchall()
    repo = Repository(path)
    with repo.connection() as conn:
        preserved = [tuple(r) for r in conn.execute("SELECT id,normalized_answer,answer,chinese,error_count,occurrence_count,first_error_at,last_error_at FROM wrong_words")]
        assert preserved == original
        groups = conn.execute("SELECT review_due_date, count(*) FROM wrong_words GROUP BY review_due_date").fetchall()
        assert [r[1] for r in groups] == [40,40,40,1]
        first = [r[0] for r in conn.execute("SELECT answer FROM wrong_words WHERE review_due_date='2026-09-16' ORDER BY error_count DESC")]
        assert first == [f"word{i}" for i in range(120,80,-1)]
    assert repo.today_review_ids() == []
    assert len(list((tmp_path / "migration_backups").glob("*.db"))) == 1
    Repository(path)
    assert len(list((tmp_path / "migration_backups").glob("*.db"))) == 1


def test_real_database_copy_preserves_every_existing_column(clock, tmp_path):
    source_path = Path("backups/before-daily-review-20260915/english_dictation.db")
    if not source_path.exists():
        pytest.skip("real database backup only exists on the owner's computer")
    path = tmp_path / "data" / "test.db"; path.parent.mkdir()
    source = sqlite3.connect(source_path.resolve().as_uri()+"?mode=ro", uri=True)
    target = sqlite3.connect(path); source.backup(target); target.close()
    tables = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    original = {t: ([r[1] for r in source.execute(f'PRAGMA table_info("{t}")')], source.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()) for t in tables}
    source.close()
    repo = Repository(path)
    with repo.connection() as conn:
        for table, (columns, rows) in original.items():
            names = ','.join('"'+c+'"' for c in columns)
            assert [tuple(r) for r in conn.execute(f'SELECT {names} FROM "{table}" ORDER BY rowid')] == rows
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    articles, unresolved = repo.load_articles()
    assert articles and repo.list_wrong_words()
    assert repo.today_review_ids() == []
    clock[0] += timedelta(days=1)
    repo.configure_review_budgets(40, 40)
    session = daily(repo)
    assert 0 < session.total <= 40
    submit(repo, session)
    submit(repo, new_full_session(articles[0]), correct=False)
    record = repo.list_wrong_practice_history()[0]
    manual = new_saved_wrong_session(record, repo.get_wrong_answers(record['id']))
    submit(repo, manual)
    assert Repository(path).list_wrong_words()


def test_daily_ui_count_submit_draft_and_book(clock, tmp_path, ui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from src.ui.dialogs import WrongWordsDialog
    monkeypatch.setattr(QMessageBox, "information", lambda *a: QMessageBox.Ok)
    repo = Repository(tmp_path / "db"); seed(repo,73)
    window = ui(repo)
    assert window.actions["今日复习"].text() == "今日复习（40）"
    window.start_daily_review()
    assert window.session.total == 40 and len(window.answer_edits) == 40
    key = window.session.session_key
    first = window.session.target_ids[0]
    window.answer_edits[first].setText(window.session.article.item_map[first].answer)
    window.start_daily_review()
    assert window.session.session_key == key and window.answer_edits[first].text()
    for item_id, edit in window.answer_edits.items():
        edit.setText(window.session.article.item_map[item_id].answer)
    # Use the injected calendar in grading too.
    monkeypatch.setattr('src.ui.main_window.grade_session', lambda s: _grade_on(s, clock[0]))
    window._commit_submission()
    assert window.session.correct_count == 40
    assert window.actions["今日复习"].text() == "今日复习（0）"
    book = WrongWordsDialog(repo)
    assert book.table.columnCount() == 7
    assert book.table.horizontalHeaderItem(5).text() == "记忆状态"
    assert book.table.horizontalHeaderItem(6).text() == "复习安排"
    book.close()


def _grade_on(session, day):
    grade_session(session)
    session.submitted_at = datetime.combine(day, datetime.min.time())


def test_failed_save_rolls_back_schedule_and_retry(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo)
    session = daily(repo)
    with repo.connection() as conn:
        conn.execute("CREATE TRIGGER fail_review BEFORE UPDATE ON daily_review_items BEGIN SELECT RAISE(ABORT,'fail'); END")
    with pytest.raises(sqlite3.IntegrityError):
        submit(repo, session)
    assert word(repo)["review_stage"] == 0 and len(repo.today_review_ids()) == 1
    assert repo.list_history() == []
    with repo.connection() as conn:
        conn.execute("DROP TRIGGER fail_review")
    repo.save_submitted_session(session)
    assert word(repo)["review_stage"] == 1


def test_priority_reserves_recent_due_before_old_then_error_ties(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo, 3)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET error_count=5, last_error_at='2026-09-10'")
        conn.execute("UPDATE wrong_words SET last_error_at='2026-09-14' WHERE id=2")
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-01' WHERE id=3")
    assert repo.today_review_ids() == [2,1,3]


def test_deletion_does_not_refill_daily_limit_or_lose_backlog(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo,73)
    selected = repo.today_review_ids()
    repo.delete_wrong_word(selected[0])
    assert len(repo.today_review_ids()) == 39
    assert len(Repository(repo.database_path).today_review_ids()) == 39
    with repo.connection() as conn:
        assert conn.execute("SELECT count(*) FROM daily_review_items").fetchone()[0] == 40
    repo.clear_wrong_words()
    assert repo.today_review_ids() == []


def test_unusable_example_kept_but_not_counted(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo, 2)
    with repo.connection() as conn:
        conn.execute("UPDATE word_examples SET sentence='missing answer' WHERE wrong_word_id=2")
    assert repo.today_review_ids() == [1]
    assert len(repo.list_wrong_words()) == 2


def test_next_day_ui_refresh_and_zero_message(clock, tmp_path, ui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a[2]))
    repo = Repository(tmp_path / "db"); seed(repo, 1, '2026-09-16')
    window = ui(repo)
    window.start_daily_review()
    assert len(messages) == 1 and messages[0].startswith("今天没有需要复习的词。")
    assert "明天1词" in messages[0]
    clock[0] += timedelta(days=1)
    window._refresh_daily_review()
    assert window.actions["今日复习"].text() == "今日复习（1）"


def test_status_reads_do_not_create_plan_or_change_dates(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo, 70)
    with repo.connection() as conn:
        before = list(conn.iterdump())
    for _ in range(3):
        assert repo.review_status() == dict(completed=0, remaining=0, waiting=70)
    with repo.connection() as conn:
        assert list(conn.iterdump()) == before
    repo.today_review_ids()
    assert repo.review_status() == dict(completed=0, remaining=40, waiting=30)
    submit(repo, daily(repo))
    assert repo.review_status() == dict(completed=40, remaining=0, waiting=30)
    clock[0] += timedelta(days=1)
    assert repo.review_status() == dict(completed=0, remaining=0, waiting=30)
    assert len(repo.today_review_ids()) == 30
    assert repo.review_status() == dict(completed=0, remaining=30, waiting=0)


def test_completed_ui_reports_backlog_without_refill(clock, tmp_path, ui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a[2]))
    repo = Repository(tmp_path / "db"); seed(repo, 70)
    window = ui(repo); window.start_daily_review()
    for item_id, edit in window.answer_edits.items():
        edit.setText(window.session.article.item_map[item_id].answer)
    monkeypatch.setattr('src.ui.main_window.grade_session', lambda s: _grade_on(s, clock[0]))
    window._commit_submission()
    assert "今日已提交：40词\n待完成：0词" in window.progress_text.text()
    assert "还有30词已到复习时间" in window.progress_text.text()
    assert "另有30词已到复习时间" in window.actions["今日复习"].toolTip()
    window.start_daily_review()
    assert "今日已提交40词，待完成0词" in messages[-1]
    assert "另有30词已到复习时间" in messages[-1]
    assert repo.today_review_ids() == []


@pytest.mark.parametrize("value,expected", [(None,"待安排"),("2026-09-13","到期待安排"),
    ("2026-09-15","到期待安排"),("2026-09-16","明天"),("2026-09-18","09-18")])
def test_due_labels_show_review_plan(value, expected):
    from src.services.daily_review import due_label
    assert due_label(value, date(2026,9,15)) == expected
    if value and value <= "2026-09-15":
        assert due_label(value, date(2026,9,15), planned=True) == "今日待复习"


def test_status_deleted_slots_and_unusable_words(clock, tmp_path):
    repo = Repository(tmp_path / "db"); seed(repo, 3)
    with repo.connection() as conn:
        conn.execute("UPDATE word_examples SET sentence='missing' WHERE wrong_word_id=3")
    ids = repo.today_review_ids()
    repo.delete_wrong_word(ids[0])
    assert repo.review_status() == dict(completed=0, remaining=1, waiting=1)


def test_status_messages_distinguish_empty_and_completed():
    from src.services.daily_review import review_status_text
    assert review_status_text(dict(completed=0, remaining=0, waiting=0)) == "今天没有需要复习的词。"
    assert review_status_text(dict(completed=20, remaining=0, waiting=0)) == "今日20词已完成。"
    assert "今日已完成10词，尚有30词待完成" in review_status_text(dict(completed=10, remaining=30, waiting=5))
    assert "没有已安排" in review_status_text(dict(completed=0, remaining=0, waiting=5))
