from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QAbstractItemView, QListWidget


class ArticleList(QListWidget):
    """Explorer-like range selection; right-click preserves the selected group."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionRectVisible(True)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setDragEnabled(False)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.RightButton and item and item.isSelected():
            self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            event.accept()
            return
        super().mousePressEvent(event)
