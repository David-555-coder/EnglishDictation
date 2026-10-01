"""Stress the real article library via an isolated database copy."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from src.database.repository import Repository
from src.ui.main_window import MainWindow

original = Path(sys.argv[1]).resolve()
before = hashlib.sha256(original.read_bytes()).hexdigest()
root = Path(tempfile.mkdtemp(prefix='EnglishDictationSwitch_'))
source = sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)
target = sqlite3.connect(root / 'db')
source.backup(target); target.close(); source.close()
app = QApplication([])
w = MainWindow(Repository(root / 'db')); w.show()
QTest.qWait(500)
assert len(w.articles) > 1
draft_paths = {s.article.source_path for s in w._drafts.values()}
final_row = next(i for i,a in enumerate(w.articles) if a.source_path not in draft_paths
                 and a.source_path != w.current_article.source_path)
calls = []
switch = w._article_selected
def counted(row): calls.append(row); return switch(row)
w._article_selected = counted
start = time.perf_counter()
for i in range(200):
    w.article_list.setCurrentRow(i % len(w.articles))
    app.processEvents()
w.article_list.setCurrentRow(final_row)
QTest.qWait(300)
# Initial restoration/layout can exceed the debounce window on slower machines.
# Require >95% of rapid requests to be coalesced, and the last selection to win.
assert calls and len(calls) <= 10 and calls[-1] == final_row, calls
assert w.current_article.source_path == w.articles[final_row].source_path
assert len(w.content.findChildren(QLabel)) == len(w.current_article.paragraphs)
result = {'articles': len(w.articles), 'rapid_selection_changes': 201, 'actual_switches': len(calls),
          'seconds_including_settle_wait': round(time.perf_counter() - start, 3),
          'preview_labels': len(w.content.findChildren(QLabel))}
w.close(); app.processEvents()
assert hashlib.sha256(original.read_bytes()).hexdigest() == before
result['original_database_unchanged'] = True
print(json.dumps(result), flush=True)
