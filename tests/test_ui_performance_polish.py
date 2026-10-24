from copy import deepcopy
import pytest

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.services.learning_content import cancel_blank, edit_hint, prepare_article
from src.services.practice_service import new_full_session
from src.services.learning_evidence import result_summary
from src.ui.dialogs import WrongPracticeHistoryDialog, WrongWordsDialog
from test_library_ui import ui
from test_learning_content import story, submitted, update
from test_continuous_selection import reader


def test_geometry_cache_reuses_measurements_and_translates_positions(ui, monkeypatch):
    from src.ui.widgets.flow_layout import FlowLayout
    parent = QWidget(); flow = FlowLayout(parent, margin=4)
    for text in ('one', 'two', 'three'):
        label = QLabel(text); label.setFixedSize(60, 20); flow.addWidget(label)
    parent.show(); QApplication.processEvents(); flow.invalidate()
    calls = []
    for n in range(flow.count()):
        item = flow.itemAt(n); original = item.sizeHint
        monkeypatch.setattr(item, 'sizeHint', lambda original=original: calls.append(1) or original())
    rect = QRect(0, 0, 200, 200)
    flow.setGeometry(rect)
    measured = len(calls)
    positions = [flow.itemAt(n).geometry().topLeft() for n in range(flow.count())]
    flow.setGeometry(rect)
    assert measured and len(calls) == measured
    flow.setGeometry(rect.translated(7, 11))
    assert len(calls) == measured
    assert [flow.itemAt(n).geometry().topLeft() for n in range(flow.count())] == [p + rect.translated(7, 11).topLeft() for p in positions]
    flow.invalidate(); flow.setGeometry(rect)
    assert len(calls) > measured
    parent.close(); parent.deleteLater()


def test_result_sidebar_does_not_repeat_identical_independent_scores(tmp_path, ui):
    repo, article, _ = story(tmp_path)
    window = ui(repo)
    session = new_full_session(article)
    window._activate_session(session)
    first = session.target_ids[0]
    window.answer_edits[first].setText(session.article.item_map[first].answer)
    window._commit_submission()
    assert '首轮回忆' in window.score_text.text()
    assert '本轮答题正确' not in window.score_text.text()
    assert '原词回忆结果' in window.score_text.text()
    assert '本轮答题正确' in result_summary(session)  # Detailed evidence stays unchanged.


def test_mixed_result_keeps_distinct_recall_and_visible_answer_scores(tmp_path, ui):
    repo, article, _ = story(tmp_path)
    window = ui(repo); session = new_full_session(article)
    window._activate_session(session)
    # Use a distinct normalized word: both 'hold' occurrences intentionally share
    # one conservative evidence outcome in the existing memory rules.
    first, second = session.target_ids[2], session.target_ids[0]
    window.answer_edits[first].setText(session.article.item_map[first].answer)
    window.show_answers()
    window.answer_edits[second].setText('incorrect')
    window._commit_submission()
    assert '首轮回忆：1/1' in window.score_text.text()
    assert '本轮答题正确：1/3' in window.score_text.text()
    assert '其余2题' in window.score_text.text()


def test_styled_sort_controls_keep_a_packagable_dropdown_indicator(tmp_path, ui):
    from pathlib import Path
    from PySide6.QtGui import QPixmap
    repo, _, _ = story(tmp_path)
    window = ui(repo)
    arrow = Path(__file__).resolve().parents[1] / 'assets' / 'chevron-down.svg'
    assert not QPixmap(str(arrow)).isNull()
    assert arrow.as_posix() in window.styleSheet()


def test_learning_notes_keep_a_secondary_size_in_all_reading_states(tmp_path, ui):
    repo, article, _ = story(tmp_path)
    article = update(repo, article)
    window = ui(repo)
    window.current_article = article
    window.show_preview()
    for zoom in (70, 100, 130, 180):
        window.set_zoom(zoom)
        notes = window.content.findChildren(QLabel, 'learningNote')
        assert notes
        assert all(n.font().pixelSize() == round(15 * zoom / 100) for n in notes)
    window._activate_session(new_full_session(article))
    assert all(n.isHidden() for n in window.content.findChildren(QLabel, 'learningNote'))
    window._commit_submission()
    assert all(not n.isHidden() for n in window.content.findChildren(QLabel, 'learningNote'))
    window.read_current_wrong()
    assert window.content.findChildren(QLabel, 'learningNote')
    window.return_from_wrong_reading()
    assert all(n.font().pixelSize() == round(15 * window.zoom_percent / 100)
               for n in window.content.findChildren(QLabel, 'learningNote'))


def test_empty_and_filtered_review_tables_explain_their_state(tmp_path, ui):
    repo, article, _ = story(tmp_path)
    window = ui(repo)
    history = WrongPracticeHistoryDialog(repo, window)
    words = WrongWordsDialog(repo, window)
    assert '暂无' in history.result_count.text()
    assert '暂无' in words.result_count.text()
    submitted(repo, article)
    history.reload(); words.reload()
    assert '1' in history.result_count.text()
    assert '2' in words.result_count.text()
    words.table.selectRow(0)
    words.search.setText('nothing-matches-this-query')
    assert '没有匹配' in words.result_count.text()
    assert not words.start_button.isEnabled() and not words.delete_button.isEnabled()
    words.search.clear()
    assert '2' in words.result_count.text()
    history.close(); words.close()


def test_plan_reuses_content_edits_once_per_transaction(tmp_path, monkeypatch):
    repo, article, _ = story(tmp_path)
    submitted(repo, article); update(repo, article)
    repo.enable_review_continuity()
    with repo.connection() as db:
        db.execute("UPDATE wrong_words SET review_active=1,review_due_date=?", (repo.today().isoformat(),))
    import src.services.learning_content as content
    calls = []
    original = content.ContentResolver.__init__
    def counted(self, connection):
        calls.append(1); original(self, connection)
    monkeypatch.setattr(content.ContentResolver, '__init__', counted)
    repo.configure_review_capacity(40, 20)
    assert len(calls) == 1


def test_one_submission_refreshes_daily_state_once(tmp_path, ui, monkeypatch):
    repo, article, _ = story(tmp_path)
    window = ui(repo)
    submitted(repo, article)
    with repo.connection() as db:
        db.execute('UPDATE wrong_words SET review_due_date=?', (repo.today().isoformat(),))
    repo.configure_review_capacity(20, 20)
    window.start_daily_review()
    calls = []
    original = repo.continuity_status
    def counted():
        calls.append(1); return original()
    monkeypatch.setattr(repo, 'continuity_status', counted)
    window._commit_submission()
    assert len(calls) == 1


def test_review_example_batch_preserves_input_order_and_duplicate_ids(tmp_path):
    repo, article, _ = story(tmp_path)
    submitted(repo, article)
    with repo.connection() as db:
        ids = [r[0] for r in db.execute('SELECT id FROM wrong_words ORDER BY id')]
    requested = [ids[1], ids[0], ids[1], -1]
    before = repo.get_review_examples(requested)
    update(repo, article)
    after = repo.get_review_examples(requested)
    assert [r['id'] for r in before] == [r['id'] for r in after] == [f'book-{i}' for i in requested[:-1]]


def test_review_example_lookup_never_caches_stale_content_between_calls(tmp_path):
    repo, article, _ = story(tmp_path)
    submitted(repo, article)
    with repo.connection() as db:
        word_id = db.execute("SELECT id FROM wrong_words WHERE answer='internal'").fetchone()[0]
    first = repo.get_review_examples([word_id])[0]
    before = prepare_article(repo.load_article(article.source_path)); after = deepcopy(before)
    item = next(i for i in after.word_items if i.answer == 'internal')
    edit_hint(after, item.id, '内部的，修订版', '本次新备注')
    repo.save_learning_content(before, after)
    second = repo.get_review_examples([word_id])[0]
    assert first['chinese'] != second['chinese'] == '内部的，修订版'


def test_latest_cancelled_example_finds_an_older_valid_occurrence(tmp_path):
    repo, article, _ = story(tmp_path)
    submitted(repo, article)
    before = prepare_article(repo.load_article(article.source_path)); after = deepcopy(before)
    cancel_blank(after, after.word_items[1].id)
    repo.save_learning_content(before, after)
    with repo.connection() as db:
        word_id = db.execute("SELECT id FROM wrong_words WHERE answer='hold'").fetchone()[0]
    records = repo.get_review_examples([word_id])
    assert len(records) == 1 and records[0]['chinese'] == '容纳'


def test_geometry_cache_stays_bounded_during_resizes(ui):
    from src.ui.widgets.flow_layout import FlowLayout
    parent = QWidget(); flow = FlowLayout(parent)
    flow.addWidget(QLabel('some words that wrap'))
    for width in range(80, 600, 5):
        flow.heightForWidth(width)
        flow.setGeometry(QRect(0, 0, width, 200))
    assert len(flow._height_cache) <= 8 and len(flow._geometry_cache) <= 8
    parent.close(); parent.deleteLater()


@pytest.mark.parametrize('reverse_records', [False, True])
def test_preview_copy_respects_both_model_sentence_orders(reader, reverse_records):
    from test_continuous_selection import labels, drag, copy_key, SENTENCES
    from src.services.practice_service import new_long_term_session
    window = reader
    window.start_full_practice(); window._commit_submission()
    with window.repository.connection() as db:
        ids = [r[0] for r in db.execute('SELECT id FROM wrong_words ORDER BY id')]
    records = window.repository.get_review_examples(ids)
    if reverse_records: records.reverse()
    window._show_wrong_preview(new_long_term_session(records)); QApplication.processEvents()
    words = labels(window); drag(words[0], words[-1])
    assert copy_key(words[0]).strip() == '\n\n'.join(reversed(SENTENCES) if reverse_records else SENTENCES)
