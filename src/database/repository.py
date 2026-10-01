from __future__ import annotations

import sqlite3
import json
import logging
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from src.models.practice import PracticeMode, PracticeSession
from src.models.article import ArticleSummary
from src.services.serialization import article_to_dict, article_from_dict, session_to_dict, session_from_dict
from src.utils.text_utils import natural_sort_key


class Repository:
    DRAFT_LIMIT = 10

    @staticmethod
    def today():
        return date.today()

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_before_review_upgrade()
        self.initialize()

    def _backup_before_review_upgrade(self):
        if not self.database_path.exists() or not self.database_path.stat().st_size:
            return
        with self.connection() as source:
            columns = {r[1] for r in source.execute("PRAGMA table_info(wrong_words)")}
            if not columns or "review_relearning" in columns:
                return
            folder = self.database_path.parent / "migration_backups"
            folder.mkdir(exist_ok=True)
            path = folder / f"before-daily-review-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
            target = sqlite3.connect(path)
            try:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("升级前数据库备份校验失败")
            finally:
                target.close()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS wrong_words (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_answer TEXT NOT NULL UNIQUE,
                    answer TEXT NOT NULL,
                    chinese TEXT NOT NULL DEFAULT '',
                    error_count INTEGER NOT NULL DEFAULT 0,
                    occurrence_count INTEGER NOT NULL DEFAULT 0,
                    first_error_at TEXT NOT NULL,
                    last_error_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS word_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wrong_word_id INTEGER NOT NULL REFERENCES wrong_words(id) ON DELETE CASCADE,
                    source_article TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    sentence TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(wrong_word_id, source_path, sentence)
                );
                CREATE TABLE IF NOT EXISTS practice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submitted_at TEXT NOT NULL,
                    article_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    practice_type TEXT NOT NULL,
                    target_count INTEGER NOT NULL,
                    correct_count INTEGER NOT NULL,
                    wrong_count INTEGER NOT NULL,
                    accuracy REAL NOT NULL,
                    viewed_answer INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS practice_answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    normalized_answer TEXT NOT NULL,
                    user_answer TEXT NOT NULL,
                    is_correct INTEGER NOT NULL,
                    is_blank INTEGER NOT NULL,
                    chinese TEXT NOT NULL DEFAULT '',
                    sentence TEXT NOT NULL,
                    paragraph_text TEXT NOT NULL DEFAULT '',
                    paragraph_index INTEGER NOT NULL DEFAULT -1,
                    item_start INTEGER NOT NULL DEFAULT -1,
                    item_end INTEGER NOT NULL DEFAULT -1,
                    sentence_start INTEGER NOT NULL DEFAULT -1,
                    sentence_end INTEGER NOT NULL DEFAULT -1
                );
                CREATE TABLE IF NOT EXISTS article_library (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL,
                    added_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS practice_drafts (
                    session_key TEXT PRIMARY KEY,
                    article_path TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            existing = {row[1] for row in connection.execute("PRAGMA table_info(practice_answers)")}
            migrations = {
                "paragraph_text": "TEXT NOT NULL DEFAULT ''", "paragraph_index": "INTEGER NOT NULL DEFAULT -1",
                "item_start": "INTEGER NOT NULL DEFAULT -1", "item_end": "INTEGER NOT NULL DEFAULT -1",
                "sentence_start": "INTEGER NOT NULL DEFAULT -1", "sentence_end": "INTEGER NOT NULL DEFAULT -1",
            }
            for column, definition in migrations.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE practice_answers ADD COLUMN {column} {definition}")
            additions = {
                "article_library": {"snapshot_json": "TEXT NOT NULL DEFAULT ''"},
                "practice_sessions": {"submission_key": "TEXT"},
                "practice_answers": {"source_article": "TEXT NOT NULL DEFAULT ''", "source_path": "TEXT NOT NULL DEFAULT ''"},
                "wrong_words": {"consecutive_correct": "INTEGER NOT NULL DEFAULT 0", "last_practiced_at": "TEXT", "last_correct": "INTEGER"},
            }
            for table, columns in additions.items():
                present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in present: connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS session_submission_key ON practice_sessions(submission_key)")
            connection.execute("""CREATE INDEX IF NOT EXISTS practice_answers_review_lookup
                ON practice_answers(normalized_answer, is_correct, id DESC)""")
            columns = {r[1] for r in connection.execute("PRAGMA table_info(wrong_words)")}
            review_columns = {
                "review_stage": "INTEGER NOT NULL DEFAULT 0",
                "review_last_date": "TEXT",
                "review_due_date": "TEXT",
                "review_correct_count": "INTEGER NOT NULL DEFAULT 0",
                "review_wrong_count": "INTEGER NOT NULL DEFAULT 0",
                "review_relearning": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in review_columns.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE wrong_words ADD COLUMN {name} {definition}")
            # Retain occupied daily slots even if the user deletes a word/history.
            connection.execute("""CREATE TABLE IF NOT EXISTS daily_review_days (
                day TEXT PRIMARY KEY)""")
            day_columns = {r[1] for r in connection.execute("PRAGMA table_info(daily_review_days)")}
            # Existing plans/drafts stay frozen during upgrade; new plans explicitly start unlocked.
            for name, definition in {"started": "INTEGER NOT NULL DEFAULT 1",
                                     "daily_limit": "INTEGER NOT NULL DEFAULT 40"}.items():
                if name not in day_columns:
                    connection.execute(f"ALTER TABLE daily_review_days ADD COLUMN {name} {definition}")
            connection.execute("""CREATE TABLE IF NOT EXISTS daily_review_items (
                day TEXT NOT NULL, word_id INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(day, word_id))""")
            legacy = connection.execute("""SELECT id FROM wrong_words
                WHERE review_due_date IS NULL ORDER BY error_count DESC, last_error_at DESC, id""").fetchall()
            for index, row in enumerate(legacy):
                due = self.today() + timedelta(days=1 + index // 40)
                connection.execute("UPDATE wrong_words SET review_due_date=? WHERE id=?", (due.isoformat(), row[0]))
            self._prune_drafts(connection, self.DRAFT_LIMIT)

    def review_status(self):
        """Read-only display statistics; never create or refill a daily plan."""
        day = self.today().isoformat()
        with self.connection() as connection:
            row = connection.execute("""SELECT
                (SELECT count(*) FROM daily_review_items WHERE day=? AND completed=1),
                (SELECT count(*) FROM daily_review_items i JOIN wrong_words w ON w.id=i.word_id
                 WHERE i.day=? AND i.completed=0),
                (SELECT count(*) FROM wrong_words w WHERE w.review_due_date<=?
                 AND NOT EXISTS (SELECT 1 FROM daily_review_items i WHERE i.day=? AND i.word_id=w.id))
                """, (day, day, day, day)).fetchone()
            return dict(completed=row[0], remaining=row[1], waiting=row[2])

    def pending_today_review_ids(self, today=None) -> set[int]:
        """Read the existing plan without creating or changing one."""
        today = today or self.today()
        with self.connection() as connection:
            return {row[0] for row in connection.execute("""SELECT word_id FROM daily_review_items
                WHERE day=? AND completed=0""", (today.isoformat(),))}

    def pending_today_review_answers(self, day: str) -> set[str]:
        with self.connection() as connection:
            return {row[0] for row in connection.execute("""SELECT w.normalized_answer
                FROM daily_review_items i JOIN wrong_words w ON w.id=i.word_id
                WHERE i.day=? AND i.completed=0""", (day,))}

    @staticmethod
    def _review_limit(connection):
        from src.services.daily_review import DAILY_LIMIT, DAILY_LIMIT_OPTIONS
        row = connection.execute("SELECT value FROM app_settings WHERE key='daily_review_limit'").fetchone()
        try:
            value = json.loads(row[0]) if row else DAILY_LIMIT
            return value if type(value) is int and value in DAILY_LIMIT_OPTIONS else DAILY_LIMIT
        except (ValueError, TypeError):
            return DAILY_LIMIT

    def review_limit(self, today=False):
        with self.connection() as connection:
            if today:
                row = connection.execute("SELECT daily_limit FROM daily_review_days WHERE day=?",
                                         (self.today().isoformat(),)).fetchone()
                if row:
                    return row[0]
            return self._review_limit(connection)

    def set_review_limit(self, limit):
        from src.services.daily_review import DAILY_LIMIT_OPTIONS
        if type(limit) is not int or limit not in DAILY_LIMIT_OPTIONS:
            raise ValueError("每日数量必须为20、40、60或80")
        day = self.today().isoformat()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = connection.execute("SELECT started FROM daily_review_days WHERE day=?", (day,)).fetchone()
            frozen = bool(plan and plan[0])
            connection.execute("INSERT INTO app_settings VALUES ('daily_review_limit', ?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(limit),))
            if plan and not frozen:
                # Only a never-started preview is replaceable. Never touch word dates or progress.
                connection.execute("DELETE FROM daily_review_items WHERE day=?", (day,))
                connection.execute("DELETE FROM daily_review_days WHERE day=?", (day,))
            return not frozen

    def today_review_ids(self, start=False):
        from src.services.daily_review import select_review_ids
        from src.services.practice_service import new_long_term_session
        day = self.today().isoformat()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM daily_review_days WHERE day=?", (day,)).fetchone():
                rows = connection.execute("""SELECT * FROM wrong_words
                    WHERE review_due_date<=? ORDER BY review_due_date ASC,
                    error_count DESC, last_error_at DESC, id""", (day,)).fetchall()
                def usable(word_id):
                    examples = self.get_review_examples([word_id])
                    if not examples:
                        return False
                    try:
                        new_long_term_session(examples)
                    except (ValueError, KeyError, TypeError):
                        logging.getLogger(__name__).warning("错词 %s 例句不可用，保留待修复", word_id)
                        return False
                    return True
                limit = self._review_limit(connection)
                chosen = select_review_ids(rows, limit, self.today(), usable)
                connection.execute("INSERT INTO daily_review_days(day,started,daily_limit) VALUES (?,0,?)", (day, limit))
                connection.executemany("INSERT INTO daily_review_items(day,word_id) VALUES (?,?)", [(day, i) for i in chosen])
            if start:
                connection.execute("UPDATE daily_review_days SET started=1 WHERE day=?", (day,))
            return [r[0] for r in connection.execute("""SELECT i.word_id FROM daily_review_items i
                JOIN wrong_words w ON w.id=i.word_id
                WHERE i.day=? AND i.completed=0 ORDER BY i.rowid""", (day,))]

    def _save_daily_results(self, connection, session, outcomes, submitted_at):
        from src.services.daily_review import review_transition
        day = session.view_state.get("daily_review_day")
        # Stale drafts and same-day retries cannot advance a second time.
        if day != submitted_at[:10]:
            return
        connection.execute("UPDATE daily_review_days SET started=1 WHERE day=?", (day,))
        for normalized, correct in outcomes.items():
            row = connection.execute("""SELECT w.* FROM wrong_words w
                JOIN daily_review_items i ON i.word_id=w.id
                WHERE i.day=? AND i.completed=0 AND w.normalized_answer=?
                AND w.review_due_date<=? AND (w.review_last_date IS NULL OR w.review_last_date<?)""",
                (day, normalized, day, day)).fetchone()
            if not row:
                # A saved daily draft may be submitted after the same word was
                # credited in a full article. A later wrong answer still brings
                # its next review forward, without advancing it twice today.
                if not correct and connection.execute("""SELECT 1 FROM wrong_words w
                    JOIN daily_review_items i ON i.word_id=w.id
                    WHERE i.day=? AND i.completed=1 AND w.normalized_answer=?
                    AND w.review_last_date=?""", (day, normalized, day)).fetchone():
                    tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                    self._bring_due_forward(connection, normalized, tomorrow)
                continue
            correct = bool(correct)
            stage, relearning, interval = review_transition(row["review_stage"], row["review_relearning"], correct)
            due = date.fromisoformat(day) + timedelta(days=interval)
            connection.execute("""UPDATE wrong_words SET review_stage=?, review_relearning=?, review_last_date=?, review_due_date=?,
                review_correct_count=review_correct_count+?, review_wrong_count=review_wrong_count+?
                WHERE id=?""", (stage, int(relearning), day, due.isoformat(), int(correct), int(not correct), row["id"]))
            connection.execute("UPDATE daily_review_items SET completed=1 WHERE day=? AND word_id=?", (day, row["id"]))

    @staticmethod
    def _bring_due_forward(connection, normalized, tomorrow):
        connection.execute("""UPDATE wrong_words SET
            review_relearning=CASE WHEN review_stage>=2 THEN 1 ELSE 0 END,
            review_stage=CASE WHEN review_stage>=2 THEN review_stage ELSE 0 END,
            review_due_date=CASE WHEN review_due_date IS NULL OR review_due_date>? THEN ? ELSE review_due_date END
            WHERE normalized_answer=?""", (tomorrow, tomorrow, normalized))

    def _credit_due_article_reviews(self, connection, outcomes, day):
        from src.services.daily_review import review_transition

        for normalized, correct in outcomes.items():
            if not correct:
                continue
            row = connection.execute("""SELECT * FROM wrong_words
                WHERE normalized_answer=? AND review_due_date<=?
                AND (review_last_date IS NULL OR review_last_date<?)
                AND substr(last_error_at, 1, 10)<?""", (normalized, day, day, day)).fetchone()
            if not row:
                continue
            stage, relearning, interval = review_transition(row["review_stage"], row["review_relearning"], True)
            due = (date.fromisoformat(day) + timedelta(days=interval)).isoformat()
            connection.execute("""UPDATE wrong_words SET review_stage=?, review_relearning=?,
                review_last_date=?, review_due_date=?, review_correct_count=review_correct_count+1
                WHERE id=?""", (stage, int(relearning), day, due, row["id"]))
            completed = connection.execute("""UPDATE daily_review_items SET completed=1
                WHERE day=? AND word_id=? AND completed=0""", (day, row["id"]))
            if completed.rowcount:
                connection.execute("UPDATE daily_review_days SET started=1 WHERE day=?", (day,))

    def save_submitted_session(self, session: PracticeSession) -> int:
        if not session.submitted:
            raise ValueError("只能保存已提交的练习")
        item_map = session.article.item_map
        submitted_at = (session.submitted_at or session.started_at).isoformat(timespec="seconds")
        with self.connection() as connection:
            existing = connection.execute("SELECT id FROM practice_sessions WHERE submission_key=?", (session.session_key,)).fetchone()
            if existing:
                session.database_id = existing[0]
                connection.execute("DELETE FROM practice_drafts WHERE session_key=?", (session.session_key,))
                return existing[0]
            cursor = connection.execute("""
                INSERT INTO practice_sessions
                (submitted_at, article_name, source_path, practice_type, target_count,
                 correct_count, wrong_count, accuracy, viewed_answer, submission_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (submitted_at, session.article.filename, session.article.source_path, session.mode.value,
                    session.total, session.correct_count, session.wrong_count, session.accuracy,
                    0, session.session_key))
            session_id = int(cursor.lastrowid)
            answer_rows = []
            for result in session.results:
                item = item_map[result.item_id]
                answer_rows.append((session_id, item.id, item.answer, item.normalized_answer, result.user_answer,
                                    int(result.is_correct), int(not result.user_answer.strip()), item.chinese,
                                    item.sentence, item.paragraph_text, item.paragraph_index, item.start, item.end,
                                    item.sentence_start, item.sentence_end, item.filename, item.source_path))
            connection.executemany("""
                INSERT INTO practice_answers
                (session_id, item_id, answer, normalized_answer, user_answer, is_correct,
                 is_blank, chinese, sentence, paragraph_text, paragraph_index, item_start,
                 item_end, sentence_start, sentence_end, source_article, source_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, answer_rows)
            for result in session.results:
                item = item_map[result.item_id]
                if not result.is_correct:
                    self._record_wrong(connection, item, submitted_at)
            outcomes = {}
            for result in session.results:
                normalized = item_map[result.item_id].normalized_answer
                outcomes[normalized] = outcomes.get(normalized, True) and result.is_correct
            updates = [(correct, correct, submitted_at, normalized)
                       for normalized, correct in outcomes.items()]
            connection.executemany("""UPDATE wrong_words SET consecutive_correct=CASE WHEN ? THEN consecutive_correct+1 ELSE 0 END,
                last_correct=?, last_practiced_at=? WHERE normalized_answer=?""", updates)
            if session.mode == PracticeMode.DAILY_REVIEW:
                self._save_daily_results(connection, session, outcomes, submitted_at)
            else:
                day = submitted_at[:10]
                tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                for normalized, correct in outcomes.items():
                    if not correct:
                        self._bring_due_forward(connection, normalized, tomorrow)
                if session.mode == PracticeMode.FULL_ARTICLE:
                    self._credit_due_article_reviews(connection, outcomes, day)
            connection.execute("DELETE FROM practice_drafts WHERE session_key=?", (session.session_key,))
        session.database_id = session_id
        return session_id

    @staticmethod
    def _record_wrong(connection: sqlite3.Connection, item, submitted_at: str) -> None:
        connection.execute("""
            INSERT INTO wrong_words
            (normalized_answer, answer, chinese, error_count, occurrence_count, first_error_at, last_error_at)
            VALUES (?, ?, ?, 1, 1, ?, ?)
            ON CONFLICT(normalized_answer) DO UPDATE SET
                answer=excluded.answer,
                chinese=CASE WHEN excluded.chinese <> '' THEN excluded.chinese ELSE wrong_words.chinese END,
                error_count=wrong_words.error_count + 1,
                occurrence_count=wrong_words.occurrence_count + 1,
                last_error_at=excluded.last_error_at
        """, (item.normalized_answer, item.answer, item.chinese, submitted_at, submitted_at))
        wrong_id = connection.execute(
            "SELECT id FROM wrong_words WHERE normalized_answer=?", (item.normalized_answer,)
        ).fetchone()[0]
        connection.execute("""
            INSERT INTO word_examples
            (wrong_word_id, source_article, source_path, sentence, occurrence_count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(wrong_word_id, source_path, sentence) DO UPDATE SET
                occurrence_count=word_examples.occurrence_count + 1
        """, (wrong_id, item.filename, item.source_path, item.sentence))

    def list_wrong_words(self, order_by: str = "alphabet") -> list[dict]:
        orders = {"alphabet": "w.normalized_answer COLLATE NOCASE ASC",
                  "errors": "w.error_count DESC, w.normalized_answer COLLATE NOCASE ASC",
                  "recent": "w.last_error_at DESC"}
        order = orders.get(order_by, orders["alphabet"])
        with self.connection() as connection:
            rows = connection.execute(f"""
                SELECT w.*, COUNT(e.id) AS example_count,
                       COALESCE(GROUP_CONCAT(DISTINCT e.source_article), '') AS sources
                FROM wrong_words w LEFT JOIN word_examples e ON e.wrong_word_id=w.id
                GROUP BY w.id ORDER BY {order}
            """).fetchall()
            return [dict(row) for row in rows]

    def get_wrong_word_examples(self, wrong_word_id: int) -> list[dict]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM word_examples WHERE wrong_word_id=? ORDER BY occurrence_count DESC, id",
                (wrong_word_id,))]

    def delete_wrong_word(self, wrong_word_id: int) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM wrong_words WHERE id=?", (wrong_word_id,))

    def clear_wrong_words(self) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM wrong_words")

    def list_history(self, limit: int = 500) -> list[dict]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM practice_sessions ORDER BY submitted_at DESC, id DESC LIMIT ?", (limit,))]

    def list_wrong_practice_history(self, limit: int = 500, order_by: str = "completed") -> list[dict]:
        with self.connection() as connection:
            records = [dict(row) for row in connection.execute("""
                SELECT * FROM practice_sessions
                WHERE wrong_count > 0
                ORDER BY submitted_at DESC, id DESC LIMIT ?
            """, (limit,))]
        if order_by == "article":
            # Stable sorting keeps newest-first order within the same article.
            records.sort(key=lambda record: natural_sort_key(record["article_name"]))
        return records

    def get_practice_session(self, session_id: int) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            return dict(row) if row else None

    def get_wrong_answers(self, session_id: int) -> list[dict]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM practice_answers WHERE session_id=? AND is_correct=0 ORDER BY id", (session_id,))]

    def delete_practice_session(self, session_id: int) -> bool:
        """删除选中的历史练习及其答案快照，不影响长期错词本。"""
        return bool(self.delete_practice_sessions([session_id]))

    def delete_practice_sessions(self, session_ids) -> int:
        """Atomically delete selected history snapshots, never long-term wrong words."""
        ids = list(dict.fromkeys(int(session_id) for session_id in session_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM practice_sessions WHERE id IN ({placeholders})", ids)
            return cursor.rowcount

    def save_article_path(self, source_path: str | Path) -> None:
        path = Path(source_path).resolve()
        with self.connection() as connection:
            connection.execute("""
                INSERT INTO article_library (source_path, filename, added_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_path) DO UPDATE SET filename=excluded.filename
            """, (str(path), path.name, datetime.now().isoformat(timespec="seconds")))

    def list_article_paths(self) -> list[str]:
        with self.connection() as connection:
            return [row[0] for row in connection.execute(
                "SELECT source_path FROM article_library ORDER BY filename COLLATE NOCASE, id"
            )]

    def delete_article_path(self, source_path: str | Path) -> bool:
        return bool(self.delete_article_paths([source_path]))

    def delete_article_paths(self, source_paths) -> int:
        """Remove library rows and their drafts atomically, never documents/history."""
        paths = list(dict.fromkeys(str(Path(path).resolve()) for path in source_paths))
        deleted = 0
        with self.connection() as connection:
            for path in paths:
                deleted += connection.execute("DELETE FROM article_library WHERE source_path=?", (path,)).rowcount
                connection.execute("DELETE FROM practice_drafts WHERE article_path=?", (path,))
        return deleted

    def save_article(self, article):
        payload = json.dumps(article_to_dict(article), ensure_ascii=False)
        with self.connection() as connection:
            connection.execute("""INSERT INTO article_library(source_path, filename, added_at, snapshot_json)
                VALUES (?, ?, ?, ?) ON CONFLICT(source_path) DO UPDATE SET
                filename=excluded.filename, snapshot_json=excluded.snapshot_json""",
                (article.source_path, article.filename, datetime.now().isoformat(timespec="seconds"), payload))

    def load_articles(self):
        with self.connection() as connection:
            rows = connection.execute("SELECT source_path, snapshot_json FROM article_library").fetchall()
        articles, unresolved = [], []
        for row in rows:
            try:
                if not row["snapshot_json"]: raise ValueError("legacy path only")
                articles.append(article_from_dict(json.loads(row["snapshot_json"])))
            except (ValueError, TypeError, KeyError):
                unresolved.append(row["source_path"])
        return articles, unresolved

    def load_article_catalog(self):
        # JSON is inspected inside SQLite; full snapshots do not cross into Python.
        with self.connection() as connection:
            rows = connection.execute("""SELECT source_path, filename,
                CASE WHEN json_valid(snapshot_json) THEN
                    json_array_length(snapshot_json, '$.word_items') END AS target_count
                FROM article_library""").fetchall()
        summaries, unresolved = [], []
        for row in rows:
            if row["target_count"] is None:
                unresolved.append(row["source_path"])
            else:
                summaries.append(ArticleSummary(row["source_path"], row["filename"], row["target_count"]))
        return summaries, unresolved

    def load_article(self, source_path):
        with self.connection() as connection:
            row = connection.execute("SELECT snapshot_json FROM article_library WHERE source_path=?",
                                     (source_path,)).fetchone()
        if row is None:
            raise ValueError("文章记录不存在")
        return article_from_dict(json.loads(row[0]))

    def save_draft(self, session):
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM practice_sessions WHERE submission_key=?", (session.session_key,)).fetchone():
                return
            if session.mode == PracticeMode.DAILY_REVIEW:
                connection.execute("UPDATE daily_review_days SET started=1 WHERE day=?",
                                   (session.view_state.get("daily_review_day"),))
            connection.execute("""INSERT INTO practice_drafts VALUES (?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
                (session.session_key, session.article.source_path, json.dumps(session_to_dict(session), ensure_ascii=False), datetime.now().isoformat()))
            self._prune_drafts(connection, self.DRAFT_LIMIT)

    @staticmethod
    def _prune_drafts(connection: sqlite3.Connection, limit: int) -> int:
        # Whole-article drafts occupy article slots, not round slots. Preserve
        # existing same-article rounds; review modes retain their own old limit.
        limit = max(0, int(limit))
        cursor = connection.execute("""
            DELETE FROM practice_drafts
            WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.mode') END = ?
            AND COALESCE(json_extract(payload, '$.submitted'), 0) = 0
            AND article_path NOT IN (
                SELECT article_path FROM practice_drafts
                WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.mode') END = ?
                AND COALESCE(json_extract(payload, '$.submitted'), 0) = 0
                GROUP BY article_path
                ORDER BY MAX(updated_at) DESC, MAX(rowid) DESC LIMIT ?
            )
        """, (PracticeMode.FULL_ARTICLE.value, PracticeMode.FULL_ARTICLE.value, limit))
        removed = cursor.rowcount
        cursor = connection.execute("""
            DELETE FROM practice_drafts
            WHERE json_valid(payload) AND json_extract(payload, '$.mode') != ?
            AND COALESCE(json_extract(payload, '$.submitted'), 0) = 0
            AND session_key NOT IN (
                SELECT session_key FROM practice_drafts
                WHERE json_valid(payload) AND json_extract(payload, '$.mode') != ?
                AND COALESCE(json_extract(payload, '$.submitted'), 0) = 0
                ORDER BY updated_at DESC, rowid DESC LIMIT ?
            )
        """, (PracticeMode.FULL_ARTICLE.value, PracticeMode.FULL_ARTICLE.value, limit))
        return removed + cursor.rowcount

    def prune_drafts(self, limit: int = DRAFT_LIMIT) -> int:
        with self.connection() as connection:
            return self._prune_drafts(connection, limit)

    def load_drafts(self):
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT session_key, payload FROM practice_drafts ORDER BY updated_at DESC, rowid DESC").fetchall()
        drafts = []
        for key, payload in rows:
            try:
                drafts.append(session_from_dict(json.loads(payload)))
            except (ValueError, KeyError, TypeError):
                logging.getLogger(__name__).warning("跳过无法读取的草稿 %s；原记录已保留", key)
        return drafts

    def draft_keys(self):
        with self.connection() as connection:
            return {row[0] for row in connection.execute("SELECT session_key FROM practice_drafts")}

    def unfinished_articles(self):
        """Latest unfinished whole-article round per article, ordered by activity."""
        with self.connection() as connection:
            rows = connection.execute("""SELECT article_path, payload, updated_at FROM practice_drafts
                WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.mode') END = ?
                ORDER BY updated_at DESC, rowid DESC""", (PracticeMode.FULL_ARTICLE.value,)).fetchall()
        records, seen = [], set()
        for path, payload, updated_at in rows:
            if path in seen:
                continue
            try:
                session = session_from_dict(json.loads(payload))
                if session.submitted:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            seen.add(path)
            records.append((session, updated_at))
            if len(records) >= self.DRAFT_LIMIT:
                break
        return records

    def has_wrong_history(self):
        with self.connection() as connection:
            return connection.execute("SELECT 1 FROM practice_sessions WHERE wrong_count>0 LIMIT 1").fetchone() is not None

    def delete_draft(self, session_key):
        with self.connection() as connection:
            connection.execute("DELETE FROM practice_drafts WHERE session_key=?", (session_key,))

    def get_setting(self, key, default=None):
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            try:
                return json.loads(row[0]) if row else default
            except (ValueError, TypeError):
                logging.getLogger(__name__).warning("设置 %s 无法读取，使用默认值", key)
                return default

    def set_setting(self, key, value):
        with self.connection() as connection:
            connection.execute("INSERT INTO app_settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (key, json.dumps(value, ensure_ascii=False)))

    def get_review_examples(self, word_ids):
        records = []
        with self.connection() as connection:
            for word_id in word_ids:
                word = connection.execute("SELECT * FROM wrong_words WHERE id=?", (word_id,)).fetchone()
                if not word: continue
                row = connection.execute("""SELECT a.*, s.source_path AS legacy_path, s.article_name AS legacy_name
                    FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id
                    WHERE a.normalized_answer=? AND a.is_correct=0 ORDER BY a.id DESC LIMIT 1""", (word["normalized_answer"],)).fetchone()
                if row:
                    data = dict(row)
                    data["source_path"] = data["source_path"] or data["legacy_path"]
                    data["source_article"] = data["source_article"] or data["legacy_name"]
                else:
                    example = connection.execute("SELECT * FROM word_examples WHERE wrong_word_id=? ORDER BY id DESC LIMIT 1", (word_id,)).fetchone()
                    if not example: continue
                    data = dict(example)
                    data.update(answer=word["answer"], chinese=word["chinese"], paragraph_text=example["sentence"],
                                paragraph_index=-1, item_start=example["sentence"].find(word["answer"]))
                data["id"] = f"book-{word_id}"
                records.append(data)
        return records
