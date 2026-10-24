import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMenu, QPlainTextEdit, QWidget, QInputDialog, QMainWindow

from src.ui.widgets.text_menus import (TextLabel, TextLineEdit, TextEdit, PlainTextEdit,
                                     text_menu, bind_text_menus, plain_label_text, style_menu)
from test_library_ui import ui, sample_word
from src.database.repository import Repository

_single_shot = QTimer.singleShot


def app():
    return QApplication.instance() or QApplication([])


def action(menu, title):
    return next(a for a in menu.actions() if a.text() == title)


@pytest.mark.parametrize('colour,background,bold', [('#ba354b','transparent',700),('#075cab','#edf5ff',700),('#b42318','#fff2f0',700),('#19723c','#f0fbf4',400)])
@pytest.mark.parametrize('pixels', [14,32,58])
def test_reading_menu_stays_small_and_neutral_under_zoom_and_feedback(colour,background,bold,pixels):
    app(); root=QWidget();root.setStyleSheet(f'QWidget{{font-size:{pixels}px;color:{colour};}}')
    label=TextLabel('attention',root);label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setStyleSheet(f'font-size:{pixels}px;color:{colour};background:{background};font-weight:{bold};')
    label.setSelection(0,9)
    menu=text_menu(label);menu.ensurePolished()
    assert menu.font().pixelSize()==14 and not menu.font().bold()
    assert menu.sizeHint().height()<100
    assert [a.text() for a in menu.actions()]==['复制','全选']
    assert menu.palette().color(QPalette.Window).name()=='#ffffff'
    assert menu.palette().color(QPalette.WindowText).name()=='#2d3b4d'
    assert all(a.font().pixelSize()==14 and not a.font().bold() for a in menu.actions())
    action(menu,'复制').trigger();assert QApplication.clipboard().text()=='attention'
    menu.deleteLater();root.deleteLater()


def test_label_select_all_and_copy_preserve_rich_text_unicode_without_html():
    app();label=TextLabel();label.setTextFormat(Qt.RichText);label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setText('<p>We <b>hold</b>（保持） 😀 attention.</p>')
    menu=text_menu(label);assert not action(menu,'复制').isEnabled()
    action(menu,'全选').trigger()
    assert label.selectedText()==plain_label_text(label)
    menu.deleteLater();menu=text_menu(label);action(menu,'复制').trigger()
    assert QApplication.clipboard().text()=='We hold（保持） 😀 attention.'
    menu.deleteLater();label.deleteLater()


@pytest.mark.parametrize('cls',[TextLineEdit,TextEdit,PlainTextEdit])
def test_native_edit_actions_keep_clipboard_undo_and_readonly_behaviour(cls):
    app();widget=cls();widget.setText('word') if isinstance(widget,QLineEdit) else widget.setPlainText('word')
    widget.selectAll();menu=text_menu(widget)
    assert all(a.icon().isNull() for a in menu.actions())
    action(menu,'复制').trigger();assert QApplication.clipboard().text()=='word'
    action(menu,'剪切').trigger()
    text=lambda:widget.text() if isinstance(widget,QLineEdit) else widget.toPlainText()
    assert text()==''
    menu.deleteLater();menu=text_menu(widget);action(menu,'撤销').trigger();assert text()=='word'
    widget.selectAll();QApplication.clipboard().setText('新词 😀')
    menu.deleteLater();menu=text_menu(widget);action(menu,'粘贴').trigger();assert text()=='新词 😀'
    widget.setReadOnly(True);widget.selectAll();menu.deleteLater();menu=text_menu(widget)
    assert action(menu,'复制').isEnabled()
    assert all(not a.isEnabled() for a in menu.actions() if a.text() in ('剪切','粘贴','删除'))
    menu.deleteLater();widget.deleteLater()


def test_real_context_event_uses_same_menu_and_preserves_selected_text():
    app();root=QWidget();label=TextLabel('selected text',root);label.setGeometry(0,0,200,50)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse);label.setSelection(0,8);root.show();app().processEvents()
    captured=[]
    def inspect():
        menu=QApplication.activePopupWidget()
        if isinstance(menu,QMenu):
            captured.append((menu.font().pixelSize(),[a.text() for a in menu.actions()]))
            action(menu,'复制').trigger();menu.close()
    _single_shot(0,inspect)
    QApplication.sendEvent(label,QContextMenuEvent(QContextMenuEvent.Mouse,QPoint(5,5),label.mapToGlobal(QPoint(5,5))))
    assert captured==[(14,['复制','全选'])]
    assert QApplication.clipboard().text()=='selected'
    root.deleteLater()


def test_native_dialog_fields_are_bound_without_changing_text_or_readonly():
    app();dialog=QInputDialog();dialog.setInputMode(QInputDialog.TextInput);dialog.setTextValue('draft')
    bind_text_menus(dialog);edit=dialog.findChild(QLineEdit)
    assert edit.text()=='draft' and not edit.isReadOnly()
    assert hasattr(edit,'_native_text_menu')
    edit.selectAll();captured=[]
    def inspect():
        menu=QApplication.activePopupWidget()
        if isinstance(menu,QMenu):captured.extend(a.text() for a in menu.actions());menu.close()
    dialog.show();app().processEvents();_single_shot(0,inspect)
    QApplication.sendEvent(edit,QContextMenuEvent(QContextMenuEvent.Mouse,QPoint(5,5),edit.mapToGlobal(QPoint(5,5))))
    assert '复制' in captured and 'Copy' not in captured and edit.text()=='draft'
    dialog.reject();dialog.deleteLater()


def test_native_static_prompt_created_later_receives_same_menu_policy():
    app();root=QMainWindow();bind_text_menus(root);root.show();app().processEvents()
    captured=[]
    def inspect():
        dialog=QApplication.activeModalWidget()
        if isinstance(dialog,QInputDialog):
            captured.append(hasattr(dialog.findChild(QLineEdit),'_native_text_menu'))
            dialog.reject()
    _single_shot(30,inspect)
    QInputDialog.getText(root,'原生输入','请输入',text='draft')
    assert captured==[True]
    root.close();root.deleteLater()


def test_styling_navigation_preserves_nested_menus_and_their_actions():
    from shiboken6 import isValid
    app();root=QMainWindow();parent=root.menuBar().addMenu('文章与设置');child=parent.addMenu('数据管理')
    entry=child.addAction('备份');bind_text_menus(root)
    assert isValid(child) and isValid(entry) and entry in child.actions()
    assert child.font().pixelSize()==14
    root.deleteLater()


def test_all_learning_views_keep_same_text_menu_and_do_not_reintroduce_edit(ui,tmp_path):
    window=ui(Repository(tmp_path/'db'));window._import_paths([str(sample_word(tmp_path))])
    for zoom in (70,100,160,180):
        window.set_zoom(zoom)
        label=next(l for l in window.content.findChildren(TextLabel) if 'ambition' in plain_label_text(l))
        menu=text_menu(label);menu.ensurePolished();assert menu.font().pixelSize()==14
        assert [a.text() for a in menu.actions()]==['复制','全选'];menu.deleteLater()
    window.start_full_practice();edit=next(iter(window.answer_edits.values()));edit.setText('wrong')
    menu=text_menu(edit);assert not any('修正' in a.text() for a in menu.actions());menu.deleteLater()
    window._commit_submission();menu=text_menu(edit)
    assert all(not a.isEnabled() for a in menu.actions() if a.text() in ('剪切','粘贴','删除'));menu.deleteLater()
    window.read_current_wrong()
    for label in window.content.findChildren(TextLabel):
        menu=text_menu(label);assert [a.text() for a in menu.actions()]==['复制','全选'];menu.deleteLater()
