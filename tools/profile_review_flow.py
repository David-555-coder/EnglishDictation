"""Paired review-flow diagnostics, always using an isolated database copy.

Usage: python tools/profile_review_flow.py SOURCE_ROOT SNAPSHOT OUTPUT [--edits]
Loads 120/200/300 real example records, but synthetic due dates/answers are only
applied to the disposable copy. This is not a learning-outcome simulation.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import argparse
import ctypes
from ctypes import wintypes
import gc
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import statistics
import sys
import tempfile
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def private_mb():
    if os.name != 'nt':
        return None
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'working', 'paged_peak', 'paged',
                                               'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak', 'private')]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    read = ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not read(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return round(counters.private / 1048576, 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--edits', action='store_true')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    sys.path.insert(0, str(args.source.resolve()))
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
    from src.database.repository import Repository
    from src.services.daily_review import new_daily_session
    from src.services.learning_content import prepare_article, edit_hint
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    for name in ('msyh.ttc', 'msyhbd.ttc'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/name))
    QMessageBox.information = lambda *a, **k: QMessageBox.Ok
    QMessageBox.warning = lambda *a, **k: QMessageBox.Ok
    snapshot_hash = hashlib.sha256(args.snapshot.read_bytes()).hexdigest()
    report = {'source': str(args.source.resolve()), 'synthetic_edits': args.edits,
              'renderer': 'Qt offscreen Fusion; not desktop frame-rate evidence', 'loads': {}}
    with tempfile.TemporaryDirectory(prefix='EnglishDictationReviewFlow_') as temp:
        path = Path(temp)/'data'/'english_dictation.db'
        path.parent.mkdir()
        source = sqlite3.connect(args.snapshot.resolve().as_uri()+'?mode=ro', uri=True)
        target = sqlite3.connect(path)
        source.backup(target); target.close(); source.close()
        repo = Repository(path)
        repo.today = lambda: date(2026, 10, 20)
        if args.edits:
            with repo.connection() as db:
                entry = db.execute('SELECT source_path FROM article_library ORDER BY rowid LIMIT 1').fetchone()[0]
            before = prepare_article(repo.load_article(entry))
            after = deepcopy(before)
            for item in list(after.word_items)[:32]:
                edit_hint(after, item.id, item.chinese, '辨析备注，仅用于隔离性能测试')
            report['edited_paragraphs'] = repo.save_learning_content(before, after)
        original_single_shot = QTimer.singleShot
        QTimer.singleShot = lambda *a: None
        window = MainWindow(repo)
        QTimer.singleShot = original_single_shot
        window.resize(1480, 1000); window.show(); app.processEvents()
        repo.enable_review_continuity()
        sql_count = [0]; json_count = [0]; counting = [False]
        original_connection = repo.connection
        original_loads = json.loads

        @contextmanager
        def traced_connection():
            with original_connection() as db:
                db.set_trace_callback(lambda _: sql_count.__setitem__(0, sql_count[0]+int(counting[0])))
                yield db

        def counted_loads(*a, **k):
            if counting[0]: json_count[0] += 1
            return original_loads(*a, **k)

        repo.connection = traced_connection
        json.loads = counted_loads

        def settle():
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()

        def measure(action):
            sql_count[0] = json_count[0] = 0
            counting[0] = True
            start = time.perf_counter()
            try:
                result = action(); settle()
                elapsed = (time.perf_counter()-start)*1000
            finally:
                counting[0] = False
            return result, {'ms': round(elapsed, 3), 'sql': sql_count[0], 'json_loads': json_count[0]}

        try:
            with repo.connection() as db:
                all_ids = [r[0] for r in db.execute('SELECT id FROM wrong_words ORDER BY id')]
            available = repo.get_review_examples(all_ids)
            usable_ids = [int(r['id'].split('-')[-1]) for r in available]
            for size in (120, 200, 300):
                ids = usable_ids[:size]
                assert len(ids) == size
                with repo.connection() as db:
                    db.execute('UPDATE wrong_words SET review_active=0')
                    db.executemany("UPDATE wrong_words SET review_active=1,review_due_date='2026-10-20' WHERE id=?",
                                   [(i,) for i in ids])
                template_path = Path(temp)/f'load-{size}.db'
                with repo.connection() as db:
                    template = sqlite3.connect(template_path)
                    db.backup(template); template.close()
                samples = {name: [] for name in ('plan', 'examples', 'render', 'input_100', 'submit', 'read_wrong', 'return_result')}
                hashes = {}
                widgets = []
                memory = []
                for repeat in range(args.repeats):
                    window._autosave.stop(); window._view_save.stop()
                    # Saved answers carry reconstructed paragraph indices. Do
                    # not let one timed submission alter the next repetition's
                    # examples, card count or memory footprint.
                    template = sqlite3.connect(template_path)
                    target = sqlite3.connect(path)
                    template.backup(target); target.close(); template.close()
                    with repo.connection() as db:
                        db.execute("DELETE FROM daily_review_items WHERE day='2026-10-20'")
                        db.execute("DELETE FROM daily_review_days WHERE day='2026-10-20'")
                        db.executemany("UPDATE wrong_words SET review_due_date='2026-10-20' WHERE id=?", [(i,) for i in ids])
                    _, sample = measure(lambda: repo.configure_review_capacity(size, 0))
                    samples['plan'].append(sample)
                    selected = repo.today_review_ids()
                    hashes['plan_ids'] = digest(selected)
                    records, sample = measure(lambda: repo.get_review_examples(ids))
                    samples['examples'].append(sample)
                    hashes['examples'] = digest(records)
                    random.seed(40+size)
                    session = new_daily_session(records, '2026-10-20')
                    _, sample = measure(lambda: window._activate_session(session))
                    samples['render'].append(sample)
                    item_map = session.article.item_map
                    first = next(iter(window.answer_edits.values()))

                    def type_input():
                        for index in range(100): first.setText('typing check '+str(index))

                    _, sample = measure(type_input); samples['input_100'].append(sample)
                    for n, (key, edit) in enumerate(window.answer_edits.items()):
                        edit.setText(item_map[key].answer if n % 4 else 'incorrect')
                    window._autosave.stop(); window._view_save.stop()
                    _, sample = measure(window._commit_submission); samples['submit'].append(sample)
                    hashes['grading'] = digest([(r.correct_answer, r.is_correct) for r in session.results])
                    _, sample = measure(window.read_current_wrong); samples['read_wrong'].append(sample)
                    _, sample = measure(window.return_from_wrong_reading); samples['return_result'].append(sample)
                    assert window.session is session and session.submitted
                    widgets.append(len(window.content.findChildren(QWidget)))
                    gc.collect(); memory.append(private_mb())
                report['loads'][str(size)] = {
                    'samples': samples, 'hashes': hashes, 'widgets': widgets, 'private_mb_by_cycle': memory,
                    'median_ms': {name: round(statistics.median(v['ms'] for v in values), 3) for name, values in samples.items()},
                    'max_ms': {name: max(v['ms'] for v in values) for name, values in samples.items()},
                }
                assert len(set(widgets)) == 1, 'Repeated workloads must keep the same widget count'
            window.close(); window.deleteLater(); settle()
            gc.collect(); report['private_mb_after_window_close'] = private_mb()
        finally:
            json.loads = original_loads
    assert hashlib.sha256(args.snapshot.read_bytes()).hexdigest() == snapshot_hash
    report['snapshot_unchanged'] = True
    report['repeat_fixture_restored'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v['median_ms'] for k, v in report['loads'].items()}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
