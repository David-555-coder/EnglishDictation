"""Real-data migration/selection QA on disposable copies, never live practice."""
from contextlib import closing
from datetime import date, timedelta
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.database.repository import Repository
from src.services.memory_scheduler import state_from_row


def snapshots(connection):
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {table: ([r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')],
                    connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()) for table in tables}


def copied(source, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as target:
        source.backup(target)


def verify(database, output, today):
    database = database.resolve()
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    clock = Repository.today
    report = {"passed": False, "synthetic_learning_written_to_original": False}
    try:
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro", uri=True)) as source:
            before = snapshots(source)
            with tempfile.TemporaryDirectory(prefix="EnglishDictationStreams_") as temporary:
                root = Path(temporary)
                path = root / "migration" / "copy.db"
                copied(source, path)
                Repository.today = staticmethod(lambda: today)
                repo = Repository(path)
                with repo.connection() as conn:
                    for table, (columns, rows) in before.items():
                        names = ",".join('"'+c+'"' for c in columns)
                        assert [tuple(r) for r in conn.execute(f'SELECT {names} FROM "{table}" ORDER BY rowid')] == rows, table
                    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    report["streams_after_migration"] = {r[0]: r[1] for r in conn.execute(
                        "SELECT review_pool,count(*) FROM wrong_words GROUP BY review_pool")}
                report["all_original_columns_and_plans_preserved"] = True
                Repository(path)
                report["one_time_migration_backup"] = len(list((path.parent / "migration_backups").glob("*.db"))) == 1
                assert report["one_time_migration_backup"] or "review_pool" in before["wrong_words"][0]

                # Next day avoids replacing an owner's already-started current plan.
                plan_day = today + timedelta(days=1)
                report["selection_scenarios"] = []
                for index, budgets in enumerate(((40,20), (80,20), (53,7), (80,0))):
                    path = root / f"selection-{index}" / "copy.db"
                    copied(source, path)
                    Repository.today = staticmethod(lambda: today)
                    repo = Repository(path)  # Assign once at the upgrade date, not future dates.
                    Repository.today = staticmethod(lambda: plan_day)
                    learning = repo.list_wrong_words()
                    started = time.perf_counter()
                    repo.configure_review_budgets(*budgets, today_only=True)
                    ids = repo.today_review_ids(start=True)
                    seconds = time.perf_counter() - started
                    rows = {r["id"]: r for r in repo.list_wrong_words()}
                    pools = {pool: sum(rows[i]["review_pool"] == pool for i in ids)
                             for pool in ("maintenance", "backlog")}
                    assert pools["maintenance"] <= budgets[0] and pools["backlog"] <= budgets[1]
                    assert len(ids) == len(set(ids)) and all(rows[i]["review_due_date"] <= plan_day.isoformat() for i in ids)
                    assert repo.list_wrong_words() == learning  # Queue creation is not learning.
                    assert Repository(path).today_review_ids() == ids
                    report["selection_scenarios"].append(dict(day=plan_day.isoformat(), budgets=budgets,
                        selected=pools, selected_with_model=sum(state_from_row(rows[i]) is not None for i in ids),
                        seconds=round(seconds,4), status=repo.review_stream_status()))
                report["passed"] = True
    finally:
        Repository.today = staticmethod(clock)
        report["original_database_unchanged"] = hashlib.sha256(database.read_bytes()).hexdigest() == digest
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert report["original_database_unchanged"]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    print(json.dumps(verify(args.database, args.output, args.date), ensure_ascii=False))
