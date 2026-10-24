"""Transactional integration of memory evidence and FSRS-7 inference."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from src.models.practice import PracticeMode
from src.services.fsrs7 import MODEL_ID, MemoryState, next_state, interval
from src.services.learning_evidence import SCHEMA, as_utc, kind_for_item

LOGGER = logging.getLogger(__name__)

MEMORY_COLUMNS = {
    "fsrs_model": "TEXT", "fsrs_stability": "REAL", "fsrs_difficulty": "REAL",
    "fsrs_stability_fast": "REAL", "fsrs_last_at": "TEXT", "fsrs_due_at": "TEXT",
    "fsrs_episode": "TEXT", "fsrs_reviews": "INTEGER NOT NULL DEFAULT 0",
}

# A day boundary is not a memory reset. This is an observable-evidence guard,
# not a FSRS interval or a claim that one hour removes all priming effects.
RECENT_READING_SECONDS = 3600


def reading_invalidates(connection, normalized, evidence, last, now, kind, protected):
    try:
        began = as_utc(evidence["began_at"])
        for exposure in connection.execute(
                "SELECT event_key,observed_at FROM memory_exposures WHERE normalized_answer=?", (normalized,)):
            observed = as_utc(exposure["observed_at"])
            if observed > now or protected:
                continue  # Evidence entered before revelation is not erased.
            if kind == "relearning":
                if exposure["event_key"] == evidence.get("pre_round_exposure_key") and observed <= began:
                    continue
                if began <= observed <= now:
                    return True
            elif (not last or observed > last) and (
                    observed.astimezone().date() == now.astimezone().date()
                    or 0 <= (now-observed).total_seconds() < RECENT_READING_SECONDS):
                return True
    except (ValueError, TypeError, KeyError):
        return True  # Malformed evidence cannot create a successful recall.
    return False


def state_from_row(row):
    row = dict(row)
    if row.get("fsrs_model") != MODEL_ID:
        return None
    try:
        as_utc(row["fsrs_last_at"])
        return MemoryState(row["fsrs_stability"], row["fsrs_difficulty"], row["fsrs_stability_fast"])
    except (ValueError, TypeError, KeyError):
        LOGGER.warning("无效记忆状态，保留错词并等待重新确认：%s", row.get("id"))
        return None


def initialize(connection):
    columns = {r[1] for r in connection.execute("PRAGMA table_info(wrong_words)")}
    for name, definition in MEMORY_COLUMNS.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE wrong_words ADD COLUMN {name} {definition}")
    for statement in """
        CREATE TABLE IF NOT EXISTS memory_events (
            id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, normalized_answer TEXT NOT NULL,
            observed_at TEXT NOT NULL, kind TEXT NOT NULL, rating INTEGER, elapsed_days REAL,
            model TEXT, state_json TEXT, UNIQUE(session_key, normalized_answer));
        CREATE INDEX IF NOT EXISTS memory_events_word ON memory_events(normalized_answer, observed_at);
        CREATE TABLE IF NOT EXISTS memory_exposures (
            event_key TEXT NOT NULL, normalized_answer TEXT NOT NULL, observed_at TEXT NOT NULL,
            PRIMARY KEY(event_key, normalized_answer));
        CREATE INDEX IF NOT EXISTS memory_exposures_word ON memory_exposures(normalized_answer, observed_at);
    """.split(";"):
        if statement.strip():
            connection.execute(statement)


def warm_failure_anchor(connection, normalized, evidence, last, now):
    """Find a real, current failed parent; never infer one from a UI phase.

    A warm failure did not update an existing model's episode. The subsequent
    closed-book correction can still be relearning, but needs an explicit failed
    parent and short elapsed time, not a delayed independent Good from old state.
    All checks use existing transactional history; no schema/history rewrite.
    """
    parent_key = evidence.get("parent_key")
    episode = evidence.get("episode")
    if (not last or not isinstance(parent_key, str) or not parent_key
            or not isinstance(episode, str) or not 0 < len(episode) <= 200):
        return None
    parent = connection.execute("""SELECT e.id,e.observed_at,s.memory_context
        FROM memory_events e JOIN practice_sessions s ON s.submission_key=e.session_key
        WHERE e.session_key=? AND e.normalized_answer=? AND e.rating IS NULL
        AND e.kind IN ('study','exposed')
        AND EXISTS (SELECT 1 FROM practice_answers a WHERE a.session_id=s.id
                    AND a.normalized_answer=e.normalized_answer AND a.is_correct=0)
        AND e.id=(SELECT MAX(m.id) FROM memory_events m WHERE m.normalized_answer=e.normalized_answer)
        """, (parent_key, normalized)).fetchone()
    if not parent:
        return None
    try:
        context = json.loads(parent["memory_context"])
        anchor = as_utc(parent["observed_at"])
        began = as_utc(evidence["began_at"])
        if not (isinstance(context, dict) and type(context.get("schema")) is int
                and context["schema"] == SCHEMA and context.get("episode") == evidence.get("episode")
                and last < anchor <= began <= now and 0 < (now-anchor).total_seconds() <= 86400):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    # Reading before a correction is expected. Reading during it isn't evidence
    # of closed-book recall, including a reveal from another software view.
    exposures = connection.execute("SELECT event_key,observed_at FROM memory_exposures WHERE normalized_answer=?",
                                   (normalized,)).fetchall()
    for exposure in exposures:
        try:
            exposure_time = as_utc(exposure["observed_at"])
            # An immediately started round may share its wall-clock timestamp
            # with its preview. Exempt only that exact, recorded pre-round event;
            # another view/reveal at the same instant remains disqualifying.
            if (exposure["event_key"] == evidence.get("pre_round_exposure_key")
                    and exposure_time <= began):
                continue
            if began <= exposure_time <= now:
                return None
        except (ValueError, TypeError):
            return None
    return anchor


def save_results(connection, session, outcomes, submitted_at):
    submitted_time = as_utc(session.submitted_at or session.started_at)
    day = (session.submitted_at or session.started_at).date().isoformat()
    evidence = session.view_state.get("memory", {})
    episode = evidence.get("episode", session.session_key)
    stale_daily = session.mode == PracticeMode.DAILY_REVIEW and session.view_state.get("daily_review_day") != day
    item_map = session.article.item_map
    for normalized, correct in outcomes.items():
        now = submitted_time
        answer_times = [evidence.get("answered_at", {}).get(r.item_id) for r in session.results
                        if item_map[r.item_id].normalized_answer == normalized]
        if answer_times and all(answer_times):
            try:
                observed = max(as_utc(value) for value in answer_times)
                began = as_utc(evidence["began_at"])
                if began <= observed <= submitted_time:
                    now = observed
            except (ValueError, TypeError, KeyError):
                pass
        row = connection.execute("SELECT * FROM wrong_words WHERE normalized_answer=?", (normalized,)).fetchone()
        if row is None:
            continue  # Correct new words do not enter the wrong-word book.
        row = dict(row)
        kinds = {kind_for_item(session, r.item_id) for r in session.results
                 if item_map[r.item_id].normalized_answer == normalized}
        kind = "exposed" if "exposed" in kinds else "unknown" if "unknown" in kinds else (
            "study" if "study" in kinds else "relearning" if "relearning" in kinds else "first")
        previous = state_from_row(row)
        last = as_utc(row["fsrs_last_at"]) if previous else None
        anchor = None
        eligible = kind == "first"
        if kind == "relearning":
            eligible = bool(last and row["fsrs_episode"] == episode and 0 <= (now-last).total_seconds() <= 86400)
            if not eligible:
                anchor = warm_failure_anchor(connection, normalized, evidence, last, now)
                eligible = anchor is not None
            if not eligible:
                kind = "study"
        if eligible and kind in {"first", "relearning"}:
            # A protected answer already entered before reveal remains evidence.
            protected = all(r.item_id in evidence.get("protected", {}) for r in session.results
                            if item_map[r.item_id].normalized_answer == normalized)
            if reading_invalidates(connection, normalized, evidence, last, now, kind, protected):
                eligible, kind = False, "study"
        if eligible and last and now < last:
            eligible, kind = False, "clock_conflict"
        if eligible and last and now == last:
            eligible, kind = False, "duplicate_instant"
        if eligible and last and (now.astimezone().date() == last.astimezone().date()
                                  or (now-last).total_seconds() < RECENT_READING_SECONDS):
            kind = "relearning"
        if stale_daily:
            eligible, kind = False, "stale_draft"
        # First failure after reading establishes initial learning, never a fake
        # successful long-term recall. Subsequent corrections use real elapsed time.
        if not eligible and previous is None and not correct and kind not in {"unknown", "stale_draft", "clock_conflict"}:
            eligible, kind = True, "initial_learning"
        if eligible and anchor:
            # Use the observed failed parent as the short-term starting point.
            # Do not insert a pretend cold attempt or mutate its original event.
            previous = next_state(previous, 1, (anchor-last).total_seconds()/86400)
            last = anchor
        elapsed = max(0, (now-last).total_seconds()/86400) if last else 0.
        rating = 3 if correct else 1
        state = next_state(previous, rating, elapsed) if eligible else None
        if state:
            model_interval = interval(state)
            model_due = now + timedelta(days=model_interval)
            # A daily word queue is not a sub-day Anki scheduler: correction is
            # optional now, the next independent appointment is no earlier than tomorrow.
            confirm = not correct or kind == "initial_learning" or (kind == "relearning" and bool(row["review_relearning"]))
            tomorrow_date = now.astimezone().date()+timedelta(days=1)
            due = (tomorrow_date if confirm else max(tomorrow_date, model_due.astimezone().date())).isoformat()
            stage = min(4, sum(model_interval >= threshold for threshold in (3, 7, 14, 30)))
            connection.execute("""UPDATE wrong_words SET fsrs_model=?,fsrs_stability=?,fsrs_difficulty=?,
                fsrs_stability_fast=?,fsrs_last_at=?,fsrs_due_at=?,fsrs_episode=?,fsrs_reviews=fsrs_reviews+1,
                review_due_date=?,review_last_date=?,review_stage=?,review_relearning=?,review_pool='maintenance',
                review_correct_count=review_correct_count+?,review_wrong_count=review_wrong_count+?
                WHERE id=?""", (MODEL_ID, state.stability, state.difficulty, state.stability_fast,
                                now.isoformat(), model_due.isoformat(), episode, due, day, stage, int(confirm),
                                int(correct), int(not correct), row["id"]))
        elif not stale_daily and (kind not in {"unknown", "clock_conflict", "duplicate_instant"} or not correct):
            tomorrow = (now.astimezone().date()+timedelta(days=1)).isoformat()
            if not correct:
                connection.execute("""UPDATE wrong_words SET review_relearning=1,
                    review_due_date=CASE WHEN review_due_date IS NULL OR review_due_date>? THEN ? ELSE review_due_date END
                    WHERE id=?""", (tomorrow, tomorrow, row["id"]))
            elif row["review_due_date"] and row["review_due_date"] <= day:
                # A warm success on an overdue word still needs confirmation.
                # Extra reading of a future-due word is never treated as failure.
                connection.execute("UPDATE wrong_words SET review_relearning=1 WHERE id=?", (row["id"],))
        connection.execute("""INSERT INTO memory_events(session_key,normalized_answer,observed_at,kind,rating,
            elapsed_days,model,state_json) VALUES (?,?,?,?,?,?,?,?)""",
                           (session.session_key, normalized, now.isoformat(), kind, rating if state else None,
                            elapsed if state else None, MODEL_ID if state else None,
                            json.dumps(state.__dict__) if state else None))
        session.view_state.setdefault("evidence_outcomes", {})[normalized] = {"kind": kind, "rating": rating if state else None}
        if not stale_daily and (state or (not correct and kind in {"first", "relearning", "study", "exposed"})):
            connection.execute("""UPDATE wrong_words SET review_active=1,
                review_enrolled_at=COALESCE(review_enrolled_at,?) WHERE id=?""", (now.isoformat(), row["id"]))
        if session.view_state.get("consolidation"):
            connection.execute("""UPDATE consolidation_attempts SET attempted_at=?
                WHERE normalized_answer=? AND session_key=? AND attempted_at IS NULL""",
                (now.isoformat(), normalized, session.session_key))
        if state and anchor:
            session.view_state["evidence_outcomes"][normalized]["relearning_anchor"] = {
                "parent_key": evidence["parent_key"], "observed_at": anchor.isoformat()}
        # Other practice satisfies today's fixed plan only with eligible evidence.
        # An exposed daily attempt consumes its original slot, but remains due for
        # independent confirmation; it never creates an extra slot today.
        if not stale_daily and ((state and kind == "first") or session.mode == PracticeMode.DAILY_REVIEW):
            changed = connection.execute("UPDATE daily_review_items SET completed=1 WHERE day=? AND word_id=? AND completed=0",
                                         (day, row["id"]))
            if changed.rowcount:
                connection.execute("UPDATE daily_review_days SET started=1 WHERE day=?", (day,))
