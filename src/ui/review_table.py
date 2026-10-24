"""Shared, bounded text layout for review tables; never changes stored values."""
from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QTableWidget


class ReviewItemDelegate(QStyledItemDelegate):
    def __init__(self, wrapped_columns, parent):
        super().__init__(parent)
        self.wrapped_columns = frozenset(wrapped_columns)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() not in self.wrapped_columns:
            option.features &= ~QStyleOptionViewItem.WrapText


class ReviewTable(QTableWidget):
    def __init__(self, columns, wrapped_columns=(), parent=None):
        super().__init__(0, columns, parent)
        self.wrapped_columns = tuple(wrapped_columns)
        self.setWordWrap(bool(wrapped_columns))
        self.setTextElideMode(Qt.ElideRight)
        self.setItemDelegate(ReviewItemDelegate(wrapped_columns, self))
        self.setStyleSheet('QTableWidget::item {padding:4px 9px;}')
        self.verticalHeader().setDefaultSectionSize(34)
        self._row_timer = QTimer(self)
        self._row_timer.setSingleShot(True)
        self._row_timer.setInterval(40)
        self._row_timer.timeout.connect(self.refresh_row_heights)
        self.horizontalHeader().sectionResized.connect(self._queue_row_heights)

    def set_review_headers(self, labels, left_columns):
        self.left_columns = frozenset(left_columns)
        self.setHorizontalHeaderLabels(labels)
        for column in range(self.columnCount()):
            self.horizontalHeaderItem(column).setTextAlignment(self.column_alignment(column))

    def column_alignment(self, column):
        return Qt.AlignLeft | Qt.AlignVCenter if column in self.left_columns else Qt.AlignCenter

    def _queue_row_heights(self, *_):
        if self.wrapped_columns:
            self._row_timer.start()

    def refresh_row_heights(self):
        self._row_timer.stop()
        if not self.wrapped_columns:
            return
        metrics = self.fontMetrics()
        maximum = max(34, 2 * metrics.height() + 10)
        for row in range(self.rowCount()):
            height = 34
            for column in self.wrapped_columns:
                item = self.item(row, column)
                if item is not None:
                    bounds = metrics.boundingRect(QRect(0, 0, max(1, self.columnWidth(column) - 20), 100000),
                                                  Qt.TextWordWrap, item.text())
                    height = max(height, min(maximum, bounds.height() + 10))
            if self.rowHeight(row) != height:
                self.setRowHeight(row, height)
