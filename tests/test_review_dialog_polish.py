import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
import os
from pathlib import Path

from test_library_ui import ui
from test_daily_review import seed
from test_dialog_preferences import add_history
from src.database.repository import Repository
from src.ui.dialogs import WrongWordsDialog, WrongPracticeHistoryDialog


@pytest.fixture(autouse=True)
def readable_fonts(ui):
    for name in ('msyh.ttc', 'segoeui.ttf'):
        path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))


def select_rows(table, *rows):
    table.clearSelection()
    model = table.selectionModel()
    for row in rows:
        model.select(table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)


def test_dialog_default_widths_are_compact(tmp_path, ui):
    repo = Repository(tmp_path / 'db')
    parent = ui(repo)
    book = WrongWordsDialog(repo, parent)
    history = WrongPracticeHistoryDialog(repo, parent)
    assert book.width() <= 960
    assert history.width() <= 740


def test_wordbook_footer_contains_only_delete_start_and_close(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); seed(repo, 3)
    book = WrongWordsDialog(repo, ui(repo))
    assert sorted(button.text() for button in book.findChildren(QPushButton)) == sorted([
        '删除选中错词', '开始练习', '关闭'])
    assert not book.start_button.isEnabled() and not book.delete_button.isEnabled()
    select_rows(book.table, 0, 2)
    assert book.start_button.isEnabled() and book.delete_button.isEnabled()
    assert '2词' in book.start_button.text()
    assert '2' in book.delete_button.text()
    book.show(); QApplication.processEvents()
    assert book.delete_button.x() < book.start_button.x()
    for button in (book.delete_button, book.start_button):
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 28


def test_headers_and_cells_use_matching_text_and_short_field_alignment(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); seed(repo, 2); add_history(repo)
    parent = ui(repo)
    for dialog, left_columns in ((WrongWordsDialog(repo, parent), {0, 1, 4}),
                                 (WrongPracticeHistoryDialog(repo, parent), {1})):
        for column in range(dialog.table.columnCount()):
            expected = Qt.AlignLeft | Qt.AlignVCenter if column in left_columns else Qt.AlignCenter
            assert dialog.table.horizontalHeaderItem(column).textAlignment() == expected
            assert dialog.table.item(0, column).textAlignment() == expected


def test_filter_excludes_hidden_selection_from_counts_and_practice(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); seed(repo, 3)
    book = WrongWordsDialog(repo, ui(repo))
    select_rows(book.table, 0, 1, 2)
    book.search.setText('word1')
    assert '1词' in book.start_button.text()
    emitted = []; book.practiceRequested.connect(emitted.append)
    book.start_button.click()
    assert emitted == [[book.table.item(1, 0).data(Qt.UserRole)]]


def test_filter_with_no_visible_selection_disables_actions(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); seed(repo, 3)
    book = WrongWordsDialog(repo, ui(repo))
    book.table.selectRow(0); book.search.setText('word2')
    assert not book.start_button.isEnabled() and not book.delete_button.isEnabled()
    assert book.start_button.text() == '开始练习'


def test_long_chinese_and_english_keep_full_text_with_bounded_row_height(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); seed(repo, 1)
    chinese = '这是一个需要换行才能显示的较长中文释义，完整内容必须在悬停提示中保留' * 3
    with repo.connection() as conn:
        conn.execute('UPDATE wrong_words SET chinese=?', (chinese,))
    book = WrongWordsDialog(repo, ui(repo)); book.show(); QApplication.processEvents()
    assert book.table.item(0, 1).text() == chinese
    assert book.table.item(0, 1).toolTip() == chinese
    book.table.refresh_row_heights()
    metrics = book.table.fontMetrics()
    assert metrics.height() + 10 < book.table.rowHeight(0) <= max(34, 2 * metrics.height() + 10)


def test_clear_entry_is_in_settings_and_cancel_preserves_records(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db'); seed(repo, 2); add_history(repo)
    window = ui(repo)
    calls = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: calls.append(args) or QMessageBox.No)
    assert window.actions['清空错词本…'] in window.data_management_menu.actions()
    window.actions['清空错词本…'].trigger()
    assert calls and calls[0][-1] == QMessageBox.No
    assert len(repo.list_wrong_words()) == 2 and len(repo.list_history()) == 4


def test_confirmed_clear_uses_existing_semantics_without_deleting_history(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db'); seed(repo, 2); add_history(repo)
    window = ui(repo)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Yes)
    window.actions['清空错词本…'].trigger()
    assert not repo.list_wrong_words() and len(repo.list_history()) == 4


def test_history_multi_selection_only_enables_delete(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); add_history(repo)
    dialog = WrongPracticeHistoryDialog(repo, ui(repo))
    select_rows(dialog.table, 0, 1)
    assert dialog.delete_button.isEnabled() and not dialog.start_button.isEnabled()
    select_rows(dialog.table, 1)
    assert dialog.start_button.isEnabled()


def test_cancel_word_deletion_preserves_selected_and_other_rows(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db'); seed(repo, 3)
    book = WrongWordsDialog(repo, ui(repo)); select_rows(book.table, 0, 2)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No)
    book.delete_button.click()
    assert len(repo.list_wrong_words()) == 3


@pytest.mark.parametrize('kind,width', [('book', 860), ('book', 950), ('book', 1100),
                                       ('history', 720), ('history', 900), ('history', 1100)])
def test_resized_dialogs_fit_columns_and_preserve_sort_and_tooltips(tmp_path, ui, kind, width):
    repo = Repository(tmp_path / 'db'); seed(repo, 3); add_history(repo)
    parent = ui(repo)
    dialog = WrongWordsDialog(repo, parent) if kind == 'book' else WrongPracticeHistoryDialog(repo, parent)
    dialog.sort.setCurrentIndex(1)
    order = [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())]
    tooltips = [dialog.table.item(row, 3 if kind == 'book' else 0).toolTip()
                for row in range(dialog.table.rowCount())]
    dialog.show(); dialog.resize(width, 500); QApplication.processEvents(); QTest.qWait(70)
    assert dialog.table.horizontalScrollBar().maximum() == 0
    assert [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())] == order
    assert [dialog.table.item(row, 3 if kind == 'book' else 0).toolTip()
            for row in range(dialog.table.rowCount())] == tooltips


def test_dragging_window_coalesces_row_measurement(tmp_path, ui):
    repo = Repository(tmp_path / 'db'); seed(repo, 100)
    dialog = WrongWordsDialog(repo, ui(repo)); dialog.show(); QApplication.processEvents(); QTest.qWait(70)
    timer = dialog.table._row_timer
    timer.timeout.disconnect()
    calls = []
    def measure():
        calls.append(1)
        dialog.table.refresh_row_heights()
    timer.timeout.connect(measure)
    for width in (900, 920, 940, 960, 980):
        dialog.resize(width, 550); QApplication.processEvents()
    QTest.qWait(70)
    assert len(calls) == 1
