from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeyEvent
from src.ui.widgets.text_menus import TextLineEdit
from math import ceil


class AnswerLineEdit(TextLineEdit):
    requestMove = Signal(int)
    focusEntered = Signal(str)

    def __init__(self, item_id: str, answer_length: int, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.setMinimumWidth(0)
        self.setMaximumWidth(320)
        self.setPlaceholderText("")
        self.setAccessibleName("默写答案")
        self.setProperty("answerLength", answer_length)
        self.setProperty("flowInput", True)

    def update_preferred_width(self):
        """Measure on render, zoom or focus, not on each input character."""
        self.ensurePolished()
        metrics = self.fontMetrics()
        expected = self.property("answerText") or ("n" * (self.property("answerLength") or 8))
        scale = self.property("flowScale") or max(.7, metrics.height() / 26)
        words = len(expected.split())
        low, high = (120, 170) if words <= 1 else (180, 250) if words <= 3 else (220, 320)
        measured = max(metrics.horizontalAdvance(expected), metrics.horizontalAdvance(self.text())) / scale + 24
        width = round(min(high, max(low, ceil(measured / 10) * 10)) * scale)
        self.setMaximumWidth(round(high * scale))
        if self.property("preferredFlowWidth") == width:
            return False
        self.setProperty("preferredFlowWidth", width)
        if self.parentWidget() and self.parentWidget().layout():
            self.parentWidget().layout().invalidate()
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Backspace and event.modifiers() == Qt.ShiftModifier:
            if not self.isReadOnly():
                self.clear()
            event.accept()
            return
        if event.key() in (0x01000004, 0x01000005):  # Enter / Return
            if not event.isAutoRepeat():
                self.requestMove.emit(-1 if event.modifiers() & Qt.ShiftModifier else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focusEntered.emit(self.item_id)
