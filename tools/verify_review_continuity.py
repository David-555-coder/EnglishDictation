"""Snapshot replay + queue pressure, never a measurement of learning efficacy.

Reads the supplied backup read-only. All migrations/attempts live in temp copies.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
import argparse
import hashlib
import json
import random
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.database.repository import Repository
from src.services.daily_review import select_review_ids, new_daily_session
from src.services.fsrs7 import MODEL_ID, next_state
from src.services.grading import grade_session
from src.services.practice_service import new_wrong_session


def digest_tables(db):
    tables = ('wrong_words', 'word_examples', 'practice_sessions', 'practice_answers', 'article_library', 'practice_drafts')
    result = {}
    for table in tables:
        columns = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
        rows = db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY rowid').fetchall()
        result[table] = (columns, hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest())
    return result


def replay(source, root):
    path = root / 'snapshot.db'
    with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as before, sqlite3.connect(path) as copy:
        originals = digest_tables(before)
        before.backup(copy)
    repo = Repository(path)
    day = repo.today().isoformat()
    with repo.connection() as db:
        last = db.execute("SELECT submission_key FROM practice_sessions WHERE practice_type='今日复习' ORDER BY submitted_at DESC LIMIT 1").fetchone()
        if not last:
            raise AssertionError('snapshot has no daily first round')
        failures = {r[0] for r in db.execute("""SELECT normalized_answer FROM practice_answers a JOIN practice_sessions s
            ON a.session_id=s.id WHERE s.submission_key=? AND a.is_correct=0""", (last[0],))}
        rows = [dict(r) for r in db.execute("""SELECT w.*,
            (SELECT MAX(i.day) FROM daily_review_items i WHERE i.word_id=w.id AND i.completed=1 AND i.day<?) AS review_last_completed_day,
            (SELECT MAX(e.observed_at) FROM memory_events e WHERE e.normalized_answer=w.normalized_answer AND e.kind='first' AND e.rating IS NOT NULL) AS review_last_independent_at
            FROM wrong_words w""", (day,))]
        usable_cache = {}
        def usable(wid):
            if wid not in usable_cache:
                usable_cache[wid] = bool(repo._review_examples(db, [wid]))
            return usable_cache[wid]
        legacy = {}
        for cap in (40, 60, 80, 120):
            ids = select_review_ids(rows, cap, repo.today(), usable)
            names = {r['normalized_answer'] for r in rows if r['id'] in ids}
            legacy[cap] = dict(selected=len(ids), yesterday_failures_returned=len(names & failures))
    started = time.perf_counter()
    repo.enable_review_continuity()
    with repo.connection() as db:
        for table, (columns, expected) in originals.items():
            actual = [tuple(r) for r in db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY rowid')]
            assert hashlib.sha256(json.dumps(actual, ensure_ascii=False).encode()).hexdigest() == expected, table
    result = dict(failed_words=len(failures), legacy=legacy, old_learning_columns_unchanged=True)
    result['mapped_capacity'] = repo.review_capacity()
    ids = repo.today_review_ids()
    names = {r['normalized_answer'] for r in rows if r['id'] in ids}
    result.update(new_selected=len(ids), yesterday_failures_returned=len(names & failures), status=repo.continuity_status())
    result['migration_and_plan_ms'] = round((time.perf_counter()-started)*1000, 2)
    assert failures.issubset(names)
    with repo.connection() as db:
        result['selection_reasons'] = dict(db.execute('SELECT selection_reason,count(*) FROM daily_review_items WHERE day=? GROUP BY selection_reason',(day,)))
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    return result


def seed(repo, day):
    state = next_state(None, 1)
    with repo.connection() as db:
        for i in range(337):
            name, active = f'word{i}', i < 37
            due = day if active else day-timedelta(days=20)
            wid = db.execute("""INSERT INTO wrong_words(answer,normalized_answer,error_count,first_error_at,last_error_at,
                review_due_date,review_pool,review_active) VALUES (?,?,1,?,?,?, ?,?)""",
                (name,name,day.isoformat(),day.isoformat(),due.isoformat(),'maintenance' if active else 'backlog',int(active))).lastrowid
            db.execute("INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence) VALUES (?,'demo','demo',?)",(wid,f'Use {name} here.'))
            if active:
                db.execute("""UPDATE wrong_words SET fsrs_model=?,fsrs_stability=?,fsrs_difficulty=?,fsrs_stability_fast=?,fsrs_last_at=?,review_relearning=1
                    WHERE id=?""",(MODEL_ID,state.stability,state.difficulty,state.stability_fast,(day-timedelta(days=1)).isoformat(),wid))


def stress(root, cap, success, missed):
    current = [date(2026,10,16)]
    original = Repository.__dict__['today']
    Repository.today = staticmethod(lambda: current[0])
    try:
        repo = Repository(root / f'{cap}-{success}-{missed}.db')
        repo.enable_review_continuity(); seed(repo, current[0]); repo.configure_review_capacity(cap, 20)
        rng = random.Random(42)
        delayed, selected_total, old_intake, return_count, peak_wait = [], 0, 0, 0, 0
        for offset in range(28):
            current[0] = date(2026,10,16)+timedelta(days=offset)
            if missed and offset in (6,13,20):
                continue
            ids = repo.today_review_ids(start=True)
            assert len(ids) == len(set(ids)) and len(ids) <= cap
            with repo.connection() as db:
                for row in db.execute('SELECT original_due_date,selection_reason FROM daily_review_items WHERE day=?',(current[0].isoformat(),)):
                    due = date.fromisoformat(row['original_due_date'])
                    assert due <= current[0]
                    if row['selection_reason'] == 'scheduled_return':
                        delayed.append((current[0]-due).days); return_count += 1
                    else:
                        old_intake += 1
            selected_total += len(ids)
            if not ids:
                continue
            session = new_daily_session(repo.get_review_examples(ids), current[0].isoformat())
            session.view_state['memory']['began_at'] = datetime.combine(current[0], datetime.min.time()).isoformat()
            session.answers = {i: session.article.item_map[i].answer if rng.random() < success else 'incorrect' for i in session.target_ids}
            grade_session(session); session.submitted_at = datetime.combine(current[0],datetime.min.time())+timedelta(hours=12)
            repo.save_submitted_session(session)
            if session.wrong_ids:
                correction = new_wrong_session(session)
                correction.view_state['memory']['began_at'] = (session.submitted_at+timedelta(minutes=5)).isoformat()
                correction.answers = {i: correction.article.item_map[i].answer if rng.random() < .5 else 'incorrect' for i in correction.target_ids}
                grade_session(correction); correction.submitted_at = session.submitted_at+timedelta(minutes=10)
                repo.save_submitted_session(correction)
            assert repo.today_review_ids() == []  # No automatic extra slots.
            peak_wait = max(peak_wait, repo.continuity_status()['return_waiting'])
        return dict(days=28,total_cap=cap,assumed_first_success=success,missed_days=3 if missed else 0,
                    selected_total=selected_total,old_intake=old_intake,return_attempts=return_count,
                    maximum_return_delay_days=max(delayed,default=0),mean_return_delay_days=round(sum(delayed)/max(1,len(delayed)),3),
                    peak_return_waiting=peak_wait,all_capacity_and_no_future_checks_passed=True)
    finally:
        Repository.today = original


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--snapshot',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(); source = args.snapshot.resolve()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='continuity-qa-') as folder:
        root = Path(folder)
        report = dict(snapshot_replay=replay(source,root), simulations=[stress(root,cap,success,missed)
                      for cap in (40,80,120) for success in (0.,.25) for missed in (False,True)],
                      learning_efficacy_measured=False,models_or_weights_fitted=False)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    report['snapshot_unchanged'] = True
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['snapshot_replay'],ensure_ascii=False))


if __name__ == '__main__': main()
