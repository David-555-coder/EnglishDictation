from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView

from test_library_ui import ui
from test_daily_review import seed
from src.database.repository import Repository
from src.ui.dialogs import WrongPracticeHistoryDialog, WrongWordsDialog


def add_history(repo):
    with repo.connection() as conn:
        for stamp, name in (
            ('2026-10-01T08:03:04', 'djci2.docx'),
            ('2026-10-01T17:23:45', 'djci10.docx'),
            ('2026-09-30T12:34:56', 'djci1.docx'),
            ('2025-12-31T23:59:59', 'very-long-article-name-' * 8 + '.docx'),
        ):
            conn.execute('''INSERT INTO practice_sessions
                (submitted_at,article_name,source_path,practice_type,target_count,
                 correct_count,wrong_count,accuracy,viewed_answer)
                VALUES (?, ?, ?, '整篇文章', 2, 1, 1, 50, 0)''', (stamp, name, name))


def test_dialog_sorts_survive_reopen_and_new_repository_independently(tmp_path, ui):
    path = tmp_path / 'db'
    repo = Repository(path)
    seed(repo, 3)
    add_history(repo)
    parent = ui(repo)
    book = WrongWordsDialog(repo, parent)
    book.sort.setCurrentIndex(book.sort.findData('errors'))
    book.close()
    history = WrongPracticeHistoryDialog(repo, parent)
    history.sort.setCurrentIndex(history.sort.findData('article'))
    history.close()

    reopened = Repository(path)
    book = WrongWordsDialog(reopened, parent)
    history = WrongPracticeHistoryDialog(reopened, parent)
    all_history = WrongPracticeHistoryDialog(reopened, parent, wrong_only=False)
    assert book.sort.currentData() == 'errors'
    assert book.table.item(0, 0).text() == 'word2'
    assert history.sort.currentData() == 'article'
    assert history.table.item(0, 1).text() == 'djci1.docx'
    assert all_history.sort.currentData() == 'completed'
    assert len(reopened.list_history()) == 4
    assert len(reopened.list_wrong_words()) == 3


def test_history_short_dates_keep_exact_time_order_and_tooltips(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db')
    monkeypatch.setattr(repo, 'today', lambda: date(2026, 10, 1))
    add_history(repo)
    dialog = WrongPracticeHistoryDialog(repo, ui(repo))
    assert dialog.windowTitle() == '历史错词练习'
    assert [dialog.table.item(row, 0).text() for row in range(4)] == [
        '今天', '今天', '昨天', '2025-12-31']
    assert dialog.table.item(0, 0).toolTip() == '2026-10-01 17:23:45'
    assert dialog.table.item(1, 0).toolTip() == '2026-10-01 08:03:04'
    assert dialog.table.item(3, 1).toolTip() == repo.list_history()[3]['article_name']
    assert dialog.table.item(0, 3).textAlignment() == Qt.AlignCenter
    assert dialog.table.horizontalHeader().sectionResizeMode(1) == QHeaderView.Interactive
    dialog.show()
    dialog.resize(1200, 550)
    QApplication.processEvents()
    assert dialog.table.columnWidth(1) <= 360
    dialog.table.setColumnWidth(1, 300)
    assert dialog.table.columnWidth(1) == 300


@pytest.mark.parametrize('bad_value', ['unknown', ['errors'], None])
def test_invalid_sort_preferences_fall_back_without_writing_on_open(tmp_path, ui, monkeypatch, bad_value):
    repo = Repository(tmp_path / 'db')
    repo.set_setting('wrong_words_sort', bad_value)
    repo.set_setting('wrong_history_sort', bad_value)
    writes = []
    monkeypatch.setattr(repo, 'set_setting', lambda *args: writes.append(args))
    parent = ui(repo)
    book = WrongWordsDialog(repo, parent)
    history = WrongPracticeHistoryDialog(repo, parent)
    assert book.sort.currentData() == 'alphabet'
    assert history.sort.currentData() == 'completed'
    assert not writes


def test_sort_save_failure_does_not_break_current_sort(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db')
    seed(repo, 3)
    dialog = WrongWordsDialog(repo, ui(repo))
    def fail(*args):
        raise OSError('read-only settings')
    monkeypatch.setattr(repo, 'set_setting', fail)
    dialog.sort.setCurrentIndex(dialog.sort.findData('errors'))
    assert dialog.table.item(0, 0).text() == 'word2'
