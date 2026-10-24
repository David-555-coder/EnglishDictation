from __future__ import annotations
import logging
from datetime import date, timedelta
from src.services.daily_review import due_label
from src.ui.review_table import ReviewTable

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox,
                               QPushButton, QTableWidgetItem, QVBoxLayout, QLineEdit)


def recent_error_label(value: str, today: date) -> str:
    error_day = date.fromisoformat(value[:10])
    if error_day == today:
        return "今天"
    if error_day == today - timedelta(days=1):
        return "昨天"
    return error_day.strftime("%m-%d" if error_day.year == today.year else "%Y-%m-%d")


def restore_sort_preference(dialog, key: str) -> None:
    dialog.sort_setting_key = key
    saved = dialog.repository.get_setting(key)
    index = dialog.sort.findData(saved) if isinstance(saved, str) else -1
    if index >= 0:
        dialog.sort.setCurrentIndex(index)


def save_sort_preference(dialog) -> None:
    try:
        dialog.repository.set_setting(dialog.sort_setting_key, dialog.sort.currentData())
    except Exception:
        logging.exception("保存排序偏好失败")
        QMessageBox.warning(dialog, "排序未保存", "本次排序仍可使用，但未能保存。下次打开可能恢复为原来的排序。")
    dialog.reload()


def confirm_clear_wrong_words(repository, parent) -> bool:
    if QMessageBox.warning(parent, "清空错词本",
            "确定永久清空全部错词和例句吗？此操作不能撤销。\n\n"
            "学习历史和原 Word 不会删除。建议先从“导出”保存学习备份。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
        return False
    try:
        repository.clear_wrong_words()
        return True
    except Exception:
        logging.exception("清空错词本失败")
        QMessageBox.warning(parent, "清空失败", "错词本未能清空，请稍后重试。")
        return False


class WrongPracticeHistoryDialog(QDialog):
    practiceRequested = Signal(int)

    def __init__(self, repository, parent=None, wrong_only=True):
        super().__init__(parent)
        self.repository = repository
        self.wrong_only = wrong_only
        self.setWindowTitle("历史错词练习" if wrong_only else "全部学习历史")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        title = QLabel("选择记录，双击或点击“开始练习”" if wrong_only else "最近 500 次提交记录")
        title.setToolTip("选择一次有错词的练习，双击或点击“开始练习”" if wrong_only
                         else "最近 500 次提交记录；包括全部答对的练习")
        title.setObjectName("dialogTitle")
        title.setWordWrap(True)
        self.result_count = title
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        title_row.addWidget(QLabel("排序："))
        self.sort = QComboBox()
        self.sort.addItem("完成时间（最新优先）", "completed")
        self.sort.addItem("文章顺序（字母数字）", "article")
        restore_sort_preference(self, "wrong_history_sort" if wrong_only else "all_history_sort")
        self.sort.currentIndexChanged.connect(lambda *_: save_sort_preference(self))
        title_row.addWidget(self.sort)
        layout.addLayout(title_row)
        self.table = ReviewTable(6, parent=self)
        self.table.set_review_headers(["完成时间", "文章", "类型", "总题数", "错题", "正确率"], {1})
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        # Keep short article names near their other fields instead of stretching
        # the article column across all remaining space. Columns remain resizable.
        for column, width in ((1, 210), (2, 138), (3, 70), (4, 60), (5, 84)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.table.doubleClicked.connect(self._start)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.delete_button = QPushButton("删除选中记录")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setAutoDefault(False)
        self.delete_button.clicked.connect(self._delete_selected)
        row.addWidget(self.delete_button)
        row.addStretch()
        self.start_button = QPushButton("开始练习")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setAutoDefault(False)
        self.start_button.clicked.connect(self._start)
        close = QPushButton("关闭")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        row.addWidget(self.start_button)
        row.addWidget(close)
        layout.addLayout(row)
        delete_action = QAction(self)
        delete_action.setShortcut(QKeySequence.Delete)
        delete_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        delete_action.triggered.connect(self._delete_selected)
        self.table.addAction(delete_action)
        self.reload()

    def reload(self):
        order_by = self.sort.currentData() or "completed"
        records = self.repository.list_wrong_practice_history(order_by=order_by) if self.wrong_only else self.repository.list_history()
        if not self.wrong_only and order_by == "article":
            from src.utils.text_utils import natural_sort_key
            records.sort(key=lambda record: natural_sort_key(record["article_name"]))
        self.result_count.setText((f'共 {len(records)} 条记录 · 双击或点击“开始练习”' if self.wrong_only
                                   else f'最近 {len(records)} 条提交记录') if records else
                                  ('暂无有错词的练习记录' if self.wrong_only else '暂无学习记录'))
        today = self.repository.today()
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [recent_error_label(record["submitted_at"], today), record["article_name"],
                      record["practice_type"], str(record["target_count"]), str(record["wrong_count"]),
                      f"{record['accuracy']:.1f}%"]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, record["id"])
                cell.setToolTip(record["submitted_at"].replace("T", " ") if col == 0 else value)
                cell.setTextAlignment(self.table.column_alignment(col))
                self.table.setItem(row, col, cell)
        self._update_buttons()

    def _selected_rows(self):
        return sorted(index.row() for index in self.table.selectionModel().selectedRows(0))

    def _selected_ids(self):
        return [int(self.table.item(row, 0).data(Qt.UserRole)) for row in self._selected_rows()
                if self.table.item(row, 0)]

    def _update_buttons(self):
        count = len(self._selected_rows())
        self.start_button.setEnabled(count == 1)
        self.delete_button.setEnabled(count > 0)
        self.delete_button.setText(f"删除选中的 {count} 条记录" if count > 1 else "删除选中记录")

    def _selected_id(self):
        ids = self._selected_ids()
        return ids[0] if len(ids) == 1 else None

    def _start(self, *_):
        session_id = self._selected_id()
        if session_id is None:
            QMessageBox.information(self, "请选择记录", "请先选择一条练习记录。")
            return
        self.practiceRequested.emit(int(session_id))
        self.accept()

    def _delete_selected(self):
        session_ids = self._selected_ids()
        if not session_ids:
            QMessageBox.information(self, "请选择记录", "请先选择要删除的练习记录。")
            return
        if QMessageBox.question(self, "删除练习记录",
                f"确定删除选中的 {len(session_ids)} 条练习历史及其答题明细吗？\n\n长期错词本不会受到影响，此操作不能撤销。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.repository.delete_practice_sessions(session_ids)
            self.reload()
        except Exception:
            logging.exception("删除学习历史失败"); QMessageBox.warning(self, "删除失败", "记录未能删除，请稍后重试。")

    def _show_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if index.isValid():
            selected_rows = set(self._selected_rows())
            if index.row() not in selected_rows:
                self.table.clearSelection()
                self.table.selectRow(index.row())
            else:
                self.table.setCurrentCell(index.row(), 0, QItemSelectionModel.NoUpdate)
        count = len(self._selected_rows())
        from src.ui.widgets.text_menus import style_menu
        menu = style_menu(QMenu(self))
        start = menu.addAction("练习这次错词")
        start.setEnabled(count == 1)
        delete = menu.addAction(f"删除选中的 {count} 条记录" if count > 1 else "删除这条记录")
        delete.setEnabled(count > 0)
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is start:
            self._start()
        elif chosen is delete:
            self._delete_selected()
        menu.deleteLater()


class WrongWordsDialog(QDialog):
    practiceRequested = Signal(list)

    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.setWindowTitle("长期错词本")
        self.resize(950, 590)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("排序："))
        self.sort = QComboBox()
        self.sort.addItem("字母顺序", "alphabet")
        self.sort.addItem("错误次数", "errors")
        self.sort.addItem("最近错误", "recent")
        restore_sort_preference(self, "wrong_words_sort")
        self.sort.currentIndexChanged.connect(lambda *_: save_sort_preference(self))
        top.addWidget(self.sort)
        from src.ui.widgets.text_menus import TextLineEdit
        self.search = TextLineEdit(); self.search.setPlaceholderText("搜索英文或中文")
        self.search.setMaximumWidth(360)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search)
        top.addStretch()
        self.result_count = QLabel()
        self.result_count.setObjectName('resultCount')
        top.addWidget(self.result_count)
        layout.addLayout(top)
        self.table = ReviewTable(7, wrapped_columns=(0, 1), parent=self)
        self.table.set_review_headers(["英文", "中文", "错误", "最近错误", "来源", "记忆状态", "复习安排"], {0, 1, 4})
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(45)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in ((2, 54), (3, 84), (4, 110), (5, 102), (6, 112)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        self.delete_button = QPushButton("删除选中错词")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setAutoDefault(False)
        self.delete_button.clicked.connect(self._delete)
        self.start_button = QPushButton("开始练习")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setAutoDefault(False)
        self.start_button.clicked.connect(self._practice_selected)
        close = QPushButton("关闭")
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        buttons.addWidget(self.delete_button); buttons.addStretch()
        buttons.addWidget(self.start_button); buttons.addWidget(close)
        layout.addLayout(buttons)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.reload()

    def reload(self):
        records = self.repository.list_wrong_words(self.sort.currentData() or "alphabet")
        today = self.repository.today()
        today_iso = today.isoformat()
        planned = self.repository.pending_today_review_ids(today)
        selections = self.repository.review_selection_details(today)
        self._search_texts = [(r["answer"] + r["chinese"]).casefold() for r in records]
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(records))
            for row, record in enumerate(records):
                values = [record["answer"], record["chinese"], record["error_count"],
                          recent_error_label(record["last_error_at"], today), record["sources"],
                          ("等待重新学习" if not record["review_active"] else "需要巩固" if record["review_relearning"] or not record["fsrs_model"] else "按计划复习"),
                          ("分批加入" if not record["review_active"] and record["id"] not in planned else
                           due_label(record["review_due_date"], today, record["id"] in planned))]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    tooltip = str(value) if col != 6 else (record["review_due_date"] or "待安排")
                    if col == 3:
                        tooltip = record["last_error_at"].replace("T", " ")
                    if col == 5:
                        tooltip = ("FSRS-7：已记录真实回忆；当日纠错单独记录，不等于长期掌握。"
                                   if record["fsrs_model"] else "旧记录保留；等下一次真实回忆后逐步建立 FSRS-7 记忆状态。")
                        if record["review_relearning"]:
                            tooltip += "\n需要后续独立回忆确认。"
                        if record["fsrs_due_at"]:
                            tooltip += "\n模型建议时间：" + record["fsrs_due_at"]
                        tooltip += ("\n等待分批重新学习；旧记忆记录和原安排仍然保留。"
                                    if not record["review_active"] else
                                    "\n已在持续复习中；近期错词不会因刚练过而被排到旧词后面。")
                    if col == 6 and record["review_due_date"] and record["review_due_date"] <= today_iso:
                        due = date.fromisoformat(record["review_due_date"])
                        tooltip = f"原定复习：{due.isoformat()}"
                        if due < today:
                            tooltip += f"\n已等待 {(today - due).days} 天"
                        tooltip += ("\n已列入今日任务。" if record["id"] in planned
                                    else "\n仍在队列中，等待后续安排；原定日期不变。")
                    item.setToolTip(tooltip)
                    if col == 6 and record['id'] in selections:
                        selected = selections[record['id']]
                        if selected['policy_version']:
                            why = '按原计划返回' if selected['selection_reason'] == 'scheduled_return' else '分批加入旧词'
                            item.setToolTip(tooltip + f"\n本次原因：{why}\n选入时间：{selected['selected_at']}\n排队规则：{selected['policy_version']}")
                    item.setData(Qt.UserRole, record["id"])
                    item.setTextAlignment(self.table.column_alignment(col))
                    self.table.setItem(row, col, item)
            self.table.refresh_row_heights()
            self._filter()
        finally:
            self.table.setUpdatesEnabled(True)

    def _filter(self, *_):
        if not hasattr(self, "table"): return
        query = self.search.text().casefold().strip()
        visible = 0
        for row, text in enumerate(getattr(self, "_search_texts", [])):
            hidden = query not in text
            visible += not hidden
            if self.table.isRowHidden(row) != hidden:
                self.table.setRowHidden(row, hidden)
        total = self.table.rowCount()
        self.result_count.setText('暂无错词' if not total else '没有匹配的词' if not visible
                                  else f'找到 {visible} / {total} 词' if query else f'共 {total} 词')
        self._update_buttons()

    def _selected_rows(self):
        return sorted(index.row() for index in self.table.selectionModel().selectedRows(0)
                      if not self.table.isRowHidden(index.row()))

    def _update_buttons(self):
        count = len(self._selected_rows())
        self.start_button.setEnabled(count > 0)
        self.delete_button.setEnabled(count > 0)
        self.start_button.setText(f"开始练习（{count}词）" if count else "开始练习")
        self.delete_button.setText(f"删除选中的 {count} 个错词" if count > 1 else "删除选中错词")

    def _practice_selected(self):
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "请选择错词", "选中要练习的词，可按 Ctrl 或 Shift 多选。"); return
        self.practiceRequested.emit([self.table.item(row, 0).data(Qt.UserRole) for row in rows]); self.accept()

    def _selected_id(self):
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.UserRole) if row >= 0 and self.table.item(row, 0) else None

    def _delete(self):
        rows = self._selected_rows()
        if not rows: return
        if QMessageBox.question(self, "删除错词", f"确定删除选中的 {len(rows)} 个错词及例句吗？学习历史不会删除。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        try:
            for row in rows: self.repository.delete_wrong_word(int(self.table.item(row, 0).data(Qt.UserRole)))
            self.reload()
        except Exception:
            logging.exception("删除错词失败"); QMessageBox.warning(self, "删除失败", "部分错词可能未删除，请刷新后重试。")
