"""Disposable packaged QA for correction linkage and timely maintenance."""
from datetime import timedelta


def verify(root, parent, app):
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
    from src.database.repository import Repository
    from src.services.daily_review import select_review_ids
    from src.services.fsrs7 import MODEL_ID, next_state
    from src.ui.main_window import MainWindow

    path = root / 'learning-flow' / 'copy.db'
    repo = Repository(path)
    day = repo.today().isoformat()
    with repo.connection() as conn:
        word_ids = []
        for answer in ('example', 'existing'):
            cur = conn.execute("""INSERT INTO wrong_words(answer,normalized_answer,error_count,
                first_error_at,last_error_at,review_due_date,review_pool)
                VALUES (?,?,1,?,?,?,'maintenance')""", (answer, answer, day, day, day))
            word_ids.append(cur.lastrowid)
            conn.execute("""INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence)
                VALUES (?,'example.docx','example.docx',?)""", (cur.lastrowid, f'Use {answer} here.'))
        state = next_state(None, 3)
        conn.execute("""UPDATE wrong_words SET fsrs_model=?,fsrs_stability=?,fsrs_stability_fast=?,
            fsrs_difficulty=?,fsrs_last_at=?,fsrs_episode='previous-episode',fsrs_reviews=1 WHERE id=?""",
            (MODEL_ID, state.stability, state.stability_fast, state.difficulty,
             (repo.today()-timedelta(days=8)).isoformat(), word_ids[1]))

    def settle():
        loop = QEventLoop()
        QTimer.singleShot(180, loop.quit)
        loop.exec()
        app.processEvents()

    def dispose(window):
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()

    first = MainWindow(repo, parent)
    try:
        first.show(); settle()
        first.start_long_term_practice(word_ids)
        first.start_full_practice()
        for edit in first.answer_edits.values():
            edit.setText('incorrect')
        first._commit_submission(); settle()
        session_id = first.session.database_id
        episode = first.session.view_state['memory']['episode']
        assert all(row['fsrs_reviews'] == 1 for row in repo.list_wrong_words())
        assert repo.get_practice_session(session_id)['memory_context']
    finally:
        dispose(first)

    reopened = Repository(path)
    second = MainWindow(reopened, parent)
    try:
        second.show(); settle()
        from unittest.mock import patch
        from src.services.learning_evidence import utc_now
        # Exercise the coarse Windows wall-clock boundary deterministically in
        # the packaged program, not just when timing happens to expose it.
        with patch('src.services.learning_evidence.utc_now', return_value=utc_now()):
            second.start_saved_wrong_practice(session_id)
            assert second.pending_review_session.view_state['memory']['phase'] == 'relearning'
            assert second.pending_review_session.view_state['memory']['episode'] == episode
            second.start_full_practice()
        for edit in second.answer_edits.values():
            edit.setText(second.session.article.item_map[edit.item_id].answer)
        second._commit_submission(); settle()
        reviewed = reopened.list_wrong_words()
        with reopened.connection() as conn:
            diagnostics = {
                'reviews': [(row['normalized_answer'], row['fsrs_reviews']) for row in reviewed],
                'memory': second.session.view_state['memory'],
                'events': [dict(row) for row in conn.execute('SELECT * FROM memory_events ORDER BY id')],
                'exposures': [dict(row) for row in conn.execute('SELECT * FROM memory_exposures')],
            }
        assert all(row['fsrs_reviews'] == 2 for row in reviewed), diagnostics
        assert '纠错后结果' in second.score_text.text()
        with reopened.connection() as conn:
            events = conn.execute('SELECT kind,rating FROM memory_events ORDER BY id DESC LIMIT 2').fetchall()
            assert all(tuple(event) == ('relearning', 3) for event in events)
            import json
            context = json.loads(reopened.get_practice_session(second.session.database_id)['memory_context'])
            assert 'existing' in context['relearning_anchors']
            assert conn.execute('SELECT count(*) FROM practice_sessions').fetchone()[0] == 2
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        dispose(second)

    today = repo.today()
    state = next_state(None, 1)
    stable = next_state(None, 3)
    rows = [dict(id=i, review_pool='maintenance', review_due_date=day,
                 review_relearning=1, error_count=1, last_error_at=day,
                 fsrs_model=MODEL_ID, fsrs_stability=state.stability,
                 fsrs_stability_fast=state.stability_fast, fsrs_difficulty=state.difficulty,
                 fsrs_last_at=(today-timedelta(days=1)).isoformat()) for i in range(1, 42)]
    waiting = dict(rows[0], id=42, review_due_date=(today-timedelta(days=6)).isoformat(),
                   fsrs_stability=stable.stability, fsrs_stability_fast=stable.stability_fast,
                   fsrs_difficulty=stable.difficulty, review_relearning=0)
    selected = select_review_ids(rows+[waiting], 40, today, lambda _: True)
    assert len(selected) == 40 and 42 in selected and len(set(selected)) == 40
    # A large overdue maintenance pool must not take all slots from recent work.
    old = [dict(waiting, id=i) for i in range(100, 600)]
    selected = select_review_ids(old+rows[:40], 40, today, lambda _: True)
    assert len(selected) == len(set(selected)) == 40
    assert sum(word_id < 100 for word_id in selected) == 32
    return ['restart_history_ui_link', 'closed_book_correction_model_credit',
            'separate_persisted_first_round', 'existing_warm_failure_history_credit',
            'bounded_maintenance_recovery', 'timely_maintenance_priority',
            'same_tick_preview_credit']
