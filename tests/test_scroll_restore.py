"""Scroll work must survive layout changes but yield to a newer user action."""
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_focus_and_resume import practice
from test_library_ui import ui


def saved_position(window, index=23):
    item_id = window.session.target_ids[index]
    window.answer_edits[item_id].setFocus()
    QApplication.processEvents()
    window._center_answer(item_id)
    window._save_active_draft()
    return item_id, window.scroll.verticalScrollBar().value()


def test_draft_scroll_waits_for_real_scroll_range(tmp_path, ui, monkeypatch):
    window = practice(tmp_path, ui, 40)
    _, desired = saved_position(window)
    assert desired > 0
    bar = window.scroll.verticalScrollBar()
    maximum = bar.maximum()
    # Recreating a page temporarily leaves the scroll area with range 0..0.
    # Suppress following to test restoration itself, not an accidental fallback.
    monkeypatch.setattr(window, '_queue_answer_follow', lambda *_: None)
    bar.setRange(0, 0)
    window._restore_view(window.session.session_key, window._content_generation)
    bar.setRange(0, maximum)
    QApplication.processEvents()
    QTest.qWait(20)
    assert abs(bar.value() - desired) <= 3


@pytest.mark.parametrize('user_action', ['scroll', 'focus', 'close'])
def test_late_restore_yields_to_newer_user_action(tmp_path, ui, monkeypatch, user_action):
    window = practice(tmp_path, ui, 40)
    original, _ = saved_position(window)
    callbacks = []
    monkeypatch.setattr(QTimer, 'singleShot', lambda _delay, callback: callbacks.append(callback))
    window._render_practice(True)
    restore = callbacks[-1]
    QApplication.processEvents()
    QTest.qWait(30)
    bar = window.scroll.verticalScrollBar()
    other = window.answer_edits[window.session.target_ids[2]]
    if user_action == 'scroll':
        QTest.keyClick(bar, Qt.Key_Home)
        bar.setValue(0)
    elif user_action == 'focus':
        QTest.mouseClick(other, Qt.LeftButton)
        QApplication.processEvents()
    else:
        window.close()
    expected = bar.value()
    focus = QApplication.focusWidget()
    restore()
    QApplication.processEvents()
    QTest.qWait(20)
    assert bar.value() == expected
    assert QApplication.focusWidget() is focus
    if user_action == 'focus':
        assert focus is other and other.item_id != original


def test_manual_scroll_cancels_restore_waiting_for_range(tmp_path, ui, monkeypatch):
    window = practice(tmp_path, ui, 40)
    _, desired = saved_position(window)
    assert desired > 0
    bar = window.scroll.verticalScrollBar()
    maximum = bar.maximum()
    monkeypatch.setattr(window, '_queue_answer_follow', lambda *_: None)
    bar.setRange(0, 0)
    window._restore_view(window.session.session_key, window._content_generation)
    QTest.keyClick(bar, Qt.Key_Home)
    bar.setRange(0, maximum)
    QApplication.processEvents()
    QTest.qWait(20)
    assert bar.value() == 0


def test_save_during_render_preserves_pending_scroll_and_new_answers(tmp_path, ui):
    window = practice(tmp_path, ui, 40)
    item_id, desired = saved_position(window)
    assert desired > 0
    window._render_practice(True)  # Deferred callback is suppressed by this fixture.
    window.scroll.verticalScrollBar().setValue(0)
    window.answer_edits[item_id].setText('new answer while loading')
    assert window._save_active_draft()
    draft = next(s for s in window.repository.load_drafts()
                 if s.session_key == window.session.session_key)
    assert draft.view_state['scroll'] == desired
    assert draft.answers[item_id] == 'new answer while loading'


@pytest.mark.parametrize('width,zoom', [(1000, 70), (1000, 130), (1480, 100), (1480, 180)])
def test_repeated_focus_mode_changes_keep_reading_anchor(tmp_path, ui, width, zoom):
    window = practice(tmp_path, ui, 50)
    window.resize(width, 800)
    window.set_zoom(zoom)
    QApplication.processEvents()
    item_id, _ = saved_position(window, 28)
    QTest.qWait(30)
    edit = window.answer_edits[item_id]
    bar = window.scroll.verticalScrollBar()
    before = edit.mapTo(window.content, QPoint()).y() - bar.value()
    for _ in range(6):
        window.actions['专注模式'].trigger()
        QTest.qWait(30)
        assert abs(edit.mapTo(window.content, QPoint()).y() - bar.value() - before) <= 3
        assert QApplication.focusWidget() is edit
    QTest.qWait(150)
    assert abs(edit.mapTo(window.content, QPoint()).y() - bar.value() - before) <= 3


def test_page_that_fits_finishes_restore_at_zero(tmp_path, ui):
    window = practice(tmp_path, ui)
    window.session.view_state['scroll'] = 200
    window._restore_view(window.session.session_key, window._content_generation)
    QApplication.processEvents()
    QTest.qWait(30)
    assert window.scroll.verticalScrollBar().maximum() == 0
    assert window.scroll.verticalScrollBar().value() == 0
    assert window._pending_view_restore is None


def test_idle_page_does_not_poll_layout_or_keep_scroll_timers_running(tmp_path, ui, monkeypatch):
    window = practice(tmp_path, ui, 40)
    saved_position(window)
    QTest.qWait(100)
    calls = []
    original = window._reading_layout_ready
    monkeypatch.setattr(window, '_reading_layout_ready', lambda: (calls.append(1), original())[1])
    QTest.qWait(200)
    assert calls == []
    assert not window._layout_timer.isActive() and not window._follow_timer.isActive()
    assert window._pending_view_restore is None and window._pending_follow is None
