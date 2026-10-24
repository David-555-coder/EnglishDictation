from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout
from src.models.practice import PracticeMode


class UnfinishedPracticeDialog(QDialog):
    """One explicit resume entry, with stable selection even for identical names."""

    def __init__(self, records, parent=None):
        super().__init__(parent)
        self.records = records
        self.setWindowTitle("未完成练习")
        self.resize(610, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择草稿，继续未完成的练习："))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        for session, updated_at in records:
            try:
                timestamp = datetime.fromisoformat(updated_at).strftime("%m-%d %H:%M")
            except ValueError:
                timestamp = updated_at
            title = session.article.filename
            if session.mode == PracticeMode.DAILY_REVIEW:
                day = session.view_state.get('daily_review_day') or session.started_at.date().isoformat()
                title = f"复习草稿 · {day} · {session.total}词"
            elif session.mode != PracticeMode.FULL_ARTICLE:
                title = f"{title} · 错词草稿 {session.started_at:%Y-%m-%d}"
            if session.submitted and not session.database_id:
                title = '成绩待保存 · ' + title
            item = QListWidgetItem(f"{title}\n已填写 {session.filled} / {session.total} · 最后练习 {timestamp}")
            item.setToolTip(session.article.source_path)
            self.list.addItem(item)
        self.list.setStyleSheet("QListWidget::item { padding: 10px 8px; }")
        layout.addWidget(self.list, 1)
        note = QLabel("文章默写显示最近10篇，同篇多轮只显示最近一轮；复习草稿另外按日期列出。\n旧复习草稿不会自动变成今天的任务；继续练习不会删除文章或已有成绩。")
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
