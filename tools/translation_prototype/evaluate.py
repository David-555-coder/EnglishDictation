"""Explicit online sample runner. Never imports Repository or opens user files."""
import argparse
import json
from pathlib import Path
import statistics
import time

from PySide6.QtCore import QCoreApplication, QTimer

from tools.translation_prototype.engine import TranslationClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--ids', nargs='*')
    args = parser.parse_args()
    samples = json.loads(Path(__file__).with_name('samples.json').read_text(encoding='utf-8'))
    if args.ids:
        samples = [case for case in samples if case['id'] in args.ids]
    app = QCoreApplication([])
    client = TranslationClient(timeout_ms=10000, cache_size=0)
    rows = []
    state = {'index': 0}

    def save():
        times = [r['seconds'] for r in rows if not r['error']]
        result = {'provider': client.provider.name, 'synthetic_samples': True,
                  'normal_learning_database_opened': False,
                  'characters_requested': sum(len(r['text']) for r in rows),
                  'summary': {'requests': len(rows), 'successful': len(times),
                              'median_seconds': statistics.median(times) if times else None,
                              'p95_seconds': sorted(times)[max(0, int(len(times)*.95)-1)] if times else None},
                  'rows': rows}
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    def next_case():
        index = state['index']
        if index >= len(samples):
            save(); print(json.dumps({'finished': len(rows)}, ensure_ascii=False), flush=True)
            app.quit(); return
        state['started'] = time.perf_counter()
        client.request(samples[index]['text'])

    def completed(token, text, error, cached):
        case = samples[state['index']]
        rows.append({**case, 'translation': text, 'error': error,
                     'seconds': round(time.perf_counter()-state['started'], 3)})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        state['index'] += 1
        save()
        # Sequential, modest queries. Respect quota: no auto retries or mirrors.
        QTimer.singleShot(350, next_case)

    client.completed.connect(completed)
    QTimer.singleShot(0, next_case)
    app.exec()


if __name__ == '__main__':
    main()
