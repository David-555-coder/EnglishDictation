"""Read-only queue comparison and synthetic capacity stress, NOT learning efficacy.

The historical selector is frozen at the Oct 8 policy. Synthetic attempts below
all fail and return tomorrow, solely to exercise capacity/coverage. They never
become user records, fitted parameters, or an estimate of retention improvement.
"""
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import argparse
import hashlib
import json
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.database.repository import Repository
from src.services.daily_review import select_review_ids, timely_maintenance
from src.services.fsrs7 import MODEL_ID, next_state, retrievability
from src.services.learning_evidence import as_utc
from src.services.memory_scheduler import state_from_row
from src.services.practice_service import new_long_term_session


def historical_select(rows, limit, today, usable, now):
    ranked = []
    for row in rows:
        due = date.fromisoformat(row['review_due_date'])
        if due > today or row['review_pool'] != 'maintenance':
            continue
        state = state_from_row(row)
        risk = ((1, retrievability(state, max(0, (now-as_utc(row['fsrs_last_at'])).total_seconds()/86400)))
                if state else (0, 0.))
        try:
            recent = -as_utc(row['last_error_at']).timestamp()
        except (TypeError, ValueError):
            recent = 0
        waiting = due
        try:
            completed = date.fromisoformat(row['review_last_completed_day'])
            if completed <= today:
                waiting = max(waiting, completed+timedelta(days=1))
        except (TypeError, ValueError, KeyError):
            pass
        protected = (today-waiting).days >= 3
        rank = (0 if protected else 1, waiting if protected else today, *risk,
                0 if row['review_relearning'] else 1, due, -row['error_count'], recent, row['id'])
        ranked.append((rank, row['id']))
    result = []
    for _, word_id in sorted(ranked):
        if len(result) == limit:
            break
        if usable(word_id):
            result.append(word_id)
    return result


def stress(selector, new_per_day, initial=500, days=28):
    base = date(2026, 10, 12)
    rows, first_visits, arrival = [], {}, {}
    state = next_state(None, 3)
    def add(day, old=False):
        word_id = len(rows)+1
        arrival[word_id] = -1 if old else day
        rows.append(dict(id=word_id, review_pool='maintenance',
            review_due_date=(base+timedelta(days=-6 if old else day)).isoformat(),
            review_relearning=0, error_count=1, last_error_at=base.isoformat(),
            fsrs_model=MODEL_ID, fsrs_stability=state.stability,
            fsrs_difficulty=state.difficulty, fsrs_stability_fast=state.stability_fast,
            fsrs_last_at=(datetime.combine(base, datetime.min.time(), timezone.utc)
                          + timedelta(days=-8 if old else day-1, hours=12)).isoformat(),
            review_last_completed_day=None))
    for _ in range(initial):
        add(0, True)
    for offset in range(days):
        day = base+timedelta(days=offset)
        if offset < len(new_per_day):
            for _ in range(new_per_day[offset]):
                add(offset)
        now = datetime.combine(day, datetime.min.time(), timezone.utc)+timedelta(hours=12)
        selected = selector(rows, 40, day, lambda _: True, now=now)
        assert len(selected) == len(set(selected)) <= 40
        for word_id in selected:
            row = rows[word_id-1]
            assert date.fromisoformat(row['review_due_date']) <= day
            first_visits.setdefault(word_id, offset)
            updated = next_state(state_from_row(row), 1, (now-as_utc(row['fsrs_last_at'])).total_seconds()/86400)
            row.update(fsrs_stability=updated.stability, fsrs_difficulty=updated.difficulty,
                       fsrs_stability_fast=updated.stability_fast, fsrs_last_at=now.isoformat(),
                       review_due_date=(day+timedelta(days=1)).isoformat(),
                       review_last_completed_day=day.isoformat(), review_relearning=1)
    waits = [first_visits[i]-a for i, a in arrival.items() if a >= 0 and i in first_visits]
    return dict(days=days, daily_capacity=40, introduced_after_start=sum(new_per_day),
        unique_covered=len(first_visits), initial_old_covered=sum(i <= initial for i in first_visits),
        new_covered=sum(i > initial for i in first_visits),
        new_never_selected=sum(i > initial and i not in first_visits for i in arrival),
        maximum_new_first_wait=max(waits, default=None),
        first_new_selected_day=first_visits.get(initial+1))


def verify(database, output, day):
    database = database.resolve()
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    report = dict(passed=False, learning_effect_measured=False, synthetic_learning_written=False,
                  automatic_budget_change=False, selection_day=day.isoformat())
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(':memory:')) as snapshot:
        source.backup(snapshot)
        snapshot.row_factory = sqlite3.Row
        before = list(snapshot.iterdump())
        rows = [dict(r) for r in snapshot.execute("""SELECT w.*,
            (SELECT MAX(i.day) FROM daily_review_items i WHERE i.word_id=w.id
             AND i.completed=1 AND i.day<?) AS review_last_completed_day
            , (SELECT MAX(e.observed_at) FROM memory_events e WHERE e.normalized_answer=w.normalized_answer
               AND e.kind='first' AND e.rating IS NOT NULL) AS review_last_independent_at
            FROM wrong_words w WHERE w.review_pool='maintenance' AND w.review_due_date<=?""",
            (day.isoformat(), day.isoformat()))]
        lookup = {r['id']: r for r in rows}
        now = datetime.combine(day, datetime.min.time()).astimezone()+timedelta(hours=12)
        usable_cache = {}
        def usable(word_id):
            if word_id not in usable_cache:
                examples = Repository._review_examples(snapshot, [word_id])
                try:
                    usable_cache[word_id] = bool(examples) and bool(new_long_term_session(examples).total)
                except (TypeError, ValueError, KeyError):
                    usable_cache[word_id] = False
            return usable_cache[word_id]
        report['static_comparison'] = {}
        for name, selector in [('previous', historical_select), ('updated', select_review_ids)]:
            unchanged = deepcopy(rows)
            started = time.perf_counter()
            selected = selector(rows, 40, day, usable, now=now)
            assert rows == unchanged
            assert len(selected) == len(set(selected)) <= 40
            recent = sum(timely_maintenance(lookup[i], day) for i in selected)
            elapsed = sorted((now-as_utc(lookup[i]['fsrs_last_at'])).total_seconds()/86400
                             for i in selected if state_from_row(lookup[i]))
            report['static_comparison'][name] = dict(selected=len(selected), recent_due=recent,
                older_due=len(selected)-recent,
                median_days_since_model_update=(elapsed[(len(elapsed)-1)//2]+elapsed[len(elapsed)//2])/2 if elapsed else None,
                selection_seconds=round(time.perf_counter()-started, 4))
        assert list(snapshot.iterdump()) == before
        report['snapshot_unchanged'] = True
    report['synthetic_capacity_stress'] = []
    for arrivals in ([1], [50, 50, 50, 50]):
        report['synthetic_capacity_stress'].append(dict(new_arrivals=arrivals,
            assumption='Every selected synthetic attempt fails and returns tomorrow; queue behavior only.',
            previous=stress(historical_select, arrivals), updated=stress(select_review_ids, arrivals)))
    report['original_database_unchanged'] = hashlib.sha256(database.read_bytes()).hexdigest() == digest
    assert report['original_database_unchanged']
    report['passed'] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.database, args.output, args.date), ensure_ascii=False))
