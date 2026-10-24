from __future__ import annotations
import json

import logging
from shiboken6 import isValid
from html import escape
import re
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette, QShortcut, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QProgressDialog, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QSplitter, QToolBar, QToolButton, QVBoxLayout, QWidget, QLineEdit,
                               QDialog, QInputDialog)

from src.models.practice import PracticeMode, PracticeSession
from src.models.article import ArticleSummary
from src.models.rules import RecognitionRules
from src.services.library import ArticleLibrary, backup_library
from src.services.backup_restore import prepare_library_restore
from src.services.grading import grade_session
from src.services.practice_service import (group_items_by_sentence, new_full_session,
                                            new_saved_wrong_session, new_wrong_session, new_long_term_session)
from src.ui.editing_dialogs import RulesDialog, TargetEditorDialog
from src.ui.import_worker import ImportWorker
from src.ui.lifecycle import owned_dialog
from src.ui.daily_review_dialog import ReviewQuantityDialog
from src.ui.shortcut_help import ShortcutHelpDialog
from src.ui.dialogs import WrongPracticeHistoryDialog, WrongWordsDialog, confirm_clear_wrong_words
from src.ui.unfinished_dialog import UnfinishedPracticeDialog
from src.ui.quick_write_dialogs import QuickWriteEditor, QuickWritePanel
from src.ui.widgets.answer_line_edit import AnswerLineEdit
from src.ui.widgets.article_list import ArticleList
from src.ui.widgets.flow_layout import FlowLayout
from src.ui.widgets.toolbar_focus import ToolbarFocus
from src.ui.widgets.text_menus import TextLabel, TextLineEdit, bind_text_menus
from src.ui.widgets.reading_selection import ReadingSelection
from src.ui.typography import is_article_heading, reading_html
from src.utils.text_utils import natural_sort_key

LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.repository.enable_review_continuity()
        self.library = ArticleLibrary(repository)
        self._import_worker = None
        self.pending_library_restore = None
        self._closed_for_restore = False
        self._restoring = False
        self._drafts = {}
        self._saved_draft_states = {}
        self._wrong_history_available = None
        self._answers_visible = False
        self._focus_mode = False
        self._focus_layout = None
        self._preview_session_key = None
        self._review_home_visible = False
        self._wrong_reading = False
        self._wrong_reading_scroll = 0
        self._result_page_cache = None
        self._daily_progress_detail = ""
        self._last_article_path = repository.get_setting("last_article_path", "")
        self._content_generation = 0
        self._reading_translation = None
        self._last_focused_answer = None
        self._layout_anchor = None
        self._pending_follow = None
        self._pending_view_restore = None
        self._view_restore_revision = 0
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.timeout.connect(self._flush_pending_follow)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._restore_layout_anchor)
        self._paragraph_items = {}
        self._pending_article_path = None
        self._article_switch_delay = 130
        self._article_switch = QTimer(self)
        self._article_switch.setSingleShot(True)
        self._article_switch.timeout.connect(self._flush_article_selection)
        try:
            self._drafts = {s.session_key: s for s in repository.load_drafts()}
        except Exception:
            LOGGER.exception("读取草稿失败，原记录已保留")
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(600)
        self._autosave.timeout.connect(self._save_active_draft)
        self._view_save = QTimer(self)
        self._view_save.setSingleShot(True)
        self._view_save.setInterval(3000)
        self._view_save.timeout.connect(self._save_active_draft)
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(50)
        self._zoom_timer.timeout.connect(self._apply_zoom_to_content)
        self.articles = []
        self.current_article = None
        self.session = None
        self.pending_review_session = None
        self.answer_edits: dict[str, AnswerLineEdit] = {}
        self.answer_hints: dict[str, QLabel] = {}
        try:
            self.zoom_percent = max(70, min(180, int(repository.get_setting("zoom", 100))))
        except (ValueError, TypeError, OverflowError):
            self.zoom_percent = 100
        self._completion_notified = False
        self.setWindowTitle("英文文章智能默写")
        self.resize(1480, 900)
        self.setMinimumSize(980, 650)
        self._build_toolbar()
        self._build_body()
        self._build_extra_menu()
        self._toolbar_focus = ToolbarFocus(self.main_toolbar, self)
        self.main_toolbar.setVisible(bool(repository.get_setting("toolbar_visible", True)))
        self._apply_style()
        bind_text_menus(self)
        self.statusBar().showMessage("准备就绪：先导入 Word 文章")
        self._update_actions()
        self._refresh_daily_review()
        self._daily_timer = QTimer(self)
        self._daily_timer.setInterval(60000)
        self._daily_timer.timeout.connect(self._refresh_daily_review)
        self._daily_timer.start()
        QTimer.singleShot(120, self._restore_article_library)

    def _build_extra_menu(self):
        menu = self.menuBar().addMenu("文章与设置")
        for name, callback in (("本篇识别规则", self.edit_rules), ("默认识别规则", self.edit_default_rules),
                               ("编辑学习内容", self.edit_targets), ("每日复习数量", self.configure_review_limit)):
            action = menu.addAction(name); action.triggered.connect(callback); self.actions[name] = action
        menu.addSeparator()
        self.data_management_menu = menu.addMenu("数据管理")
        clear = self.data_management_menu.addAction("清空错词本…")
        clear.triggered.connect(self.clear_wrong_book)
        self.actions["清空错词本…"] = clear
        practice_menu = self.menuBar().addMenu("练习")
        self.practice_menu = practice_menu
        practice_menu.addAction(self.actions["开始默写"])
        practice_menu.addAction(self.actions["提交批改"])
        practice_menu.addSeparator()
        unfinished = practice_menu.addAction("未完成练习…")
        unfinished.triggered.connect(self.resume_draft)
        self.actions["未完成练习…"] = unfinished

        self.view_menu = self.menuBar().addMenu("视图")
        focus = self.view_menu.addAction("专注模式")
        focus.setCheckable(True)
        focus.setShortcut(QKeySequence("F8"))
        focus.triggered.connect(self._set_focus_mode)
        self.actions["专注模式"] = focus
        self.view_menu.addSeparator()
        toolbar_action = self.main_toolbar.toggleViewAction()
        toolbar_action.setText("显示主工具栏")
        self.view_menu.addAction(toolbar_action)
        self.actions["显示主工具栏"] = toolbar_action
        self.main_toolbar.visibilityChanged.connect(self._toolbar_visibility_changed)
        library_action = self.view_menu.addAction("显示文章列表")
        library_action.setCheckable(True)
        library_action.setChecked(not self.library_collapsed)
        library_action.triggered.connect(lambda visible: self._set_library_collapsed(not visible))
        self.actions["显示文章列表"] = library_action

        help_menu = self.menuBar().addMenu("帮助")
        shortcuts = help_menu.addAction("快捷键说明")
        shortcuts.setShortcut(QKeySequence("F1"))
        shortcuts.triggered.connect(self.show_shortcut_help)
        self.addAction(shortcuts)
        self.actions["快捷键说明"] = shortcuts

    def show_shortcut_help(self):
        with owned_dialog(ShortcutHelpDialog(self)) as dialog:
            dialog.exec()

    def _build_toolbar(self):
        toolbar = QToolBar("主要操作", self)
        self.main_toolbar = toolbar
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        toolbar.setObjectName("mainToolbar")
        self.addToolBar(toolbar)
        leading = QWidget(); leading.setFixedWidth(8); toolbar.addWidget(leading)
        self.actions = {}

        def add_action(text, slot, shortcut=None, target=toolbar):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            target.addAction(action)
            self.actions[text] = action
            return action

        def add_menu_button(text, items):
            button = QToolButton(toolbar)
            button.setText(text)
            button.setObjectName("toolbarMenuButton")
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setPopupMode(QToolButton.InstantPopup)
            menu = QMenu(button)
            for name, slot, shortcut in items:
                if name is None:
                    menu.addSeparator()
                    continue
                action = add_action(name, slot, shortcut, menu)
                if shortcut:
                    self.addAction(action)
            button.setMenu(menu)
            toolbar.addWidget(button)
            return button

        self.import_button = add_menu_button("导入", (
            ("导入文章", self.import_files, "Ctrl+O"),
            ("导入文件夹", self.import_folder, None),
            (None, None, None),
            ("恢复学习备份…", self.restore_data, None),
        ))
        self.export_button = add_menu_button("导出", (
            ("导出默写纸", self.export_current_worksheet, None),
            ("导出答案版", self.export_current_answer, None),
            (None, None, None),
            ("导出学习备份…", self.backup_data, None),
        ))
        toolbar.addSeparator()
        add_action("原文", lambda: self.show_preview(explicit=True), "Ctrl+1")
        add_action("显示答案", self.show_answers)
        toolbar.addSeparator()
        add_action("历史错词练习", self.show_history, "Ctrl+R")
        add_action("错词本", self.show_wrong_words)
        add_action("今日复习", self.start_daily_review)
        toolbar.widgetForAction(self.actions["今日复习"]).setObjectName("dailyReviewButton")
        self.daily_review_button = toolbar.widgetForAction(self.actions["今日复习"])
        self.daily_review_button.setPopupMode(QToolButton.MenuButtonPopup)
        daily_menu = QMenu(self.daily_review_button)
        daily_menu.addAction("复习数量设置…", self.configure_review_limit)
        daily_menu.addAction("今天再加一些…", self.append_daily_review)
        daily_menu.addAction("小组再练提醒…", self.configure_consolidation)
        self.daily_review_button.setMenu(daily_menu)
        toolbar.addSeparator()
        add_action("快速成文", self.show_quick_write)
        toolbar.widgetForAction(self.actions["快速成文"]).setObjectName("quickWriteButton")
        self.more_button = QToolButton(toolbar)
        self.more_button.setText("更多")
        self.more_button.setObjectName("toolbarMenuButton")
        self.more_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self.more_button)
        self._compact_toolbar_names = ("历史错词练习", "错词本", "快速成文")
        self._more_toolbar_entries = {}
        for name in self._compact_toolbar_names:
            original = self.actions[name]
            entry = more_menu.addAction(name)
            entry.setEnabled(original.isEnabled())
            entry.triggered.connect(lambda checked=False, action=original: action.trigger())
            self._more_toolbar_entries[name] = entry
        self.more_button.setMenu(more_menu)
        self._more_toolbar_action = toolbar.addWidget(self.more_button)
        self._more_toolbar_action.setVisible(False)
        self._compact_toolbar = False
        self._compact_history_shortcut = QShortcut(QKeySequence("Ctrl+R"), self, activated=self.show_history)
        self._compact_history_shortcut.setEnabled(False)
        # Keep the two core shortcuts and menu entries without occupying toolbar space.
        for text, slot, shortcut in (("开始默写", self.start_full_practice, "Ctrl+2"),
                                     ("提交批改", self.submit, "Ctrl+Enter")):
            action = QAction(text, self)
            action.triggered.connect(slot)
            action.setShortcut(QKeySequence(shortcut))
            self.addAction(action)
            self.actions[text] = action
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setStyleSheet("background:transparent;")
        toolbar.addWidget(spacer)
        self.zoom_panel = QFrame()
        self.zoom_panel.setObjectName("zoomPanel")
        zoom_layout = QHBoxLayout(self.zoom_panel)
        zoom_layout.setContentsMargins(10, 0, 10, 0); zoom_layout.setSpacing(0)
        zoom_layout.addWidget(QLabel("显示："))
        zoom_out = QToolButton(); zoom_out.setText("−"); zoom_out.setFixedWidth(26)
        zoom_out.setToolTip("缩小文章（Ctrl+−）")
        zoom_out.clicked.connect(lambda: self.set_zoom(self.zoom_percent - 10))
        zoom_layout.addWidget(zoom_out)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        zoom_layout.addWidget(self.zoom_label, 1)
        zoom_in = QToolButton(); zoom_in.setText("＋"); zoom_in.setFixedWidth(26)
        zoom_in.setToolTip("放大文章（Ctrl++）")
        zoom_in.clicked.connect(lambda: self.set_zoom(self.zoom_percent + 10))
        zoom_layout.addWidget(zoom_in)
        self.zoom_panel.setFixedWidth(190)
        toolbar.addWidget(self.zoom_panel)
        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self.set_zoom(self.zoom_percent + 10))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.set_zoom(self.zoom_percent - 10))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.set_zoom(100))

    def show_quick_write(self):
        trigger = self.main_toolbar.widgetForAction(self.actions["快速成文"])
        if trigger is None or not trigger.isVisible():
            trigger = self.more_button
        while True:
            with owned_dialog(QuickWritePanel(self.repository, self)) as panel:
                panel.adjustSize()
                point = trigger.mapToGlobal(QPoint(0, trigger.height()))
                screen = QApplication.screenAt(point) or QApplication.primaryScreen()
                if screen:
                    area = screen.availableGeometry()
                    x = min(max(point.x(), area.left() + 8), area.right() - panel.width() - 8)
                    y = point.y()
                    if y + panel.height() > area.bottom():
                        y = trigger.mapToGlobal(QPoint(0, 0)).y() - panel.height()
                    panel.move(x, max(area.top() + 8, y))
                else:
                    panel.move(point)
                panel.exec()
                open_manager = panel.manage_requested
            if not open_manager:
                break
            with owned_dialog(QuickWriteEditor(self.repository, self)) as editor:
                if editor.exec() != QDialog.Accepted:
                    break

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_more_toolbar_action"):
            self._set_toolbar_compact(self.width() < 1280)

    def _set_toolbar_compact(self, compact):
        if compact == self._compact_toolbar:
            return
        self._compact_toolbar = compact
        for name in self._compact_toolbar_names:
            self.actions[name].setVisible(not compact)
        self._more_toolbar_action.setVisible(compact)
        self._compact_history_shortcut.setEnabled(compact)

    def _build_body(self):
        splitter = QSplitter(Qt.Horizontal)
        self.splitter = splitter
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        left = QFrame(); left.setObjectName("sidePanel")
        self.article_panel = left
        left_layout = QVBoxLayout(left); left_layout.setContentsMargins(16, 18, 16, 16)
        heading = QLabel("文章列表"); heading.setObjectName("panelTitle"); left_layout.addWidget(heading)
        self.article_summary = QLabel("文章：0 篇\n总重点词：0"); self.article_summary.setObjectName("muted")
        left_layout.addWidget(self.article_summary)
        self.article_search = TextLineEdit()
        self.article_search.setObjectName('articleSearch')
        self.article_search.setPlaceholderText("搜索文章名称…")
        self.article_search.textChanged.connect(self._filter_articles)
        left_layout.addWidget(self.article_search)
        self.article_list = ArticleList()
        self.article_list.setObjectName("articleList")
        self.article_list.currentRowChanged.connect(self._schedule_article_selection)
        self.article_list.itemSelectionChanged.connect(self._schedule_article_selection)
        self.article_list.itemClicked.connect(self._article_list_clicked)
        self.article_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.article_list.customContextMenuRequested.connect(self._article_context_menu)
        left_layout.addWidget(self.article_list, 1)
        self.remove_article_button = QPushButton("从列表移除")
        self.remove_article_button.setObjectName("secondaryButton")
        self.remove_article_button.clicked.connect(self.remove_selected_article)
        left_layout.addWidget(self.remove_article_button)
        delete_shortcut = QAction(self.article_list)
        delete_shortcut.setShortcut(QKeySequence.Delete)
        delete_shortcut.setShortcutContext(Qt.WidgetShortcut)
        delete_shortcut.triggered.connect(self.remove_selected_article)
        self.article_list.addAction(delete_shortcut)

        center = QWidget(); center_layout = QVBoxLayout(center); center_layout.setContentsMargins(16, 14, 6, 14)
        center_layout.setSpacing(6)
        self.mode_title = QLabel("欢迎使用英文文章智能默写")
        self.mode_title.setObjectName("contentTitle")
        center_layout.addWidget(self.mode_title)
        self.mode_detail = QLabel(); self.mode_detail.setObjectName("muted")
        self.mode_detail.setWordWrap(True); self.mode_detail.hide()
        center_layout.addWidget(self.mode_detail)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame)
        self.content, self.content_layout = self._empty_content_page()
        self.scroll.setWidget(self.content); self.scroll.viewport().installEventFilter(self)
        self.content.installEventFilter(self)
        self.content.setObjectName("articleContent")
        self.reading_selection = ReadingSelection(self.content, self.scroll, self._cancel_pending_follow)
        self.reading_selection.translation_handler = self._translate_reading_selection
        self.reading_selection.translation_allowed = self._can_translate_reading
        self.scroll.verticalScrollBar().valueChanged.connect(self._queue_view_save)
        self.scroll.verticalScrollBar().rangeChanged.connect(lambda *_: self._schedule_anchor_restore())
        self.scroll.verticalScrollBar().installEventFilter(self)
        self.scroll.verticalScrollBar().sliderPressed.connect(self._cancel_pending_follow)
        center_layout.addWidget(self.scroll, 1)

        right = QFrame(); right.setObjectName("statusPanel")
        self.status_panel = right
        right.installEventFilter(self)
        right.setMinimumWidth(180); right.setMaximumWidth(210)
        right_layout = QVBoxLayout(right); right_layout.setContentsMargins(10, 12, 10, 12)
        right_layout.setSpacing(7)
        label = QLabel("学习状态"); label.setObjectName("panelTitle"); right_layout.addWidget(label)
        self.progress_text = QLabel("请选择或导入文章")
        self.progress_text.setWordWrap(True); self.progress_text.setObjectName("statusCard")
        right_layout.addWidget(self.progress_text)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        self.progress.setFormat("填写进度 %p%")
        right_layout.addWidget(self.progress)
        self.score_text = QLabel("提交前不会显示正确率")
        self.score_text.setWordWrap(True); self.score_text.setObjectName("scoreCard")
        right_layout.addWidget(self.score_text)
        self.primary_action_button = QPushButton("开始默写")
        self.primary_action_button.setObjectName("primaryButton")
        self.primary_action_button.setMinimumHeight(42)
        self.primary_action_button.clicked.connect(self._run_primary_action)
        right_layout.addWidget(self.primary_action_button)
        self.read_wrong_button = QPushButton("阅读本轮错词")
        self.read_wrong_button.setObjectName("secondaryButton")
        self.read_wrong_button.clicked.connect(self.read_current_wrong)
        self.read_wrong_button.setToolTip("在主阅读区查看本轮错词的正确原句；可随时返回当前结果。")
        right_layout.addWidget(self.read_wrong_button)
        self.reading_back_button = QPushButton("返回本轮结果")
        self.reading_back_button.setObjectName("secondaryButton")
        self.reading_back_button.clicked.connect(self.return_from_wrong_reading)
        right_layout.addWidget(self.reading_back_button)
        self.return_result_button = QPushButton("返回原轮结果")
        self.return_result_button.setObjectName("secondaryButton")
        self.return_result_button.clicked.connect(self.return_to_consolidation_origin)
        self.return_result_button.setToolTip("返回加练前的错词和结果；原轮成绩保持原样。")
        right_layout.addWidget(self.return_result_button)
        hint = QLabel("Enter  下一词\nShift+Enter  上一词\nShift+Backspace  清空\nCtrl+滚轮  缩放\nF8  切换专注模式")
        self.shortcut_hint = hint
        hint.setToolTip("Enter：下一词\nShift+Enter：上一词\nShift+Backspace：清空当前输入\nCtrl+滚轮：缩放文章\nF8：切换专注模式")
        hint.setObjectName("hintCard"); right_layout.addWidget(hint)
        right_layout.addStretch()

        self.library_shell = QWidget()
        shell_layout = QVBoxLayout(self.library_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0); shell_layout.setSpacing(0)
        shell_layout.addWidget(left, 1)
        self.library_collapsed = False
        self._expanded_library_width = 260
        splitter.addWidget(self.library_shell); splitter.addWidget(center); splitter.addWidget(right)
        splitter.setSizes([260, 1020, 190])
        splitter.setStretchFactor(1, 1)
        splitter.splitterMoved.connect(lambda *_: self._sync_zoom_panel())
        self._sync_zoom_panel()
        if self.repository.get_setting("library_collapsed", False):
            self._set_library_collapsed(True)

    def _run_primary_action(self):
        if self.session is not None and self.session.submitted and not self.session.database_id:
            self.retry_save_results()
        elif self._wrong_reading:
            self._start_after_wrong_reading()
        elif self.pending_review_session is not None:
            self.start_full_practice()
        elif self.session is not None and not self.session.submitted:
            self.submit()
        elif self.session is not None and self.session.submitted and self.session.wrong_ids:
            self.retry_current_wrong()
        elif self._review_home_visible:
            self.start_daily_review()
        elif self._library_article() is not None:
            self.start_full_practice()

    def _sync_zoom_panel(self):
        if hasattr(self, "status_panel"):
            self.zoom_panel.setFixedWidth(self.status_panel.width())

    def _set_library_collapsed(self, collapsed):
        self._remember_layout_anchor()
        sizes = self.splitter.sizes()
        if collapsed and not self.library_collapsed:
            self._expanded_library_width = max(200, sizes[0])
        self.library_collapsed = collapsed
        self.article_panel.setVisible(not collapsed)
        self.library_shell.setVisible(not collapsed and not self._focus_mode)
        self.library_shell.setMinimumWidth(200)
        width = 0 if collapsed else self._expanded_library_width
        self.splitter.setSizes([width, max(200, sum(sizes) - width - sizes[2]), sizes[2]])
        if "显示文章列表" in self.actions:
            self.actions["显示文章列表"].setChecked(not collapsed)
        self._schedule_anchor_restore()

    def _toolbar_visibility_changed(self, _visible):
        # The captured anchor precedes user-triggered layout changes; do not steal focus.
        self._remember_layout_anchor()
        self._schedule_anchor_restore()

    def _set_focus_mode(self, enabled):
        if enabled == self._focus_mode:
            return
        self._remember_layout_anchor()
        if enabled:
            self._focus_layout = (not self.main_toolbar.isHidden(), self.library_collapsed,
                                  self.splitter.sizes(), self._expanded_library_width)
            self._focus_mode = True
            self.main_toolbar.hide()
            self.library_shell.hide()
        else:
            self._focus_mode = False
            toolbar_visible, collapsed, sizes, expanded_width = self._focus_layout
            self._expanded_library_width = expanded_width
            self._set_library_collapsed(collapsed)
            self.library_shell.setVisible(not collapsed)
            self.main_toolbar.setVisible(toolbar_visible)
            self.splitter.setSizes(sizes)
            self._focus_layout = None
        self.actions["专注模式"].setChecked(enabled)
        self.actions["显示主工具栏"].setEnabled(not enabled)
        self.actions["显示文章列表"].setEnabled(not enabled)
        self._schedule_anchor_restore()

    def _remember_layout_anchor(self):
        if (self._layout_anchor is not None or self._pending_view_restore is not None
                or not hasattr(self, "scroll")):
            return
        bar = self.scroll.verticalScrollBar()
        height = self.scroll.viewport().height()
        anchor = self.answer_edits.get(self._last_focused_answer)
        if anchor is not None:
            offset = anchor.mapTo(self.content, QPoint(0, 0)).y() - bar.value()
            if not 0 <= offset < height:
                anchor = None
        if anchor is None:
            widgets = []
            for index in range(self.content_layout.count()):
                widget = self.content_layout.itemAt(index).widget()
                if widget and widget.objectName() == 'originalPage':
                    widgets.extend(widget.findChildren(QFrame, 'readingParagraph'))
                elif widget:
                    widgets.append(widget)
            for widget in widgets:
                y = widget.mapTo(self.content, QPoint()).y()
                if not widget.isHidden() and y + widget.height() > bar.value():
                    anchor = widget
                    break
        offset = anchor.mapTo(self.content, QPoint(0, 0)).y() - bar.value() if anchor else 0
        self._layout_anchor = (self._content_generation, anchor, offset, bar.value())

    def _schedule_anchor_restore(self):
        # Repeated resize/layout notifications must not keep postponing the
        # already queued restore. One event-loop turn handles the latest state.
        if ((self._layout_anchor is not None or self._pending_view_restore is not None)
                and not self._layout_timer.isActive()):
            self._layout_timer.start(0)

    def _reading_layout_ready(self):
        # Newly inserted cards may still have their temporary minimum heights.
        # Neither a saved offset nor focus-follow is meaningful until the flow
        # height and the scroll range describe the same, fully laid out page.
        height = self.content_layout.totalHeightForWidth(self.content.width())
        return (self.content.height() >= height
                and self.scroll.verticalScrollBar().maximum() + self.scroll.viewport().height()
                >= self.content.height())

    def _restore_layout_anchor(self):
        pending = self._pending_view_restore
        if pending is not None:
            generation, key, desired = pending
            if (generation != self._content_generation or self.session is None
                    or key != self.session.session_key):
                self._pending_view_restore = None
                return
            bar = self.scroll.verticalScrollBar()
            self.layout().activate()
            self.splitter.widget(1).layout().activate()
            self.content_layout.activate()
            # A fresh page can still have the viewport-sized placeholder even
            # though its height-for-width is already known. Resize once to that
            # real height; QScrollArea updates its range on the resize event.
            # This avoids depending on another delayed layout notification.
            height = max(self.scroll.viewport().height(),
                         self.content_layout.totalHeightForWidth(self.content.width()))
            if self.content.height() != height:
                self.content.resize(self.content.width(), height)
                self.content_layout.activate()
            # A rebuilt long page initially has range 0..0. Retain the saved
            # intent until Qt computes its range instead of losing it at zero.
            # rangeChanged wakes us; there is no polling or fixed sleep.
            if not self._reading_layout_ready():
                return
            self._pending_view_restore = None
            self._layout_anchor = None
            bar.setValue(desired)
            if self._pending_follow is not None and not self._follow_timer.isActive():
                self._follow_timer.start(0)
            return
        state = self._layout_anchor
        if state is None or state[0] != self._content_generation:
            self._layout_anchor = None
            return
        # Finish the outer viewport resize and inner flow rewrap before measuring.
        # Keep the same anchor during resize events emitted by these layout passes.
        self.layout().activate()
        self.splitter.widget(1).layout().activate()
        self.content_layout.activate()
        _generation, anchor, offset, fallback = state
        value = anchor.mapTo(self.content, QPoint(0, 0)).y() - offset if anchor else fallback
        self.scroll.verticalScrollBar().setValue(value)
        self._layout_anchor = None
        self._layout_timer.stop()

    def _update_reading_margins(self):
        available = self.scroll.viewport().width()
        limit = round(1000 * self.zoom_percent / 100)
        extra = max(0, available - limit - 10)
        margins = (4 + extra // 2, 6, 6 + extra - extra // 2, 18)
        current = self.content_layout.contentsMargins()
        if margins != (current.left(), current.top(), current.right(), current.bottom()):
            self.content_layout.setContentsMargins(*margins)

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#f3f5f7; color:#24364b; font-family:'Microsoft YaHei UI'; font-size:15px; }
            QLabel { background:transparent; }
            QToolBar#mainToolbar { background:#ffffff; border:none; border-bottom:1px solid #e4e8ee; spacing:2px; padding:8px 0; }
            QFrame#zoomPanel { background:transparent; border-left:1px solid #e4e8ee; }
            QFrame#zoomPanel QToolButton { padding:7px 3px; }
            QToolBar#mainToolbar::separator { background:#e6eaf0; width:1px; margin:9px 7px; }
            QToolButton { background:transparent; border:1px solid transparent; border-radius:7px; padding:7px 9px; color:#405268; }
            QToolButton:hover { background:#eef2f7; color:#255d98; } QToolButton:pressed { background:#e3ebf5; }
            QToolButton[keyboardFocus='true']:focus { border-color:#86a8cf; }
            QToolButton#toolbarMenuButton { font-weight:500; }
            QToolButton#quickWriteButton { background:#f8fafc; color:#405b79; border-color:#e8edf3; }
            QToolButton#quickWriteButton:hover { background:#eef3f9; border-color:#d9e3ef; }
            QToolButton#dailyReviewButton { background:#eaf1fa; color:#245e9d; font-weight:600; }
            QToolButton#dailyReviewButton:hover { background:#e0ebf7; }
            QToolButton#dailyReviewButton::menu-button { border:none; background:transparent; width:16px; }
            QToolButton:disabled { color:#a9b5c3; }
            QFrame#sidePanel { background:#f9fafc; border-right:1px solid #e4e8ee; }
            QFrame#statusPanel { background:#fbfcfd; border-left:1px solid #e4e8ee; }
            QLabel#panelTitle { font-size:18px; font-weight:600; color:#30445c; }
            QLabel#contentTitle { font-size:16px; font-weight:600; color:#607187; padding:4px 4px 8px 4px; }
            QLabel#muted { color:#7b8798; font-size:13px; line-height:1.5; }
            QListWidget#articleList { border:none; background:transparent; outline:none; padding:0; }
            QListWidget#articleList::item { padding:10px 12px; margin:2px 0; border-radius:8px; }
            QListWidget#articleList::item:hover { background:#eef2f7; }
            QListWidget#articleList::item:selected { background:#e5edf8; color:#285d96; }
            QPushButton { border:1px solid #dce3eb; border-radius:8px; padding:8px 14px; background:#ffffff; }
            QPushButton:hover { border-color:#a5bdd8; background:#f4f7fb; } QPushButton:pressed { background:#e6eef8; }
            QPushButton#primaryButton { background:#2a6db5; color:white; border-color:#2a6db5; font-weight:600; }
            QPushButton#primaryButton:hover { background:#215f9f; }
            QPushButton:disabled, QPushButton#primaryButton:disabled, QPushButton#dangerButton:disabled { background:#f0f3f7; color:#8c9aad; border-color:#dce3ec; }
            QPushButton#dangerButton { color:#b42318; border-color:#f0b8b3; background:#fff8f7; }
            QLabel#statusCard, QLabel#scoreCard { background:#f1f5f9; border:1px solid #e5eaf0; border-radius:9px; padding:10px; }
            QLabel#hintCard { background:transparent; border:none; color:#71839a; font-size:13px; padding:8px 2px; }
            QLabel#resultCount { color:#71839a; font-size:13px; background:transparent; }
            QComboBox, QSpinBox { background:#ffffff; border:1px solid #dce3eb; border-radius:7px; padding:6px 10px; min-height:22px; }
            QComboBox:hover, QSpinBox:hover { border-color:#a5bdd8; }
            QComboBox:focus, QSpinBox:focus { border-color:#5f95ce; }
            QComboBox::drop-down { border:none; width:24px; }
            QComboBox::down-arrow { image:url("@down-arrow@"); width:12px; height:8px; }
            QComboBox { padding-right:26px; }
            QComboBox QAbstractItemView { background:white; border:1px solid #dce3eb; selection-background-color:#e5edf8; selection-color:#245e9d; padding:4px; outline:none; }
            QCheckBox { spacing:8px; padding:3px 0; }
            QProgressBar { border:1px solid #d4e0ec; border-radius:7px; background:#edf2f7; text-align:center; height:19px; }
            QProgressBar::chunk { background:#3b8ddd; border-radius:6px; }
            QScrollArea { background:#f3f5f7; } QScrollArea > QWidget > QWidget { background:#f3f5f7; }
            QFrame#originalPage { background:#ffffff; border:1px solid #e1e6ec; border-radius:12px; }
            QFrame#readingParagraph { background:transparent; border:none; }
            QScrollBar:vertical { background:transparent; width:10px; margin:2px 0; }
            QScrollBar::handle:vertical { background:#bdcad9; min-height:36px; border-radius:5px; }
            QScrollBar::handle:vertical:hover { background:#91a8c1; }
            QScrollBar::handle:vertical:pressed { background:#698bac; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; border:none; background:none; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:none; }
            QScrollBar:horizontal { background:transparent; height:10px; margin:0 2px; }
            QScrollBar::handle:horizontal { background:#bdcad9; min-width:36px; border-radius:5px; }
            QScrollBar::handle:horizontal:hover { background:#91a8c1; }
            QScrollBar::handle:horizontal:pressed { background:#698bac; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; border:none; background:none; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:none; }
            QLineEdit { border:2px solid #8dbbe7; border-radius:7px; background:white; padding:5px 7px; color:#143a5f; selection-background-color:#78afe3; }
            QLineEdit:focus { border-color:#1674d1; background:#fafdff; }
            QLineEdit#articleSearch { border:1px solid #dce3eb; border-radius:8px; background:#ffffff; padding:8px 10px; }
            QLineEdit#articleSearch:focus { border-color:#8eafd3; }
            QLineEdit[answerState='correct'] { border-color:#37a866; background:#f0fbf4; color:#19723c; }
            QLineEdit[answerState='wrong'] { border-color:#db4d4d; background:#fff3f2; color:#b42318; }
            QTableWidget { border:1px solid #d8e2ec; border-radius:8px; gridline-color:#e7edf3; background:white; alternate-background-color:#f7faff; }
            QHeaderView::section { background:#edf4fb; border:none; border-right:1px solid #d8e2ec; padding:9px; font-weight:600; }
            QMenu { background:white; border:1px solid #d4dfeb; border-radius:7px; padding:5px; }
            QMenu::item { padding:8px 28px 8px 12px; border-radius:5px; } QMenu::item:selected { background:#e5f1ff; color:#075cab; }
        """.replace('@down-arrow@', (Path(__file__).resolve().parents[2] / 'assets' / 'chevron-down.svg').as_posix()))

    def eventFilter(self, watched, event):
        if watched is getattr(self, "status_panel", None) and event.type() == QEvent.Resize:
            self._sync_zoom_panel()
        if isinstance(watched, AnswerLineEdit) and event.type() in (
                QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self._cancel_pending_follow()
        if watched is self.content and event.type() in (QEvent.Resize, QEvent.LayoutRequest):
            self._schedule_anchor_restore()
            if self._pending_follow is not None and not self._follow_timer.isActive():
                self._follow_timer.start(0)
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._remember_layout_anchor()
            self._update_reading_margins()
            self._schedule_anchor_restore()
        if watched is self.scroll.viewport() and event.type() in (QEvent.Wheel, QEvent.MouseButtonPress):
            self._cancel_pending_follow()
        if watched is self.scroll.verticalScrollBar() and event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.KeyPress):
            self._cancel_pending_follow()
        if watched is self.scroll.viewport() and event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom_percent = max(70, min(180, self.zoom_percent + (10 if delta > 0 else -10)))
                self.zoom_label.setText(f"{self.zoom_percent}%")
                self._zoom_timer.start()
            return True
        return super().eventFilter(watched, event)

    def set_zoom(self, value):
        self._zoom_timer.stop()
        self.zoom_percent = max(70, min(180, int(value)))
        self.zoom_label.setText(f"{self.zoom_percent}%")
        self._apply_zoom_to_content()

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择 Word 文章", "", "Word 文档 (*.docx *.doc)")
        if paths:
            self._start_import(paths)

    def import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含 Word 的文件夹")
        if folder:
            self._start_import([str(path) for path in Path(folder).iterdir()
                                if path.is_file() and path.suffix.casefold() in {".docx", ".doc"}
                                and not path.name.startswith("~$")])

    def _default_rules(self):
        try:
            return RecognitionRules(**self.repository.get_setting("rules", {}))
        except (ValueError, TypeError):
            LOGGER.warning("默认识别规则无效，使用内置规则")
            return RecognitionRules()

    def _start_import(self, paths):
        if self._import_worker and self._import_worker.isRunning(): return
        if not self._save_active_draft(): return
        known = {a.source_path for a in self.articles}
        paths = sorted({str(Path(p).resolve()) for p in paths} - known, key=lambda p: natural_sort_key(Path(p).name))
        if not paths:
            self.statusBar().showMessage("没有新的 Word 文件可导入。", 4000); return
        self._import_errors = []
        self._import_progress = QProgressDialog("正在导入并保存本地副本…", "取消", 0, len(paths), self)
        self._import_progress.setWindowModality(Qt.WindowModal)
        worker = ImportWorker(self.library, paths, self._default_rules(), self)
        self._import_worker = worker
        self._import_progress.canceled.connect(worker.cancel)
        worker.progressChanged.connect(self._on_import_progress)
        worker.articleReady.connect(self._on_article_ready)
        worker.failed.connect(self._on_import_error)
        worker.finished.connect(self._on_import_finished)
        worker.start()
        self._update_actions()

    def _on_import_progress(self, index, path):
        self._import_progress.setValue(index)
        if path: self._import_progress.setLabelText("正在导入：" + Path(path).name)

    def _on_article_ready(self, article):
        if not any(a.source_path == article.source_path for a in self.articles): self.articles.append(article)

    def _on_import_error(self, path, error):
        self._import_errors.append(Path(path).name + "：" + error)
        LOGGER.error("导入失败：%s (%s)", Path(path).name, error)

    def _on_import_finished(self):
        self._import_progress.close()
        self._import_progress.deleteLater()
        self._import_progress = None
        worker = self._import_worker
        self._import_worker = None
        if worker is not None:
            worker.deleteLater()
        self.articles.sort(key=lambda a: natural_sort_key(a.filename))
        self._reload_article_list()
        if (self.article_list.currentRow() < 0 and self.articles
                and self.session is None and self.pending_review_session is None):
            self.article_list.setCurrentRow(0)
        if self._import_errors: QMessageBox.warning(self, "部分文件未导入", "\n".join(self._import_errors[:8]))
        self.statusBar().showMessage("导入完成，文章和本地副本已保存。", 5000)
        self._update_actions()

    def _import_paths(self, paths):
        known = {Path(article.source_path).resolve() for article in self.articles}
        errors, added = [], 0
        progress = QProgressDialog("正在解析 Word…", "取消", 0, len(paths), self)
        progress.setWindowModality(Qt.WindowModal); progress.setMinimumDuration(350)
        for index, path in enumerate(sorted(paths, key=lambda p: natural_sort_key(Path(p).name))):
            progress.setValue(index); progress.setLabelText(f"正在解析：{Path(path).name}")
            if progress.wasCanceled():
                break
            try:
                resolved = Path(path).resolve()
                if resolved in known:
                    continue
                article = self.library.import_file(resolved, self._default_rules())
                self.articles.append(article)
                known.add(resolved); added += 1
            except Exception as exc:
                LOGGER.exception("导入失败：%s", Path(path).name)
                errors.append(f"{Path(path).name}：{exc}")
        progress.setValue(len(paths))
        self.articles.sort(key=lambda article: natural_sort_key(article.filename))
        self._reload_article_list()
        if (added and self.article_list.currentRow() < 0
                and self.session is None and self.pending_review_session is None):
            self.article_list.setCurrentRow(0)
        if errors:
            preview = "\n".join(errors[:8])
            QMessageBox.warning(self, "部分文件未导入", f"成功导入 {added} 篇，以下文件失败：\n{preview}")
        else:
            self.statusBar().showMessage(f"已导入 {added} 篇文章", 5000)

    def _restore_article_library(self):
        if self._closed_for_restore:
            return
        try:
            articles, paths = self.repository.load_article_catalog()
        except Exception:
            LOGGER.exception("恢复文章列表失败")
            self.statusBar().showMessage("未能读取已保存的文章列表，请检查数据库权限或日志。", 10000)
            return
        known = {a.source_path for a in self.articles}
        self.articles.extend(a for a in articles if a.source_path not in known)
        self.articles.sort(key=lambda a: natural_sort_key(a.filename))
        self._restoring = True
        self._reload_article_list()
        # Delayed startup/import must not replace a practice or its submitted results.
        active_practice = self.session is not None or self.pending_review_session is not None or self.current_article is not None
        self._restoring = False
        if not active_practice and not self._review_home_visible:
            if self.articles:
                selected = next((i for i, a in enumerate(self.articles)
                                 if a.source_path == self._last_article_path), 0)
                self.article_list.setCurrentRow(selected)
                self._pending_article_path = self.articles[selected].source_path
                self._flush_article_selection()
            else:
                self._show_review_home(self.repository.continuity_status())
            if any(s.submitted and not s.database_id for s in self._drafts.values()):
                self.statusBar().showMessage('有批改结果尚未保存，请从“练习 → 未完成练习…”打开并重试保存。')
        if self.articles and not any(s.submitted and not s.database_id for s in self._drafts.values()):
            self.statusBar().clearMessage()
        valid_paths, missing_paths = [], []
        for path in paths:
            if Path(path).is_file():
                valid_paths.append(path)
            else:
                missing_paths.append(path)
        # 移动盘未接入等情况可能只是暂时的，不自动删除已保存的路径。
        if valid_paths:
            self.statusBar().showMessage(f"正在恢复上次的 {len(valid_paths)} 篇文章…")
            self._start_import(valid_paths)
        if missing_paths:
            self.statusBar().showMessage(f"另有 {len(missing_paths)} 个原文件暂时无法找到，路径已保留，下次启动会重试。", 10000)

    def _show_review_home(self, status):
        self._cancel_article_switch()
        self.session = None
        self.pending_review_session = None
        self.current_article = None
        self._clear_content()
        self._review_home_visible = True
        self._review_home_status = status
        self.article_list.blockSignals(True)
        self.article_list.clearSelection(); self.article_list.setCurrentRow(-1)
        self.article_list.blockSignals(False)
        completed = status['completed'] > 0 and status['remaining'] == 0
        title = '今日复习已完成' if completed else '今日复习'
        self.mode_title.setText(title)
        if completed:
            text = (f"今天计划中的 {status['completed']} 词已提交。\n"
                    "需要回顾时，可从“历史错词练习”打开本轮记录。")
        elif status['remaining']:
            text = f"今天待完成 {status['remaining']} 词。点击右侧“开始今日复习”进入今天的任务。"
        else:
            text = "今天暂时没有可开始的复习任务。"
        detail = QLabel(text); detail.setWordWrap(True)
        self.content_layout.addWidget(detail)
        from src.services.review_continuity import plan_summary, future_text
        plan_note = QLabel(plan_summary(status)+"\n"+future_text(status))
        plan_note.setWordWrap(True); plan_note.setObjectName('muted')
        self.content_layout.addWidget(plan_note)
        note = QLabel('以前的复习草稿已保留，可从“练习 → 未完成练习…”按日期手动打开。')
        note.setWordWrap(True); note.setObjectName('muted')
        self.content_layout.addWidget(note); self.content_layout.addStretch()
        self.progress.setValue(100 if completed else 0)
        self.progress.setFormat('今日已提交' if completed else '填写进度 %p%')
        self.progress_text.setText(f"今日已提交：{status['completed']}词\n待完成：{status['remaining']}词")
        from src.services.review_continuity import status_text
        self.progress_text.setToolTip(status_text(status))
        self.score_text.setText('错词仍按计划复习' if completed else '开始后独立作答')
        self.score_text.setToolTip('提交不代表全部记牢，原成绩和复习安排保持不变。')
        self._update_actions()

    def _reload_article_list(self):
        self._cancel_article_switch()
        current_path = self.current_article.source_path if self.current_article else None
        self.article_list.blockSignals(True); self.article_list.clear()
        selected = -1
        for index, article in enumerate(self.articles):
            item = QListWidgetItem(f"{article.filename}\n重点词：{article.target_count}")
            item.setToolTip(article.filename + "\n" + article.source_path)
            item.setData(Qt.UserRole, article.source_path); self.article_list.addItem(item)
            if article.source_path == current_path:
                selected = index
        self.article_list.blockSignals(False)
        self.article_summary.setText(f"文章：{len(self.articles)} 篇\n总重点词：{sum(a.target_count for a in self.articles)}")
        if selected >= 0:
            self.article_list.blockSignals(True)
            self.article_list.setCurrentRow(selected)
            self.article_list.blockSignals(False)
        self._filter_articles()
        self._update_actions()

    def _filter_articles(self, *_):
        if not hasattr(self, "article_list"): return
        query = self.article_search.text().strip().casefold()
        for index, article in enumerate(self.articles):
            item = self.article_list.item(index)
            if item: item.setHidden(query not in article.filename.casefold())
        self._schedule_article_selection()

    def _selected_article_items(self):
        return [item for item in self.article_list.selectedItems() if not item.isHidden()]

    def _cancel_article_switch(self):
        self._article_switch.stop()
        self._pending_article_path = None

    def _schedule_article_selection(self, *_):
        selected = self._selected_article_items()
        count = len(selected)
        self.remove_article_button.setEnabled(count > 0)
        self.remove_article_button.setText(f"移除选中 {count} 篇" if count > 1 else "从列表移除")
        self._cancel_article_switch()
        if count != 1:
            return
        path = selected[0].data(Qt.UserRole)
        if self.current_article and path == self.current_article.source_path:
            return
        self._pending_article_path = path
        if self._article_switch_delay == 0:
            self._flush_article_selection()
        else:
            self._article_switch.start(self._article_switch_delay)

    def _flush_article_selection(self):
        path = self._pending_article_path
        self._cancel_article_switch()
        if path is None:
            return True
        selected = self._selected_article_items()
        if len(selected) != 1 or selected[0].data(Qt.UserRole) != path:
            return True
        row = next((i for i, a in enumerate(self.articles) if a.source_path == path), -1)
        return self._article_selected(row)

    def _article_selected(self, row):
        if not 0 <= row < len(self.articles):
            return False
        if self.current_article and self.current_article.source_path == self.articles[row].source_path:
            return True
        if not self._save_active_draft():
            previous = next((i for i,a in enumerate(self.articles) if self.current_article and a.source_path == self.current_article.source_path), -1)
            self.article_list.blockSignals(True); self.article_list.setCurrentRow(previous); self.article_list.blockSignals(False)
            return False
        article = self._materialize_article(self.articles[row])
        if article is None:
            self.article_list.blockSignals(True)
            previous = next((i for i, a in enumerate(self.articles)
                             if self.current_article and a.source_path == self.current_article.source_path), -1)
            self.article_list.setCurrentRow(previous)
            self.article_list.blockSignals(False)
            return False
        self.current_article = article
        self._last_article_path = article.source_path
        self.session = None
        self._preview_session_key = None
        self.pending_review_session = None
        self.show_preview()
        return True

    def _article_list_clicked(self, item):
        if (len(self._selected_article_items()) == 1 and self.current_article is not None
                and item.data(Qt.UserRole) == self.current_article.source_path
                and (self.session is not None or self.pending_review_session is not None)):
            self.show_preview(explicit=True)

    def _article_context_menu(self, pos: QPoint):
        index = self.article_list.indexAt(pos)
        if index.isValid() and not self.article_list.item(index.row()).isSelected():
            self.article_list.setCurrentRow(index.row())
        self._cancel_article_switch()
        count = len(self._selected_article_items())
        with owned_dialog(QMenu(self)) as menu:
            preview = menu.addAction("查看原文")
            practice = menu.addAction("开始整篇默写")
            preview.setEnabled(count == 1); practice.setEnabled(count == 1)
            menu.addSeparator()
            remove = menu.addAction(f"从列表移除选中的 {count} 篇文章")
            remove.setEnabled(count > 0)
            chosen = menu.exec(self.article_list.viewport().mapToGlobal(pos))
            if chosen is remove:
                self.remove_selected_article()
            else:
                self._schedule_article_selection()
                if chosen is preview: self.show_preview(explicit=True)
                elif chosen is practice: self.start_full_practice()

    def remove_selected_article(self):
        self._cancel_article_switch()
        paths = {item.data(Qt.UserRole) for item in self._selected_article_items()}
        articles = [a for a in self.articles if a.source_path in paths]
        if not articles:
            QMessageBox.information(self, "请选择文章", "请先在左侧框选文章，或用 Ctrl / Shift 多选。")
            return
        extra = "\n这些文章的未完成草稿也会移除。" if any(s.article.source_path in paths for s in self._drafts.values()) else ""
        names = "\n".join(a.filename for a in articles[:6]) + ("\n……" if len(articles) > 6 else "")
        if QMessageBox.question(self, "从列表移除",
                f"确定从列表移除选中的 {len(articles)} 篇文章吗？\n{names}{extra}\n\n原始 Word、本地副本、学习历史和长期错词均保留。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.repository.delete_article_paths(paths)
        except Exception:
            LOGGER.exception("移除文章记录失败")
            QMessageBox.warning(self, "移除失败", "数据库未能保存修改，文章仍保留在列表中。请查看日志。")
            return
        current_removed = self.current_article is not None and self.current_article.source_path in paths
        self._drafts = {k: s for k, s in self._drafts.items() if s.article.source_path not in paths}
        self._saved_draft_states = {k: state for k, state in self._saved_draft_states.items() if k in self._drafts}
        self.articles = [a for a in self.articles if a.source_path not in paths]
        if current_removed:
            self._autosave.stop(); self._view_save.stop()
            self.current_article = None; self.session = None
            self.pending_review_session = None
            self._clear_content()
        self._reload_article_list()
        if current_removed and self.articles:
            first = next((i for i in range(self.article_list.count()) if not self.article_list.item(i).isHidden()), -1)
            self.article_list.setCurrentRow(first)
        if not self.articles or (current_removed and self.article_list.currentRow() < 0):
            self._clear_content(); self.mode_title.setText("请导入 Word 文章")
            self.progress_text.setText("尚未导入文章"); self.score_text.setText("提交前不会显示正确率")
        self.statusBar().showMessage(f"已从列表移除 {len(articles)} 篇文章；Word 和学习历史均保留", 5000)

    def show_preview(self, explicit=False):
        if not self._flush_article_selection(): return
        if not self.current_article:
            return
        active = self.session is not None and not self.session.submitted
        if active:
            from src.services.learning_evidence import expose
            expose(self.session)
        if not self._save_active_draft(): return
        if self.session is not None and not self.session.submitted:
            self._preview_session_key = self.session.session_key
        self.session = None
        self.pending_review_session = None
        self.current_article = next((a for a in self.articles if a.source_path == self.current_article.source_path), self.current_article)
        # Automatic rendering on startup is not proof that the user read a
        # whole article. Explicit previews and mid-round reveals are observable.
        if active or explicit:
            self._record_reading(self.current_article.word_items, "original")
        self.mode_title.setText(f"原文模式　·　{self.current_article.filename}")
        self._clear_content()
        page = QFrame(); page.setObjectName('originalPage')
        page_layout = QVBoxLayout(page); page_layout.setContentsMargins(28, 26, 28, 30)
        page_layout.setSpacing(18)
        self.content_layout.addWidget(page)
        first_paragraph = True
        for paragraph in self.current_article.paragraphs:
            if not paragraph.text.strip():
                continue
            card = QFrame(); card.setObjectName('readingParagraph')
            layout = QVBoxLayout(card); layout.setContentsMargins(0, 0, 0, 0)
            card.setProperty("paragraphText", paragraph.text)
            label = TextLabel()
            heading = is_article_heading(paragraph.style_name) or (
                first_paragraph and len(paragraph.text.strip()) <= 100
                and not any(s.is_target for s in paragraph.segments)
                and any(s.style.bold for s in paragraph.segments)
                and not paragraph.text.rstrip().endswith(('.', '。', '!', '?')))
            first_paragraph = False
            label.setProperty("articleHeading", heading)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setCursor(Qt.IBeamCursor)
            if any(s.is_target for s in paragraph.segments):
                html = ''.join(('<span style="color:#ba354b;font-weight:700">' + escape(s.text) + '</span>')
                               if s.is_target else escape(s.text) for s in paragraph.segments)
                label.setTextFormat(Qt.RichText)
                label.setText(reading_html(html))
            else:
                label.setTextFormat(Qt.RichText)
                label.setText(reading_html(escape(paragraph.text)))
            article = self.current_article
            layout.addWidget(label)
            from src.services.learning_content import note_for
            for item in article.word_items:
                if item.paragraph_index == paragraph.index and note_for(item):
                    note = QLabel(f"{item.answer} · {note_for(item)}"); note.setObjectName('learningNote'); note.setWordWrap(True)
                    note.setStyleSheet('color:#65778c;font-size:14px;background:transparent;')
                    layout.addWidget(note)
            page_layout.addWidget(card)
        self.content_layout.addStretch()
        self.progress_text.setText(f"文章：{self.current_article.filename}\n重点词：{self.current_article.target_count}")
        self.progress.setValue(0); self.progress.setFormat("填写进度 %p%")
        self.progress_text.setToolTip("")
        self.score_text.setText("提交前不会显示正确率"); self.score_text.setToolTip("")
        self._apply_zoom_to_content()
        # A newly built page starts at its own top, before any reading anchor exists.
        self._cancel_pending_follow(); self.scroll.verticalScrollBar().setValue(0)
        self._update_actions()

    def start_full_practice(self):
        if self._wrong_reading:
            self._start_after_wrong_reading()
            return
        if not self._flush_article_selection(): return
        if self.pending_review_session is not None:
            try:
                session = self.repository.prepare_learning_session(self.pending_review_session)
            except ValueError as exc:
                QMessageBox.information(self, '题目已调整', str(exc)); return
            self.pending_review_session = None
            from src.services.learning_evidence import SCHEMA, utc_now
            memory = session.view_state.get("memory", {})
            if memory.get("schema") == SCHEMA:
                # The preview is expected study before the closed-book round,
                # not a revelation during that round. Draft resume never resets it.
                memory["began_at"] = utc_now()
            self._activate_session(session)
            self._save_active_draft()
            return
        article = self._library_article()
        if not article:
            QMessageBox.information(self, "请先导入文章", "当前只有错词句子快照。整篇练习需要先导入对应 Word。")
            return
        if not article.word_items:
            QMessageBox.information(self, "没有重点词", "本篇在当前规则下未识别到重点词。\n可从“文章与设置”调整本篇识别规则或手动选择挖空。")
            return
        if not self._save_active_draft(): return
        if any(s.article.source_path == self.current_article.source_path for s in self._drafts.values()):
            if QMessageBox.question(self, "开始新一轮", "本篇已有未完成练习。仍要新开一轮吗？\n旧草稿会保留，不会删除。",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        self.current_article = article
        self.pending_review_session = None
        self._preview_session_key = None
        self.session = new_full_session(article)
        if self.mode_title.text().startswith("原文模式"):
            self.session.view_state["memory"]["phase"] = "study"
        self._completion_notified = False
        self._render_practice(full_article=True)
        self._save_active_draft()

    @contextmanager
    def _batch_content_layout(self):
        """Do not relayout the visible page after each individual card change."""
        updates, layout = self.content.updatesEnabled(), self.content_layout.isEnabled()
        self.content.setUpdatesEnabled(False)
        self.content_layout.setEnabled(False)
        try:
            yield
        finally:
            self.content_layout.setEnabled(layout)
            self.content.setUpdatesEnabled(updates)

    @staticmethod
    def _empty_content_page():
        content = QWidget(); content.setObjectName('articleContent')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 6, 6, 18); layout.setSpacing(12)
        return content, layout

    def _result_page_key(self):
        session = self.session
        if session is None or not session.submitted:
            return None
        return (id(session), session.session_key, id(session.article), tuple(session.target_ids),
                tuple((r.item_id, r.user_answer, r.correct_answer, r.is_correct) for r in session.results))

    def _discard_result_page(self):
        cache, self._result_page_cache = self._result_page_cache, None
        if cache and cache['content'] is not self.content and isValid(cache['content']):
            cache['content'].hide()
            cache['content'].setParent(None)
            cache['content'].deleteLater()

    def _mount_content_page(self, *, retain_old=False):
        old = self.scroll.takeWidget()
        if old is not None:
            old.hide(); old.removeEventFilter(self)
        self.scroll.setWidget(self.content)
        self.content.installEventFilter(self)
        self.reading_selection.set_root(self.content)
        if old is not None and old is not self.content:
            # takeWidget() already detaches it. Keep a retained page outside
            # the live window tree: parenting it back to the window would
            # repolish thousands of hidden children and slow the reader too.
            if not retain_old:
                old.deleteLater()

    @contextmanager
    def _rebuild_content_page(self, *, retain_result=False):
        """Replace the old tree once; retain at most the current result page."""
        self._discard_result_page()
        cache = None
        if retain_result:
            cache = dict(key=self._result_page_key(), content=self.content, layout=self.content_layout,
                         edits=self.answer_edits, hints=self.answer_hints, paragraphs=self._paragraph_items,
                         zoom=self.zoom_percent, title=self.mode_title.text(), title_tip=self.mode_title.toolTip(),
                         detail=self.mode_detail.text(), detail_tip=self.mode_detail.toolTip(),
                         detail_visible=not self.mode_detail.isHidden(), daily_detail=self._daily_progress_detail)
        self.content, self.content_layout = self._empty_content_page()
        self._mount_content_page(retain_old=cache is not None)
        try:
            with self._batch_content_layout():
                yield
        finally:
            self._result_page_cache = cache

    def _restore_result_page(self, restore_scroll):
        cache = self._result_page_cache
        if (not cache or cache['key'] != self._result_page_key()
                or not isValid(cache['content'])):
            self._discard_result_page()
            return False
        self._result_page_cache = None
        self._cancel_article_switch()
        self._reset_content_state()
        self.content, self.content_layout = cache['content'], cache['layout']
        self._mount_content_page()
        self.answer_edits, self.answer_hints = cache['edits'], cache['hints']
        self._paragraph_items, self._daily_progress_detail = cache['paragraphs'], cache['daily_detail']
        self.mode_title.setText(cache['title']); self.mode_title.setToolTip(cache['title_tip'])
        self.mode_detail.setText(cache['detail']); self.mode_detail.setToolTip(cache['detail_tip'])
        self.mode_detail.setVisible(cache['detail_visible'])
        if cache['zoom'] != self.zoom_percent:
            self._apply_zoom_to_content()
        else:
            self.reading_selection.bind()
        self._show_results(notify=False, refresh_content=False)
        self._defer_practice_restore(restore_scroll)
        return True

    def _build_practice_content(self, full_article):
        self._clear_content(); self.answer_edits = {}
        for item in self.session.article.word_items:
            self._paragraph_items.setdefault(item.paragraph_index, []).append(item)
        mode_name = self.session.mode.value
        self.mode_title.setText(self.session.article.filename if self.session.mode == PracticeMode.DAILY_REVIEW
                               else f"{mode_name}　·　{self.session.article.filename}")
        if self.session.view_state.get('consolidation'):
            self.mode_title.setText(f"小组再练　·　{self.session.total}词")
            self.mode_title.setToolTip(self.session.article.filename)
        else:
            self.mode_title.setToolTip("")
        if (self.session.mode == PracticeMode.DAILY_REVIEW
                and self.session.view_state.get('daily_review_day') != self.repository.today().isoformat()):
            day = self.session.view_state.get('daily_review_day') or self.session.started_at.date().isoformat()
            self.mode_title.setText(f"复习草稿 · {day} · {self.session.total}词")
            self.mode_title.setToolTip('这是以前保存的复习草稿，提交不计入今天的复习任务。')
        if self.session.mode == PracticeMode.DAILY_REVIEW and not self.session.view_state.get('consolidation'):
            self._set_daily_review_detail()
        target_set = set(self.session.target_ids)
        if full_article:
            for paragraph in self.session.article.paragraphs:
                self._add_practice_text(paragraph.text, paragraph.index, 0, len(paragraph.text), target_set,
                                        heading=is_article_heading(paragraph.style_name))
        else:
            for items in group_items_by_sentence(self.session):
                first = items[0]
                self._add_practice_text(first.paragraph_text, first.paragraph_index,
                                        first.sentence_start, first.sentence_end, target_set)
        self.content_layout.addStretch()

    def _render_practice(self, full_article: bool, restore_scroll=None):
        with self._batch_content_layout():
            self._build_practice_content(full_article)
        self._update_progress(); self._apply_zoom_to_content(); self._update_actions()
        if self.session.submitted:
            self._show_results(notify=False)
        self._defer_practice_restore(restore_scroll)

    def _defer_practice_restore(self, restore_scroll=None):
        key = self.session.session_key
        generation = self._content_generation
        revision = self._view_restore_revision
        # Startup may save a draft again before the deferred restore executes.
        # Preserve the requested offset instead of saving the temporary top.
        self._pending_view_restore = (generation, key, self.session.view_state.get("scroll", 0))
        if restore_scroll is not None:
            self._pending_view_restore = (generation, key, restore_scroll)
        QTimer.singleShot(0, lambda: self._restore_view(key, generation, revision, restore_scroll))

    def _add_practice_text(self, paragraph_text, paragraph_index, start_bound, end_bound, target_set, heading=False):
        if not paragraph_text[start_bound:end_bound].strip():
            return
        card, flow = self._paragraph_card()
        card.setProperty("paragraphText", paragraph_text[start_bound:end_bound])
        items = sorted([item for item in self._paragraph_items.get(paragraph_index, [])
                        if item.start >= start_bound
                        and item.end <= end_bound], key=lambda item: item.start)
        cursor = start_bound
        for item in items:
            if item.start > cursor:
                self._add_text(flow, paragraph_text[cursor:item.start])
            if item.id in target_set:
                edit = AnswerLineEdit(item.id, len(item.answer))
                edit.setProperty("answerLength", len(item.answer))
                edit.setProperty("answerText", item.answer)
                edit.setText(self.session.answers.get(item.id, ""))
                edit.textChanged.connect(lambda text, item_id=item.id: self._answer_changed(item_id, text))
                edit.requestMove.connect(lambda direction, item_id=item.id: self._move_from(item_id, direction))
                edit.focusEntered.connect(self._answer_focused)
                edit.installEventFilter(self)
                flow.addWidget(edit); self.answer_edits[item.id] = edit
                hint = TextLabel(f"正确答案：{item.answer}")
                hint.setObjectName("inlineAnswer")
                hint.setTextFormat(Qt.PlainText)
                hint.setProperty("flowText", True)
                hint.setProperty("flowAnswerHint", True)
                hint.setWordWrap(True)
                hint.setIndent(0)  # A styled border must not add QLabel's automatic text indent.
                hint.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
                hint.setMinimumWidth(0)
                hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
                hint.setCursor(Qt.IBeamCursor)
                hint.setStyleSheet("color:#b42318;font-weight:700;background:#fff2f0;border:1px solid #efb5ae;border-radius:6px;padding:5px 8px;")
                hint.hide(); flow.addWidget(hint); self.answer_hints[item.id] = hint
            else:
                self._add_text(flow, item.answer)
            cursor = item.end
        if cursor < end_bound:
            self._add_text(flow, paragraph_text[cursor:end_bound])
        if heading:
            for label in card.findChildren(QLabel):
                label.setProperty("articleHeading", True)
        if items:
            self._add_learning_notes(flow, items, visible=self.session.submitted)
        self.content_layout.addWidget(card)

    def _paragraph_card(self):
        card = QFrame(); card.setObjectName("paragraphCard")
        card.setStyleSheet("QFrame#paragraphCard{background:white;border:1px solid #e2e7ed;border-radius:10px;}")
        flow = FlowLayout(card, margin=14, h_spacing=1, v_spacing=10)
        return card, flow

    @staticmethod
    def _text_label(text):
        label = TextLabel(text if text else " "); label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setIndent(0)
        label.setProperty("flowText", True)
        label.setMinimumWidth(0)
        label.setStyleSheet("background:transparent;")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setCursor(Qt.IBeamCursor)
        return label

    def _add_text(self, flow, text, target=False):
        tokens = re.findall(r"\n|[（(【][^\n()（）【】]+[）)】][.,!?，。！？]*[ \t]*|[A-Za-z0-9]+(?:[-’'][A-Za-z0-9]+)*[.,!?;:]*[ \t]*|[ \t]+|[^\x00-\x7f]{1,5}|.", text)
        for token in tokens or [" "]:
            label = self._text_label(token if token != "\n" else "")
            if token == "\n": label.setProperty("flowBreak", True)
            if re.match(r"^[（(【]", token) or re.fullmatch(r"[.,!?;:，。！？；：）)】]+[ \t]*", token):
                label.setProperty("keepWithPrevious", True)
            if target: label.setStyleSheet("color:#d61f32;font-weight:700;background:transparent;")
            flow.addWidget(label)

    def _add_learning_notes(self, flow, items, visible=True):
        from src.services.learning_content import note_for
        for item in items:
            if not note_for(item): continue
            label = self._text_label(f"易混备注 · {item.answer}：{note_for(item)}")
            label.setObjectName('learningNote')
            label.setStyleSheet('color:#65778c;background:#f3f6fa;border-radius:5px;padding:4px 7px;')
            label.setProperty('flowWidthLimit', 420)
            flow.addWidget(label); label.setVisible(visible)

    def _answer_changed(self, item_id, text):
        if not self.session or self.session.submitted:
            return
        if self.reading_selection.selected_text(): self.reading_selection.clear()
        self.session.answers[item_id] = text
        from src.services.learning_evidence import answer_changed
        answer_changed(self.session, item_id)
        self._queue_save()
        self._update_progress()
        if self.session.unfilled == 0 and not self._completion_notified:
            self._completion_notified = True
            self.statusBar().showMessage("本轮已全部填写，可继续检查；只有点击“提交批改”才会判断对错。")

    def _move_from(self, item_id, direction):
        ids = list(self.answer_edits)
        if item_id not in ids:
            return
        self._cancel_pending_follow()
        if direction > 0 and item_id == ids[-1]:
            if not self.session or self.session.submitted:
                return
            blank = next((i for i in ids if not self.session.answers.get(i, "").strip()), None)
            if blank is None:
                if self._confirm_finished_submission():
                    self._commit_submission()
                return
            index = ids.index(blank)
        else:
            index = max(0, min(len(ids) - 1, ids.index(item_id) + direction))
        edit = self.answer_edits[ids[index]]
        edit.setFocus(); edit.selectAll()
        self._queue_answer_follow(ids[index])
        self._queue_view_save()

    def _answer_focused(self, item_id):
        self._last_focused_answer = item_id
        edit = self.answer_edits.get(item_id)
        if edit is not None and edit.update_preferred_width():
            self._remember_layout_anchor()
            self.content_layout.invalidate()
        self._queue_answer_follow(item_id)

    def _queue_answer_follow(self, item_id):
        if self.session is None or item_id not in self.answer_edits:
            return
        self._pending_follow = (self._content_generation, self.session.session_key, item_id)
        if not self._follow_timer.isActive():
            self._follow_timer.start(0)

    def _cancel_pending_follow(self):
        self._pending_follow = None
        self._follow_timer.stop()
        self._layout_anchor = None
        self._layout_timer.stop()
        self._pending_view_restore = None
        self._view_restore_revision += 1

    def _flush_pending_follow(self):
        pending = self._pending_follow
        if pending is None or pending[0] != self._content_generation or self.session is None:
            return
        if pending[1] != self.session.session_key:
            return
        self._restore_layout_anchor()
        if self._pending_view_restore is not None or not self._reading_layout_ready():
            return
        self._pending_follow = None
        edit = self.answer_edits.get(pending[2])
        if edit is None or QApplication.focusWidget() is not edit:
            return
        self._center_answer(pending[2])

    def _confirm_finished_submission(self):
        with owned_dialog(QMessageBox(self)) as dialog:
            dialog.setWindowTitle("本轮已填写完成")
            dialog.setText("所有题目已经填写完成，是否提交批改？")
            dialog.setInformativeText("提交后统一批改并锁定本轮答案。")
            submit_button = dialog.addButton("提交批改", QMessageBox.AcceptRole)
            check_button = dialog.addButton("继续检查", QMessageBox.RejectRole)
            dialog.setDefaultButton(check_button)
            dialog.setEscapeButton(check_button)
            dialog.exec()
            return dialog.clickedButton() is submit_button

    def _center_answer(self, item_id):
        edit = self.answer_edits.get(item_id)
        if not edit or not edit.isVisible(): return
        y = edit.mapTo(self.content, QPoint(0, 0)).y()
        bar = self.scroll.verticalScrollBar()
        height = self.scroll.viewport().height()
        relative_y = y - bar.value()
        top = round(height * 0.12)
        bottom = max(top + edit.height(), round(height * 0.76))
        if relative_y < top:
            target = y - top
        elif relative_y + edit.height() > bottom:
            target = y + edit.height() - bottom
        else:
            return
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target)))

    def submit(self):
        if not self.session or self.session.submitted:
            return
        if self.session.unfilled:
            answer = QMessageBox.question(self, "仍有空白答案",
                f"还有 {self.session.unfilled} 个词尚未填写，空白会按错误处理。确定仍然提交吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        else:
            if not self._confirm_finished_submission():
                return
        self._commit_submission()

    def _commit_submission(self):
        if not self.session or self.session.submitted: return
        if not self._save_active_draft():
            QMessageBox.warning(self, "尚未提交", "当前草稿保存失败，请检查数据目录后重试，答案还在本窗口中。")
            return
        grade_session(self.session)
        daily_status = None
        try:
            self.repository.save_submitted_session(self.session)
            daily_status = self._refresh_daily_review()
            self._wrong_history_available = None
            self._drafts.pop(self.session.session_key, None)
            self._saved_draft_states.pop(self.session.session_key, None)
        except Exception:
            LOGGER.exception("保存学习记录失败")
            self.session.database_id = None
            self._save_active_draft()
            QMessageBox.warning(self, "保存失败", "批改已完成，成绩待保存。请点击右侧“重试保存成绩”，不会重复累计。")
        self._show_results(daily_status=daily_status)

    def _show_results(self, notify=True, daily_status=None, *, refresh_content=True):
        self.reading_selection.clear()
        self._answers_visible = False
        if notify and self.session.wrong_ids:
            self._record_reading([self.session.article.item_map[i] for i in self.session.wrong_ids], "feedback")
        result_map = {result.item_id: result for result in self.session.results}
        self.content.setUpdatesEnabled(False)
        self.content_layout.setEnabled(False)
        try:
            if refresh_content:
                for item_id, edit in self.answer_edits.items():
                    result = result_map[item_id]
                    edit.setReadOnly(True)
                    edit.setProperty("answerState", "correct" if result.is_correct else "wrong")
                    edit.style().unpolish(edit); edit.style().polish(edit)
                    hint = self.answer_hints.get(item_id)
                    if hint:
                        hint.hide()
                        if not result.is_correct:
                            hint.setText(f"正确答案：{result.correct_answer}")
                            hint.setStyleSheet("color:#b42318;font-weight:700;background:#fff2f0;border:1px solid #efb5ae;border-radius:6px;padding:5px 8px;"
                                              + f"font-size:{round(18 * self.zoom_percent / 100)}px;")
                            hint.show()
            from src.services.learning_evidence import result_summary
            full_summary = result_summary(self.session)
            summary = full_summary
            if self.session.view_state.get("memory", {}).get("phase") == "relearning":
                summary = summary.split("\n", 1)[0]
            elif summary.startswith('首轮回忆：') and '\n其余' not in summary:
                # Fully independent answers already have the same numerator and
                # denominator. Keep the full evidence in the tooltip, not twice
                # in the narrow sidebar. Mixed evidence retains both figures.
                summary = summary.split('\n', 1)[0]
            blanks = sum(not r.user_answer.strip() for r in self.session.results)
            self.score_text.setText(summary + f"\n剩余错词：{self.session.wrong_count}"
                                    + (f"\n未填写：{blanks}" if blanks else ''))
            first = self.session.view_state.get("first_round_result")
            if first:
                self.score_text.setText(f"{first.get('label','首轮回忆')}：{first['correct']}/{first['total']}\n" + self.score_text.text())
            self.score_text.setText('原词回忆结果\n' + self.score_text.text())
            self.score_text.setToolTip('按题目指定的原词判分；近义表达暂不自动判对。\n' + full_summary)
            for note in self.content.findChildren(QLabel, 'learningNote'): note.show()
            self.progress.setValue(100)
            self.progress.setFormat("本轮已提交")
            self.progress_text.setText("本轮已提交\n可核对错词原句。")
            self.progress_text.setToolTip("本轮已提交，答案已锁定。完成本轮不代表已经记牢，后续复习仍按原计划安排。")
            if self.session.view_state.get('consolidation') and not self.session.wrong_count:
                self.progress_text.setText("本组已全部答对\n可返回原轮结果。")
            if self.session.wrong_count:
                if refresh_content:
                    self._add_wrong_summary()
                if self.session.view_state.get("memory", {}).get("phase") == "relearning":
                    minutes = self.repository.get_setting("consolidation_suggest_minutes", 10)
                    self.progress_text.setText("本轮纠错已结束\n可阅读错词或加练。")
                    self.progress_text.setToolTip(f"先读懂剩余错词的原句。有余力可约{minutes}分钟后再练一组，每组最多10词，不必练到全对。")
                    if self.session.view_state.get('consolidation'):
                        self.progress_text.setText("本组已结束\n可阅读错词或返回原轮。")
                        self.progress_text.setToolTip("每词今天最多加练一次。剩余错词会在后续复习中返回，不必今天反复练到全对。")
            if not self.session.database_id:
                self.progress_text.setText("成绩尚未保存，请点击右侧“重试保存成绩”。")
            elif self.session.mode == PracticeMode.DAILY_REVIEW:
                if self.session.view_state.get("daily_review_day") != self.session.submitted_at.date().isoformat():
                    self.progress_text.setText("跨日草稿成绩已保存，本轮不更新记忆模型。\n请从“今日复习”进入今天的任务。")
                else:
                    from src.services.review_continuity import status_text
                    status = daily_status if daily_status is not None else self.repository.continuity_status()
                    self.progress_text.setText(f"今日已提交：{status['completed']}词\n待完成：{status['remaining']}词")
                    if status['return_waiting']:
                        self.progress_text.setText(self.progress_text.text()+f"\n还有{status['return_waiting']}词已到复习时间，分批继续。")
                    self.progress_text.setToolTip(status_text(status) + "\n提交不等于全部记牢；错词还会按计划返回。")
        finally:
            self.content_layout.setEnabled(True)
            if refresh_content:
                for flow in self.content.findChildren(FlowLayout): flow.invalidate()
                self.content_layout.invalidate(); self.content.adjustSize()
            self.content.setUpdatesEnabled(True)
            self.content.update()
        if not self.session.wrong_count and notify:
            message = "本篇文章全部正确。" if self.session.mode == PracticeMode.FULL_ARTICLE else "本轮错词已经全部正确，本次错词已练习完成。"
            QMessageBox.information(self, "全部正确", message)
        self.statusBar().showMessage("本轮已提交批改，成绩已保存。" if self.session.database_id
                                     else "批改已完成，成绩待保存，请重试保存。", 5000)
        self._update_actions()

    def _add_wrong_summary(self):
        if self.content.findChild(QFrame, 'wrongSummary') is not None:
            return
        panel = QFrame(); panel.setObjectName("wrongSummary")
        panel.setStyleSheet("QFrame#wrongSummary{background:#fff7f6;border:1px solid #f3c8c4;border-radius:9px;padding:10px;} QLabel{background:transparent;border:none;padding:2px;}")
        layout = QVBoxLayout(panel); title = QLabel("本次错词"); title.setStyleSheet("font-weight:700;color:#a92b21;")
        layout.addWidget(title)
        item_map = self.session.article.item_map
        lines = []
        for result in self.session.results:
            if result.is_correct: continue
            item = item_map[result.item_id]
            text = f"{result.user_answer or '（空白）'}  ✗  →  {result.correct_answer}"
            if item.chinese: text += f"　（{item.chinese}）"
            lines.append(text)
        label = TextLabel("\n".join(lines)); label.setWordWrap(True); label.setStyleSheet("color:#a92b21;")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse); label.setCursor(Qt.IBeamCursor)
        layout.addWidget(label)
        self.content_layout.insertWidget(max(0, self.content_layout.count() - 1), panel)
        self.reading_selection.bind()

    def show_answers(self):
        if not self.session or self.session.submitted:
            return
        if self._answers_visible:
            self._hide_answer_hints()
            self.statusBar().showMessage("答案已隐藏", 3000)
            return
        self._apply_answer_hints()
        self.statusBar().showMessage("答案已显示在输入框旁", 3000)

    def _apply_answer_hints(self):
        self.reading_selection.clear()
        from src.services.learning_evidence import expose
        expose(self.session)
        self._record_reading([self.session.article.item_map[i] for i in self.session.target_ids], "answer")
        self._queue_save()
        self._remember_layout_anchor()
        self._answers_visible = True
        item_map = self.session.article.item_map
        for item_id, edit in self.answer_edits.items():
            hint = self.answer_hints.get(item_id)
            if hint:
                hint.setText(f"答案：{item_map[item_id].answer}")
                hint.setStyleSheet("color:#075cab;font-weight:700;background:#edf6ff;border:1px solid #9bc8ef;border-radius:6px;padding:5px 8px;"
                                  + f"font-size:{round(18 * self.zoom_percent / 100)}px;")
                hint.show()
        for note in self.content.findChildren(QLabel, 'learningNote'): note.show()
        for flow in self.content.findChildren(FlowLayout): flow.invalidate()
        self.content_layout.invalidate(); self.content.adjustSize()
        self._update_actions()
        self._schedule_anchor_restore()

    def _hide_answer_hints(self):
        self.reading_selection.clear()
        self._remember_layout_anchor()
        self._answers_visible = False
        for hint in self.answer_hints.values():
            hint.hide()
        for note in self.content.findChildren(QLabel, 'learningNote'): note.hide()
        for flow in self.content.findChildren(FlowLayout): flow.invalidate()
        self.content_layout.invalidate(); self.content.adjustSize()
        self._update_actions()
        self._schedule_anchor_restore()

    def show_history(self):
        self._open_history(True)

    def show_all_history(self):
        self._open_history(False)

    def _open_history(self, wrong_only):
        try:
            with owned_dialog(WrongPracticeHistoryDialog(self.repository, self, wrong_only=wrong_only)) as dialog:
                dialog.practiceRequested.connect(self.start_saved_wrong_practice)
                dialog.exec()
            self._wrong_history_available = None
            self._update_actions()
        except Exception:
            LOGGER.exception("读取历史失败"); QMessageBox.warning(self, "读取失败", "暂时无法读取学习历史，请检查数据目录权限。")

    def start_saved_wrong_practice(self, session_id):
        if not self._save_active_draft(): return
        record = self.repository.get_practice_session(session_id)
        answers = self.repository.get_wrong_answers(session_id)
        if not record or not answers:
            QMessageBox.information(self, "没有错词", "这条记录不存在，或已经没有可练习的错词。")
            return
        try:
            next_session = self.repository.prepare_learning_session(new_saved_wrong_session(record, answers))
        except ValueError as exc:
            QMessageBox.warning(self, "无法恢复记录", str(exc)); return
        self._show_wrong_preview(next_session)

    def retry_current_wrong(self):
        if not self.session or not self.session.submitted or not self.session.wrong_ids:
            return
        if not self.session.database_id:
            self.retry_save_results()
            if not self.session.database_id: return
        if self.session.view_state.get("memory", {}).get("phase") == "relearning":
            remaining = self.repository.consolidation_remaining(self.session)
            if not remaining:
                return
            maximum = min(10, len(remaining))
            if maximum == 1:
                self.start_consolidation(1)
                return
            count, ok = QInputDialog.getInt(self, "再练一组", f"还有{len(remaining)}词可加练。\n这一组练几个？", maximum, 1, maximum)
            if ok:
                self.start_consolidation(count)
            return
        try:
            self._show_wrong_preview(self.repository.prepare_learning_session(new_wrong_session(self.session)))
        except ValueError as exc:
            QMessageBox.information(self, '题目已调整', str(exc))

    def read_current_wrong(self):
        if not self.session or not self.session.submitted or not self.session.wrong_ids:
            return
        if self._wrong_reading:
            return
        offset = self.scroll.verticalScrollBar().value()
        self._record_reading([self.session.article.item_map[i] for i in self.session.wrong_ids], "result-reading")
        with self._rebuild_content_page(retain_result=True):
            self._clear_content()
            self._wrong_reading = True
            self._wrong_reading_scroll = offset
            self.mode_title.setText(f"阅读本轮错词　·　{self.session.wrong_count}词")
            self.progress_text.setText(f"剩余错词：{self.session.wrong_count}\n读完可直接返回\n也可自行开始默写")
            self.progress_text.setToolTip("仅阅读不创建练习、不修改成绩；可以直接返回本轮结果。")
            reading = PracticeSession(self.session.article, self.session.mode, self.session.wrong_ids)
            try:
                reading = self.repository.prepare_learning_session(reading)
            except ValueError:
                self.mode_detail.setText('这些旧题已取消挖空或更换目标词。原成绩保留，可从修正后的文章开始新练习。')
                self.mode_detail.show()
                reading = None
            if reading is not None and reading.article.metadata.get('learning_revision_display'):
                self.mode_detail.setText('当前阅读修正版；返回结果仍保留当时题目与成绩。'); self.mode_detail.show()
            if reading is None:
                self._update_actions(); return
            targets = set(reading.target_ids)
            for items in group_items_by_sentence(reading):
                first = items[0]
                card, flow = self._paragraph_card()
                text, start, end = first.paragraph_text, first.sentence_start, first.sentence_end
                card.setProperty("paragraphText", text[start:end])
                cursor = start
                for item in sorted(items, key=lambda item: item.start):
                    self._add_text(flow, text[cursor:item.start])
                    before = flow.count()
                    self._add_text(flow, text[item.start:item.end], target=item.id in targets)
                    for index in range(before, flow.count()):
                        flow.itemAt(index).widget().setStyleSheet("color:#075cab;background:#edf5ff;font-weight:700;")
                    cursor = item.end
                self._add_text(flow, text[cursor:end])
                self._add_learning_notes(flow, items)
                self.content_layout.addWidget(card)
            self.content_layout.addStretch()
        self.scroll.verticalScrollBar().setValue(0)
        self._apply_zoom_to_content(); self._update_actions()

    def return_from_wrong_reading(self):
        if self._wrong_reading and self.session is not None:
            offset = self._wrong_reading_scroll
            if not self._restore_result_page(offset):
                self._activate_session(self.session, restore_scroll=offset)

    def _start_after_wrong_reading(self):
        self.retry_current_wrong()
        if self.pending_review_session is not None:
            self.start_full_practice()

    def return_to_consolidation_origin(self):
        if not self.session or not self.session.submitted or not self.session.database_id:
            return
        snapshot = self.session.view_state.get("consolidation_origin")
        if not self.session.view_state.get("consolidation") or not isinstance(snapshot, dict):
            return
        try:
            from src.services.serialization import session_from_dict
            origin = session_from_dict(snapshot)
            if not origin.submitted or not origin.database_id or origin.view_state.get("consolidation"):
                raise ValueError("原轮结果无效")
            self._activate_session(origin)
        except Exception:
            LOGGER.exception("恢复原轮结果失败")
            QMessageBox.warning(self, "暂时无法返回", "原轮结果暂时无法打开；当前成绩保留，可从练习历史查看。")

    def start_consolidation(self, limit=10):
        if not self.session or not self._save_active_draft():
            return
        try:
            self.session.view_state["scroll"] = (self._wrong_reading_scroll if self._wrong_reading
                                                 else self.scroll.verticalScrollBar().value())
            session = self.repository.create_consolidation(self.session, limit)
            if session is None:
                self.statusBar().showMessage("本轮没有可再练的词。剩余错词会在后续复习中返回，无需今天反复刷。", 7000)
                return
            self._drafts[session.session_key] = session
            self._activate_session(session)  # No answer preview; feedback follows submission.
            self.statusBar().showMessage("小组再练：先遮住英文独立作答，提交后核对。每词今天最多加练一次，不重复占每日名额。", 9000)
        except Exception:
            LOGGER.exception("开始小组再练失败")
            QMessageBox.warning(self, "暂时无法开始", "未能保存小组练习，请稍后重试；原成绩保留。")

    def configure_consolidation(self):
        value, ok = QInputDialog.getInt(self, "小组再练提醒", "建议隔多少分钟再练？\n只是文字提醒，不强制等待，也不改变记忆算法。",
                                       int(self.repository.get_setting("consolidation_suggest_minutes", 10)), 0, 120)
        if ok:
            self.repository.set_setting("consolidation_suggest_minutes", value)
            self._update_actions()

    def show_wrong_words(self):
        try:
            with owned_dialog(WrongWordsDialog(self.repository, self)) as dialog:
                dialog.practiceRequested.connect(self.start_long_term_practice)
                dialog.exec()
            self._refresh_daily_review()
        except Exception:
            LOGGER.exception("读取错词本失败"); QMessageBox.warning(self, "读取失败", "暂时无法读取错词本，请检查数据目录权限。")

    def clear_wrong_book(self):
        if confirm_clear_wrong_words(self.repository, self):
            self._refresh_daily_review()
            self.statusBar().showMessage("错词本已清空；学习历史和原 Word 已保留。", 5000)

    def configure_review_limit(self):
        try:
            status = self.repository.continuity_status()
            with owned_dialog(ReviewQuantityDialog(*self.repository.review_capacity(today=True), status, self)) as dialog:
                if dialog.exec() != QDialog.Accepted:
                    return
                result = self.repository.configure_review_capacity(dialog.total.value(), dialog.old.value(),
                                                                  today_only=dialog.today_only.isChecked())
                today_only = dialog.today_only.isChecked()
                scope = "仅今天" if dialog.today_only.isChecked() else "今天及以后"
            self._refresh_daily_review()
            total, old = result["budgets"]
            text = f"设置已保存（{scope}）。\n今天最多{total}词，其中最多重新学{old}个旧词。"
            if result["started"]:
                text += f"\n新增{result['added']}个任务，原任务和输入保留。\n已开始任务不因降低上限而减少。"
                if not today_only:
                    text += "较小默认值从明天生效。"
            else:
                text += "\n今天生效；已按新数量更新未开始的任务。"
            QMessageBox.information(self, "每日复习数量", text)
        except Exception:
            LOGGER.exception("修改每日复习数量失败")
            QMessageBox.warning(self, "设置失败", "未能更新复习数量，请稍后重试。")

    def append_daily_review(self):
        try:
            status = self.repository.continuity_status()
            if not status["waiting"]:
                QMessageBox.information(self, "今天再加一些", "目前没有其他已到期的词可添加；已有任务不变。")
                return
            with owned_dialog(ReviewQuantityDialog(20, 20 if status["old_waiting"] else 0, status, self, append=True)) as dialog:
                if dialog.exec() != QDialog.Accepted:
                    return
                added = self.repository.append_review_capacity(dialog.total.value(), dialog.old.value())
            self._refresh_daily_review()
            self.statusBar().showMessage(f"已追加{added}个今日任务；原任务和输入保留，日常默认数量不变。", 8000)
        except Exception:
            LOGGER.exception("追加今日任务失败")
            QMessageBox.warning(self, "追加失败", "未能追加任务，原任务和输入仍保留。")

    def _refresh_daily_review(self):
        try:
            from src.services.review_continuity import status_text
            count = len(self.repository.today_review_ids())
            self.actions["今日复习"].setText(f"今日复习（{count}）")
            status = self.repository.continuity_status()
            planned = status['completed'] + status['remaining']
            self.actions["今日复习"].setToolTip(f"今天安排{planned}词，每日最多{status['total_limit']}词。\n"
                "先安排当天到期复习，剩余名额再加入旧词；未来数量只作提醒。\n"
                + status_text(status)
                + "\n可在“文章与设置 → 每日复习数量”调整上限。")
            if self._review_home_visible:
                self._show_review_home(status)
            return status
        except Exception:
            LOGGER.exception("读取今日复习失败")
            self.actions["今日复习"].setText("今日复习（暂不可用）")
            self.actions["今日复习"].setToolTip("暂时无法读取复习状态，请稍后重试。")

    def start_daily_review(self):
        if not self._save_active_draft():
            return
        try:
            from src.services.daily_review import new_daily_session
            ids = self.repository.today_review_ids(start=True)
            self._refresh_daily_review()
            if not ids:
                from src.services.review_continuity import status_text
                status = self.repository.continuity_status()
                text = status_text(status)
                if status["waiting"]:
                    text += "\n需要继续时，点“今日复习”右侧的小箭头 → 今天再加一些。"
                QMessageBox.information(self, "今日复习", text)
                return
            day = self.repository.today().isoformat()
            pending = self.repository.pending_today_review_answers(day)
            draft = next((s for s in self._drafts.values()
                          if s.mode == PracticeMode.DAILY_REVIEW
                          and not s.submitted and s.view_state.get("daily_review_day") == day
                          and any(s.article.item_map[i].normalized_answer in pending
                                  for i in s.target_ids)), None)
            if draft is not None:
                if self._sync_daily_draft(draft):
                    self._activate_session(draft)
                return
            records = self.repository.get_review_examples(ids)
            if not records:
                raise ValueError('今天剩余旧词的题目已取消挖空或更换目标词，暂时没有可用例句。请先检查“编辑学习内容”；原记忆和今日计划保留。')
            if len(records) < len(ids):
                QMessageBox.information(self, '部分题目已调整',
                    f'{len(ids)-len(records)}个旧词暂时没有可用题目，本次先练其余词。\n请检查被取消或更换的挖空；这些词不会被标记为已完成。')
            session = new_daily_session(records, day)
            self._activate_session(session)
            self._save_active_draft()
        except Exception as exc:
            LOGGER.exception("开始今日复习失败")
            QMessageBox.warning(self, "无法开始今日复习", str(exc))

    def _protected_documents(self):
        return self.repository.list_article_paths() + [str(p) for p in self.library.directory.glob("*.docx")]

    def _library_article(self):
        article = next((a for a in self.articles if self.current_article and a.source_path == self.current_article.source_path), None)
        return self._materialize_article(article)

    def _materialize_article(self, article):
        if not isinstance(article, ArticleSummary):
            return article
        try:
            loaded = self.library.bind_copy(self.repository.load_article(article.source_path))
            self.articles = [loaded if a.source_path == article.source_path else a for a in self.articles]
            return loaded
        except Exception:
            LOGGER.exception("读取文章快照失败")
            self.statusBar().showMessage("这篇文章暂时无法读取，记录仍保留。请重新导入或检查日志。", 10000)
            return None

    def start_long_term_practice(self, ids):
        if not self._save_active_draft(): return
        try:
            records = self.repository.get_review_examples(ids)
            if not records: raise ValueError("没有可用例句，请先完成一次整篇练习。")
            self._show_wrong_preview(new_long_term_session(records))
        except Exception as exc:
            LOGGER.exception("开始长期错词练习失败")
            QMessageBox.warning(self, "无法开始练习", str(exc))

    def export_current_worksheet(self):
        from src.services.exporter import export_worksheet
        article = self._library_article()
        if not article:
            return
        default = Path(self.current_article.source_path).with_name(f"{Path(self.current_article.filename).stem}_默写.docx")
        path, _ = QFileDialog.getSaveFileName(self, "导出默写纸", str(default), "Word 文档 (*.docx)")
        if path:
            try:
                export_worksheet(article, path, self._protected_documents())
                QMessageBox.information(self, "导出成功", f"默写纸已保存：\n{path}\n\n原始 Word 未被修改。")
            except Exception as exc:
                LOGGER.exception("导出默写纸失败"); QMessageBox.critical(self, "导出失败", str(exc))

    def export_current_answer(self):
        from src.services.exporter import export_answer
        article = self._library_article()
        if not article:
            return
        default = Path(self.current_article.source_path).with_name(f"{Path(self.current_article.filename).stem}_答案.docx")
        path, _ = QFileDialog.getSaveFileName(self, "导出答案版", str(default), "Word 文档 (*.docx)")
        if path:
            try:
                export_answer(article, path, self._protected_documents())
                QMessageBox.information(self, "导出成功", f"答案版已保存：\n{path}")
            except Exception as exc:
                LOGGER.exception("导出答案版失败"); QMessageBox.critical(self, "导出失败", str(exc))

    def _update_progress(self):
        if not self.session:
            return
        filled, total = self.session.filled, self.session.total
        unfilled = total - filled
        percentage = round(filled * 100 / total) if total else 0
        self.progress.setFormat("本轮已提交" if self.session.submitted else "填写进度 %p%")
        self.progress.setValue(percentage)
        self.progress_text.setText(f"重点词：{total}\n已填写：{filled}\n未填写：{unfilled}")
        self.progress_text.setToolTip("")
        if self._daily_progress_detail:
            self.progress_text.setText(self.progress_text.text() + '\n' + self._daily_progress_detail)
        if not unfilled and not self.session.submitted:
            self.progress_text.setText(self.progress_text.text() + "\n已填完，请检查后手动提交。")
        if not self.session.submitted:
            self.score_text.setText("默写进行中\n提交后统一批改")
            self.score_text.setToolTip("")

    def _set_daily_review_detail(self):
        """Display saved selection reasons, without reclassifying or refilling tasks."""
        day = self.session.view_state.get('daily_review_day')
        if not day:
            return
        targets = {self.session.article.item_map[i].normalized_answer for i in self.session.target_ids}
        with self.repository.connection() as db:
            rows = db.execute('''SELECT w.normalized_answer,i.selection_reason FROM daily_review_items i
                JOIN wrong_words w ON w.id=i.word_id WHERE i.day=?''', (day,)).fetchall()
        reasons = [r['selection_reason'] for r in rows if r['normalized_answer'] in targets]
        returning, old = reasons.count('scheduled_return'), reasons.count('old_intake')
        if returning + old == len(targets):
            self._daily_progress_detail = f'到期复习：{returning}\n加入旧词：{old}'
        if day == self.repository.today().isoformat():
            status = self.repository.continuity_status()
            total = status['completed'] + status['remaining']
            self.mode_detail.setText(f"今日安排 {total} 词 · 每日最多 {status['total_limit']} 词")
            self.mode_detail.setToolTip('旧词上限包含在每日总量内。先安排当天到期复习，剩余名额再加入旧词；未来数量只作提醒。')
            self.mode_detail.show()

    def _render_current(self):
        if self.session:
            full = self.session.mode == PracticeMode.FULL_ARTICLE
            self._render_practice(full)
        elif self.current_article:
            self.show_preview()

    def _apply_zoom_to_content(self):
        self.reading_selection.end_drag()
        self._remember_layout_anchor()
        self._update_reading_margins()
        updates_enabled = self.content.updatesEnabled()
        self.content.setUpdatesEnabled(False)
        try:
            self.zoom_label.setText(f"{self.zoom_percent}%")
            # Text/input sizes are applied below. Also changing the container
            # font would first propagate a redundant font change to every child.
            for widget in self.content.findChildren(QWidget):
                if isinstance(widget, (QLabel, AnswerLineEdit)):
                    style = re.sub(r"font-size\s*:[^;]+;?", "", widget.styleSheet())
                    size = (15 if widget.objectName() == 'learningNote' else 24 if widget.property("articleHeading")
                            else 18 if widget.property("flowAnswerHint") else 20)
                    updated_style = style + f"font-size:{round(size * self.zoom_percent / 100)}px;"
                    if widget.property("articleHeading") and "font-weight" not in updated_style:
                        updated_style += "font-weight:600;"
                    if widget.styleSheet() != updated_style:
                        widget.setStyleSheet(updated_style)
                if isinstance(widget, AnswerLineEdit):
                    widget.setProperty("flowScale", self.zoom_percent / 100)
                    widget.update_preferred_width()
                elif widget.property("flowAnswerHint"):
                    widget.setProperty("flowWidthLimit", round(280 * self.zoom_percent / 100))
                elif widget.objectName() == 'learningNote':
                    widget.setProperty("flowWidthLimit", round(420 * self.zoom_percent / 100))
            for flow in self.content.findChildren(FlowLayout):
                flow._v_spacing = round(10 * self.zoom_percent / 100)
                flow.invalidate()
            self.content_layout.invalidate()
            self.content.adjustSize()
        finally:
            self.content.setUpdatesEnabled(updates_enabled)
        self._schedule_anchor_restore()

        self.reading_selection.bind()

    def _can_translate_reading(self):
        return (not self._review_home_visible and self.current_article is not None
                and (self.session is None or self.session.submitted))

    def _translate_reading_selection(self, widget, point):
        if not self._can_translate_reading() or not self.reading_selection.selected_text():
            return
        # No dictionary, worker, popup or networking module is loaded on startup.
        if self._reading_translation is None:
            from src.ui.reading_translation import ReadingTranslation
            self._reading_translation = ReadingTranslation(self, self._can_translate_reading)
        self._reading_translation.show_selection(self.reading_selection, point)

    def _clear_content(self):
        self._discard_result_page()
        self._reset_content_state()
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _reset_content_state(self):
        if self._reading_translation is not None:
            self._reading_translation.dismiss()
        self.reading_selection.reset()
        self._review_home_visible = False
        self._wrong_reading = False
        self._daily_progress_detail = ""
        self.mode_detail.clear(); self.mode_detail.hide()
        self.mode_title.setToolTip("")
        self._cancel_pending_follow()
        self._content_generation += 1
        self._last_focused_answer = None
        self._answers_visible = False
        self.answer_edits = {}
        self.answer_hints = {}
        self._paragraph_items = {}

    def _update_actions(self):
        has_article = self.current_article is not None
        library_article = self._library_article()
        active = self.session is not None and not self.session.submitted
        submitted_wrong = self.session is not None and self.session.submitted and bool(self.session.wrong_ids)
        self.actions["开始默写"].setEnabled(self.pending_review_session is not None or library_article is not None or self._wrong_reading)
        self.actions["开始默写"].setText("开始默写")
        for name in ("原文", "导出默写纸", "导出答案版"):
            self.actions[name].setEnabled(library_article is not None)
        self.export_button.setEnabled(True)
        self.actions["提交批改"].setEnabled(active)
        self.actions["显示答案"].setEnabled(active)
        self.actions["显示答案"].setText("隐藏答案" if active and self._answers_visible else "显示答案")
        try:
            if self._wrong_history_available is None:
                self._wrong_history_available = self.repository.has_wrong_history()
            history_available = self._wrong_history_available
        except Exception:
            history_available = False
        self.actions["历史错词练习"].setEnabled(history_available)
        self._more_toolbar_entries["历史错词练习"].setEnabled(history_available)
        in_library = has_article and any(a.source_path == self.current_article.source_path for a in self.articles)
        selected_count = len(self._selected_article_items())
        self.remove_article_button.setEnabled(selected_count > 0)
        self.remove_article_button.setText(f"移除选中 {selected_count} 篇" if selected_count > 1 else "从列表移除")
        importing = bool(self._import_worker and self._import_worker.isRunning())
        for name in ("导入文章", "导入文件夹"): self.actions[name].setEnabled(not importing)
        for name in ("恢复学习备份…", "导出学习备份…"):
            self.actions[name].setEnabled(not importing)
        self.import_button.setEnabled(not importing)
        for name in ("本篇识别规则",):
            if name in self.actions: self.actions[name].setEnabled(in_library and not importing)
        self.actions['编辑学习内容'].setEnabled(has_article and not importing)
        if self.session is not None and self.session.submitted and not self.session.database_id:
            primary_text, primary_enabled = "重试保存成绩", True
            primary_tip = "重新保存已批改的成绩，不会重复累计"
        elif self.pending_review_session is not None:
            primary_text, primary_enabled = "开始错词默写", True
            primary_tip = "将当前错词原句中的目标词变为输入框"
        elif active:
            primary_text, primary_enabled = "提交批改", True
            primary_tip = "统一批改并锁定本轮答案（Ctrl+Enter）"
        elif submitted_wrong:
            if self.session.view_state.get("memory", {}).get("phase") == "relearning":
                remaining = self.repository.consolidation_remaining(self.session)
                primary_text, primary_enabled = ("再练一组" if remaining else "本轮已结束"), bool(remaining)
                minutes = self.repository.get_setting("consolidation_suggest_minutes", 10)
                primary_tip = f"可休息约{minutes}分钟后再练最多10词；不强制等待，每词每天最多加练一次。"
            else:
                primary_text, primary_enabled = "开始错词默写" if self._wrong_reading else "只练本轮错词", True
                primary_tip = "只练习本轮仍然答错的词"
        elif self.session is not None and self.session.submitted:
            primary_text, primary_enabled = "本轮已全部正确", False
            primary_tip = "本轮已经完成"
        elif self._review_home_visible:
            status = self._review_home_status
            primary_enabled = bool(status['remaining'])
            primary_text = '开始今日复习' if primary_enabled else '今日已完成' if status['completed'] else '今日暂无任务'
            primary_tip = '进入今天尚未提交的任务；以前的草稿可从“未完成练习”手动打开。'
        else:
            primary_text = "开始默写"
            primary_enabled = library_article is not None
            primary_tip = "开始当前文章的整篇默写（Ctrl+2）"
        self.primary_action_button.setText(primary_text)
        self.primary_action_button.setEnabled(primary_enabled)
        self.primary_action_button.setToolTip(primary_tip)
        self.read_wrong_button.setVisible(submitted_wrong and not self._wrong_reading)
        self.reading_back_button.setVisible(self._wrong_reading)
        self.return_result_button.setVisible(bool(self.session and self.session.submitted
            and self.session.database_id and self.session.view_state.get("consolidation_origin")) and not self._wrong_reading)
        preview = self.session is None and self.pending_review_session is None and not self._review_home_visible
        self.progress.setVisible(not preview and not self._wrong_reading)
        self.score_text.setVisible(not preview and not self._wrong_reading)
        self.shortcut_hint.setText(("Enter  下一词\nShift+Enter  上一词\nShift+Backspace  清空\n" if active else "")
                                  + "Ctrl+滚轮  缩放\nF8  切换专注模式")

    def _queue_save(self, *_):
        if self.session and not (self.session.submitted and self.session.database_id): self._autosave.start()

    def _queue_view_save(self, *_):
        # Scrolling must not postpone the more urgent answer-save timer.
        if self.session and not (self.session.submitted and self.session.database_id):
            self._view_save.start()

    @staticmethod
    def _draft_state(session):
        # Active articles are immutable snapshots; grading changes are included below.
        return (id(session.article), tuple(session.target_ids), tuple(sorted(session.answers.items())),
                session.mode, session.viewed_answer, session.submitted, session.submitted_at,
                tuple((r.item_id, r.user_answer, r.correct_answer, r.is_correct) for r in session.results),
                json.dumps(session.view_state, sort_keys=True, ensure_ascii=False))

    def _save_active_draft(self):
        if self._closed_for_restore:
            return True
        self._autosave.stop()
        self._view_save.stop()
        session = self.session
        if not session or (session.submitted and session.database_id): return True
        focus = QApplication.focusWidget()
        if isinstance(focus, AnswerLineEdit) and focus.item_id in self.answer_edits:
            session.view_state["focus"] = focus.item_id
        elif self._last_focused_answer in self.answer_edits:
            session.view_state["focus"] = self._last_focused_answer
        pending = self._pending_view_restore
        session.view_state["scroll"] = (pending[2] if pending is not None
            and pending[:2] == (self._content_generation, session.session_key)
            else self.scroll.verticalScrollBar().value())
        state = self._draft_state(session)
        if self._saved_draft_states.get(session.session_key) == state:
            return True
        try:
            self.repository.save_draft(session)
            self._saved_draft_states[session.session_key] = state
            self._drafts.pop(session.session_key, None)
            self._drafts = {session.session_key: session, **self._drafts}
            retained = self.repository.draft_keys()
            self._drafts = {key: value for key, value in self._drafts.items() if key in retained}
            self._saved_draft_states = {key: value for key, value in self._saved_draft_states.items() if key in retained}
            self._update_actions()
            return True
        except Exception:
            LOGGER.exception("自动保存失败")
            self.statusBar().showMessage("自动保存失败：请勿关闭窗口，检查数据目录权限后重试。")
            return False

    def _restore_view(self, key, generation=None, revision=None, scroll=None):
        if not isValid(self) or self._closed_for_restore:
            return
        if not self.session or self.session.session_key != key: return
        if generation is not None and generation != self._content_generation: return
        if revision is not None and revision != self._view_restore_revision: return
        self._layout_timer.stop()
        self._layout_anchor = None
        self._pending_view_restore = (self._content_generation, key,
                                      self.session.view_state.get("scroll", 0) if scroll is None else scroll)
        item_id = self.session.view_state.get("focus")
        blank = next((edit for item_id, edit in self.answer_edits.items()
                      if not self.session.answers.get(item_id, "").strip()), None)
        edit = self.answer_edits.get(item_id) or blank or next(iter(self.answer_edits.values()), None)
        if scroll is None:
            if edit: edit.setFocus()
            if edit: self._queue_answer_follow(edit.item_id)
        self._restore_layout_anchor()

    def _activate_session(self, session, *, restore_scroll=None):
        self._cancel_article_switch()
        self.pending_review_session = None
        self.session = session
        self._preview_session_key = None
        self.library.bind_copy(session.article)
        self.current_article = session.article
        self.article_list.blockSignals(True)
        match = next((i for i, a in enumerate(self.articles) if a.source_path == session.article.source_path), -1)
        self.article_list.setCurrentRow(match)
        self.article_list.blockSignals(False)
        self._completion_notified = session.unfilled == 0
        self._render_practice(session.mode == PracticeMode.FULL_ARTICLE, restore_scroll)

    def _sync_daily_draft(self, session):
        day = session.view_state.get("daily_review_day")
        if session.mode != PracticeMode.DAILY_REVIEW or session.submitted or day != self.repository.today().isoformat():
            return True
        pending = self.repository.pending_today_review_answers(day)
        remaining = [item_id for item_id in session.target_ids
                     if session.article.item_map[item_id].normalized_answer in pending]
        if not remaining:
            QMessageBox.information(self, "今日复习", "这轮复习已通过其他练习完成，原草稿仍保留。")
            return False
        if len(remaining) != len(session.target_ids):
            session.target_ids = remaining
            session.answers = {item_id: answer for item_id, answer in session.answers.items() if item_id in remaining}
            session.article.filename = f"今日复习：{len(remaining)}个"
            self.repository.save_draft(session)
            self._saved_draft_states[session.session_key] = self._draft_state(session)
        return True

    def resume_draft(self):
        if not self._save_active_draft(): return
        try:
            records = self.repository.unfinished_practices()
        except Exception:
            LOGGER.exception("读取未完成练习失败")
            QMessageBox.warning(self, "无法读取", "未完成练习暂时无法读取，原草稿未被删除。")
            return
        if not records:
            QMessageBox.information(self, "未完成练习", "当前没有未完成的练习草稿。")
            return
        with owned_dialog(UnfinishedPracticeDialog(records, self)) as dialog:
            if dialog.exec() == QDialog.Accepted and dialog.selected_session is not None:
                chosen = dialog.selected_session
                self._drafts[chosen.session_key] = chosen
                self._activate_session(chosen)

    def retry_save_results(self):
        if not self.session or not self.session.submitted: return
        try:
            self.repository.save_submitted_session(self.session)
            self._refresh_daily_review()
            self._wrong_history_available = None
            self._drafts.pop(self.session.session_key, None)
            self._saved_draft_states.pop(self.session.session_key, None)
            self.progress_text.setText("本轮成绩已保存，答案已锁定。")
            self._update_actions()
        except Exception:
            LOGGER.exception("重试保存成绩失败")
            QMessageBox.warning(self, "保存失败", "记录仍未保存，请检查数据目录空间及权限，再重试。")

    def discard_current_draft(self):
        if not self.session or self.session.submitted: return
        if QMessageBox.question(self, "放弃草稿", "确定删除当前这轮未提交的输入吗？文章和历史记录不会删除。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        try:
            self.repository.delete_draft(self.session.session_key)
            self._drafts.pop(self.session.session_key, None)
            self._saved_draft_states.pop(self.session.session_key, None)
            self._autosave.stop(); self.session = None
            self._view_save.stop()
            self.show_preview()
        except Exception:
            LOGGER.exception("删除草稿失败"); QMessageBox.warning(self, "删除失败", "草稿仍保留，请重试。")

    def edit_default_rules(self):
        with owned_dialog(RulesDialog(self._default_rules(), self)) as dialog:
            if dialog.exec() == QDialog.Accepted:
                try:
                    self.repository.set_setting("rules", asdict(dialog.rules()))
                    self.statusBar().showMessage("默认规则已保存，对之后新导入的文章生效。", 5000)
                except Exception:
                    LOGGER.exception("保存默认规则失败")
                    QMessageBox.warning(self, "保存失败", "默认规则未保存，请检查数据目录后重试。")

    def edit_rules(self):
        if not self.current_article or not self._save_active_draft(): return
        if not self._confirm_edit_answers(): return
        article = self._library_article()
        if not article: return
        try:
            rules = RecognitionRules(**article.metadata.get("rules", {}))
        except (ValueError, TypeError):
            rules = RecognitionRules()
        with owned_dialog(RulesDialog(rules, self)) as dialog:
            if dialog.exec() != QDialog.Accepted: return
            selected_rules = dialog.rules()
        if article.metadata.get("manually_adjusted") and not article.metadata.get('learning_edited'):
            if QMessageBox.question(self, "重新识别", "重新识别会重置本篇的手动挖空调整，继续吗？",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        try:
            updated = self.library.reparse(article, selected_rules)
            self._replace_article(updated)
        except Exception as exc:
            LOGGER.exception("重新识别失败"); QMessageBox.warning(self, "识别失败", str(exc))

    def edit_targets(self):
        self._edit_learning_content(self.current_article)

    def _edit_learning_content(self, article=None, item_id=None, paragraph_index=None, item=None):
        from copy import deepcopy
        from src.models.article import Article, Paragraph
        from src.services.learning_content import prepare_article, context_for
        if article is None: article = self.current_article
        if item is None and article is not None: item = article.item_map.get(item_id)
        if item is None and article is not None and not article.source_path:
            item = next(iter(article.word_items), None)
        source = item.source_path if item else (article.source_path if article else '')
        if article is None: return
        if not self._save_active_draft(): return
        try:
            # Always edit the latest learning material, not the submitted result.
            try:
                editing = self.repository.apply_learning_content(self.repository.load_article(source))
            except ValueError:
                if item is not None:
                    same = [deepcopy(i) for i in article.word_items if i.source_path == source and i.paragraph_index == item.paragraph_index]
                    if not same: same = [deepcopy(item)]
                    editing = Article(source, item.filename, [Paragraph(0, item.paragraph_text, [])], same)
                    editing.metadata['learning_partial'] = True
                    for i in same: i.learning_context = context_for(i); i.paragraph_index = 0
                    editing = self.repository.apply_learning_content(editing)
                else:
                    editing = self.repository.apply_learning_content(article)
            editing = prepare_article(editing)
            focus_id = item_id
            if item is not None:
                key = context_for(item)['key']
                found = next((i for i in editing.word_items if context_for(i)['key'] == key), None)
                if found is None:
                    found = next((i for i in editing.word_items if i.normalized_answer == item.normalized_answer
                                  and i.learning_context.get('paragraph_index') == context_for(item)['paragraph_index']), None)
                focus_id = found.id if found else None
                paragraph_index = found.paragraph_index if found else next((p.index for p in editing.paragraphs
                    if editing.metadata['learning_paragraphs'][str(p.index)]['paragraph_index'] == context_for(item)['paragraph_index']), None)
            active = self.session is not None and not self.session.submitted
            if active and QMessageBox.question(self, '修正学习内容',
                    '编辑时会显示答案。当前输入保留，修改从下一轮生效；看过答案会如实记录。继续吗？',
                    QMessageBox.Yes|QMessageBox.No,QMessageBox.No) != QMessageBox.Yes: return
            visible = {i.normalized_answer for i in editing.word_items}
            if active:
                from src.services.learning_evidence import expose
                ids = [i for i in self.session.target_ids if self.session.article.item_map[i].normalized_answer in visible
                       or self.session.article.item_map[i].source_path == editing.source_path]
                if ids: expose(self.session, ids)
                if not self._save_active_draft(): return
            shown_items = list(editing.word_items)
            if self.session:
                shown_items += [self.session.article.item_map[i] for i in self.session.target_ids
                                if self.session.article.item_map[i].source_path == editing.source_path]
            self._record_reading(shown_items, 'content-editor')
            with owned_dialog(TargetEditorDialog(editing, self, item_id=focus_id, paragraph_index=paragraph_index)) as dialog:
                if dialog.exec() != QDialog.Accepted: return
                count = self.repository.save_learning_content(dialog.original_article, dialog.article)
            if not count: return
            for n, cached in enumerate(self.articles):
                if cached.source_path == source:
                    self.articles[n] = self.library.bind_copy(self.repository.load_article(source))
            self._reload_article_list()
            if self.session is None and self.pending_review_session is None:
                offset = self.scroll.verticalScrollBar().value()
                self.current_article = next((a for a in self.articles if a.source_path == source), editing)
                if self.current_article is editing: self.current_article = self.repository.apply_learning_content(editing)
                self.show_preview(); self.scroll.verticalScrollBar().setValue(offset)
            elif self._wrong_reading:
                parent_offset, reader_offset = self._wrong_reading_scroll, self.scroll.verticalScrollBar().value()
                self._wrong_reading = False; self.read_current_wrong()
                self._wrong_reading_scroll = parent_offset; self.scroll.verticalScrollBar().setValue(reader_offset)
            elif self.pending_review_session is not None:
                try:
                    pending = self.repository.prepare_learning_session(self.pending_review_session)
                    self._show_wrong_preview(pending)
                except ValueError:
                    self.pending_review_session = None
                    self.current_article = self.repository.apply_learning_content(editing)
                    self.show_preview()
            self.statusBar().showMessage('修正已保存，后续新练习使用修正版；当前轮次、旧草稿和历史成绩保留。', 9000)
        except Exception as exc:
            LOGGER.exception('编辑学习内容失败')
            QMessageBox.warning(self, '修改未保存', str(exc))

    def _confirm_edit_answers(self):
        if not self.session or self.session.submitted: return True
        if QMessageBox.question(self, "调整题目", "调整题目将显示原文，当前输入会保存为草稿。继续吗？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return False
        return self._save_active_draft()

    def _replace_article(self, updated):
        self.session = None
        self.pending_review_session = None
        self.articles = [updated if a.source_path == updated.source_path else a for a in self.articles]
        self.current_article = updated
        self._reload_article_list(); self.show_preview()
        self.statusBar().showMessage("调整已保存，新练习使用新题目；旧草稿保持不变。", 7000)

    def _show_wrong_preview(self, session):
        """Show complete target answers before beginning a focused wrong-word round."""
        if session.view_state.get("memory", {}).get("phase") != "relearning":
            session.view_state.get("memory", {})["phase"] = "study"
        preview_key = self._record_reading(
            [session.article.item_map[i] for i in session.target_ids], "correction-preview")
        if preview_key:
            session.view_state.get("memory", {})["pre_round_exposure_key"] = preview_key
        self.session = None
        self.pending_review_session = session
        self.current_article = session.article
        self._clear_content()
        self.mode_title.setText(f"错词原句预览　·　{session.article.filename}")
        target_set = set(session.target_ids)
        for items in group_items_by_sentence(session):
            first = items[0]
            card, flow = self._paragraph_card()
            text = first.paragraph_text
            start_bound, end_bound = first.sentence_start, first.sentence_end
            sentence_items = sorted((item for item in session.article.word_items
                                     if item.paragraph_index == first.paragraph_index
                                     and item.start >= start_bound and item.end <= end_bound),
                                    key=lambda item: item.start)
            cursor = start_bound
            for item in sentence_items:
                if item.start > cursor: self._add_text(flow, text[cursor:item.start])
                self._add_text(flow, text[item.start:item.end], target=item.id in target_set)
                cursor = item.end
            if cursor < end_bound: self._add_text(flow, text[cursor:end_bound])
            self._add_learning_notes(flow, sentence_items)
            card.setProperty("paragraphText", text[start_bound:end_bound])
            self.content_layout.addWidget(card)
        self.content_layout.addStretch()
        self.progress.setValue(0)
        self.progress.setFormat("填写进度 %p%")
        self.progress_text.setText(f"待复习错词：{session.total}\n先阅读带答案的原句，准备好后点击右侧“开始错词默写”。")
        self.score_text.setText("当前是复习预览\n尚未开始答题，不记录成绩")
        self.progress_text.setToolTip(""); self.score_text.setToolTip("")
        self._apply_zoom_to_content(); self._update_actions()

    def _record_reading(self, items, reason):
        from src.services.learning_evidence import utc_now
        from uuid import uuid4
        at = utc_now()
        event_key = reason + ":" + at + ":" + uuid4().hex
        try:
            self.repository.record_exposure([item.normalized_answer for item in items], event_key, at)
            return event_key
        except Exception:
            LOGGER.exception("保存阅读记录失败")
            self.statusBar().showMessage("阅读记录暂未保存；当前轮次仍保留答案查看标记。", 6000)

    def backup_data(self):
        if not self._save_active_draft(): return
        if self._import_worker and self._import_worker.isRunning():
            QMessageBox.information(self, "请稍候", "导入结束后再备份，保证文章副本完整。"); return
        path, _ = QFileDialog.getSaveFileName(self, "导出学习备份", "英语学习备份.zip", "ZIP 备份 (*.zip)")
        if path:
            try:
                if not self._save_window_settings():
                    raise OSError("当前设置尚未保存，请检查数据目录权限后重试")
                backup_library(self.repository, path)
                QMessageBox.information(self, "备份完成", "已备份文章副本、识别调整、草稿、错词、历史和设置。\n需要时使用“导入 → 恢复学习备份”。")
            except Exception as exc:
                LOGGER.exception("备份失败"); QMessageBox.warning(self, "备份失败", str(exc))

    def restore_data(self):
        if self._import_worker and self._import_worker.isRunning():
            QMessageBox.information(self, "请稍候", "导入结束后再恢复，避免正在导入的文章被覆盖。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "恢复学习备份", "", "ZIP 备份 (*.zip)")
        if not path:
            return
        prepared = None
        try:
            prepared = prepare_library_restore(self.repository, path)
            message = (f"确定恢复《{Path(path).name}》吗？\n\n"
                       "恢复会替换当前全部文章、草稿、成绩、错词和设置，不会合并。\n"
                       "当前数据会先自动备份；备份失败则停止恢复。\n"
                       "软件将关闭并在恢复成功后重新启动。")
            if prepared.warnings:
                message += "\n\n兼容说明：\n" + "\n".join(prepared.warnings)
            if QMessageBox.question(self, "恢复学习备份？", message,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.pending_library_restore = prepared
            if not self.close():
                self.pending_library_restore = None
                return
            prepared = None  # app.py owns it only after a successfully saved close.
        except Exception as exc:
            LOGGER.exception("校验恢复备份失败")
            QMessageBox.warning(self, "无法恢复备份", f"{exc}\n\n当前学习数据未被替换。")
        finally:
            if prepared is not None:
                prepared.close()

    def _save_window_settings(self):
        try:
            self.repository.set_setting("zoom", self.zoom_percent)
            collapsed = self._focus_layout[1] if self._focus_mode else self.library_collapsed
            toolbar_visible = self._focus_layout[0] if self._focus_mode else not self.main_toolbar.isHidden()
            self.repository.set_setting("library_collapsed", collapsed)
            self.repository.set_setting("toolbar_visible", toolbar_visible)
            if self._last_article_path:
                self.repository.set_setting("last_article_path", self._last_article_path)
            return True
        except Exception:
            LOGGER.exception("保存窗口设置失败")
            return False

    def closeEvent(self, event):
        if self._closed_for_restore:
            event.accept()
            return
        self._cancel_article_switch()
        if self._import_worker and self._import_worker.isRunning():
            self._import_worker.cancel()
            self.statusBar().showMessage("正在结束当前文件的导入，请稍后再次关闭。")
            event.ignore(); return
        if not self._save_active_draft():
            QMessageBox.warning(self, "尚未保存", "当前输入尚未保存，已取消关闭。请检查数据目录权限后再试。")
            event.ignore(); return
        if not self._save_window_settings() and self.pending_library_restore is not None:
            QMessageBox.warning(self, "尚未保存", "当前设置保存失败，已取消恢复。请检查数据目录权限后再试。")
            event.ignore()
            return
        self._cancel_pending_follow()
        for timer in (self._daily_timer, self._zoom_timer, self._layout_timer):
            timer.stop()
        self._closed_for_restore = self.pending_library_restore is not None
        if self._reading_translation is not None:
            self._reading_translation.dismiss()
        self._discard_result_page()
        super().closeEvent(event)
