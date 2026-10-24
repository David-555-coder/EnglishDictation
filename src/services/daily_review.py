"""Legacy stream compatibility and daily-session construction.

The desktop app enables review_continuity for live selection. Keep the legacy
selector explicit for historical replay/compatibility, not as a second live rule.
Neither selector changes the FSRS-7 memory model.
"""
from datetime import date, datetime, timedelta
import random

from src.models.practice import PracticeMode
from src.services.practice_service import new_long_term_session

DAILY_LIMIT = 40
DAILY_LIMIT_OPTIONS = (20, 40, 60, 80)
MAX_REVIEW_LIMIT = 10000
MAINTENANCE = "maintenance"
BACKLOG = "backlog"
# Application capacity safeguards, not FSRS parameters/intervals. A recently due
# appointment gets the main capacity; old maintenance retains a bounded lane.
MAINTENANCE_TIMELY_DAYS = 3
MAINTENANCE_RECENT_LEARNING_DAYS = 7
MAINTENANCE_RECOVERY_DIVISOR = 5


def completed_review_day(row, today):
    """Queue rotation includes qualified independent practice outside daily work."""
    days = []
    try:
        days.append(date.fromisoformat(row.get("review_last_completed_day")))
    except (ValueError, TypeError):
        pass
    try:
        from src.services.learning_evidence import as_utc
        days.append(as_utc(row.get("review_last_independent_at")).astimezone().date())
    except (ValueError, TypeError, AttributeError):
        pass
    return max((day for day in days if day <= today), default=date.min)


def timely_maintenance(row, today):
    """Recent appointments/learning get grace, without rewriting overdue dates."""
    due = date.fromisoformat(row["review_due_date"])
    if (today-due).days < MAINTENANCE_TIMELY_DAYS:
        return True
    from src.services.memory_scheduler import state_from_row
    from src.services.learning_evidence import as_utc
    if state_from_row(row) is not None:
        last_day = as_utc(row["fsrs_last_at"]).astimezone().date()
        return due >= last_day and 0 <= (today-last_day).days < MAINTENANCE_RECENT_LEARNING_DAYS
    return False


def validate_budgets(maintenance, backlog):
    if type(maintenance) is not int or not 1 <= maintenance <= MAX_REVIEW_LIMIT:
        raise ValueError(f"正常维护数量必须是1至{MAX_REVIEW_LIMIT}的整数")
    if type(backlog) is not int or not 0 <= backlog <= MAX_REVIEW_LIMIT:
        raise ValueError(f"积压额外数量必须是0至{MAX_REVIEW_LIMIT}的整数")


def initial_review_pool(row, today):
    """One-time upgrade classification, never a rolling seven-day rule."""
    from src.services.memory_scheduler import state_from_row
    row = dict(row)
    try:
        due = date.fromisoformat(row["review_due_date"])
    except (ValueError, TypeError, KeyError):
        return BACKLOG
    return MAINTENANCE if state_from_row(row) is not None and (today-due).days < 7 else BACKLOG


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
        if completed and not remaining:
            text += "\n本轮任务完成不代表所有到期词都已复习。"
    if "maintenance_waiting" in status:
        # Stream counts are explicit; a historical plan is not relabeled as new maintenance.
        text += (f"\n正常维护：待完成{status['maintenance_remaining']}，到期待安排{status['maintenance_waiting']}。"
                 f"\n历史积压：待完成{status['backlog_remaining']}，待安排{status['backlog_waiting']}。")
        if status["legacy_remaining"]:
            text += f"\n升级前保留任务：尚余{status['legacy_remaining']}词。"
        text += (f"\n数量设置：维护上限{status['maintenance_limit']} + 积压额外{status['backlog_limit']}。"
                 "\n未到期词不补抽；完成后可主动追加，纠错不重复占名额。")
        if status["backlog_waiting"] and not status["backlog_limit"]:
            text += "\n积压额外数量当前为0；需要处理旧词时请在数量设置中增加。"
    return text


def select_review_ids(rows, limit, today, usable, *, pool=MAINTENANCE, now=None):
    """Select within one persistent stream; streams never compete for slots.

    Within maintenance, appointments due today/the previous two days get the
    main capacity. Recently learned words retain a seven-day grace window, so a
    missed appointment doesn't immediately drop behind a large old pool. Within
    that lane, least recently completed work goes first, then due date and FSRS
    risk; repeated failures cannot keep taking every slot. Older work gets a bounded
    recovery lane (one fifth, rounded across days for small custom limits),
    oldest waiting first. Unused capacity fills from the other lane. These are
    transient selection lanes, never changes to persistent pools or due dates.
    Backlog orders original due dates first. Counts/errors are only tie-breakers,
    never made-up recall probabilities or an unbounded aging score.
    """
    from src.services.memory_scheduler import state_from_row
    from src.services.fsrs7 import retrievability
    from src.services.learning_evidence import as_utc
    now = as_utc(now or datetime.combine(today, datetime.now().astimezone().timetz()))
    timely, recovery, backlog = [], [], []
    for original in rows:
        row = dict(original)
        try:
            due = date.fromisoformat(row["review_due_date"])
        except (ValueError, TypeError, KeyError):
            continue
        if due > today:
            continue
        assigned = row.get("review_pool") or initial_review_pool(row, today)
        if assigned != pool:
            continue
        try:
            recent = -as_utc(datetime.fromisoformat(row["last_error_at"])).timestamp()
        except (ValueError, TypeError):
            recent = 0
        tie = (due, -row["error_count"], recent, row["id"])
        if pool == MAINTENANCE:
            state = state_from_row(row)
            if state is None:
                # Unknown state gets a separate ordering key, not a fake probability.
                risk = (0, 0.)
            else:
                elapsed = max(0, (now - as_utc(row["fsrs_last_at"])).total_seconds()/86400)
                risk = (1, retrievability(state, elapsed))
            completed = completed_review_day(row, today)
            wait_since = max(due, completed + timedelta(days=1))
            rank = (*risk, 0 if row["review_relearning"] else 1, *tie)
            if timely_maintenance(row, today):
                timely.append(((completed, due, *rank), row["id"]))
            else:
                recovery.append(((wait_since, *rank), row["id"]))
        else:
            backlog.append((tie, row["id"]))
    chosen = []
    chosen_ids = set()
    checked = {}

    def take(ranked, count):
        if count <= 0:
            return
        for _, word_id in ranked:
            if word_id in chosen_ids:
                continue
            if word_id not in checked:
                checked[word_id] = usable(word_id)
            if checked[word_id]:
                chosen.append(word_id)
                chosen_ids.add(word_id)
                count -= 1
                if not count:
                    break

    if pool == MAINTENANCE:
        timely.sort()
        recovery.sort()
        slots, remainder = divmod(max(0, limit), MAINTENANCE_RECOVERY_DIVISOR)
        recovery_slots = slots + int(today.toordinal() % MAINTENANCE_RECOVERY_DIVISOR < remainder)
        take(timely, limit - recovery_slots)
        take(recovery, recovery_slots)
        take(timely, limit - len(chosen))
        take(recovery, limit - len(chosen))
    else:
        take(sorted(backlog), limit)
    return chosen


def new_daily_session(records, day):
    groups = {MAINTENANCE: [], BACKLOG: []}
    for record in records:
        groups[record.get("review_pool", MAINTENANCE)].append(record)
    # Maintain existing sentence grouping and vary order within each stream,
    # while presenting maintenance before additional historical work.
    for group in groups.values():
        random.shuffle(group)
    records = groups[MAINTENANCE] + groups[BACKLOG]
    session = new_long_term_session(records)
    session.mode = PracticeMode.DAILY_REVIEW
    session.article.filename = f"今日复习：{session.total}个"
    session.view_state["daily_review_day"] = day
    session.view_state["daily_review_stream_counts"] = {pool: len(group) for pool, group in groups.items()}
    session.view_state["daily_review_item_pools"] = {
        item_id: record.get("review_pool", MAINTENANCE) for item_id, record in zip(session.target_ids, records)}
    return session
