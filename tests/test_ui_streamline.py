import sqlite3
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from test_library_ui import ui, sample_word
from test_daily_review import seed
from src.database.repository import Repository
from src.ui.dialogs import WrongWordsDialog
from src.ui.widgets.flow_layout import FlowLayout


def test_zoom_and_daily_review_stay_visible_at_minimum_width(tmp_path, ui):
    window = ui(Repository(tmp_path / 'db'))
    window.actions['今日复习'].setText('今日复习（80）')
    for width in (980, 1100, 1280):
        window.resize(width, 650)
        window.show()
        QApplication.processEvents()
        toolbar = window.main_toolbar
        zoom_buttons = [toolbar.widgetForAction(action) for action in toolbar.actions()
                        if action.text() in ('−', '＋')]
        assert len(zoom_buttons) == 2
        assert window.zoom_label.isVisible()
        assert all(button.isVisible() for button in zoom_buttons)
        assert toolbar.widgetForAction(window.actions['今日复习']).isVisible()


def test_compact_toolbar_keeps_secondary_actions_accessible(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path / 'db')
    monkeypatch.setattr(repo, 'has_wrong_history', lambda: True)
    window = ui(repo)
    window.resize(980, 650)
    window.show()
    QApplication.processEvents()
    assert window.more_button.isVisible()
    assert window._compact_history_shortcut.isEnabled()
    assert [action.text() for action in window.more_button.menu().actions()] == [
        '历史错词练习', '错词本', '快速成文']
    assert all(action.isEnabled() for action in window.more_button.menu().actions())
    opened = []
    monkeypatch.setattr(window, '_open_history', lambda wrong_only: opened.append(wrong_only))
    window.more_button.menu().actions()[0].trigger()
    assert opened == [True]
    window.activateWindow()
    QApplication.processEvents()
    QTest.keyClick(window, Qt.Key_R, Qt.ControlModifier)
    assert opened == [True, True]
    window.resize(1480, 650)
    QApplication.processEvents()
    assert not window.more_button.isVisible()
    assert not window._compact_history_shortcut.isEnabled()
    assert all(window.main_toolbar.widgetForAction(window.actions[name]).isVisible()
               for name in ('历史错词练习', '错词本', '快速成文'))


def test_shortcut_hint_shows_all_four_shortcuts(tmp_path, ui):
    window = ui(Repository(tmp_path / 'db'))
    hint = window.status_panel.findChild(QLabel, 'hintCard')
    assert hint.text().count('\n') == 3
    for shortcut in ('Enter', 'Shift+Enter', 'Shift+Backspace', 'Ctrl+滚轮'):
        assert shortcut in hint.text()


def test_toolbar_groups_and_practice_menu_remain_available_when_toolbar_hidden(tmp_path, ui):
    w = ui(Repository(tmp_path/'db'))
    actions = w.main_toolbar.actions()
    labels = [a.text() for a in actions]
    assert not any('继续练习' in text for text in labels)
    assert labels.index('显示答案') == labels.index('原文')+1
    assert [a.text() for a in w.import_button.menu().actions() if not a.isSeparator()] == ['导入文章', '导入文件夹', '恢复学习备份…']
    assert [a.text() for a in w.export_button.menu().actions() if not a.isSeparator()] == ['导出默写纸', '导出答案版', '导出学习备份…']
    assert w.actions['导入文章'].shortcut().toString() == 'Ctrl+O'
    assert w.export_button.isEnabled() and not w.actions['导出默写纸'].isEnabled()
    assert actions.index(next(a for a in actions if w.main_toolbar.widgetForAction(a) is w.export_button)) == actions.index(next(a for a in actions if w.main_toolbar.widgetForAction(a) is w.import_button)) + 1
    assert labels.index('快速成文') < actions.index(next(a for a in actions if w.main_toolbar.widgetForAction(a) is w.zoom_label))
    assert [a.text() for a in w.practice_menu.actions() if not a.isSeparator()] == ['开始默写','提交批改','未完成练习…']


def test_failed_result_save_can_retry_from_primary_button(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path/'db'); w = ui(repo)
    w._import_paths([str(sample_word(tmp_path))]); w.start_full_practice()
    save = repo.save_submitted_session
    def fail(session):
        raise sqlite3.OperationalError('disk unavailable')
    monkeypatch.setattr(repo,'save_submitted_session',fail)
    w._commit_submission()
    assert w.primary_action_button.text() == '重试保存成绩'
    assert w.primary_action_button.isEnabled()
    monkeypatch.setattr(repo,'save_submitted_session',save)
    w.primary_action_button.click()
    assert len(repo.list_history())==1
    assert w.session.database_id


def test_wordbook_reads_calendar_once_per_reload(tmp_path, ui, monkeypatch):
    repo = Repository(tmp_path/'db'); seed(repo, 100)
    today = repo.today; calls = []
    monkeypatch.setattr(repo,'today',lambda: calls.append(1) or today())
    dialog = WrongWordsDialog(repo)
    assert not dialog.table.showGrid()
    assert len(calls)==1
    dialog.search.setText('word99')
    assert sum(not dialog.table.isRowHidden(i) for i in range(100))==1
    dialog.search.clear()
    assert all(not dialog.table.isRowHidden(i) for i in range(100))
    dialog.close()


def test_flow_measures_each_item_once_per_layout(tmp_path, ui, monkeypatch):
    # Exercise the actual paragraph layout with a wrapping line.
    w = ui(Repository(tmp_path/'db')); w._import_paths([str(sample_word(tmp_path))]); w.start_full_practice()
    flow = w.content.findChild(FlowLayout)
    calls=[]
    for i in range(flow.count()):
        item=flow.itemAt(i); original=item.sizeHint
        monkeypatch.setattr(item,'sizeHint',lambda original=original: calls.append(1) or original())
    visible=sum(not flow.itemAt(i).widget().isHidden() for i in range(flow.count()))
    flow._do_layout(QRect(0,0,140,0),False)
    assert len(calls)<=visible
