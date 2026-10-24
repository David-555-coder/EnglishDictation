import pytest

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea
from shiboken6 import isValid

from src.database.repository import Repository
from test_library_ui import ui


def test_help_menu_is_last_and_f1_is_available_without_an_article(tmp_path, ui):
    window = ui(Repository(tmp_path / 'copy.db'))
    menus = [action.text() for action in window.menuBar().actions()]
    assert menus == ['文章与设置', '练习', '视图', '帮助']
    action = window.actions['快捷键说明']
    assert action.isEnabled() and action.shortcut() == QKeySequence('F1')


@pytest.mark.parametrize('trigger', ['menu', 'f1', 'focus_f1'])
def test_help_opens_from_menu_and_f1_and_is_released(tmp_path, ui, monkeypatch, trigger):
    from src.ui.shortcut_help import ShortcutHelpDialog
    window = ui(Repository(tmp_path / 'copy.db')); window.show()
    QApplication.processEvents()
    if trigger == 'focus_f1':
        window._set_focus_mode(True)
    hint = window.shortcut_hint.text()
    opened = []

    def inspect(dialog):
        opened.append(dialog)
        assert dialog.windowTitle() == '快捷键说明'
        assert dialog.findChildren(QLabel, 'shortcutKey')
        return dialog.Rejected

    monkeypatch.setattr(ShortcutHelpDialog, 'exec', inspect)
    if trigger == 'menu':
        window.actions['快捷键说明'].trigger()
    else:
        window.activateWindow(); window.setFocus(); QApplication.processEvents()
        QTest.keyClick(window, Qt.Key_F1)
    assert len(opened) == 1
    assert window.shortcut_hint.text() == hint
    assert window._focus_mode == (trigger == 'focus_f1')
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(opened[0])


def test_help_lists_bound_shortcuts_and_explains_scope(tmp_path, ui):
    from src.ui.shortcut_help import ShortcutHelpDialog, SHORTCUT_SECTIONS
    window = ui(Repository(tmp_path / 'copy.db'))
    dialog = ShortcutHelpDialog(window)
    keys = '\n'.join(label.text() for label in dialog.findChildren(QLabel, 'shortcutKey'))
    for text in ('Ctrl+O', 'Ctrl+1', 'Ctrl+2', 'Ctrl+R', 'Ctrl+Enter', 'F8', 'F1', 'Ctrl+0',
                 'Enter', 'Shift+Enter', 'Shift+Backspace', 'Delete', 'Esc', 'Ctrl+C', 'Ctrl+Z'):
        assert text in keys
    all_text = '\n'.join(label.text() for label in dialog.findChildren(QLabel))
    assert '整篇默写' in all_text and '今日复习' in all_text
    assert '文章列表' in all_text and '历史记录' in all_text
    assert len(SHORTCUT_SECTIONS) == 4
    for name in ('导入文章', '原文', '开始默写', '提交批改', '历史错词练习', '专注模式', '快捷键说明'):
        assert window.actions[name].shortcut().toString() in keys
    dialog.deleteLater()


def test_help_fits_small_window_and_escape_closes_it(tmp_path, ui):
    from src.ui.shortcut_help import ShortcutHelpDialog
    window = ui(Repository(tmp_path / 'copy.db'))
    dialog = ShortcutHelpDialog(window); dialog.show()
    QApplication.processEvents()
    scroll = dialog.findChild(QScrollArea)
    if dialog.height() >= 660:
        assert scroll.verticalScrollBar().maximum() == 0
    dialog.resize(560, 420); QApplication.processEvents()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() > 0
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    assert scroll.widget().findChildren(QLabel, 'shortcutKey')[-1].isVisible()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert not dialog.isVisible()
    dialog.deleteLater()
