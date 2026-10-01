"""Visual/structural QA on an isolated learning-database copy."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from src.database.repository import Repository
from src.services.exporter import export_answer, export_worksheet
from src.services.library import ArticleLibrary
from src.ui.main_window import MainWindow


def main():
    out = ROOT / 'qa' / 'typography-20260928'
    data = out / 'data'; data.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'data' / 'english_dictation.db'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True) as origin:
        with sqlite3.connect(data / source.name) as target: origin.backup(target)
    shutil.copytree(ROOT / 'data' / 'articles', data / 'articles', dirs_exist_ok=True)
    repo = Repository(data / source.name)
    articles, missing = repo.load_articles()
    print('Articles:', len(articles), 'Missing:', missing, flush=True)
    blank = max(articles, key=lambda a: sum(not p.text.strip() for p in a.paragraphs))
    dense = next((a for a in articles if a.filename == 'kcyj6.docx'),
                 max(articles, key=lambda a: max((len(p.text) for p in a.paragraphs), default=0)))
    app = QApplication.instance() or QApplication([]); app.setStyle('Fusion')
    for name in ('msyh.ttc', 'msyhbd.ttc'):
        QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts') / name))
    QMessageBox.question = lambda *a: QMessageBox.Yes
    QMessageBox.information = lambda *a: QMessageBox.Ok
    window = MainWindow(repo); window._article_switch_delay = 0
    window.show(); QTest.qWait(150)
    for number, article in enumerate([blank, dense]):
        print('Checking', article.filename, article.target_count, flush=True)
        for i in range(window.article_list.count()):
            item = window.article_list.item(i)
            if article.filename in item.text():
                window.article_list.setCurrentRow(i); break
        QTest.qWait(150)
        window.show_preview(); window.resize(1480, 900); window.set_zoom(130)
        QTest.qWait(150)
        window.grab().save(str(out / f'{number}-preview.png'))
        window.start_full_practice(); QTest.qWait(150)
        assert len(window.answer_edits) == article.target_count
        window.grab().save(str(out / f'{number}-practice.png'))
        window.resize(1000, 700); window.set_zoom(180); QTest.qWait(150)
        assert window.scroll.horizontalScrollBar().maximum() == 0
        window.grab().save(str(out / f'{number}-narrow.png'))
        bound = ArticleLibrary(repo).bind_copy(article)
        export_worksheet(bound, out / f'{number}-worksheet.docx')
        export_answer(bound, out / f'{number}-answer.docx')
    window.close(); app.processEvents()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    print('QA output:', out, 'Live database unchanged:', digest, flush=True)


if __name__ == '__main__': main()
