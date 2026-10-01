from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout


class UnfinishedPracticeDialog(QDialog):
    """One explicit resume entry, with stable selection even for identical names."""

    def __init__(self, records, parent=None):
        super().__init__(parent)
        self.records = records
        self.setWindowTitle("未完成练习")
        self.resize(610, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择一篇文章，继续上次未完成的默写："))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        for session, updated_at in records:
            try:
                timestamp = datetime.fromisoformat(updated_at).strftime("%m-%d %H:%M")
            except ValueError:
                timestamp = updated_at
            item = QListWidgetItem(f"{session.article.filename}\n已填写 {session.filled} / {session.total} · 最后练习 {timestamp}")
            item.setToolTip(session.article.source_path)
            self.list.addItem(item)
        self.list.setStyleSheet("QListWidget::item { padding: 10px 8px; }")
        layout.addWidget(self.list, 1)
        note = QLabel("保留最近 10 篇未完成练习，超出后自动清理最旧草稿。\n同篇多轮只显示最近一轮；不会删除文章、成绩或错词。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64758a;")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.resume_button = buttons.button(QDialogButtonBox.Ok)
        self.resume_button.setText("继续练习")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        layout.addWidget(buttons)
        self.list.setCurrentRow(0)
        self.list.setFocus(Qt.OtherFocusReason)

    @property
    def selected_session(self):
        row = self.list.currentRow()
        return self.records[row][0] if 0 <= row < len(self.records) else None
