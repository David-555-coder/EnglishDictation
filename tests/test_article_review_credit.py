"""Whole-article recall can satisfy a due review without creating repeat credit."""

from src.database.repository import Repository
from src.models.practice import PracticeMode
from src.services.practice_service import new_saved_wrong_session, new_long_term_session
from src.ui.dialogs import WrongWordsDialog
from PySide6.QtWidgets import QApplication
from test_daily_review import clock, daily, seed, submit, word
from test_library_ui import ui


def article_session(repo, word_ids):
    records = repo.get_review_examples(word_ids)
    session = new_saved_wrong_session({"id": "article", "article_name": "a.docx"}, records)
    session.mode = PracticeMode.FULL_ARTICLE
    return session


def test_due_article_success_completes_planned_word_once(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    assert repo.today_review_ids() == [1]
    submit(repo, article_session(repo, [1]))
    record = word(repo)
    assert (record["review_stage"], record["review_correct_count"], record["review_last_date"]) == (1, 1, "2026-09-15")
    assert record["review_due_date"] == "2026-09-19"
    assert repo.review_status() == dict(completed=1, remaining=0, waiting=0)
    assert repo.today_review_ids() == []
    assert not repo.set_review_limit(60)  # Completed plans cannot be rebuilt.
    submit(repo, article_session(repo, [1]))
    assert word(repo)["review_correct_count"] == 1
    assert word(repo)["review_due_date"] == "2026-09-19"


def test_article_credit_outside_daily_cap_leaves_other_words_untouched(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo, 70)
    selected = repo.today_review_ids()
    assert len(selected) == 40 and 1 not in selected
    before = {r["id"]: (r["review_stage"], r["review_due_date"]) for r in repo.list_wrong_words()}
    submit(repo, article_session(repo, [1]))
    assert repo.today_review_ids() == selected
    after = {r["id"]: (r["review_stage"], r["review_due_date"]) for r in repo.list_wrong_words()}
    assert after[1] == (1, "2026-09-19")
    assert all(after[i] == before[i] for i in range(2, 71))
    assert repo.review_status() == dict(completed=0, remaining=40, waiting=29)


def test_same_day_error_and_manual_success_do_not_advance(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    with repo.connection() as connection:
        connection.execute("UPDATE wrong_words SET last_error_at='2026-09-15 08:00:00'")
    submit(repo, article_session(repo, [1]))
    submit(repo, new_long_term_session(repo.get_review_examples([1])))
    assert word(repo)["fsrs_reviews"] == 1
    assert word(repo)["review_due_date"] == "2026-09-19"
    assert word(repo)["review_correct_count"] == 1


def test_one_wrong_occurrence_prevents_article_credit(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    records = repo.get_review_examples([1])
    second = dict(records[0], id=999, sentence="Remember word0 again.", paragraph_text="Remember word0 again.")
    session = new_saved_wrong_session({"id": "article"}, records + [second])
    session.mode = PracticeMode.FULL_ARTICLE
    session.answers[session.target_ids[0]] = "word0"
    submit(repo, session, correct=False)
    assert word(repo)["review_stage"] == 0
    assert word(repo)["review_correct_count"] == 0
    assert word(repo)["review_due_date"] == "2026-09-16"


def test_stale_daily_draft_wrong_after_article_credit_moves_review_forward(clock, tmp_path):
    repo = Repository(tmp_path / "db")
    seed(repo)
    stale = daily(repo)
    submit(repo, article_session(repo, [1]))
    submit(repo, stale, correct=False)
    assert word(repo)["review_stage"] == 1  # Duplicate timestamp doesn't fabricate a second model observation.
    assert word(repo)["review_due_date"] == "2026-09-16"
    assert word(repo)["review_correct_count"] == 1
    assert repo.review_status()["completed"] == 1


def test_active_daily_draft_preserves_other_answers_after_article_credit(clock, tmp_path, ui):
    repo = Repository(tmp_path / "db")
    seed(repo, 2)
    window = ui(repo)
    window.start_daily_review()
    draft = window.session
    remaining_item = next(item_id for item_id in draft.target_ids
                          if draft.article.item_map[item_id].normalized_answer == "word1")
    window.answer_edits[remaining_item].setText("word1")
    assert window._save_active_draft()
    submit(repo, article_session(repo, [1]))
    window.start_daily_review()
    assert window.session.session_key == draft.session_key
    assert window.session.total == 1
    assert window.answer_edits[remaining_item].text() == "word1"
    assert len(window.answer_edits) == 1
    assert Repository(repo.database_path).load_drafts()[0].total == 1


def test_wordbook_labels_are_compact_with_full_dates_in_tooltips(clock, tmp_path, ui):
    repo = Repository(tmp_path / "db")
    seed(repo, 2, due="2026-09-13")
    repo.today_review_ids()
    dialog = WrongWordsDialog(repo)
    assert dialog.table.horizontalHeaderItem(6).text() == "复习安排"
    dialog.show(); QApplication.processEvents()
    assert 150 <= dialog.table.columnWidth(0) < 260
    assert dialog.table.item(0, 3).text() == "昨天"
    assert dialog.table.item(0, 3).toolTip() == "2026-09-14"
    assert dialog.table.item(0, 6).text() == "今日待复习"
    assert "原定复习：2026-09-13" in dialog.table.item(0, 6).toolTip()
    dialog.close()
