import json
import math
from pathlib import Path

import pytest

from src.services.fsrs7 import MemoryState, next_state, interval, retrievability, MODEL_ID, UPSTREAM_REVISION

FIXTURE = json.loads((Path(__file__).parent/'fixtures/fsrs7_oracle.json').read_text())


@pytest.mark.parametrize('vector', FIXTURE['vectors'])
def test_official_rust_scalar_vectors(vector):
    assert FIXTURE['revision'] == UPSTREAM_REVISION
    s,d,sf,t,rating,dr = vector['input']
    previous = MemoryState(s,d,sf) if s else None
    state = next_state(previous,int(rating),t)
    actual = [state.stability,state.difficulty,state.stability_fast,
              retrievability(previous,t) if previous else 1, interval(state,dr)]
    for index,(value,expected) in enumerate(zip(actual,vector['output'])):
        if index < 4:
            assert value == pytest.approx(expected,rel=.0005,abs=.00002)
        else:
            assert value == pytest.approx(expected,rel=.015,abs=.002)


@pytest.mark.parametrize('t', [0,1/86400,1/1440,.5,1,30,365,10000])
def test_dual_trace_curve_and_intervals(t):
    state = MemoryState(10,5,8)
    assert 0 <= retrievability(state,t) <= 1
    assert retrievability(state,t+1) < retrievability(state,t)
    for retention in (.8,.9,.95):
        assert retrievability(state,interval(state,retention)) == pytest.approx(retention,abs=.00001)
    assert interval(state,.95) < interval(state,.9) < interval(state,.8)


@pytest.mark.parametrize('invalid', [math.nan,math.inf,-1])
def test_rejects_invalid_time_and_states(invalid):
    with pytest.raises(ValueError):
        next_state(None,3,invalid)
    with pytest.raises(ValueError):
        MemoryState(invalid,5,1)


def test_same_day_relearning_uses_minutes_not_rounded_days():
    state = next_state(None,1)
    immediate = next_state(state,3,0)
    delayed = next_state(state,3,10/1440)
    assert immediate != delayed
    assert delayed.stability_fast != delayed.stability
    assert MODEL_ID.startswith('fsrs7-dual-34')
