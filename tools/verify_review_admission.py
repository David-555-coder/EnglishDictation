"""Verify admission on disposable snapshot copies, not normal learning storage.

Queue replay checks counts/invariants only; it does not simulate what the user
would have remembered on an alternative plan and does not measure efficacy.
"""
from datetime import date, timedelta
from pathlib import Path
import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.database.repository import Repository


def learning_digest(db):
    names = [r[0] for r in db.execute("""SELECT name FROM sqlite_master WHERE type='table'
        AND name NOT IN ('daily_review_days','daily_review_items','app_settings','sqlite_sequence') ORDER BY name""")]
    result = {}
    for name in names:
        quoted = '"'+name.replace('"', '""')+'"'
        rows = [tuple(r) for r in db.execute(f"SELECT * FROM {quoted} ORDER BY rowid")]
        result[name] = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
    return result


def clone(source, target):
    with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return Repository(target)


def verify(source):
    original_today = Repository.__dict__["today"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as src:
        day = src.execute("SELECT max(day) FROM daily_review_days").fetchone()[0]
    assert day is not None
    Repository.today = staticmethod(lambda: date.fromisoformat(day))
    result = {"day": day, "learning_efficacy_measured": False, "source_opened_read_only": True}
    try:
        with tempfile.TemporaryDirectory(prefix="review-admission-qa-") as directory:
            root = Path(directory)
            preserved = clone(source, root / "preserved.db")
            with preserved.connection() as db:
                before = learning_digest(db)
                plan = [tuple(r) for r in db.execute("SELECT * FROM daily_review_items WHERE day=? ORDER BY rowid", (day,))]
                days = [tuple(r) for r in db.execute("SELECT * FROM daily_review_days ORDER BY day")]
                settings = [tuple(r) for r in db.execute("SELECT * FROM app_settings ORDER BY key")]
            preserved.enable_review_continuity()
            ids = preserved.today_review_ids()
            result["preserved_current_plan"] = dict(remaining=len(ids), selected=len(plan), status=preserved.continuity_status())
            with preserved.connection() as db:
                assert learning_digest(db) == before
                assert plan == [tuple(r) for r in db.execute("SELECT * FROM daily_review_items WHERE day=? ORDER BY rowid", (day,))]
                assert days == [tuple(r) for r in db.execute("SELECT * FROM daily_review_days ORDER BY day")]
                assert settings == [tuple(r) for r in db.execute("SELECT * FROM app_settings ORDER BY key")]

            replay = clone(source, root / "replay.db")
            with replay.connection() as db:
                # Recover only saved original appointment dates. Model states,
                # answers and correctness are not fabricated or replayed.
                original_due = list(db.execute("""SELECT original_due_date,word_id FROM daily_review_items
                    WHERE day=? AND original_due_date IS NOT NULL""", (day,)))
                db.executemany("UPDATE wrong_words SET review_due_date=? WHERE id=?", [tuple(r) for r in original_due])
                db.execute("DELETE FROM daily_review_items WHERE day=?", (day,))
                db.execute("DELETE FROM daily_review_days WHERE day=?", (day,))
                before = learning_digest(db)
                due = db.execute("SELECT count(*) FROM wrong_words WHERE review_active=1 AND review_due_date<=?", (day,)).fetchone()[0]
            total, old = replay.review_capacity()
            started = time.perf_counter()
            ids = replay.today_review_ids()
            elapsed_ms = round((time.perf_counter()-started)*1000, 2)
            status = replay.continuity_status()
            assert len(ids) == len(set(ids)) <= total
            assert status["planned_return"] == min(due, total)
            assert status["planned_old"] == min(old, max(0, total-due))
            with replay.connection() as db:
                assert learning_digest(db) == before
                assert db.execute("SELECT count(*) FROM daily_review_items WHERE day=? AND original_due_date>?", (day, day)).fetchone()[0] == 0
            result["same_day_original_dates_replay"] = dict(selected=len(ids), returning=status["planned_return"],
                old=status["planned_old"], known_multiday_aggregate=status["future_reserved"],
                selection_ms=elapsed_ms, memory_and_history_unchanged=True)

            appended = clone(source, root / "appended.db")
            with appended.connection() as db:
                before = learning_digest(db)
                plan = [tuple(r) for r in db.execute("SELECT * FROM daily_review_items WHERE day=? ORDER BY rowid", (day,))]
                settings = [tuple(r) for r in db.execute("SELECT * FROM app_settings ORDER BY key")]
            added = appended.append_review_capacity(20, 4)
            assert added <= 4
            with appended.connection() as db:
                after = [tuple(r) for r in db.execute("SELECT * FROM daily_review_items WHERE day=? ORDER BY rowid", (day,))]
                assert after[:len(plan)] == plan and len(after) == len(plan)+added
                assert learning_digest(db) == before
                assert settings == [tuple(r) for r in db.execute("SELECT * FROM app_settings ORDER BY key")]
            assert len(Repository(appended.database_path).today_review_ids()) == len(appended.today_review_ids())
            result["explicit_append"] = dict(request_total=20, request_old=4, added=added,
                original_items_and_completion_unchanged=True, memory_and_history_unchanged=True,
                defaults_unchanged=True)

            tomorrow = clone(source, root / "tomorrow.db")
            Repository.today = staticmethod(lambda: date.fromisoformat(day)+timedelta(days=1))
            with tomorrow.connection() as db:
                before = learning_digest(db)
            ids = tomorrow.today_review_ids()
            status = tomorrow.continuity_status()
            with tomorrow.connection() as db:
                assert learning_digest(db) == before
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert len(ids) == len(set(ids)) <= status["total_limit"]
            result["next_day_current_dates_preview"] = dict(selected=len(ids), returning=status["planned_return"],
                old=status["planned_old"], memory_and_history_unchanged=True,
                warning="Current saved dates only; not a prediction of actual future answers or workload.")
    finally:
        Repository.today = original_today
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    result.update(source_unchanged=True, passed=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.snapshot.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
