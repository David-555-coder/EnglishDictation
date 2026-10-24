"""Packaged, disposable QA for review streams and their compact controls."""
from datetime import datetime, timedelta


def verify(root, window, app, output):
    from PySide6.QtCore import QEvent, QCoreApplication
    from PySide6.QtWidgets import QLabel
    from src.database.repository import Repository
    from src.services.daily_review import new_daily_session
    from src.services.fsrs7 import MODEL_ID, next_state
    from src.services.grading import grade_session
    from src.ui.daily_review_dialog import ReviewQuantityDialog

    repo = Repository(root / "streams" / "copy.db")
    today = repo.today()
    day = today.isoformat()
    state = next_state(None,3)
    with repo.connection() as conn:
        for word_id in range(1,81):
            answer = f"example{word_id}"
            cur = conn.execute("""INSERT INTO wrong_words
                (answer,normalized_answer,error_count,first_error_at,last_error_at,review_due_date,review_pool)
                VALUES (?,?,1,?,?,?,?)""", (answer,answer,day,day,day,"maintenance" if word_id <= 50 else "backlog"))
            conn.execute("""INSERT INTO word_examples(wrong_word_id,source_article,source_path,sentence)
                VALUES (?, 'example.docx','example.docx',?)""",(cur.lastrowid,f"Use {answer} here."))
            if word_id <= 50:
                conn.execute("""UPDATE wrong_words SET fsrs_model=?,fsrs_stability=?,fsrs_difficulty=?,
                    fsrs_stability_fast=?,fsrs_last_at=? WHERE id=?""",(MODEL_ID,state.stability,state.difficulty,
                    state.stability_fast,(datetime.now().astimezone()-timedelta(days=5)).isoformat(),cur.lastrowid))
    repo.configure_review_budgets(41,7)
    initial = repo.today_review_ids(start=True)
    status = repo.review_stream_status()
    assert len(initial) == 48 and status["maintenance_remaining"] == 41 and status["backlog_remaining"] == 7
    session = new_daily_session(repo.get_review_examples(initial), day)
    pools = session.view_state["daily_review_item_pools"]
    ordered = [pools[item_id] for item_id in session.target_ids]
    assert ordered == ["maintenance"]*41 + ["backlog"]*7
    repo.save_draft(session)
    assert repo.configure_review_budgets(50,11,today_only=True)["added"] == 13
    assert repo.today_review_ids()[:48] == initial
    assert len(repo.today_review_ids()) == 61
    assert repo.review_budgets() == (41,7)
    repo.configure_review_budgets(20,0,today_only=True)
    assert len(repo.today_review_ids()) == 61
    session.answers = {item_id: session.article.item_map[item_id].answer for item_id in session.target_ids}
    grade_session(session)
    repo.save_submitted_session(session)
    assert repo.review_status()["completed"] == 48
    assert all(r["review_pool"] == "maintenance" for r in repo.list_wrong_words() if r["id"] in initial)
    assert repo.append_today_reviews(maintenance=3,backlog=4) == 4  # No extra maintenance is due.
    assert len(repo.today_review_ids()) == 17 and not set(repo.today_review_ids()) & set(initial)
    reopened = Repository(repo.database_path)
    assert reopened.today_review_ids() == repo.today_review_ids()
    status = reopened.review_stream_status()
    assert status["maintenance_remaining"] == 9 and status["backlog_remaining"] == 8

    for width in (480,540,720):
        dialog = ReviewQuantityDialog(*repo.review_budgets(today=True),status,window)
        dialog.resize(width,dialog.sizeHint().height())
        dialog.show(); app.processEvents()
        for label in dialog.findChildren(QLabel):
            if label.hasHeightForWidth():
                assert label.height() >= label.heightForWidth(label.width())
        assert dialog.maintenance.isVisible() and dialog.backlog.isVisible()
        if width == 540:
            dialog.grab().save(str(output.with_name(output.stem+"-review-settings.png")))
        dialog.close(); dialog.deleteLater()
    append = ReviewQuantityDialog(20,5,status,window,append=True)
    append.show(); app.processEvents()
    assert append.today_only.isHidden()
    append.grab().save(str(output.with_name(output.stem+"-review-append.png")))
    append.close(); append.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); app.processEvents()
    with repo.connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return ["independent_review_budgets", "custom_review_quantity", "maintenance_before_backlog",
            "append_preserves_started_tasks", "today_only_quantity", "backlog_promotes_on_effective_review",
            "no_repeated_daily_words", "review_stream_restart", "compact_quantity_dialogs"]
