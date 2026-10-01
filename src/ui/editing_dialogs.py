from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout)

from src.models.rules import RecognitionRules
from src.services.target_editor import set_target, remove_target


class RulesDialog(QDialog):
    def __init__(self, rules, parent=None):
        super().__init__(parent)
        self.setWindowTitle("识别规则")
        layout = QFormLayout(self)
        self.mode = QComboBox()
        for title, value in (("标准模式（推荐）", "standard"),
                             ("宽松模式", "lenient"),
                             ("严格模式", "strict")):
            self.mode.addItem(title, value)
        if rules.format_mode == "any" and rules.require_chinese:
            preset = "lenient"
        elif rules.format_mode == "red_and_bold":
            preset = "strict"
        else:
            preset = "standard"
        self.mode.setCurrentIndex(self.mode.findData(preset))
        self.headings = QCheckBox("排除 Word 标题/Heading 样式")
        self.headings.setChecked(rules.skip_headings)
        layout.addRow("识别预设", self.mode)
        layout.addRow(self.headings)
        note = QLabel("标准：红色或加粗，并且紧邻中文释义。\n"
                      "宽松：不看格式，只要英文紧邻中文释义。\n"
                      "严格：必须同时红色且加粗，可以没有中文释义。\n"
                      "宽松模式中少数无格式短语边界不明时，可用“手动调整挖空”合并。")
        note.setWordWrap(True)
        layout.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def rules(self):
        mode = self.mode.currentData()
        if mode == "lenient":
            return RecognitionRules("any", True, self.headings.isChecked())
        if mode == "strict":
            return RecognitionRules("red_and_bold", False, self.headings.isChecked())
        return RecognitionRules("red_or_bold", True, self.headings.isChecked())


class TargetEditorDialog(QDialog):
    def __init__(self, article, parent=None):
        super().__init__(parent)
        self.article = deepcopy(article)
        self.setWindowTitle("手动调整挖空 · " + article.filename)
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选中英文后可设为一题；框选多个目标可合并。拆分时先取消原题，再分别选取。只修改软件中的练习，不修改 Word。"))
        self.paragraphs = QComboBox()
        for p in article.paragraphs:
            self.paragraphs.addItem(f"第 {p.index + 1} 段：{p.text[:70]}", p.index)
        layout.addWidget(self.paragraphs)
        self.text = QTextEdit(); self.text.setReadOnly(True)
        layout.addWidget(self.text, 3)
        self.targets = QListWidget(); layout.addWidget(self.targets, 1)
        row = QHBoxLayout()
        add = QPushButton("将选中英文设为一题"); add.clicked.connect(self._add)
        remove = QPushButton("取消下方选中题目的挖空"); remove.clicked.connect(self._remove)
        row.addWidget(add); row.addWidget(remove); layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.paragraphs.currentIndexChanged.connect(self._render)
        self._render()

    def _render(self):
        index = self.paragraphs.currentData()
        if index is None: return
        paragraph = next(p for p in self.article.paragraphs if p.index == index)
        self.text.setPlainText(paragraph.text)
        selections = []
        self.targets.clear()
        for item in self.article.word_items:
            if item.paragraph_index != index: continue
            row = QListWidgetItem(item.answer + ("（" + item.chinese + "）" if item.chinese else ""))
            row.setData(Qt.UserRole, item.id); self.targets.addItem(row)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = self.text.textCursor()
            # QTextCursor uses UTF-16 units; Python character offsets may include emoji.
            selection.cursor.setPosition(len(paragraph.text[:item.start].encode("utf-16-le")) // 2)
            selection.cursor.setPosition(len(paragraph.text[:item.end].encode("utf-16-le")) // 2, QTextCursor.KeepAnchor)
            selection.format.setBackground(QColor("#dceeff")); selections.append(selection)
        self.text.setExtraSelections(selections)

    def _add(self):
        cursor = self.text.textCursor()
        raw = self.text.toPlainText().encode("utf-16-le")
        start = len(raw[:cursor.selectionStart() * 2].decode("utf-16-le"))
        end = len(raw[:cursor.selectionEnd() * 2].decode("utf-16-le"))
        try:
            set_target(self.article, self.paragraphs.currentData(), start, end)
            self._render()
        except ValueError as exc: QMessageBox.information(self, "无法设为题目", str(exc))

    def _remove(self):
        row = self.targets.currentItem()
        if row:
            remove_target(self.article, row.data(Qt.UserRole)); self._render()
