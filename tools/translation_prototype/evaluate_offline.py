"""Compare official Argos en->zh package with a minimal CTranslate2 runtime.

Not the full Argos application: no spaCy/Stanza, no document sentence splitter.
Each short sample is fed whole, matching the proposed one/two-sentence use case.
"""
import argparse
import json
from pathlib import Path
import statistics
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    import psutil
    process = psutil.Process()
    before = process.memory_info().rss
    started = time.perf_counter()
    import ctranslate2
    import sentencepiece
    imports_seconds = time.perf_counter()-started
    model = Path(args.model)
    load_start = time.perf_counter()
    translator = ctranslate2.Translator(str(model/'model'), device='cpu',
                                      compute_type='int8', inter_threads=1, intra_threads=2)
    tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(model/'sentencepiece.model'))
    load_seconds = time.perf_counter()-load_start
    rows = []
    cases = json.loads(Path(__file__).with_name('samples.json').read_text(encoding='utf-8'))
    for case in cases:
        start = time.perf_counter()
        tokens = tokenizer.encode(case['text'], out_type=str)
        result = translator.translate_batch([tokens], beam_size=4, replace_unknowns=True,
                                            length_penalty=.2, max_decoding_length=256)
        # Match the official Argos tokenizer's post-processing, including CJK.
        text = tokenizer.decode(result[0].hypotheses[0]).replace('▁', ' ').replace('_', ' ').strip()
        row = {**case, 'translation': text, 'seconds': round(time.perf_counter()-start, 3), 'error': ''}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    elapsed = [r['seconds'] for r in rows]
    result = {'provider':'Argos EN-ZH 1.9 + CTranslate2 INT8 (minimal runner)',
              'synthetic_samples':True, 'normal_learning_database_opened':False,
              'ctranslate2':ctranslate2.__version__, 'sentencepiece':sentencepiece.__version__,
              'summary': {'requests':len(rows), 'successful':len(rows),
                          'median_seconds':statistics.median(elapsed),
                          'p95_seconds':sorted(elapsed)[max(0,int(len(elapsed)*.95)-1)],
                          'imports_seconds':round(imports_seconds,3),
                          'load_seconds':round(load_seconds,3),
                          'rss_before_bytes':before, 'rss_after_bytes':process.memory_info().rss},
              'rows':rows}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
