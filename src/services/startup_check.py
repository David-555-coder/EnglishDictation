"""Generated-data checks for dated startup recovery and compact scrollbars."""
from datetime import datetime, timedelta


def verify(root, app, output):
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, Qt, QTimer
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
    from src.database.repository import Repository
    from src.services.daily_review import new_daily_session
    from src.services.grading import grade_session
    from src.ui.main_window import MainWindow
    from src.ui.unfinished_dialog import UnfinishedPracticeDialog

    def settle(ms=70):
        loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()
        app.processEvents()

    def dispose(window):
        window.close(); window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); app.processEvents()

    repo = Repository(root / 'startup' / 'data' / 'copy.db')
    repo.enable_review_continuity()
    day = repo.today().isoformat()
    old_day = (repo.today() - timedelta(days=2)).isoformat()
    with repo.connection() as db:
        for i in range(80):
            word = f'word{i}'
            wid = db.execute('''INSERT INTO wrong_words(answer,normalized_answer,chinese,error_count,
                first_error_at,last_error_at,review_due_date,review_active)
                VALUES (?,?,?,1,?,?,?,1)''', (word, word, '示例词', day, day, day)).lastrowid
            db.execute('''INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence)
                VALUES (?,'generated','generated',?)''', (wid, f'Review {word}（示例词） in a complete sentence before moving on.'))
    repo.configure_review_capacity(80, 20)
    ids = repo.today_review_ids(start=True)
    records = repo.get_review_examples(ids)
    current = new_daily_session(records, day)
    old = new_daily_session(records[:40], old_day)
    grade_session(current); current.submitted_at = datetime.now(); repo.save_submitted_session(current)
    repo.save_draft(old)
    with repo.connection() as db:
        original = db.execute('SELECT payload FROM practice_drafts WHERE session_key=?', (old.session_key,)).fetchone()[0]
    window = MainWindow(repo)
    try:
        window.show(); settle(200)
        assert window.session is None and not window.answer_edits
        assert window.mode_title.text() == '今日复习已完成'
        assert '80' in window.progress_text.text() and window.actions['今日复习'].text() == '今日复习（0）'
        window.grab().save(str(output.with_name(output.stem + '-startup-completed.png')))
        drafts = repo.unfinished_practices()
        assert any(s.session_key == old.session_key for s, _ in drafts)
        dialog = UnfinishedPracticeDialog(drafts, window)
        dialog.show(); settle()
        assert any(old_day in dialog.list.item(i).text() for i in range(dialog.list.count()))
        dialog.grab().save(str(output.with_name(output.stem + '-dated-drafts.png')))
        dialog.close(); dialog.deleteLater()
        with repo.connection() as db:
            assert db.execute('SELECT payload FROM practice_drafts WHERE session_key=?', (old.session_key,)).fetchone()[0] == original
        window._activate_session(old); window.set_zoom(160); settle()
        assert old_day in window.mode_title.text()
        for width in (980, 1480):
            window.resize(width, 760); settle()
            bar = window.scroll.verticalScrollBar()
            assert bar.width() == 10 and bar.maximum() > 0
            option = QStyleOptionSlider(); bar.initStyleOption(option)
            assert bar.style().subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarAddLine, bar).isEmpty()
            assert bar.style().subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSubLine, bar).isEmpty()
            pos = window.scroll.viewport().rect().center()
            event = QWheelEvent(pos, window.scroll.viewport().mapToGlobal(pos), QPoint(), QPoint(0, -120),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            app.sendEvent(window.scroll.viewport(), event)
            assert bar.value() > 0
            assert window.session.session_key == old.session_key and not window.session.submitted
            if width == 1480:
                window.grab().save(str(output.with_name(output.stem + '-rounded-scrollbar.png')))
    finally:
        dispose(window)
    with repo.connection() as db: assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    return ['startup_skips_old_daily_draft', 'startup_shows_completed_today', 'preserved_dated_review_drafts',
            'manual_old_draft_date', 'rounded_scrollbar_no_arrows', 'scroll_wheel_after_styling']
