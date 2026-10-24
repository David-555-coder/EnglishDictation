"""Plain-language total capacity and old-word subset controls."""
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                               QSpinBox, QVBoxLayout)
from src.services.daily_review import MAX_REVIEW_LIMIT


class ReviewQuantityDialog(QDialog):
    def __init__(self, total, old, status, parent=None, *, append=False):
        super().__init__(parent)
        self.append = append
        self.setWindowTitle("今天再加一些" if append else "每日复习数量")
        self.setMinimumWidth(480)
        self.resize(550, 430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        title = QLabel(self.windowTitle())
        title.setStyleSheet("font-size:20px;font-weight:600;color:#173653;")
        layout.addWidget(title)
        for text in ("先照顾已在复习的词；有余力时，再逐批加入以前没记牢的旧词。",
                     f"已到复习时间：{status.get('return_waiting', 0)}词 · 等待重新学习：{status.get('old_waiting', 0)}词"):
            label = QLabel(text); label.setWordWrap(True); layout.addWidget(label)
        self.total, self.old = QSpinBox(), QSpinBox()
        self.maintenance, self.backlog = self.total, self.old  # Integration aliases, total/subset semantics.
        for label, spin, value, minimum in (
            ("本次最多追加多少词" if append else "每天最多练多少词", self.total, total, 0 if append else 1),
            ("其中，最多重新学多少旧词", self.old, old, 0),
        ):
            row = QHBoxLayout(); row.addWidget(QLabel(label), 1)
            spin.setRange(minimum, MAX_REVIEW_LIMIT); spin.setValue(value); spin.setSuffix(" 词")
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            spin.setToolTip("直接输入整数，或用键盘↑↓调整。旧词数包含在总量内，不是额外增加。")
            spin.setMinimumHeight(42); spin.setFixedWidth(150)
            spin.setStyleSheet("QSpinBox {background:white;border:1px solid #c8d8e7;border-radius:8px;padding:7px 12px;}")
            row.addWidget(spin); layout.addLayout(row)
        self.total.setAccessibleName("每日总词数"); self.old.setAccessibleName("其中旧词上限")
        quick = QHBoxLayout(); quick.setSpacing(8); quick.addWidget(QLabel("总量快捷选择"))
        for value in (20, 40, 60, 80, 120):
            button = QPushButton(str(value)); button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, n=value: self.total.setValue(n)); quick.addWidget(button)
        layout.addLayout(quick)
        self.summary = QLabel(); self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#075cab;font-weight:600;"); layout.addWidget(self.summary)
        note = QLabel("先安排当天到期复习，剩余名额再加入旧词，不强行凑满。\n未到时间的词不提前抽取；同词纠错不重复占名额。")
        note.setWordWrap(True); note.setStyleSheet("color:#61758a;"); layout.addWidget(note)
        from src.services.review_continuity import future_text
        self.future_note = QLabel(future_text(status))
        self.future_note.setWordWrap(True); self.future_note.setStyleSheet("color:#61758a;")
        self.future_note.setVisible(bool(self.future_note.text())); layout.addWidget(self.future_note)
        self.append_note = QLabel("本次严格按所填数量追加，不自动补齐之前剩余名额；日常设置不变。")
        self.append_note.setWordWrap(True); self.append_note.setStyleSheet("color:#61758a;")
        self.append_note.setVisible(append); layout.addWidget(self.append_note)
        self.today_only = QCheckBox("仅今天调整，不改变以后每天的数量")
        self.today_only.setChecked(append); self.today_only.setVisible(not append)
        if not append: layout.addWidget(self.today_only)
        if not append and status.get("started"):
            frozen = QLabel("今天已开始：增加数量可以加任务；减少数量不会删除已有任务和输入。")
            frozen.setWordWrap(True); layout.addWidget(frozen)
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton("取消"); cancel.setAutoDefault(False); cancel.clicked.connect(self.reject)
        self.confirm = QPushButton("添加任务" if append else "保存设置")
        self.confirm.setObjectName("primaryButton"); self.confirm.setDefault(True); self.confirm.clicked.connect(self._confirm)
        buttons.addWidget(cancel); buttons.addWidget(self.confirm); layout.addLayout(buttons)
        self.total.valueChanged.connect(self._refresh_summary); self.old.valueChanged.connect(self._refresh_summary)
        self.today_only.toggled.connect(self._refresh_summary); self._refresh_summary()

    def _refresh_summary(self):
        self.old.setMaximum(self.total.value())
        prefix = "本次最多追加" if self.append else "今天最多" if self.today_only.isChecked() else "每天最多"
        self.summary.setText(f"{prefix}{self.total.value()}词，其中旧词最多{self.old.value()}词。\n不足时按实际数量安排，不强行补满。")
        self.confirm.setEnabled(self.total.value() > 0)

    def _confirm(self):
        self.total.interpretText(); self.old.interpretText()
        if self.total.value() > 0 and self.old.value() <= self.total.value(): self.accept()
