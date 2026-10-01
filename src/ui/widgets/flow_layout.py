from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QStyle


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 7, v_spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._height_cache = {}
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def insertWidgetAfter(self, existing, widget):
        """Insert a companion widget immediately after another flow item."""
        from PySide6.QtWidgets import QWidgetItem
        index = next((i for i, item in enumerate(self._items) if item.widget() is existing), len(self._items) - 1)
        self._items.insert(index + 1, QWidgetItem(widget))
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def invalidate(self):
        # Qt invalidates the parent layout when text, fonts, visibility or item
        # sizes change; explicit invalidation also handles our flow properties.
        if hasattr(self, "_height_cache"):
            self._height_cache.clear()
        super().invalidate()

    def expandingDirections(self):
        return Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        key = (width, self._h_spacing, self._v_spacing, self.getContentsMargins())
        height = self._height_cache.get(key)
        if height is None:
            height = self._do_layout(QRect(0, 0, width, 0), True)
            # Resizing a window must not leave an unbounded collection of widths.
            if len(self._height_cache) >= 8:
                self._height_cache.pop(next(iter(self._height_cache)))
            self._height_cache[key] = height
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():
                continue
            minimum = item.minimumSize()
            size = size.expandedTo(QSize(min(85, minimum.width()), minimum.height()))
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect, test_only):
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        x, y, line_height = effective.x(), effective.y(), 0
        available = max(1, effective.width())
        measured = []
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = QSize(item.sizeHint())
            preferred = widget.property("preferredFlowWidth") if widget else None
            width = min(available, preferred or hint.width())
            if width < hint.width() and item.hasHeightForWidth():
                hint.setHeight(max(hint.height(), item.heightForWidth(width)))
            hint.setWidth(width)
            measured.append((item, widget, hint))

        row = []

        def place_row():
            if not test_only:
                for row_item, row_x, row_size in row:
                    # Center text beside taller input fields instead of top-aligning it.
                    row_item.setGeometry(QRect(QPoint(row_x, y + (line_height - row_size.height()) // 2), row_size))

        for index, (item, widget, hint) in enumerate(measured):
            if widget and widget.property("flowBreak"):
                place_row(); row = []
                y += max(line_height, hint.height()) + self._v_spacing
                x, line_height = effective.x(), 0
                if not test_only: item.setGeometry(QRect(x, y, 0, 0))
                continue
            space_x = self._h_spacing
            space_y = self._v_spacing
            if space_x < 0:
                space_x = widget.style().layoutSpacing(QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal)
            # Keep short Chinese hints/punctuation with the preceding word when
            # the pair fits a whole line. Long pairs still wrap rather than overflow.
            group_width = hint.width()
            for _, following, following_size in measured[index + 1:index + 3]:
                if not following or not following.property("keepWithPrevious"):
                    break
                group_width += space_x + following_size.width()
            required = group_width if group_width <= available else hint.width()
            next_x = x + hint.width() + space_x
            if x + required > effective.right() + 1 and line_height > 0:
                place_row(); row = []
                x = effective.x()
                y += line_height + space_y
                next_x = x + hint.width() + space_x
                line_height = 0
            row.append((item, x, hint))
            x = next_x
            line_height = max(line_height, hint.height())
        place_row()
        return y + line_height - rect.y() + bottom
