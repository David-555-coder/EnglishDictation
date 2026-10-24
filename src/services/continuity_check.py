"""Isolated packaged QA for the actual production review policy."""
from datetime import datetime, timedelta


def verify(root, parent, app, output):
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QLabel
    from src.database.repository import Repository
    from src.services.daily_review import new_daily_session
    from src.services.practice_service import new_wrong_session
    from src.services.grading import grade_session
    from src.ui.main_window import MainWindow
    from src.ui.daily_review_dialog import ReviewQuantityDialog
    repo = Repository(root / 'continuity' / 'copy.db')
    repo.enable_review_continuity()
    day = repo.today().isoformat()
    with repo.connection() as db:
        for i in range(120):
            name = f'word{i}'
            wid = db.execute("""INSERT INTO wrong_words(answer,normalized_answer,error_count,first_error_at,last_error_at,
                review_due_date,review_active) VALUES (?,?,1,?,?,?,?)""",(name,name,day,day,day if i<74 else '2026-01-01',int(i<74))).lastrowid
            db.execute("INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence) VALUES (?,'demo','demo',?)",(wid,f'Use {name} here.'))
    repo.configure_review_capacity(80,20)
    ids = repo.today_review_ids(start=True)
    assert len(ids) == 80 and set(range(1,75)).issubset(ids)
    with repo.connection() as db:
        assert db.execute("SELECT count(*) FROM daily_review_items WHERE selection_reason='old_intake'").fetchone()[0] == 6
    for width in (480,550,720):
        dialog = ReviewQuantityDialog(80,20,repo.continuity_status(),parent)
        dialog.resize(width,dialog.sizeHint().height()); dialog.show(); app.processEvents()
        assert '80词，其中旧词最多20词' in dialog.summary.text()
        assert all(not any(term in label.text() for term in ('维护','积压')) for label in dialog.findChildren(QLabel))
        for label in dialog.findChildren(QLabel):
            if label.hasHeightForWidth(): assert label.height() >= label.heightForWidth(label.width())
        if width == 550: dialog.grab().save(str(output.with_name(output.stem+'-continuity-settings.png')))
        dialog.close(); dialog.deleteLater()
    def submit(session, at):
        grade_session(session); session.submitted_at=at; repo.save_submitted_session(session)
    first = new_daily_session(repo.get_review_examples(ids[:13]),day)
    at = datetime.now(); submit(first,at)
    correction = new_wrong_session(first); submit(correction,at+timedelta(seconds=1))
    extra = repo.create_consolidation(correction)
    assert extra.total == 10 and not extra.view_state['memory']['exposed']
    restored = Repository(repo.database_path).load_drafts()
    assert any(s.session_key == extra.session_key and s.view_state.get('consolidation') for s in restored)
    assert repo.create_consolidation(correction).total == 3
    assert repo.create_consolidation(correction) is None
    child = MainWindow(repo,parent)
    child.show(); child._activate_session(extra); app.processEvents()
    assert not child._answers_visible and len(child.answer_edits) == 10
    assert child.mode_title.text() == '小组再练　·　10词'
    child.grab().save(str(output.with_name(output.stem+'-consolidation.png')))
    child._commit_submission(); app.processEvents()
    assert child.return_result_button.isVisible() and child.read_wrong_button.isVisible()
    with repo.connection() as db:
        before_return = [tuple(r) for r in db.execute('SELECT * FROM practice_sessions')]
    child.return_result_button.click(); app.processEvents()
    assert child.session.session_key == correction.session_key
    assert child.session.results == correction.results and child.session.wrong_count == 13
    assert child.progress.format() == '本轮已提交'
    assert not child.primary_action_button.isEnabled() and child.read_wrong_button.isVisible()
    with repo.connection() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM practice_sessions')] == before_return
        memory_before = [tuple(r) for r in db.execute('SELECT * FROM wrong_words')]
        plan_before = [tuple(r) for r in db.execute('SELECT * FROM daily_review_items')]
    for width in (980, 1480, 2400):
        child.resize(width, 760); app.processEvents()
        assert child.status_panel.width() <= 210
        assert child.read_wrong_button.fontMetrics().horizontalAdvance(child.read_wrong_button.text()) + 30 <= child.read_wrong_button.width()
        for label in (child.progress_text, child.score_text):
            assert label.height() >= label.heightForWidth(label.width())
        if width == 1480:
            child.grab().save(str(output.with_name(output.stem+'-reading-result.png')))
    assert child.actions['专注模式'].shortcut().toString() == 'F8'
    child.actions['专注模式'].trigger(); app.processEvents()
    assert child.status_panel.isVisible() and child.read_wrong_button.isVisible()
    child.actions['专注模式'].trigger(); app.processEvents()
    child.read_wrong_button.click()
    from PySide6.QtWidgets import QFrame
    text = '\n'.join(c.property('paragraphText') for c in child.content.findChildren(QFrame) if c.property('paragraphText'))
    assert child._wrong_reading and 'Use word0 here.' in text
    assert app.activeModalWidget() is None
    child.grab().save(str(output.with_name(output.stem+'-wrong-reader.png')))
    assert child.session.session_key == correction.session_key
    assert child.pending_review_session is None
    with repo.connection() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM wrong_words')] == memory_before
        assert [tuple(r) for r in db.execute('SELECT * FROM daily_review_items')] == plan_before
    for zoom in (70, 100, 180):
        child.set_zoom(zoom); app.processEvents()
        assert child.scroll.horizontalScrollBar().maximum() == 0
        assert child._wrong_reading and not child.answer_edits
    child.reading_back_button.click(); app.processEvents()
    assert not child._wrong_reading and child.session.session_key == correction.session_key
    child.close(); child.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); app.processEvents()
    with repo.connection() as db: assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    admission_checks = verify_admission(root, parent, app, output)
    return ['continuity_production_policy','total_contains_old_intake','yesterday_cohort_priority',
            'plain_language_capacity','bounded_closed_book_group','consolidation_restart_draft',
            'return_to_original_result','read_without_new_round','reading_preserves_models_and_plan',
            'compact_result_sidebar','f8_keeps_reading_accessible','reader_zoom_wrapping'] + admission_checks


def verify_admission(root, parent, app, output):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QLabel
    from src.database.repository import Repository
    from src.services.review_continuity import status_text
    from src.ui.daily_review_dialog import ReviewQuantityDialog
    from src.ui.main_window import MainWindow

    repo = Repository(root / 'admission' / 'copy.db')
    repo.enable_review_continuity()
    today = repo.today()
    with repo.connection() as db:
        for i in range(321):
            word = f'admission{i}'
            offset = 0 if i < 182 else 1 if i < 201 else 2 if i < 206 else 3 if i < 241 else -20
            due = (today+timedelta(days=offset)).isoformat()
            wid = db.execute("""INSERT INTO wrong_words(answer,normalized_answer,first_error_at,last_error_at,
                review_due_date,review_active) VALUES (?,?,?, ?,?,?)""",
                (word,word,today.isoformat(),today.isoformat(),due,int(i < 241))).lastrowid
            db.execute("INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence) VALUES (?,'demo','demo',?)",
                       (wid,f'Use {word} here.'))
        original_memory = [tuple(r) for r in db.execute('SELECT * FROM wrong_words ORDER BY id')]
    repo.configure_review_capacity(200, 40)
    assert len(repo.today_review_ids()) == 200
    status = repo.continuity_status()
    assert status['planned_return'] == 182 and status['planned_old'] == 18
    assert status['future_reserved'] == 241 and '今日名额已满' in status_text(status)
    with repo.connection() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM wrong_words ORDER BY id')] == original_memory
    for append in (False, True):
        for width in (480, 550, 720):
            dialog = ReviewQuantityDialog(20 if append else 200, 4 if append else 40, status, parent, append=append)
            dialog.resize(width, dialog.sizeHint().height()); dialog.show(); app.processEvents()
            assert '明天19词' in dialog.future_note.text()
            if append:
                assert '本次最多追加20词，其中旧词最多4词' in dialog.summary.text()
                assert '不自动补齐' in dialog.append_note.text()
            for label in dialog.findChildren(QLabel):
                if label.isVisible() and label.hasHeightForWidth():
                    assert label.height() >= label.heightForWidth(label.width())
            if width == 550:
                suffix = '-admission-append.png' if append else '-admission-settings.png'
                dialog.grab().save(str(output.with_name(output.stem+suffix)))
            dialog.close(); dialog.deleteLater()
    home = MainWindow(repo, parent)
    home.show(); home._show_review_home(status); app.processEvents()
    assert any('重新学习旧词18词' in label.text() for label in home.findChildren(QLabel))
    home.resize(1480, 900); app.processEvents()
    home.grab().save(str(output.with_name(output.stem+'-admission-home.png')))
    home.close(); home.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); app.processEvents()

    extra = Repository(root / 'strict-append' / 'copy.db')
    extra.enable_review_continuity()
    with extra.connection() as db:
        for i in range(280):
            word = f'extra{i}'
            wid = db.execute("""INSERT INTO wrong_words(answer,normalized_answer,first_error_at,last_error_at,
                review_due_date,review_active) VALUES (?,?,?,?,?,?)""",
                (word,word,today.isoformat(),today.isoformat(),today.isoformat(),int(i < 130))).lastrowid
            db.execute("INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence) VALUES (?,'demo','demo',?)",
                       (wid,f'Use {word} here.'))
    extra.configure_review_capacity(200, 33)
    original = extra.today_review_ids(start=True)
    assert len(original) == 163
    with extra.connection() as db:
        db.execute('UPDATE daily_review_days SET old_limit=40')
        db.execute("UPDATE app_settings SET value='40' WHERE key='review_old_limit'")
        memory = [tuple(r) for r in db.execute('SELECT * FROM wrong_words ORDER BY id')]
    assert extra.append_review_capacity(20, 4) == 4
    assert extra.today_review_ids()[:163] == original and len(extra.today_review_ids()) == 167
    assert extra.append_review_capacity(20, 0) == 0
    assert extra.review_capacity() == (200, 40)
    assert Repository(extra.database_path).today_review_ids() == extra.today_review_ids()
    with extra.connection() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM wrong_words ORDER BY id')] == memory
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    return ['daily_182_returns_plus_18_old','future_daily_counts_advisory_only','strict_append_total_and_old_caps',
            'append_does_not_spend_unused_slots','append_defaults_and_memory_preserved','append_restart_no_refill',
            'admission_settings_layout','admission_home_explains_composition']
