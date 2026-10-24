"""Generate independent f32 reference vectors from official Rust FSRS-7.

Fetch model_v7.rs and inference_v7.rs from the pinned revision into
build/fsrs7-reference, then compile tools/fsrs7_oracle.rs with:
rustc --target wasm32-wasip1 -O -A dead_code -o build/fsrs7-oracle.wasm ...
wasmtime is only a QA dependency, never an application dependency.
"""
import json
from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.fsrs7 import MemoryState, interval, next_state, retrievability, UPSTREAM_REVISION
from wasmtime import Engine, Module, Store, Linker, WasiConfig

root = Path(__file__).resolve().parents[1]
rng = random.Random(70034)
inputs = [[0, 0, 0, 0, rating, retention] for rating in (1, 2, 3, 4) for retention in (.8,.9,.95)]
for _ in range(1200):
    inputs.append([10**rng.uniform(-3,4), rng.uniform(1,10), 10**rng.uniform(-3,4),
                   rng.choice([0,1/86400,10/1440, .5,1,30,365,1000]), rng.randint(1,4), rng.choice([.8,.9,.95])])
with tempfile.TemporaryDirectory() as directory:
    directory = Path(directory)
    inp, out = directory/'in.txt', directory/'out.txt'
    inp.write_text('\n'.join(' '.join(map(str,x)) for x in inputs), encoding='utf-8')
    engine = Engine(); store = Store(engine); wasi = WasiConfig()
    wasi.stdin_file = str(inp); wasi.stdout_file = str(out); store.set_wasi(wasi)
    linker = Linker(engine); linker.define_wasi()
    instance = linker.instantiate(store, Module.from_file(engine, str(root/'build/fsrs7-oracle.wasm')))
    instance.exports(store)['_start'](store)
    expected = [list(map(float, line.split())) for line in out.read_text().splitlines()]
vectors = []
max_relative = 0
for values, reference in zip(inputs, expected, strict=True):
    s,d,sf,t,rating,dr = values
    previous = MemoryState(s,d,sf) if s else None
    actual_state = next_state(previous,int(rating),t)
    actual = [actual_state.stability,actual_state.difficulty,actual_state.stability_fast,
              retrievability(previous,t) if previous else 1, interval(actual_state,dr)]
    for index,(a,b) in enumerate(zip(actual,reference)):
        relative = abs(a-b)/max(abs(b),.0001)
        max_relative = max(max_relative,relative)
        # The upstream Newton interval solver permits 0.001 recall residual.
        if index == 4 and abs(a-b) > max(.002,abs(b)*.015):
            assert abs(retrievability(actual_state,b)-dr) < .0011, (values,actual,reference)
        elif index != 4:
            assert abs(a-b) <= max(.00002,abs(b)*.0005), (values,actual,reference)
    vectors.append({'input':values,'output':reference})
fixture = root/'tests/fixtures/fsrs7_oracle.json'
fixture.parent.mkdir(exist_ok=True)
fixture.write_text(json.dumps({'revision':UPSTREAM_REVISION,'vectors':vectors},indent=2),encoding='utf-8')
print(json.dumps({'vectors':len(vectors),'max_relative_including_newton_intervals':max_relative,'fixture':str(fixture)}))
