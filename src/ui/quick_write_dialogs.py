"""Small copy panel and editor for user-owned writing prompts."""

from __future__ import annotations

import sqlite3
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.services.quick_write import (
    FILENAME_TOKEN, copy_text, load_quick_write, normalized_filename, save_quick_write,
)


class QuickWritePanel(QDialog):
    def __init__(self, repository, parent=None):
        super().__init__(parent, Qt.Popup)
        self.repository = repository
        self.config = load_quick_write(repository)
        self.manage_requested = False
        self.setObjectName("quickWritePanel")
        self.setFixedWidth(370)
        self.setStyleSheet("""
            QDialog#quickWritePanel { background:#ffffff; border:1px solid #d7e3ef; border-radius:12px; }
            QLabel#quickTitle { font-size:18px; font-weight:700; color:#102f50; }
            QLabel#quickHint { color:#667b91; font-size:12px; }
            QFrame#stepRow { background:#ffffff; border:1px solid #e3ebf4; border-radius:8px; }
            QLabel#stepNumber { color:#176dbf; background:#edf5ff; border-radius:11px;
                                min-width:22px; min-height:22px; max-width:22px; max-height:22px;
                                font-size:12px; font-weight:600; }
            QLabel#stepName { font-size:14px; font-weight:500; color:#183b5d; }
            QPushButton#copyStep { color:#075cab; background:#f0f7ff; border:1px solid #c6def5;
                                   border-radius:6px; padding:5px 10px; font-size:13px; }
            QPushButton#copyStep:hover { background:#dcecff; border-color:#9fc9ef; }
            QFrame#filenameField { background:white; border:1px solid #cddfeb; border-radius:7px; }
            QLineEdit#filenameInput { background:transparent; border:0; padding:6px 4px; }
            QLabel#fileExtension { color:#667b91; font-size:13px; padding-right:5px; }
            QPushButton#manageSteps { border:0; color:#075cab; background:transparent;
                                      padding:4px; font-size:13px; }
            QPushButton#manageSteps:hover { text-decoration:underline; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 14, 15, 10)
        layout.setSpacing(8)
        title = QLabel("快速成文")
        title.setObjectName("quickTitle")
        layout.addWidget(title)
        hint = QLabel("按顺序复制到同一个 AI 对话")
        hint.setObjectName("quickHint")
        layout.addWidget(hint)

        rows = QWidget()
        rows.setStyleSheet("background:transparent;")
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 2, 0, 2)
        rows_layout.setSpacing(6)
        self.copy_buttons = []
        for index, step in enumerate(self.config["steps"]):
            row_widget = QFrame()
            row_widget.setObjectName("stepRow")
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(9, 6, 7, 6)
            row.setSpacing(9)
            number = QLabel(str(index + 1))
            number.setObjectName("stepNumber")
            number.setAlignment(Qt.AlignCenter)
            row.addWidget(number)
            name = QLabel(step["name"])
            name.setObjectName("stepName")
            name.setWordWrap(True)
            row.addWidget(name, 1)
            button = QPushButton("复制")
            button.setObjectName("copyStep")
            button.setAccessibleName(f"复制第 {index + 1} 步：{step['name']}")
            button.clicked.connect(lambda checked=False, i=index, b=button: self.copy_step(i, b))
            row.addWidget(button)
            rows_layout.addWidget(row_widget)
            self.copy_buttons.append(button)
        if len(self.config["steps"]) > 4:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setMaximumHeight(250)
            scroll.setStyleSheet("QScrollArea,QScrollArea > QWidget > QWidget{background:transparent;border:0;}")
            scroll.setWidget(rows)
            layout.addWidget(scroll)
        else:
            layout.addWidget(rows)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("background:#e6edf4;max-height:1px;border:0;")
        layout.addWidget(divider)
        filename_label = QLabel("输出文件名（可选）")
        layout.addWidget(filename_label)
        filename_field = QFrame()
        filename_field.setObjectName("filenameField")
        filename_row = QHBoxLayout(filename_field)
        filename_row.setContentsMargins(7, 0, 5, 0)
        filename_row.setSpacing(2)
        self.filename = QLineEdit(self.config["filename"])
        self.filename.setObjectName("filenameInput")
        self.filename.setPlaceholderText("留空则按文章标题命名")
        self.filename.setAccessibleName("输出文件名")
        self.filename.editingFinished.connect(self.save_filename)
        filename_row.addWidget(self.filename)
        extension = QLabel(".docx")
        extension.setObjectName("fileExtension")
        filename_row.addWidget(extension)
        layout.addWidget(filename_field)
        footer = QHBoxLayout()
        footer.addStretch()
        manage = QPushButton("管理提示词")
        manage.setObjectName("manageSteps")
        manage.clicked.connect(self.open_manager)
        footer.addWidget(manage)
        layout.addLayout(footer)

    def save_filename(self):
        try:
            value = normalized_filename(self.filename.text())
            if value != self.filename.text():
                self.filename.setText(value)
            if value == self.config["filename"]:
                return True
            updated = deepcopy(self.config)
            updated["filename"] = value
            save_quick_write(self.repository, updated)
            self.config = updated
            self.filename.setText(value)
            return True
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "无法保存文件名", str(exc))
            return False

    def copy_step(self, index, button=None):
        if not self.save_filename():
            return
        prompt = self.config["steps"][index]["prompt"]
        QApplication.clipboard().setText(copy_text(prompt, self.config["filename"]))
        if button is not None:
            button.setText("已复制")
            timer = QTimer(button)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: button.setText("复制"))
            timer.timeout.connect(timer.deleteLater)
            timer.start(1400)

    def open_manager(self):
        if self.save_filename():
            self.manage_requested = True
            self.accept()

    def closeEvent(self, event):
        if self.save_filename():
            super().closeEvent(event)
        else:
            event.ignore()

    def done(self, result):
        if self.save_filename():
            super().done(result)


class QuickWriteEditor(QDialog):
    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.config = load_quick_write(repository)
        self.steps = deepcopy(self.config["steps"])
        self.current_index = -1
        self.setWindowTitle("管理快速成文提示词")
        self.resize(900, 610)
        self.setMinimumSize(690, 430)
        self.setStyleSheet("""
            QDialog { background:#f5f7fb; color:#132a45; font-family:'Microsoft YaHei UI'; }
            QListWidget, QPlainTextEdit, QLineEdit { background:white; border:1px solid #cddceb;
                border-radius:8px; padding:7px; }
            QListWidget::item { padding:8px; }
            QListWidget::item:selected { background:#dcecff; color:#075cab; }
            QPushButton { background:white; border:1px solid #c6d7e7; border-radius:7px;
                padding:7px 11px; }
            QPushButton:hover { background:#eaf3ff; }
            QPushButton#savePrompts { background:#1473d1; color:white; border-color:#1473d1; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        heading = QLabel("管理快速成文步骤")
        heading.setStyleSheet("font-size:19px;font-weight:700;")
        layout.addWidget(heading)
        hint = QLabel(f"每步可独立复制；在提示词中使用 {FILENAME_TOKEN} 可引用面板里的输出文件名。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#617891;")
        layout.addWidget(hint)
        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        self.step_list = QListWidget()
        self.step_list.setMinimumWidth(190)
        self.step_list.setMaximumWidth(240)
        self.step_list.currentRowChanged.connect(self.select_step)
        left.addWidget(self.step_list, 1)
        for labels, slots in (
            (("新增步骤", "删除步骤"), (self.add_step, self.remove_step)),
            (("上移", "下移"), (lambda: self.move_step(-1), lambda: self.move_step(1))),
        ):
            row = QHBoxLayout()
            for label, slot in zip(labels, slots):
                button = QPushButton(label)
                button.clicked.connect(slot)
                row.addWidget(button)
            left.addLayout(row)
        body.addLayout(left)
        right = QVBoxLayout()
        right.addWidget(QLabel("步骤名称"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：生成故事并核验")
        self.name_edit.textChanged.connect(self.update_list_name)
        right.addWidget(self.name_edit)
        right.addWidget(QLabel("提示词内容"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("输入这一轮要复制给 AI 的完整提示词")
        self.prompt_edit.setTabChangesFocus(True)
        right.addWidget(self.prompt_edit, 1)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = QPushButton("保存")
        save.setObjectName("savePrompts")
        save.clicked.connect(self.save)
        footer.addWidget(save)
        layout.addLayout(footer)
        self.refresh_list(0)

    def store_current(self):
        if 0 <= self.current_index < len(self.steps):
            self.steps[self.current_index] = {
                "name": self.name_edit.text(),
                "prompt": self.prompt_edit.toPlainText(),
            }

    def select_step(self, row):
        self.store_current()
        self.current_index = row
        if 0 <= row < len(self.steps):
            self.name_edit.setText(self.steps[row]["name"])
            self.prompt_edit.setPlainText(self.steps[row]["prompt"])

    def update_list_name(self, name):
        if 0 <= self.current_index < self.step_list.count():
            self.step_list.item(self.current_index).setText(name or "（未命名）")

    def refresh_list(self, row):
        self.step_list.blockSignals(True)
        self.step_list.clear()
        self.step_list.addItems([step["name"] or "（未命名）" for step in self.steps])
        self.step_list.blockSignals(False)
        self.current_index = -1
        self.step_list.setCurrentRow(row)

    def add_step(self):
        self.store_current()
        index = self.current_index + 1
        self.steps.insert(index, {"name": f"新步骤 {len(self.steps) + 1}", "prompt": ""})
        self.refresh_list(index)
        self.name_edit.selectAll()

    def remove_step(self):
        if len(self.steps) == 1:
            QMessageBox.information(self, "保留一步", "至少保留一个步骤。")
            return
        if QMessageBox.question(self, "删除步骤", "确定删除当前步骤及其提示词吗？") != QMessageBox.Yes:
            return
        index = self.current_index
        self.steps.pop(index)
        self.refresh_list(min(index, len(self.steps) - 1))

    def move_step(self, direction):
        self.store_current()
        index = self.current_index
        target = index + direction
        if not (0 <= target < len(self.steps)):
            return
        self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
        self.refresh_list(target)

    def save(self):
        self.store_current()
        if any(not step["name"].strip() or not step["prompt"].strip() for step in self.steps):
            QMessageBox.warning(self, "请补全步骤", "每个步骤都需要名称和提示词内容。")
            return
        updated = deepcopy(self.config)
        updated["steps"] = [
            {"name": step["name"].strip(), "prompt": step["prompt"].strip()}
            for step in self.steps
        ]
        try:
            save_quick_write(self.repository, updated)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()
