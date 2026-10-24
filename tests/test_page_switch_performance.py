"""Page reuse must preserve grading, evidence, selection and view state."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QWidget
from shiboken6 import isValid

from src.services.learning_content import prepare_article, edit_hint
from src.services.practice_service import new_full_session
from src.services.serialization import session_to_dict
from src.ui.widgets.flow_layout import FlowLayout
from test_learning_content import story
from test_library_ui import ui


def result_window(tmp_path, ui):
    repo, article, _ = story(tmp_path)
    window = ui(repo)
    window._activate_session(new_full_session(article))
    first = window.session.target_ids[0]
    window.answer_edits[first].setText(article.item_map[first].answer)
    window._commit_submission()
    window.show(); QApplication.processEvents()
    return window


def learning_rows(repo):
    with repo.connection() as db:
        return {name: [tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                for name in ('practice_sessions', 'practice_answers', 'wrong_words',
                             'memory_events', 'practice_drafts', 'daily_review_items')}


def test_return_reuses_exact_result_widgets_and_keeps_learning_records(tmp_path, ui):
    window = result_window(tmp_path, ui)
    root, edits = window.content, dict(window.answer_edits)
    session, rows = session_to_dict(window.session), learning_rows(window.repository)
    window.read_current_wrong(); QApplication.processEvents()
    assert window.content is not root and not window.answer_edits
    assert not root.isVisible()
    assert not window.content.findChildren(QLineEdit)
    window.return_from_wrong_reading(); QApplication.processEvents()
    assert window.content is root
    assert all(window.answer_edits[key] is edit for key, edit in edits.items())
    assert all(edit.isReadOnly() for edit in edits.values())
    assert session_to_dict(window.session) == session
    assert learning_rows(window.repository) == rows


def test_cached_return_never_rebuilds_practice_content(tmp_path, ui, monkeypatch):
    window = result_window(tmp_path, ui)
    window.read_current_wrong()
    def unexpected(*_):
        pytest.fail('Returning to the same result rebuilt all input widgets')
    monkeypatch.setattr(window, '_build_practice_content', unexpected)
    window.return_from_wrong_reading()
    assert not window._wrong_reading and window.session.submitted


def test_new_reader_replaces_current_page_instead_of_reparenting_every_card(tmp_path, ui, monkeypatch):
    window = result_window(tmp_path, ui)
    old_root = window.content
    seen = []
    original = window._paragraph_card
    def make_card():
        seen.append(window.content is not old_root and window.scroll.widget() is window.content)
        return original()
    monkeypatch.setattr(window, '_paragraph_card', make_card)
    window.read_current_wrong()
    assert seen and all(seen)
    assert window.scroll.widget() is window.content
    assert window.reading_selection.root is window.content


def test_result_summary_is_not_duplicated_on_repeated_refresh(tmp_path, ui):
    window = result_window(tmp_path, ui)
    window._show_results(notify=False)
    window._show_results(notify=False)
    assert len(window.content.findChildren(QFrame, 'wrongSummary')) == 1


@pytest.mark.parametrize('zoom', [70, 100, 180])
def test_zoom_and_focus_mode_while_reading_survive_cached_return(tmp_path, ui, zoom):
    window = result_window(tmp_path, ui)
    root = window.content
    original = session_to_dict(window.session)
    window.read_current_wrong()
    window.set_zoom(zoom); window.resize(1100, 800)
    window._set_focus_mode(True); QApplication.processEvents()
    window.return_from_wrong_reading(); QApplication.processEvents()
    assert window.content is root and window.session.submitted
    assert session_to_dict(window.session) == original
    assert all(edit.font().pixelSize() == round(20*zoom/100) for edit in window.answer_edits.values())
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert window.reading_selection.root is window.content
    window._set_focus_mode(False)


def test_switching_to_new_round_discards_hidden_result_page(tmp_path, ui):
    window = result_window(tmp_path, ui)
    root = window.content
    window.read_current_wrong()
    window._activate_session(new_full_session(window.session.article))
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); QApplication.processEvents()
    assert window._result_page_cache is None and not isValid(root)
    assert not window.session.submitted and all(not e.isReadOnly() for e in window.answer_edits.values())


def test_close_while_reading_releases_retained_result_page(tmp_path, ui):
    window = result_window(tmp_path, ui)
    root = window.content
    window.read_current_wrong(); window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); QApplication.processEvents()
    assert window._result_page_cache is None and not isValid(root)


def test_repeated_round_trips_keep_only_one_summary_and_same_result_tree(tmp_path, ui):
    window = result_window(tmp_path, ui)
    root, count = window.content, len(window.content.findChildren(QWidget))
    for _ in range(4):
        window.read_current_wrong(); QApplication.processEvents()
        assert window._result_page_cache is not None
        window.return_from_wrong_reading()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); QApplication.processEvents()
        assert window._result_page_cache is None and window.content is root
        assert len(window.content.findChildren(QWidget)) == count
        assert len(window.content.findChildren(QFrame, 'wrongSummary')) == 1
        assert set(window.reading_selection._bound).issubset(set(window.content.findChildren(QWidget)))


def test_new_content_edit_during_reader_does_not_show_stale_text_next_time(tmp_path, ui):
    window = result_window(tmp_path, ui)
    window.read_current_wrong()
    before = prepare_article(window.repository.load_article(window.session.article.source_path))
    after = deepcopy(before)
    target = next(i for i in after.word_items if i.answer == 'internal')
    edit_hint(after, target.id, '本句新释义', '修订备注')
    window.repository.save_learning_content(before, after)
    window.return_from_wrong_reading()
    window.read_current_wrong(); QApplication.processEvents()
    text = '\n'.join(w.text() for w in window.content.findChildren(QLabel))
    assert '本句新释义' in text and '修订备注' in text


def test_flow_does_not_place_every_child_again_for_identical_geometry(ui, monkeypatch):
    parent = QWidget(); flow = FlowLayout(parent, margin=4)
    for text in ('one', 'two', 'three'):
        flow.addWidget(QLabel(text))
    parent.show(); QApplication.processEvents(); flow.invalidate()
    original, calls = flow._do_layout, []
    def place(rect, test_only):
        calls.append((QRect(rect), test_only))
        return original(rect, test_only)
    monkeypatch.setattr(flow, '_do_layout', place)
    rect = QRect(0, 0, 240, 100)
    flow.setGeometry(rect)
    count = len(calls)
    flow.setGeometry(rect)
    assert len(calls) == count
    flow.invalidate(); flow.setGeometry(rect)
    assert len(calls) > count
    parent.close(); parent.deleteLater()
