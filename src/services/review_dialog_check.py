"""Synthetic review-dialog QA called only by the isolated release verifier."""
from datetime import datetime


def verify(repository, window, app, output):
    from PySide6.QtCore import QEventLoop, QItemSelectionModel, QTimer, Qt
    from PySide6.QtWidgets import QMessageBox, QPushButton
    from src.ui.dialogs import WrongWordsDialog, WrongPracticeHistoryDialog

    def settle():
        loop = QEventLoop(); QTimer.singleShot(60, loop.quit); loop.exec()
        app.processEvents()

    now = datetime.now().isoformat(timespec='seconds')
    pairs = [('a crumpled paper', '一张皱巴巴的纸'), ('a deck of cards', '一副牌'),
             ('a delicate sculpture', '精致的雕塑'), ('a dust bunny', '一团灰尘'),
             ('a food blogger', '一个美食博主'), ('a large crowd', '一大群人'),
             ('A long pause', '长时间的沉默'), ('a piece of lint', '一团绒毛'),
             ('A round of applause', '一阵掌声'), ('a set of keys', '一串钥匙'),
             ('a song trending worldwide with a very long description', '全球流行歌曲'),
             ('a word with detailed meaning', '需要较完整解释的中文释义，超长内容仍能通过悬停查看且不会改变原始文字。' * 3)]
    with repository.connection() as conn:
        for index, (answer, chinese) in enumerate(pairs):
            row = conn.execute('''INSERT INTO wrong_words
                (normalized_answer,answer,chinese,error_count,first_error_at,last_error_at,review_due_date)
                VALUES (?,?,?,1,?,?,?)''', (answer.casefold(), answer, chinese, now, now, repository.today().isoformat()))
            conn.execute('''INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence)
                VALUES (?,?,?,?)''', (row.lastrowid, f'demo{index + 1}.docx', f'demo{index + 1}.docx', f'Use {answer} here.'))
        conn.execute('''INSERT INTO practice_sessions
            (submitted_at,article_name,source_path,practice_type,target_count,correct_count,wrong_count,accuracy)
            VALUES (?,?,?,'本次错词复习',80,40,40,50)''',
                     (now, 'A long classroom article filename that needs an ellipsis.docx', 'synthetic.docx'))

    def select(table, rows):
        table.clearSelection()
        for row in rows:
            table.selectionModel().select(table.model().index(row, 0),
                                          QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def check_alignment(dialog, left_columns):
        table = dialog.table
        for col in range(table.columnCount()):
            alignment = Qt.AlignLeft | Qt.AlignVCenter if col in left_columns else Qt.AlignCenter
            assert table.horizontalHeaderItem(col).textAlignment() == alignment
            assert table.item(0, col).textAlignment() == alignment
        assert table.horizontalScrollBar().maximum() == 0

    book = WrongWordsDialog(repository, window); book.show(); settle()
    assert book.width() <= 960
    assert sorted(button.text() for button in book.findChildren(QPushButton)) == sorted([
        '删除选中错词', '开始练习', '关闭'])
    assert not book.start_button.isEnabled() and not book.delete_button.isEnabled()
    check_alignment(book, {0, 1, 4})
    long_row = next(row for row in range(book.table.rowCount()) if book.table.item(row, 0).text() == pairs[-1][0])
    assert book.table.item(long_row, 1).toolTip() == pairs[-1][1]
    assert 34 < book.table.rowHeight(long_row) <= max(34, 2 * book.table.fontMetrics().height() + 10)
    select(book.table, [0, 1]); settle(); assert '2词' in book.start_button.text()
    assert book.delete_button.x() < book.start_button.x()
    for button in (book.delete_button, book.start_button):
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 28
    book.grab().save(str(output.with_name(output.stem + '-wordbook.png')))
    before = len(repository.list_wrong_words())
    question, warning = QMessageBox.question, QMessageBox.warning
    try:
        QMessageBox.question = lambda *args: QMessageBox.No
        QMessageBox.warning = lambda *args: QMessageBox.No
        book.delete_button.click()
        assert len(repository.list_wrong_words()) == before
        assert window.actions['清空错词本…'] in window.data_management_menu.actions()
        window.actions['清空错词本…'].trigger()
        assert len(repository.list_wrong_words()) == before
    finally:
        QMessageBox.question, QMessageBox.warning = question, warning
    book.search.setText(book.table.item(0, 0).text()); settle()
    assert '1词' in book.start_button.text()
    emitted = []; book.practiceRequested.connect(emitted.append)
    book.start_button.click()
    assert emitted == [[book.table.item(0, 0).data(Qt.UserRole)]]
    book.deleteLater()

    history = WrongPracticeHistoryDialog(repository, window); history.show(); settle()
    assert history.width() <= 740
    check_alignment(history, {1})
    select(history.table, [0, 1]); settle()
    assert not history.start_button.isEnabled() and history.delete_button.isEnabled()
    assert history.table.item(0, 0).toolTip()
    history.grab().save(str(output.with_name(output.stem + '-history.png')))
    select(history.table, [0]); assert history.start_button.isEnabled()
    history.close(); history.deleteLater()
    return ['compact_review_windows', 'consistent_column_alignment', 'bounded_bilingual_rows',
            'visible_multiselection_scope', 'review_deletion_cancel', 'clear_book_settings_entry']
