"""Validate daily review on a disposable database copy; never migrate the source."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import date, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtTest import QTest
from src.database.repository import Repository
from src.ui.main_window import MainWindow
from src.ui.dialogs import WrongWordsDialog


def main():
    source = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    target_path = output / "data" / "test.db"
    target_path.parent.mkdir()
    before = sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    before.backup(target)
    target.close()
    tables = [r[0] for r in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    originals = {t: ([r[1] for r in before.execute(f'PRAGMA table_info("{t}")')], before.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()) for t in tables}
    before.close()
    repo = Repository(target_path)
    with repo.connection() as conn:
        for table, (columns, rows) in originals.items():
            names = ','.join('"'+c+'"' for c in columns)
            assert [tuple(r) for r in conn.execute(f'SELECT {names} FROM "{table}" ORDER BY rowid')] == rows, table
        due_counts = [dict(r) for r in conn.execute("SELECT review_due_date, count(*) AS words FROM wrong_words GROUP BY review_due_date ORDER BY review_due_date")]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert repo.today_review_ids() == []
    repo.today = lambda: date.today()+timedelta(days=1)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    for name in ("msyh.ttc", "msyhbd.ttc"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow(repo)
    window.show()
    QTest.qWait(300)
    window.start_daily_review()
    assert window.session and 0 < window.session.total <= 40
    QTest.qWait(200)
    window.grab().save(str(output / "daily-review.png"))
    dialog = WrongWordsDialog(repo, window)
    dialog.show(); QTest.qWait(200)
    dialog.grab().save(str(output / "wrong-words.png"))
    report = {
        "old_tables_unchanged_after_migration": True,
        "old_row_counts": {t: len(rows) for t, (_, rows) in originals.items()},
        "initial_schedule": due_counts,
        "first_review_count": window.session.total,
        "article_sources_in_review": len({i.filename for i in window.session.article.word_items}),
        "backup_database_integrity": "ok",
        "original_database_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == original_hash,
    }
    dialog.close(); window.close(); app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
