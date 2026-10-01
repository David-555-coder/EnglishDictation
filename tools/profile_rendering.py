"""Measure rendering/query changes on a database copy; print comparable hashes.

Usage: python tools/profile_rendering.py SOURCE_ROOT DATABASE_SNAPSHOT
The source root can point at a pre-change backup for an independent baseline.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
source_root, database = (Path(value).resolve() for value in sys.argv[1:3])
copy_root = Path(tempfile.mkdtemp(prefix='EnglishDictationRenderingProfile_'))
with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
    with sqlite3.connect(copy_root / 'db') as target:
        source.backup(target)
sys.path.insert(0, str(source_root))
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from src.database.repository import Repository
from src.services.practice_service import new_full_session
from src.ui.main_window import MainWindow

app = QApplication([]); app.setStyle('Fusion')
for name in ('msyh.ttc', 'msyhbd.ttc'):
    QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
repo = Repository(copy_root / 'db')
window = MainWindow(repo); window.show()
deadline = time.monotonic() + 10
while not window.articles and time.monotonic() < deadline:
    app.processEvents(); QTest.qWait(10)
QTest.qWait(150)
article = window._materialize_article(max(window.articles, key=lambda value: value.target_count))
window.current_article = article
window.session = new_full_session(article)

def timed(action):
    start = time.perf_counter(); action(); app.processEvents()
    return (time.perf_counter() - start) * 1000

render = [timed(lambda: window._render_practice(True)) for _ in range(4)]
edits = dict(window.answer_edits)
first_id = next(iter(edits)); edits[first_id].setText('performance check')
zooms = []; hashes = []
for percent in (100, 120, 130, 150, 100, 120, 130, 150):
    window.zoom_percent = percent
    zooms.append(timed(window._apply_zoom_to_content))
    widgets = window.content.findChildren(QWidget)
    geometry = [(type(widget).__name__, widget.geometry().getRect(), widget.isHidden(),
                 widget.text() if isinstance(widget, QLabel) else '') for widget in widgets]
    hashes.append(hashlib.sha256(repr(geometry).encode()).hexdigest())
assert dict(window.answer_edits) == edits
assert edits[first_id].text() == window.session.answers[first_id] == 'performance check'
window._apply_answer_hints(); app.processEvents()
window._hide_answer_hints(); app.processEvents()
assert edits[first_id].text() == 'performance check'
ids = [record['id'] for record in repo.list_wrong_words()]
examples = {}
for count in (40, len(ids)):
    times = []
    for _ in range(3):
        start = time.perf_counter(); records = repo.get_review_examples(ids[:count])
        times.append((time.perf_counter()-start)*1000)
    examples[count] = {'median_ms': round(statistics.median(times), 2), 'count': len(records),
                       'hash': hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
print(json.dumps({'source': str(source_root), 'targets': article.target_count,
                  'widgets': len(window.content.findChildren(QWidget)),
                  'render_median_ms': round(statistics.median(render[1:]), 2),
                  'zoom_median_ms': round(statistics.median(zooms), 2),
                  'layout_hashes': hashes, 'examples': examples}, ensure_ascii=False), flush=True)
window.close()
