"""Consistent text menus, isolated from reading zoom and feedback colours.

Keep native editing actions and their read-only/undo state. Reading labels use
the same presentation without installing an application-wide event filter.
"""
import re

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QKeySequence, QPalette, QTextDocument
from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QLineEdit, QMainWindow,
                               QMenu, QPlainTextEdit, QTextEdit, QWidget)
from shiboken6 import isValid


MENU_STYLE = """
QMenu {
    background-color:#ffffff; color:#2d3b4d;
    border:1px solid #dce3eb; border-radius:8px; padding:5px;
    font-family:'Microsoft YaHei UI'; font-size:14px; font-weight:400; font-style:normal;
}
QMenu::item {
    background-color:transparent; color:#2d3b4d; border:none; border-radius:5px;
    padding:7px 24px 7px 12px; margin:1px 0;
    font-family:'Microsoft YaHei UI'; font-size:14px; font-weight:400; font-style:normal;
}
QMenu::item:selected { background-color:#edf3fa; color:#245c96; }
QMenu::item:disabled { background-color:transparent; color:#a0aab7; }
QMenu::separator { height:1px; background-color:#edf0f4; margin:4px 8px; }
QMenu::icon { padding:0; }
"""


def menu_font():
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(14); font.setWeight(QFont.Normal); font.setItalic(False)
    return font


def style_menu(menu):
    """A local stylesheet wins over unscoped red/transparent label styles."""
    if not isValid(menu): return menu
    if not menu.property('consistentMenu'):
        menu.setProperty('consistentMenu', True)
        menu.setStyleSheet(MENU_STYLE)
        menu.setFont(menu_font())
        palette = QPalette(QApplication.palette())
        for role in (QPalette.Window, QPalette.Base, QPalette.Button):
            palette.setColor(role, QColor('#ffffff'))
        for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
            palette.setColor(role, QColor('#2d3b4d'))
        palette.setColor(QPalette.Highlight, QColor('#edf3fa'))
        palette.setColor(QPalette.HighlightedText, QColor('#245c96'))
        menu.setPalette(palette)
        menu.aboutToShow.connect(lambda: _style_actions(menu))
    _style_actions(menu)
    return menu


def _style_actions(menu):
    if not isValid(menu): return
    # Bind each QMenu individually; do not recursively reacquire Qt-owned
    # submenus through QAction.menu() while changing their parent menu's style.
    for action in menu.actions():
        if not isValid(action): continue
        action.setFont(menu_font())


_ACTIONS = {
    'undo': ('撤销', QKeySequence.Undo), 'redo': ('重做', QKeySequence.Redo),
    'cut': ('剪切', QKeySequence.Cut), 'copy': ('复制', QKeySequence.Copy),
    'paste': ('粘贴', QKeySequence.Paste), 'delete': ('删除', None),
    'select all': ('全选', QKeySequence.SelectAll),
    'copy link location': ('复制链接地址', None),
    'paste and match style': ('粘贴并匹配样式', None),
}
for _name, _shortcut in list(_ACTIONS.values()):
    _ACTIONS[_name] = (_name, _shortcut)


def _translate_actions(menu):
    for action in menu.actions():
        if action.isSeparator(): continue
        text = re.sub(r'\(&[A-Za-z]\)', '', action.text().split('\t', 1)[0]).replace('&', '').strip()
        translated = _ACTIONS.get(text.casefold())
        if translated:
            title, shortcut = translated
            action.setText(title)
            if shortcut is not None:
                action.setShortcut(QKeySequence(shortcut))
                action.setShortcutVisibleInContextMenu(True)
        action.setIcon(QIcon())
        submenu = action.menu()
        if submenu is not None: _translate_actions(submenu)


def plain_label_text(label):
    text = label.text()
    rich = label.textFormat() == Qt.RichText or (
        label.textFormat() == Qt.AutoText and re.search(r'<(?:p|span|div|br|b|i|a)\b', text, re.I))
    if rich:
        document = QTextDocument(); document.setHtml(text)
        return document.toPlainText()
    return text


def text_menu(widget):
    if isinstance(widget, QLabel):
        menu = QMenu(widget.window())
        selection = getattr(widget, '_reading_selection', None)
        if selection: selection.adopt_native(widget)
        selected = selection.selected_text() if selection else widget.selectedText()
        copy = menu.addAction('复制', lambda: QApplication.clipboard().setText(selected))
        copy.setShortcut(QKeySequence.Copy); copy.setEnabled(bool(selected))
        text = plain_label_text(widget)
        select = menu.addAction('全选', selection.select_all if selection else
                               lambda: widget.setSelection(0, len(text.encode('utf-16-le')) // 2))
        select.setShortcut(QKeySequence.SelectAll); select.setEnabled(bool(text))
    else:
        # Native actions already implement undo, clipboard and read-only rules.
        menu = widget.createStandardContextMenu()
        # Remove the coloured/zoomed text widget from the inheritance chain.
        menu.setParent(widget.window(), menu.windowFlags())
    _translate_actions(menu)
    if isinstance(widget,QLineEdit) and widget.isReadOnly():
        selection = getattr(widget, '_reading_selection', None)
        if selection and selection.selected_text():
            for action in menu.actions():
                if action.text() in ('复制','全选'):
                    action.triggered.disconnect()
                    action.triggered.connect(selection.copy if action.text()=='复制' else selection.select_all)
                    action.setEnabled(True)
    # Only the reading surface opts in. Native editors and management dialogs
    # keep their original actions; active independent practice never opts in.
    selection = getattr(widget, '_reading_selection', None)
    translate = getattr(selection, 'translation_handler', None)
    allowed = getattr(selection, 'translation_allowed', None)
    if translate and allowed and allowed() and re.search('[A-Za-z]', selection.selected_text()):
        point = QCursor.pos()
        menu.addSeparator()
        action = menu.addAction('翻译')
        action.triggered.connect(lambda: translate(widget, point))
    return style_menu(menu)


def show_text_menu(widget, event):
    if isinstance(widget, QLabel) and not widget.textInteractionFlags() & (
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard):
        event.ignore(); return
    menu = text_menu(widget)
    position = event.globalPos()
    if position.x() < 0 or position.y() < 0:
        position = widget.mapToGlobal(widget.rect().center())
    try:
        menu.exec(position)
    finally:
        menu.deleteLater()
    event.accept()


class TextLabel(QLabel):
    def contextMenuEvent(self, event):
        show_text_menu(self, event)


class TextLineEdit(QLineEdit):
    def contextMenuEvent(self, event):
        show_text_menu(self, event)


class TextEdit(QTextEdit):
    def contextMenuEvent(self, event):
        show_text_menu(self, event)


class PlainTextEdit(QPlainTextEdit):
    def contextMenuEvent(self, event):
        show_text_menu(self, event)


class _NativeTextMenu(QObject):
    """A few native spin/combo/prompt fields; never every article paint event."""
    def __init__(self, widget):
        super().__init__(widget); self.widget = widget
        widget.installEventFilter(self)
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            widget.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ContextMenu:
            show_text_menu(self.widget, event); return True
        return False


class _DialogChildren(QObject):
    """Cover native QInputDialog fields created after the owner was built."""
    def eventFilter(self, watched, event):
        if event.type() == QEvent.ChildAdded:
            # ChildAdded arrives before a native dialog has finished its C++
            # constructor. Query the completed objects on the next event turn.
            def bind_children():
                if isValid(watched):
                    for dialog in watched.findChildren(QDialog, options=Qt.FindDirectChildrenOnly):
                        bind_text_menus(dialog)
            QTimer.singleShot(0, bind_children)
        return False


def bind_text_menus(root):
    custom = (TextLabel, TextLineEdit, TextEdit, PlainTextEdit)
    widgets = [root] + root.findChildren(QWidget)
    for widget in widgets:
        if not isValid(widget): continue
        if isinstance(widget, QMenu):
            style_menu(widget)
        elif isinstance(widget, (QDialog, QMainWindow)) and not hasattr(widget, '_menu_dialog_children'):
            widget._menu_dialog_children = _DialogChildren(widget)
            widget.installEventFilter(widget._menu_dialog_children)
        if isinstance(widget, custom): continue
        eligible = isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)) or (
            isinstance(widget, QLabel) and widget.textInteractionFlags() & (
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard))
        if eligible and not hasattr(widget, '_native_text_menu'):
            widget._native_text_menu = _NativeTextMenu(widget)
