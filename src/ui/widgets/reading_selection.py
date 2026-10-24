"""One visible-text selection across flow labels and paragraph cards.

No application-wide event filter, hidden answer lookup, database access, or
replacement input editor. Editable fields keep their native mouse/keyboard use.
Qt's text layout maps wrapped/rich/Unicode label coordinates to cursor positions.
"""
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QKeySequence, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit
from shiboken6 import isValid

from src.ui.widgets.text_menus import plain_label_text


def units(text):
    return len(text.encode('utf-16-le')) // 2


def piece(text, start, end):
    return text.encode('utf-16-le')[2*start:2*end].decode('utf-16-le')


@dataclass
class Fragment:
    widget: object
    text: str
    start: int
    end: int
    card: object


class ReadingSelection(QObject):
    def __init__(self, root, scroll, interaction_started=None):
        super().__init__(root)
        self.root, self.scroll = root, scroll
        self.interaction_started = interaction_started
        self._bound = []
        self._selected = {}
        self._fragments = []
        self._text = ''
        self._anchor = self._position = 0
        self._dragging = False
        self._pointer = None
        self._documents = {}
        self._boxes = []
        self._window = None
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(40)
        self._scroll_timer.timeout.connect(self._autoscroll)
        root.installEventFilter(self)

    def bind(self):
        """Called after rendering; repeated zoom/render updates don't add filters."""
        window = self.root.window()
        if window is not self._window:
            if self._window is not None and isValid(self._window): self._window.removeEventFilter(self)
            self._window = window
            window.installEventFilter(self)
        known = set(self._bound)
        for widget in self.root.findChildren(QLabel) + self.root.findChildren(QLineEdit):
            if widget in known or not isValid(widget):
                continue
            if isinstance(widget, QLabel):
                if not widget.textInteractionFlags() & Qt.TextSelectableByMouse:
                    continue
                widget.setFocusPolicy(Qt.ClickFocus)  # Don't put every word in the Tab chain.
            widget._reading_selection = self
            widget.installEventFilter(self)
            self._bound.append(widget)
        self._documents.clear()
        self._boxes.clear()
        for widget, span in self._selected.items():
            if isValid(widget): widget.setSelection(*span)

    def set_root(self, root):
        """Move the one selection controller to a newly mounted content page."""
        if root is self.root:
            return
        self.reset()
        if isValid(self.root):
            self.root.removeEventFilter(self)
        self.setParent(root)
        self.root = root
        root.installEventFilter(self)

    def reset(self):
        self.clear()
        for widget in self._bound:
            if isValid(widget):
                widget.removeEventFilter(self)
                if getattr(widget, '_reading_selection', None) is self:
                    del widget._reading_selection
        self._bound.clear()
        self._fragments.clear()
        self._documents.clear()
        self._boxes.clear()
        self._text = ''

    def clear(self):
        self.end_drag()

        for widget in self._selected:
            if isValid(widget):
                if isinstance(widget, QLabel): widget.setSelection(0, 0)
                else: widget.deselect()
        self._selected.clear()
        self._anchor = self._position = 0

    def end_drag(self):
        self._dragging = False
        self._scroll_timer.stop()

    def _snapshot(self):
        self._fragments, parts, offset = [], [], 0
        previous_card = None
        bound = set(self._bound)

        def walk(layout, card=None):
            nonlocal previous_card, offset
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget()
                if widget is None:
                    if item.layout(): walk(item.layout(), card)
                    continue
                if not widget.isVisibleTo(self.root): continue
                own_card = widget if widget.property('paragraphText') is not None else card
                if widget.layout():
                    walk(widget.layout(), own_card)
                elif widget in bound:
                    if parts and own_card is not previous_card:
                        parts.append('\n\n'); offset += 2
                    text = (widget.text() or '____') if isinstance(widget, QLineEdit) else (
                        '\n' if widget.property('flowBreak') else plain_label_text(widget))
                    if widget.objectName() == 'learningNote' and parts:
                        parts.append('\n'); offset += 1
                    length = units(text)
                    self._fragments.append(Fragment(widget, text, offset, offset+length, own_card))
                    parts.append(text); offset += length; previous_card = own_card
        walk(self.root.layout())
        self._text = ''.join(parts)
        self._documents.clear()
        self._boxes.clear()

    def selected_text(self):
        lo, hi = sorted((self._anchor, self._position))
        return piece(self._text, lo, hi) if hi > lo else ''

    def selected_context(self):
        """Visible snapshot with Python offsets; never consult hidden answers."""
        lo, hi = sorted((self._anchor, self._position))
        return self._text, len(piece(self._text, 0, lo)), len(piece(self._text, 0, hi))

    def copy(self):
        text = self.selected_text()
        if text: QApplication.clipboard().setText(text)

    def select_all(self):
        self.clear(); self._snapshot()
        self._position = units(self._text)
        self._apply()

    def adopt_native(self, widget):
        """Programmatic/native label selections still use the same copy menu."""
        if not self.selected_text() and widget.hasSelectedText():
            self._snapshot()
            fragment = next((f for f in self._fragments if f.widget is widget), None)
            if fragment:
                self._anchor = fragment.start + widget.selectionStart()
                self._position = self._anchor + units(widget.selectedText())
                self._apply()

    def _document(self, label):
        if label not in self._documents:
            document = QTextDocument()
            document.setDocumentMargin(0)
            document.setDefaultFont(label.font())
            if label.textFormat() == Qt.RichText:
                document.setHtml(label.text())
            else:
                document.setPlainText(plain_label_text(label))
            document.setTextWidth(max(1, label.contentsRect().width()-2*label.margin()-max(0,label.indent())))
            self._documents[label] = document
        return self._documents[label]

    def _local_position(self, fragment, point):
        widget = fragment.widget
        if isinstance(widget, QLineEdit):
            if point.x() <= widget.contentsRect().left()+widget.textMargins().left(): return 0
            if point.x() >= widget.contentsRect().right()-widget.textMargins().right(): return units(fragment.text)
            return widget.cursorPositionAt(point) if widget.text() else (0 if point.x()<widget.width()/2 else 4)
        document = self._document(widget)
        rect = widget.contentsRect().adjusted(widget.margin(),widget.margin(),-widget.margin(),-widget.margin())
        height = document.size().height()
        y = rect.y()
        if widget.alignment() & Qt.AlignVCenter: y += max(0,(rect.height()-height)/2)
        elif widget.alignment() & Qt.AlignBottom: y += max(0,rect.height()-height)
        if point.y() < y: return 0
        if point.y() >= y+height: return units(fragment.text)
        x = rect.x()+max(0,widget.indent())
        if widget.alignment() & Qt.AlignHCenter: x += max(0,(rect.width()-document.idealWidth())/2)
        pos = document.documentLayout().hitTest(QPointF(point.x()-x,point.y()-y),Qt.FuzzyHit)
        return max(0,min(units(fragment.text),pos))

    def _at(self, global_point):
        """Closest visible text box; gaps resolve to the nearest insertion point."""
        point = self.root.mapFromGlobal(global_point)
        if not self._boxes:
            for fragment in self._fragments:
                widget=fragment.widget
                if isValid(widget) and widget.isVisibleTo(self.root) and widget.width()>0 and widget.height()>0:
                    rect=QRect(widget.mapTo(self.root,QPoint()),widget.size())
                    self._boxes.append((fragment,rect.left(),rect.top(),rect.right(),rect.bottom()))
        def distance(box):
            _,left,top,right,bottom=box
            dx = max(left-point.x(),0,point.x()-right)
            dy = max(top-point.y(),0,point.y()-bottom)
            return dy*dy+dx*dx
        if not self._boxes: return 0
        fragment = min(self._boxes,key=distance)[0]
        local = fragment.widget.mapFromGlobal(global_point)
        return fragment.start + self._local_position(fragment, local)

    def _apply(self):
        lo, hi = sorted((self._anchor,self._position))
        selected = {}
        for fragment in self._fragments:
            widget = fragment.widget
            if not isValid(widget): continue
            a,b = max(lo,fragment.start)-fragment.start, min(hi,fragment.end)-fragment.start
            if b > a:
                # Empty editors are copied as visible blanks, never hidden answers.
                if isinstance(widget,QLineEdit): b=min(b,units(widget.text()))
                if b > a: selected[widget]=(a,b-a)
        for widget in self._selected.keys()-selected.keys():
            if isValid(widget):
                if isinstance(widget,QLabel): widget.setSelection(0,0)
                else: widget.deselect()
        for widget, span in selected.items():
            if self._selected.get(widget) != span: widget.setSelection(*span)
        self._selected = selected

    def _autoscroll(self):
        if not self._dragging or self._pointer is None:
            self._scroll_timer.stop(); return
        viewport = self.scroll.viewport(); point=viewport.mapFromGlobal(self._pointer)
        delta = point.y()-viewport.height()+24 if point.y()>viewport.height()-24 else point.y()-24 if point.y()<24 else 0
        if not delta: return
        bar=self.scroll.verticalScrollBar(); before=bar.value()
        bar.setValue(before+max(-36,min(36,delta)))
        if bar.value()!=before:
            self._position=self._at(self._pointer);self._apply()

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.root:
            if kind in (QEvent.LayoutRequest,QEvent.Resize):
                self._boxes.clear();self._documents.clear()
            return False
        if watched is self._window:
            if kind in (QEvent.WindowDeactivate, QEvent.Hide, QEvent.Close):
                self._dragging=False;self._scroll_timer.stop()
            return False
        if isinstance(watched,QLineEdit) and not watched.isReadOnly():
            if kind == QEvent.MouseButtonPress and event.button()==Qt.LeftButton: self.clear()
            return False  # Native selection/editing/shortcuts remain untouched.
        if kind in (QEvent.MouseButtonPress,QEvent.MouseButtonDblClick) and event.button()==Qt.LeftButton:
            shift=bool(event.modifiers()&Qt.ShiftModifier)
            if self.interaction_started: self.interaction_started()
            if not shift or not self._fragments:
                self.clear(); self._snapshot()
                self._anchor=self._at(event.globalPosition().toPoint())
            self._position=self._at(event.globalPosition().toPoint())
            watched.setFocus(Qt.MouseFocusReason)
            if isinstance(watched,QLineEdit) and self.interaction_started: self.interaction_started()
            if kind == QEvent.MouseButtonDblClick:
                document=QTextDocument();document.setPlainText(self._text)
                cursor=QTextCursor(document);cursor.setPosition(self._position);cursor.select(QTextCursor.WordUnderCursor)
                self._anchor,self._position=cursor.selectionStart(),cursor.selectionEnd()
                self._dragging=False;self._scroll_timer.stop()
            else:
                self._dragging=True;self._pointer=event.globalPosition().toPoint();self._scroll_timer.start()
            self._apply();return True
        if kind == QEvent.MouseMove and self._dragging:
            self._pointer=event.globalPosition().toPoint();self._position=self._at(self._pointer);self._apply();return True
        if kind == QEvent.MouseButtonRelease and event.button()==Qt.LeftButton and self._dragging:
            self._position=self._at(event.globalPosition().toPoint());self._apply()
            self._dragging=False;self._scroll_timer.stop();return True
        if kind == QEvent.KeyPress:
            if event.matches(QKeySequence.Copy):
                if isinstance(watched,QLineEdit) and not self.selected_text(): return False
                self.copy();return True
            if event.matches(QKeySequence.SelectAll): self.select_all();return True
            if event.key()==Qt.Key_Escape: self.clear();return True
            if event.modifiers()&Qt.ShiftModifier and event.key() in (Qt.Key_Left,Qt.Key_Right,Qt.Key_Home,Qt.Key_End):
                document=QTextDocument();document.setPlainText(self._text)
                cursor=QTextCursor(document);cursor.setPosition(self._position)
                operations={Qt.Key_Left:QTextCursor.PreviousCharacter,Qt.Key_Right:QTextCursor.NextCharacter,
                            Qt.Key_Home:QTextCursor.StartOfLine,Qt.Key_End:QTextCursor.EndOfLine}
                operation=operations[event.key()]
                if event.modifiers()&Qt.ControlModifier:
                    operation={Qt.Key_Left:QTextCursor.PreviousWord,Qt.Key_Right:QTextCursor.NextWord,
                               Qt.Key_Home:QTextCursor.Start,Qt.Key_End:QTextCursor.End}[event.key()]
                cursor.movePosition(operation);self._position=cursor.position();self._apply();return True
        return False
