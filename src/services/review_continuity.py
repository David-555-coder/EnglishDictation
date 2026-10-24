"""Versioned application capacity policy; never changes FSRS formulas or dates.

Legacy pool columns remain for history/backup compatibility. Live scheduling uses
explicit enrollment, not a moving seven-day window or completion-age rotation.
"""
from datetime import date, datetime, timedelta
import json

from src.services.daily_review import MAX_REVIEW_LIMIT

POLICY = "continuity-2026-10-16"
# Enrollment is a one-time migration. Changing admission must not re-enroll
# legacy words, reset saved capacities, or rebuild an already begun plan.
ADMISSION_POLICY = "continuity-2026-10-23"


def initialize(db):
    additions = {
        "wrong_words": {"review_active": "INTEGER NOT NULL DEFAULT 0", "review_enrolled_at": "TEXT"},
        "daily_review_days": {"policy_version": "TEXT", "total_limit": "INTEGER", "old_limit": "INTEGER"},
        "daily_review_items": {"policy_version": "TEXT", "original_due_date": "TEXT",
                               "selection_reason": "TEXT", "selected_at": "TEXT"},
    }
    for table, columns in additions.items():
        present = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in present:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS wrong_words_active_due ON wrong_words(review_active,review_due_date)")
    db.execute("""CREATE TABLE IF NOT EXISTS consolidation_attempts (
        day TEXT NOT NULL, normalized_answer TEXT NOT NULL, session_key TEXT NOT NULL,
        episode TEXT NOT NULL, reserved_at TEXT NOT NULL, attempted_at TEXT, PRIMARY KEY(day,normalized_answer))""")


def enabled(db):
    return db.execute("SELECT 1 FROM app_settings WHERE key='review_continuity_policy' AND value=?",
                      (json.dumps(POLICY),)).fetchone() is not None


def setting(db, key, default):
    row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    try:
        value = json.loads(row[0]) if row else default
        return value if type(value) is int and 0 <= value <= MAX_REVIEW_LIMIT else default
    except (ValueError, TypeError):
        return default


def enable(repo, db):
    if enabled(db):
        return
    # Only carry the most recently practiced cohort plus valid future appointments.
    # Old weak models are retained, not all activated or reset on upgrade.
    from src.services.memory_scheduler import state_from_row
    from src.services.learning_evidence import as_utc
    rows = [dict(r) for r in db.execute("SELECT * FROM wrong_words")]
    valid = [(r, as_utc(r["fsrs_last_at"]).astimezone().date()) for r in rows if state_from_row(r)]
    latest = max((last for _, last in valid if last <= repo.today()), default=None)
    at = datetime.now().astimezone().isoformat()
    for row, last in valid:
        if last == latest or (row["review_due_date"] or "") > repo.today().isoformat():
            db.execute("UPDATE wrong_words SET review_active=1,review_enrolled_at=COALESCE(review_enrolled_at,?) WHERE id=?",
                       (at, row["id"]))
    normal, old = repo._review_budgets(db)
    for key, value in (("review_total_limit", min(MAX_REVIEW_LIMIT, normal+old)),
                       ("review_old_limit", old), ("review_continuity_policy", POLICY)):
        db.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))
    # A begun plan is immutable, including its old meanings/occupied slots.
    # An unused preview can be rebuilt when next requested.
    today = repo.today().isoformat()
    plan = db.execute("SELECT * FROM daily_review_days WHERE day=?", (today,)).fetchone()
    if plan and not plan["started"]:
        db.execute("DELETE FROM daily_review_items WHERE day=?", (today,))
        db.execute("DELETE FROM daily_review_days WHERE day=?", (today,))
    elif plan:
        db.execute("UPDATE daily_review_days SET total_limit=?,old_limit=? WHERE day=?",
                   (min(MAX_REVIEW_LIMIT, plan["daily_limit"]+plan["backlog_limit"]), plan["backlog_limit"], today))
        db.execute("""UPDATE wrong_words SET review_active=1,review_enrolled_at=COALESCE(review_enrolled_at,?)
            WHERE id IN (SELECT word_id FROM daily_review_items WHERE day=?)""", (at, today))


def validate(total, old, *, append=False):
    if (type(total) is not int or not (1 <= total <= MAX_REVIEW_LIMIT)
            or type(old) is not int or not 0 <= old <= total):
        raise ValueError(f"总量须为1至{MAX_REVIEW_LIMIT}的整数，旧词数量不得超过总量")


def capacity(db, day=None):
    if day:
        row = db.execute("SELECT total_limit,old_limit FROM daily_review_days WHERE day=?", (day,)).fetchone()
        if row and row[0] is not None:
            return tuple(row)
    total = max(1, setting(db, "review_total_limit", 40))
    return total, min(total, setting(db, "review_old_limit", 0))


def future_load(db, day):
    # Compatibility diagnostic only: a multi-day aggregate is NOT daily load
    # and must never gate admission of old words.
    until = (date.fromisoformat(day)+timedelta(days=3)).isoformat()
    return db.execute("""SELECT count(*) FROM wrong_words WHERE review_active=1 AND review_due_date<=?""",
                      (until,)).fetchone()[0]


def future_schedule(db, day):
    dates = [(date.fromisoformat(day)+timedelta(days=offset)).isoformat() for offset in (1, 2, 3)]
    counts = dict(db.execute("""SELECT review_due_date,count(*) FROM wrong_words
        WHERE review_active=1 AND review_due_date BETWEEN ? AND ? GROUP BY review_due_date""",
        (dates[0], dates[-1])))
    return [{"day": value, "count": counts.get(value, 0)} for value in dates]


def add_items(repo, db, day, total, old, *, append_limits=None):
    from src.services.memory_scheduler import state_from_row
    from src.services.learning_evidence import as_utc
    from src.services.fsrs7 import retrievability
    from src.services.practice_service import new_long_term_session
    occupied = db.execute("SELECT count(*) FROM daily_review_items WHERE day=?", (day,)).fetchone()[0]
    old_taken = db.execute("""SELECT count(*) FROM daily_review_items WHERE day=? AND selection_reason='old_intake'""",
                           (day,)).fetchone()[0]
    slots = max(0, total-occupied)
    if append_limits is not None:
        slots = min(slots, append_limits[0])
    if not slots:
        return 0
    rows = [dict(r) for r in db.execute("""SELECT w.* FROM wrong_words w WHERE review_due_date<=? AND NOT EXISTS
        (SELECT 1 FROM daily_review_items i WHERE i.day=? AND i.word_id=w.id)""", (day, day))]
    # Compare risk at the plan date, not the physical test/run date.
    now = as_utc(datetime.combine(date.fromisoformat(day), datetime.min.time()))

    def rank(row):
        state = state_from_row(row)
        risk = retrievability(state, max(0, (now-as_utc(row["fsrs_last_at"])).total_seconds()/86400)) if state else 0.
        # Saved application appointment FIRST. Recent completion is NOT demotion.
        return row["review_due_date"], risk, -row["error_count"], row["id"]

    # A batch limit applies to new tasks only. Previously unused daily slots
    # must not enlarge an explicit "add N" request.
    from src.services.review_examples import ReviewExampleLookup
    lookup = ReviewExampleLookup(db, rows)
    chosen = []
    for active, candidates in ((True, [r for r in rows if r["review_active"]]),
                               (False, [r for r in rows if not r["review_active"]])):
        allowance = slots-len(chosen)
        if not active:
            allowance = min(allowance, max(0, old-old_taken))
            if append_limits is not None:
                allowance = min(allowance, append_limits[1])
        for row in sorted(candidates, key=rank):
            if allowance <= 0:
                break
            examples = repo._review_examples(db, [row["id"]], lookup=lookup)
            if not examples:
                continue
            try:
                new_long_term_session(examples)
            except (ValueError, KeyError, TypeError):
                continue
            chosen.append((row, "scheduled_return" if active else "old_intake"))
            allowance -= 1
    at = datetime.now().astimezone().isoformat()
    db.executemany("""INSERT INTO daily_review_items(day,word_id,review_pool,policy_version,original_due_date,
        selection_reason,selected_at) VALUES (?,?,?,?,?,?,?)""",
        [(day, r["id"], r["review_pool"], ADMISSION_POLICY, r["review_due_date"], why, at) for r, why in chosen])
    return len(chosen)


def plan(repo, db, day, start=False):
    if not db.execute("SELECT 1 FROM daily_review_days WHERE day=?", (day,)).fetchone():
        total, old = capacity(db)
        db.execute("""INSERT INTO daily_review_days(day,started,daily_limit,backlog_limit,policy_version,total_limit,old_limit)
            VALUES (?,0,?,0,?,?,?)""", (day, total, ADMISSION_POLICY, total, old))
        add_items(repo, db, day, total, old)
    if start:
        db.execute("UPDATE daily_review_days SET started=1 WHERE day=?", (day,))
        db.execute("""UPDATE wrong_words SET review_active=1,review_enrolled_at=COALESCE(review_enrolled_at,?)
            WHERE id IN (SELECT word_id FROM daily_review_items WHERE day=?)""",
                   (datetime.now().astimezone().isoformat(), day))
    return [r[0] for r in db.execute("""SELECT i.word_id FROM daily_review_items i JOIN wrong_words w ON w.id=i.word_id
        WHERE i.day=? AND i.completed=0 ORDER BY i.rowid""", (day,))]


def configure(repo, db, total, old, today_only=False, *, append_limits=None):
    validate(total, old)
    if append_limits is not None:
        validate(*append_limits)
    enable(repo, db)
    day = repo.today().isoformat()
    if not today_only:
        for key, value in (("review_total_limit", total), ("review_old_limit", old)):
            db.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value)))
    previous = db.execute("SELECT * FROM daily_review_days WHERE day=?", (day,)).fetchone()
    frozen = bool(previous and previous["started"])
    if not frozen:
        db.execute("DELETE FROM daily_review_items WHERE day=?", (day,))
        db.execute("DELETE FROM daily_review_days WHERE day=?", (day,))
        db.execute("""INSERT INTO daily_review_days(day,started,daily_limit,backlog_limit,policy_version,total_limit,old_limit)
            VALUES (?,0,?,0,?,?,?)""", (day, total, ADMISSION_POLICY, total, old))
        added = add_items(repo, db, day, total, old, append_limits=append_limits)
    else:
        before_total, before_old = capacity(db, day)
        actual_total, actual_old = max(total, before_total), max(old, before_old)
        db.execute("UPDATE daily_review_days SET total_limit=?,old_limit=? WHERE day=?", (actual_total, actual_old, day))
        added = (add_items(repo, db, day, actual_total, actual_old, append_limits=append_limits)
                 if total > before_total or old > before_old else 0)
    return dict(added=added, started=frozen, budgets=capacity(db, day))


def append_tasks(repo, db, total, old):
    validate(total, old)
    enable(repo, db)
    day = repo.today().isoformat()
    before, before_old = capacity(db, day)
    validate(before+total, before_old+old)
    # Do not call plan() here: if no preview exists, that would first add an
    # unlimited regular batch outside the user's explicit "add N" request.
    db.execute("""INSERT OR IGNORE INTO daily_review_days
        (day,started,daily_limit,backlog_limit,policy_version,total_limit,old_limit)
        VALUES (?,1,?,0,?,?,?)""", (day, before, ADMISSION_POLICY, before, before_old))
    db.execute("UPDATE daily_review_days SET started=1 WHERE day=?", (day,))
    return configure(repo, db, before+total, before_old+old, True,
                     append_limits=(total, old))["added"]


def status(repo, db):
    day = repo.today().isoformat()
    total, old = capacity(db, day)
    result = repo.review_status()
    for active, key in ((1, "return_waiting"), (0, "old_waiting")):
        result[key] = db.execute("""SELECT count(*) FROM wrong_words w WHERE review_active=? AND review_due_date<=?
            AND NOT EXISTS (SELECT 1 FROM daily_review_items i WHERE i.day=? AND i.word_id=w.id)""",
            (active, day, day)).fetchone()[0]
    counts = dict(db.execute("""SELECT selection_reason,count(*) FROM daily_review_items
        WHERE day=? GROUP BY selection_reason""", (day,)))
    result.update(total_limit=total, old_limit=old, future_reserved=future_load(db, day),
                  daily_default_limit=capacity(db)[0], future_days=future_schedule(db, day),
                  planned_return=counts.get("scheduled_return", 0), planned_old=counts.get("old_intake", 0),
                  planned_other=sum(n for reason, n in counts.items() if reason not in {"scheduled_return", "old_intake"}))
    row = db.execute("SELECT started FROM daily_review_days WHERE day=?", (day,)).fetchone()
    result["started"] = bool(row and row[0])
    return result


def plan_summary(status):
    planned = status['completed']+status['remaining']
    text = (f"今天安排{planned}词：到期复习{status.get('planned_return', 0)}词"
            f"＋重新学习旧词{status.get('planned_old', 0)}词")
    if status.get("planned_other"):
        text += f"＋旧版任务{status['planned_other']}词"
    return text+"。"


def future_text(status):
    days = status.get("future_days", [])
    if not days:
        return ""
    counts = "，".join(f"{label}{row['count']}词" for label, row in zip(("明天", "后天", "大后天"), days))
    text = f"目前已安排：{counts}。\n今日作答后会变化；加入旧词也会增加后续复习量。"
    if any(row['count'] > status.get('daily_default_limit', status['total_limit']) for row in days):
        text += "\n有一天已安排的复习超过日常上限，建议先照顾已有复习。"
    return text


def status_text(status):
    if not status['completed'] and not status['remaining'] and not status['waiting']:
        future = future_text(status) if any(row['count'] for row in status.get('future_days', [])) else ""
        return "今天没有需要复习的词。"+("\n"+future if future else "")
    planned = status['completed']+status['remaining']
    text = plan_summary(status)+f"\n今日已提交{status['completed']}词，待完成{status['remaining']}词。"
    text += f"\n今日上限{status['total_limit']}词，其中旧词最多{status['old_limit']}词。"
    if status["return_waiting"] and planned >= status['total_limit']:
        text += f"\n今日名额已满，另有{status['return_waiting']}词已到复习时间；保留原日期，暂不加入旧词。"
    elif status["return_waiting"]:
        text += f"\n另有{status['return_waiting']}词已到复习时间，尚未加入当前清单；保留原日期，可主动追加。"
    elif status["old_waiting"]:
        if planned >= status['total_limit']:
            text += "\n今日名额已满，剩余旧词分批加入。"
        elif status['old_limit'] == 0:
            text += "\n当前旧词上限为0，暂不加入旧词。"
        elif status.get('planned_old', 0) >= status['old_limit']:
            text += "\n今天重新加入的旧词已达到上限。"
        elif status.get('started'):
            text += "\n今天保留原清单，有余力可主动选择“今天再加一些”。"
        else:
            text += "\n当前清单未自动补齐；可主动追加，可用原句不足时按实际数量安排。"
    text += f"\n等待重新学习：{status['old_waiting']}词，分批加入，不用一天清空。"
    future = future_text(status)
    if future:
        text += "\n"+future
    if status["completed"] and not status["remaining"]:
        text += "\n今日计划已提交，不代表全部记牢或所有到期词已完成。"
    return text + "\n纠错不重复占每日名额；未到复习时间的词不提前抽取。"
