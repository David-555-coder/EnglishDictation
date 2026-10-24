"""A compact, read-only guide; no extra learning actions or stored settings."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)


SHORTCUT_SECTIONS = (
    ('学习操作', (
        ('Enter / Shift+Enter', '下一词 / 上一词（答题框内）'),
        ('Shift+Backspace', '清空当前答案（答题框内）'),
        ('Ctrl+Enter', '提交批改（默写中）'),
        ('Ctrl+1 / Ctrl+2', '原文 / 整篇默写（或待练错词）'),
        ('Ctrl+R', '历史错词练习'),
    )),
    ('阅读与显示', (
        ('F8', '切换专注模式'),
        ('Ctrl+滚轮 / Ctrl++ / Ctrl+-', '放大或缩小正文'),
        ('Ctrl+0', '恢复 100% 显示比例'),
    )),
    ('编辑与选择', (
        ('Ctrl+C / Ctrl+V / Ctrl+X', '复制 / 粘贴 / 剪切'),
        ('Ctrl+Z / Ctrl+Y', '撤销 / 重做（可编辑输入框）'),
        ('Ctrl+A', '全选当前正文、输入框或列表'),
        ('Shift+← / → / Home / End', '扩展选择：字符 / 本行'),
        ('Ctrl+Shift+← / → / Home / End', '扩展选择：单词 / 全文'),
    )),
    ('列表与其他', (
        ('Ctrl+O', '导入文章'),
        ('Ctrl / Shift + 单击', '多选 / 连选列表项目'),
        ('Delete', '移除文章列表项目 / 删除历史记录'),
        ('Tab / Shift+Tab', '下一个 / 上一个控件'),
        ('Esc', '取消正文选择 / 关闭弹窗'),
        ('F1', '打开快捷键说明（主窗口）'),
    )),
)


class ShortcutHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('快捷键说明')
        self.setObjectName('shortcutHelp')
        self.setMinimumSize(560, 360)
        self.resize(640, min(660, self.screen().availableGeometry().height() - 60))
        self.setStyleSheet("""
            QDialog#shortcutHelp { background:#f8fafc; }
            QLabel { background:transparent; font-size:13px; color:#405268; }
            QLabel#shortcutSection { color:#30445c; font-size:14px; font-weight:600; padding-top:8px; }
            QLabel#shortcutKey { color:#285d96; }
            QLabel#shortcutNote { color:#71839a; }
            QScrollArea#shortcutScroll, QWidget#shortcutContent { background:#ffffff; border:none; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 14); layout.setSpacing(10)
        scroll = QScrollArea(); scroll.setObjectName('shortcutScroll')
        scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(); content.setObjectName('shortcutContent')
        grid = QGridLayout(content)
        grid.setContentsMargins(12, 0, 12, 12)
        grid.setHorizontalSpacing(16); grid.setVerticalSpacing(5)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        row = 0
        for title, shortcuts in SHORTCUT_SECTIONS:
            heading = QLabel(title); heading.setObjectName('shortcutSection')
            grid.addWidget(heading, row, 0, 1, 2); row += 1
            for keys, description in shortcuts:
                key = QLabel(keys); key.setObjectName('shortcutKey')
                label = QLabel(description)
                for cell in (key, label):
                    cell.setTextFormat(Qt.PlainText)
                    cell.setWordWrap(True)
                    cell.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                grid.addWidget(key, row, 0); grid.addWidget(label, row, 1); row += 1
        grid.setRowStretch(row, 1)
        scroll.setWidget(content); layout.addWidget(scroll, 1)
        note = QLabel('按所在页面和焦点生效；只读内容不可剪切、粘贴，删除前会确认。\n'
                      '今日复习请从顶部按钮进入，不用 Ctrl+2 代替选择任务。')
        note.setObjectName('shortcutNote'); note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout(); buttons.addStretch()
        close = QPushButton('关闭'); close.setAutoDefault(False); close.clicked.connect(self.reject)
        buttons.addWidget(close); layout.addLayout(buttons)
