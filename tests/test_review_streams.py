from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest

from src.database.repository import Repository
from src.services.daily_review import select_review_ids
from src.services.fsrs7 import MODEL_ID, next_state
from src.services.practice_service import new_long_term_session
from test_daily_review import clock, seed, submit
from test_library_ui import ui


def model_word(repo, word_id=1, due="2026-09-14", pool="maintenance"):
    state = next_state(None, 3)
    with repo.connection() as conn:
        conn.execute("""UPDATE wrong_words SET fsrs_model=?,fsrs_stability=?,fsrs_difficulty=?,
            fsrs_stability_fast=?,fsrs_last_at='2026-09-10T12:00:00+08:00',
            review_due_date=?,review_pool=? WHERE id=?""",
            (MODEL_ID, state.stability, state.difficulty, state.stability_fast, due, pool, word_id))


def test_backlog_cannot_displace_modeled_maintenance():
    state = next_state(None, 3)
    common = dict(error_count=1, last_error_at="2026-09-25", review_relearning=0)
    maintenance = dict(common, id=1, review_due_date="2026-10-03", fsrs_model=MODEL_ID,
        fsrs_last_at="2026-09-29T12:00:00+08:00", fsrs_stability=state.stability,
        fsrs_difficulty=state.difficulty, fsrs_stability_fast=state.stability_fast)
    backlog = dict(common, id=2, review_due_date="2026-09-23", fsrs_model=None)
    assert select_review_ids([maintenance, backlog], 1, date(2026, 10, 4), lambda _: True) == [1]


def test_independent_budgets_custom_numbers_and_no_future_fill(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 80)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_pool='backlog'")
    for word_id in range(1, 4):
        model_word(repo, word_id)
    repo.configure_review_budgets(41, 7)
    selected = repo.today_review_ids()
    assert len(selected) == 10
    assert set(range(1, 4)).issubset(selected)
    assert repo.review_budgets() == (41, 7)
    assert repo.review_stream_status()["backlog_remaining"] == 7
    assert repo.review_stream_status()["maintenance_remaining"] == 3
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_due_date='2026-09-16' WHERE review_pool='backlog'")
    repo.configure_review_budgets(41, 7)  # Replace an unstarted preview only.
    assert set(repo.today_review_ids()) == {1, 2, 3}


def test_zero_backlog_budget_never_borrows_maintenance_slots(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 5)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_pool='backlog'")
    model_word(repo)
    assert repo.review_budgets() == (40, 0)
    assert repo.today_review_ids() == [1]
    assert repo.review_stream_status()["backlog_waiting"] == 4


def test_started_plan_append_does_not_reorder_remove_or_duplicate(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 80)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_pool='backlog' WHERE id>50")
    repo.configure_review_budgets(20, 5)
    initial = repo.today_review_ids(start=True)
    session = new_long_term_session(repo.get_review_examples(initial))
    repo.save_draft(session)
    result = repo.configure_review_budgets(41, 9, today_only=True)
    expanded = repo.today_review_ids()
    assert expanded[:25] == initial
    assert len(expanded) == len(set(expanded)) == 50
    assert result["added"] == 25
    assert repo.review_budgets() == (20, 5)
    assert repo.review_budgets(today=True) == (41, 9)
    repo.configure_review_budgets(10, 0, today_only=True)
    assert repo.today_review_ids() == expanded
    assert Repository(repo.database_path).today_review_ids() == expanded
    clock[0] += timedelta(days=1)
    assert repo.review_budgets(today=True) == (20, 5)


def test_completed_plan_only_appends_on_explicit_request(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 80)
    repo.configure_review_budgets(20, 0)
    initial = repo.today_review_ids(start=True)
    from src.services.daily_review import new_daily_session
    session = new_daily_session(repo.get_review_examples(initial), repo.today().isoformat())
    submit(repo, session)
    assert repo.today_review_ids() == []
    added = repo.append_today_reviews(maintenance=13, backlog=0)
    assert added == 13
    assert len(repo.today_review_ids()) == 13
    assert not set(repo.today_review_ids()) & set(initial)
    assert repo.review_budgets() == (20, 0)
    assert repo.review_status()["completed"] == 20


def test_effective_review_promotes_backlog_without_reinitializing(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    model_word(repo, pool="backlog")
    before = repo.list_wrong_words()[0]
    session = new_long_term_session(repo.get_review_examples([1]))
    submit(repo, session)
    row = repo.list_wrong_words()[0]
    assert row["review_pool"] == "maintenance"
    assert row["fsrs_stability"] > before["fsrs_stability"]
    assert row["fsrs_reviews"] == before["fsrs_reviews"] + 1
    clock[0] += timedelta(days=120)
    reopened = Repository(repo.database_path)
    assert reopened.list_wrong_words()[0]["review_pool"] == "maintenance"
    assert reopened.today_review_ids() == [1]


def test_reading_success_does_not_promote_backlog(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_pool='backlog'")
    session = new_long_term_session(repo.get_review_examples([1]))
    session.view_state["memory"]["phase"] = "study"
    submit(repo, session)
    assert repo.list_wrong_words()[0]["review_pool"] == "backlog"
    assert repo.list_wrong_words()[0]["fsrs_model"] is None


def test_migration_assigns_once_and_preserves_old_columns_and_plans(clock, tmp_path):
    path = tmp_path / "db"
    repo = Repository(path)
    seed(repo, 3)
    model_word(repo, 1, due="2026-09-14")
    model_word(repo, 2, due="2026-09-01")
    repo.today_review_ids(start=True)
    with repo.connection() as conn:
        conn.execute("DROP INDEX IF EXISTS wrong_words_review_pool_due")
        conn.execute("ALTER TABLE wrong_words DROP COLUMN review_pool")
        conn.execute("ALTER TABLE daily_review_days DROP COLUMN backlog_limit")
        conn.execute("ALTER TABLE daily_review_items DROP COLUMN review_pool")
        originals = {t: (tuple(r[1] for r in conn.execute(f'PRAGMA table_info({t})')),
                          [tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY rowid')])
            for t in ("wrong_words", "daily_review_days", "daily_review_items")}
    repo = Repository(path)
    assert [r["review_pool"] for r in sorted(repo.list_wrong_words(), key=lambda r: r["id"])] == [
        "maintenance", "backlog", "backlog"]
    with repo.connection() as conn:
        for table, (columns, rows) in originals.items():
            names = ",".join(columns)
            assert [tuple(r) for r in conn.execute(f'SELECT {names} FROM {table} ORDER BY rowid')] == rows
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    clock[0] += timedelta(days=30)
    reopened = Repository(path)
    assert {r["id"]: r["review_pool"] for r in reopened.list_wrong_words()} == {
        1: "maintenance", 2: "backlog", 3: "backlog"}
    assert len(list((tmp_path / "migration_backups").glob("*.db"))) == 1


@pytest.mark.parametrize("normal,backlog", [(0,0), (-1,0), (True,2), (20,-1), (20,True), (10001,0)])
def test_invalid_budgets_are_atomic(clock, tmp_path, normal, backlog):
    repo = Repository(tmp_path / "db")
    seed(repo)
    ids = repo.today_review_ids()
    with pytest.raises(ValueError):
        repo.configure_review_budgets(normal, backlog)
    assert repo.today_review_ids() == ids
    assert repo.review_budgets() == (40,0)


def test_maintenance_orders_actual_recall_risk_then_confirmation_ties():
    rows = []
    for word_id, rating in ((1, 4), (2, 3), (3, 4)):
        state = next_state(None, rating)
        rows.append(dict(id=word_id, review_pool="maintenance", review_due_date="2026-09-14",
            error_count=1, last_error_at="2026-09-01", review_relearning=int(word_id == 3),
            fsrs_model=MODEL_ID, fsrs_last_at="2026-09-10T12:00:00+00:00",
            fsrs_stability=state.stability, fsrs_difficulty=state.difficulty,
            fsrs_stability_fast=state.stability_fast))
    ids = select_review_ids(rows, 3, date(2026, 9, 15), lambda _: True,
                            now=datetime(2026, 9, 15, 12, tzinfo=timezone.utc))
    # A confirmation flag no longer overrides a genuinely lower recall estimate.
    assert ids == [2, 3, 1]


def test_append_atomic_on_partial_insert_failure(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 80)
    repo.configure_review_budgets(20, 0)
    initial = repo.today_review_ids(start=True)
    with repo.connection() as conn:
        conn.execute("""CREATE TRIGGER fail_append BEFORE INSERT ON daily_review_items
            WHEN (SELECT count(*) FROM daily_review_items)>21
            BEGIN SELECT RAISE(ABORT,'test failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.configure_review_budgets(40, 20)
    assert repo.today_review_ids() == initial
    assert repo.review_budgets() == repo.review_budgets(today=True) == (20,0)


def test_promotion_atomic_with_grading(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    with repo.connection() as conn:
        conn.execute("UPDATE wrong_words SET review_pool='backlog'")
    repo.configure_review_budgets(40, 1)
    session = new_long_term_session(repo.get_review_examples(repo.today_review_ids()))
    with repo.connection() as conn:
        conn.execute("CREATE TRIGGER fail_complete BEFORE UPDATE ON daily_review_items BEGIN SELECT RAISE(ABORT,'fail'); END")
    with pytest.raises(sqlite3.IntegrityError):
        submit(repo, session)
    assert repo.list_wrong_words()[0]["review_pool"] == "backlog"
    assert repo.list_wrong_words()[0]["fsrs_model"] is None
    assert repo.list_history() == []


def test_status_is_read_only_and_deleted_slots_are_not_refilled(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 80)
    repo.configure_review_budgets(20,0)
    ids = repo.today_review_ids(start=True)
    repo.delete_wrong_word(ids[0])
    with repo.connection() as conn:
        before = list(conn.iterdump())
    status = repo.review_stream_status()
    assert status["maintenance_remaining"] == 19 and status["maintenance_waiting"] == 60
    assert status["backlog_remaining"] == status["backlog_waiting"] == 0
    repo.configure_review_budgets(20,0)
    assert repo.today_review_ids() == ids[1:]
    with repo.connection() as conn:
        assert list(conn.iterdump()) == before


def test_ui_append_preserves_partial_draft_and_next_batch(clock, tmp_path, ui, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox
    from src.ui.daily_review_dialog import ReviewQuantityDialog
    from src.models.practice import PracticeMode
    from src.services.grading import grade_session
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.Ok)
    repo = Repository(tmp_path / "db")
    seed(repo, 50)
    repo.configure_review_budgets(20,0)
    window = ui(repo)
    window.start_daily_review()
    key = window.session.session_key
    first = window.session.target_ids[0]
    window.answer_edits[first].setText("kept input")
    def choose(dialog):
        dialog.maintenance.setValue(7)
        dialog.backlog.setValue(0)
        return QDialog.Accepted
    monkeypatch.setattr(ReviewQuantityDialog, "exec", choose)
    window.append_daily_review()
    window.start_daily_review()
    assert window.session.session_key == key and window.session.total == 20
    assert window.answer_edits[first].text() == "kept input"
    assert len(repo.today_review_ids()) == 27
    for item_id, edit in window.answer_edits.items():
        edit.setText(window.session.article.item_map[item_id].answer)
    def grade_today(session):
        grade_session(session)
        session.submitted_at = datetime.combine(clock[0], datetime.min.time())
    monkeypatch.setattr("src.ui.main_window.grade_session", grade_today)
    window._commit_submission()
    window.start_daily_review()
    assert window.session.mode == PracticeMode.DAILY_REVIEW
    assert window.session.total == 7 and window.session.session_key != key
    assert not window._answers_visible


def test_ui_skip_already_completed_draft_when_new_tasks_exist(clock, tmp_path, ui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.Ok)
    repo = Repository(tmp_path / "db")
    seed(repo,50)
    repo.configure_review_budgets(20,0)
    window = ui(repo)
    window.start_daily_review()
    old = window.session
    ids = repo.today_review_ids()
    # Eligible recall in another practice completes the original tasks.
    session = new_long_term_session(repo.get_review_examples(ids))
    submit(repo, session)
    repo.append_today_reviews(maintenance=5)
    window.start_daily_review()
    assert window.session is not old and window.session.total == 5
    assert old.session_key in window._drafts  # No destructive draft cleanup.


def test_quantity_dialog_custom_input_zero_append_and_cancel(clock, tmp_path, ui, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from src.ui.daily_review_dialog import ReviewQuantityDialog
    repo = Repository(tmp_path / "db")
    seed(repo,100)
    window = ui(repo)
    dialog = ReviewQuantityDialog(40,0,repo.review_stream_status(),window)
    dialog.maintenance.lineEdit().setText("53 词")
    dialog.backlog.lineEdit().setText("11 词")
    dialog._confirm()
    assert dialog.result() == QDialog.Accepted
    assert (dialog.maintenance.value(),dialog.backlog.value()) == (53,11)
    dialog.deleteLater()
    append = ReviewQuantityDialog(0,0,repo.review_stream_status(),window,append=True)
    assert not append.confirm.isEnabled()
    append.deleteLater()
    original = repo.today_review_ids()
    monkeypatch.setattr(ReviewQuantityDialog,"exec",lambda dialog: QDialog.Rejected)
    window.configure_review_limit()
    assert repo.today_review_ids() == original
    assert repo.review_budgets() == (40,0)
