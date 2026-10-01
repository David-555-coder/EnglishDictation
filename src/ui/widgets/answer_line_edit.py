from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit


class AnswerLineEdit(QLineEdit):
    requestMove = Signal(int)
    focusEntered = Signal(str)

    def __init__(self, item_id: str, answer_length: int, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.setMinimumWidth(85)
        self.setMaximumWidth(650)
        self.setPlaceholderText("")
        self.setAccessibleName("默写答案")
        self.setProperty("answerLength", answer_length)

    def update_preferred_width(self):
        """Measure on render, zoom or focus, not on each input character."""
        self.ensurePolished()
        metrics = self.fontMetrics()
        expected = self.property("answerText") or ("n" * (self.property("answerLength") or 8))
        padding = metrics.horizontalAdvance("mm") + 24
        minimum = max(100, round(100 * metrics.height() / 24))
        width = min(650, max(minimum, metrics.horizontalAdvance(expected) + padding,
                             metrics.horizontalAdvance(self.text()) + padding))
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
