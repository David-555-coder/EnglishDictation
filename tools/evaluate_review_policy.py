"""Offline synthetic sensitivity checks; NOT a measurement of student learning.

The true-memory generator uses FSRS-7 itself (self-consistency, not independent
validation). Assumed costs and relearning success must not become user data or
training records. Cost ADR uses upstream initial coefficients, not a personally
trained policy. Only aggregate counts may be read from the optional user DB.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import exp, log, log1p
from pathlib import Path
import argparse
import hashlib
import json
import random
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.fsrs7 import MemoryState, MODEL_ID, interval, next_state, retrievability, S_MIN, S_MAX
from src.services.learning_evidence import as_utc

# fsrs-rs cost_adr.rs at the same pinned revision; BSD-3-Clause.
COEFFICIENTS = [-.202,9.14,-.0978,.226,-5.31,-7.44,24.1,-.375,1.81,-22.9,-5.82,22.3,1.72,-1.99,-19.4]


def select_historical_risk_aging(rows, limit, today):
    """Frozen pre-stream simulation baseline, NOT the current production queue."""
    now = as_utc(datetime.combine(today, datetime.min.time()) + timedelta(hours=12))
    ranked = []
    for row in rows:
        due = date.fromisoformat(row['review_due_date'])
        state = MemoryState(row['fsrs_stability'],row['fsrs_difficulty'],row['fsrs_stability_fast'])
        elapsed = max(0,(now-as_utc(row['fsrs_last_at'])).total_seconds()/86400)
        score = max(0,(.9-retrievability(state,elapsed))/.9) + max(0,(today-due).days)/14
        score += .2 if row['review_relearning'] else 0
        score += min(.05,log1p(row['error_count'])*.02)
        ranked.append((-score,due,-row['error_count'],row['id']))
    return [row[-1] for row in sorted(ranked)[:limit]]


def adr_retention(state,cost_weight):
    x = (log(state.stability)-log(S_MIN))/(log(S_MAX)-log(S_MIN))
    y = (state.difficulty-1)/9
    phi = [1,x,y,x*y,x*x]
    dot = lambda start: sum(a*b for a,b in zip(COEFFICIENTS[start:start+5],phi))
    softplus = lambda v: v if v>20 else exp(v) if v < -20 else log1p(exp(v))
    z = log1p(cost_weight)/log1p(1024)
    value = dot(0)-softplus(dot(5))*z-softplus(dot(10))*z*z
    sigmoid = 1/(1+exp(-value)) if value>=0 else exp(value)/(1+exp(value))
    return .3+.695*sigmoid


@dataclass
class Card:
    state: MemoryState
    last: float
    due: int
    errors: int=1
    confirm: bool=False
    first_review: int|None=None


def coin(seed,day,word,attempt):
    digest = hashlib.sha256(f'{seed}:{day}:{word}:{attempt}'.encode()).digest()
    return int.from_bytes(digest[:8],'little')/2**64


def simulate(backlog,new_per_day,seed,mode,weight=0,days=90):
    rng = random.Random(seed)
    cards = [Card(MemoryState(10**rng.uniform(-.7,1.5),rng.uniform(2,8),10**rng.uniform(-.8,1.4)),
                  -rng.uniform(1,30),-rng.randint(0,20)) for _ in range(backlog)]
    base = date(2026,1,1)
    seconds=attempts=completed=errors=0; overdue_peak=0
    for day in range(days):
        for _ in range(new_per_day):
            state=next_state(next_state(None,1),3,10/1440)
            cards.append(Card(state,day,day+1,confirm=True))
        due = [i for i,c in enumerate(cards) if c.due<=day]
        overdue_peak=max(overdue_peak,len(due))
        if mode=='A':
            selected=sorted(due,key=lambda i:(retrievability(cards[i].state,max(0,day-cards[i].last)),i))[:40]
        else:
            rows=[dict(id=i,review_due_date=(base+timedelta(days=cards[i].due)).isoformat(),
                       error_count=cards[i].errors,review_relearning=cards[i].confirm,
                       last_error_at=base.isoformat(),fsrs_model=MODEL_ID,
                       fsrs_stability=cards[i].state.stability,fsrs_difficulty=cards[i].state.difficulty,
                       fsrs_stability_fast=cards[i].state.stability_fast,
                       fsrs_last_at=as_utc(datetime.combine(base,datetime.min.time())+timedelta(days=cards[i].last)).isoformat())
                  for i in due]
            selected=select_historical_risk_aging(rows,40,base+timedelta(days=day))
        assert len(selected)<=40 and len(selected)==len(set(selected))
        for i in selected:
            card=cards[i]; attempts+=1; seconds+=30; completed+=1
            if card.first_review is None: card.first_review=day
            correct=coin(seed,day,i,0)<retrievability(card.state,max(0,day-card.last))
            card.state=next_state(card.state,3 if correct else 1,max(0,day-card.last)); card.last=day
            card.confirm=not correct
            if not correct:
                errors+=1; card.errors+=1; seconds+=35+30; attempts+=1
                # Exactly one correction; final failure is read, not another forced drill.
                retry_correct=coin(seed,day,i,1)<.85
                card.state=next_state(card.state,3 if retry_correct else 1,10/1440)
                card.last=day+10/1440
                if not retry_correct: seconds+=15
            dr=adr_retention(card.state,weight) if mode=='C' else .9
            card.due=day+1 if card.confirm else max(day+1,int(card.last+interval(card.state,dr)))
    remembered=sum(retrievability(c.state,max(0,days-c.last)) for c in cards)
    waits=[c.first_review for c in cards[:backlog] if c.first_review is not None]
    return dict(mode=mode,cost_weight=weight,seed=seed,new_per_day=new_per_day,initial_backlog=backlog,
                days=days,study_minutes=round(seconds/60,2),expected_remembered=round(remembered,2),
                cards=len(cards),unique_word_visits=completed,attempts=attempts,first_round_errors=errors,
                initial_words_never_selected=sum(c.first_review is None for c in cards[:backlog]),
                maximum_initial_wait_days=max(waits,default=0),peak_due_queue=overdue_peak)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--database'); parser.add_argument('--output',required=True)
    args=parser.parse_args(); aggregate={}; before=None
    if args.database:
        path=Path(args.database).resolve(); before=hashlib.sha256(path.read_bytes()).hexdigest()
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
            aggregate={name:conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                       for name in ('wrong_words','practice_sessions','practice_answers','practice_drafts')}
    started=time.perf_counter()
    # Calibration scenarios choose only a cost weight, not the 15 policy coefficients.
    calibration=[simulate(200,5,42,'C',weight,45) for weight in (0,16,64,256)]
    reference=simulate(200,5,42,'B',days=45)
    eligible=[r for r in calibration if r['expected_remembered']>=reference['expected_remembered']]
    weight=min(eligible or calibration,key=lambda r:r['study_minutes'])['cost_weight']
    results=[simulate(700,new,seed,mode,weight) for new in (0,5,15) for seed in (101,202,303) for mode in ('A','B','C')]
    report=dict(model=MODEL_ID,historical_baseline='B: pre-stream fixed90 + risk/aging',
                production_policy_evaluated=False,cost_adr_enabled=False,
                cost_adr_reason='No measured personal recall/cost data; initial policy simulation is not proof of benefit.',
                assumptions=dict(unique_daily_cap=40,maximum_corrective_retries=1,recall_seconds=30,
                                 error_read_seconds=35,correction_success=.85,final_read_seconds=15,
                                 synthetic_memory_generator='FSRS-7 self-consistency only'),
                aggregate_only=aggregate,calibration_seed=42,evaluation_seeds=[101,202,303],
                calibration=calibration,chosen_candidate_cost_weight=weight,results=results,
                elapsed_seconds=round(time.perf_counter()-started,2))
    if args.database:
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before
        report['original_database_unchanged']=True
    output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'runs':len(results),'seconds':report['elapsed_seconds'],'cost_adr_enabled':False}))


if __name__=='__main__': main()
