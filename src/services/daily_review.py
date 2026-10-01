"""Calendar-day scheduling; manual drills never advance long-term stages."""
from datetime import date, timedelta
import random

from src.models.practice import PracticeMode
from src.services.practice_service import new_long_term_session

DAILY_LIMIT = 40
DAILY_LIMIT_OPTIONS = (20, 40, 60, 80)
INTERVALS = (1, 3, 7, 14, 30)


def memory_label(stage, relearning=False):
    if relearning:
        return "待确认"
    return ("待复习" if stage == 0 else "学习中" if stage < 3
            else "较熟悉" if stage == 3 else "基本掌握")


def due_label(value, today=None, planned=False):
    if not value:
        return "待安排"
    today = today or date.today()
    due = date.fromisoformat(value)
    if due <= today:
        return "今日待复习" if planned else "到期待安排"
    if due == today + timedelta(days=1):
        return "明天"
    return due.strftime("%m-%d")


def review_status_text(status, limit=DAILY_LIMIT):
    completed, remaining, waiting = (status[key] for key in ("completed", "remaining", "waiting"))
    if remaining:
        text = f"今日已完成{completed}词，尚有{remaining}词待完成。"
    elif completed:
        text = f"今日{completed}词已完成。"
    elif waiting:
        text = "今天没有已安排的复习任务。"
    else:
        return "今天没有需要复习的词。"
    if waiting:
        text += f"\n另有{waiting}词到期待安排，仍保留在复习队列中。\n今日上限{limit}词，无需今天清空积压。"
    return text


def select_review_ids(rows, limit, today, usable):
    """Reserve 70% for oldest due dates, then recent unresolved errors; fill gaps."""
    oldest = sorted(rows, key=lambda r: r["id"])
    # Stable sorts keep newest errors first within the same due-date/error-count group.
    oldest.sort(key=lambda r: r["last_error_at"], reverse=True)
    oldest.sort(key=lambda r: (r["review_due_date"], -r["error_count"]))
    cutoff = (today - timedelta(days=6)).isoformat()
    recent = [r for r in rows if cutoff <= r["last_error_at"][:10] <= today.isoformat()
              and (not r["review_last_date"] or r["last_error_at"][:10] >= r["review_last_date"])]
    recent.sort(key=lambda r: (r["review_due_date"], r["id"]))
    recent.sort(key=lambda r: r["error_count"], reverse=True)
    recent.sort(key=lambda r: r["last_error_at"], reverse=True)
    # Confirmation remains urgent even after seven days; oldest confirmations first.
    confirmation = [r for r in oldest if r["review_relearning"]]
    beginners = [r for r in recent if r["review_stage"] < 2]
    chosen, checked = [], {}
    def take(candidates, target):
        for row in candidates:
            if len(chosen) >= target:
                break
            word_id = row["id"]
            if word_id in chosen:
                continue
            if word_id not in checked:
                checked[word_id] = usable(word_id)
            if checked[word_id]:
                chosen.append(word_id)
    take(oldest, limit * 7 // 10)
    take(confirmation, limit)
    take(beginners, limit)
    take(recent, limit)
    take(oldest, limit)
    return chosen


def review_transition(stage, relearning, correct):
    """Return (stage, awaiting_confirmation, interval_days) for a due review."""
    if relearning:
        stage = max(1, stage - 1) if correct else 0
        return stage, False, INTERVALS[stage]
    if correct:
        stage = min(stage + 1, len(INTERVALS) - 1)
        return stage, False, INTERVALS[stage]
    if stage >= 2:
        return stage, True, 1
    return 0, False, 1


def new_daily_session(records, day):
    records = list(records)
    random.shuffle(records)
    session = new_long_term_session(records)
    session.mode = PracticeMode.DAILY_REVIEW
    session.article.filename = f"今日复习：{session.total}个"
    session.view_state["daily_review_day"] = day
    return session
