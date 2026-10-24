"""Keep toolbar focus visible for keyboard navigation, quiet for mouse input."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QMenu, QToolButton, QWidget
from shiboken6 import isValid


class ToolbarFocus(QObject):
    def __init__(self, toolbar, window):
        super().__init__(window)
        self.toolbar = toolbar
        self.window = window
        self.keyboard = False
        # A global application filter would cross Python for every label's
        # paint/layout event in long articles. Only these navigation surfaces
        # need to distinguish mouse input from keyboard navigation.
        surfaces = [window, toolbar, window.menuBar(), window.content,
                    window.scroll.viewport(), window.status_panel]
        surfaces += toolbar.findChildren(QToolButton) + window.findChildren(QMenu)
        for widget in surfaces:
            widget.installEventFilter(self)

    def _apply(self, button):
        if button.property('keyboardFocus') != self.keyboard:
            button.setProperty('keyboardFocus', self.keyboard)
            button.style().unpolish(button); button.style().polish(button)
            button.update()

    def _set_keyboard(self, keyboard):
        if keyboard == self.keyboard:
            return
        self.keyboard = keyboard
        for button in self.toolbar.findChildren(QToolButton):
            self._apply(button)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.FocusIn):
            return False
        if not isValid(self.window) or not isValid(self.toolbar) or not isinstance(watched, QWidget):
            return False
        if watched is not self.window and not self.window.isAncestorOf(watched):
            return False
        if kind == QEvent.MouseButtonPress:
            self._set_keyboard(False)
        elif kind == QEvent.KeyPress:
            if event.key() not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
                self._set_keyboard(True)
        elif isinstance(watched, QToolButton) and self.toolbar.isAncestorOf(watched):
            if event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason):
                self._set_keyboard(True)
            elif event.reason() == Qt.MouseFocusReason:
                self._set_keyboard(False)
            self._apply(watched)
        return False
