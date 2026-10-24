from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
    QLineEdit, QPlainTextEdit, QSplitter, QWidget, QStackedWidget, QInputDialog)

from src.models.rules import RecognitionRules
from src.ui.widgets.text_menus import TextEdit, TextLineEdit, PlainTextEdit
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
                      "宽松模式中少数无格式短语边界不明时，可用“编辑学习内容”合并。")
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


class _ArticlePreview(TextEdit):
    clicked = Signal(int)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not self.textCursor().hasSelection():
            self.clicked.emit(self.cursorForPosition(event.position().toPoint()).position())


class TargetEditorDialog(QDialog):
    """One reading surface; local edits are previewed before the outer save."""
    def __init__(self, article, parent=None, item_id=None, paragraph_index=None):
        super().__init__(parent)
        from src.services.learning_content import prepare_article
        self.article = prepare_article(article)
        self.original_article = deepcopy(self.article)
        self._partial = any(not o.get('complete_targets', True) for o in self.article.metadata['learning_paragraphs'].values())
        self._undo = []
        self._selected_id = None
        self._paragraph_index = paragraph_index
        self._loading = False
        self.setWindowTitle("编辑学习内容 · " + article.filename)
        self.resize(1160, 760); self.setMinimumSize(800, 520)
        self.setStyleSheet("""
            QDialog{background:#f4f6f9;color:#213249;}
            QLabel{background:transparent;} QLabel#editorTitle{font-size:20px;font-weight:700;}
            QLabel#editorHint{color:#718096;font-size:13px;}
            QTextEdit,QPlainTextEdit,QLineEdit{background:white;border:1px solid #dfe5ed;border-radius:7px;padding:8px;selection-background-color:#cfe4ff;selection-color:#143b61;}
            QTextEdit:focus,QPlainTextEdit:focus,QLineEdit:focus{border-color:#8ab6e4;}
            QPushButton{background:white;border:1px solid #dce3ec;border-radius:7px;padding:9px 16px;}
            QPushButton:hover{background:#edf4fc;border-color:#b6cde8;}
            QPushButton:disabled{color:#a4aebb;}
            QPushButton#saveContent{background:#1672cf;color:white;border:0;font-weight:600;}
        """)
        layout = QVBoxLayout(self); layout.setContentsMargins(20, 18, 20, 16); layout.setSpacing(12)
        title = QLabel("编辑学习内容"); title.setObjectName('editorTitle'); layout.addWidget(title)
        hint = QLabel("点击蓝色词语修改提示；选中英文可设为挖空。修改用于后续新练习，原 Word 和已有成绩保留。")
        hint.setObjectName('editorHint'); hint.setWordWrap(True); layout.addWidget(hint)
        if self._partial:
            hint.setText('源文章未在列表中，当前仅修正历史快照中的释义和备注。恢复文章后可修改正文和挖空；原成绩保留。')
        row = QHBoxLayout()
        self.add_button = QPushButton("选中英文 → 设为挖空"); self.add_button.clicked.connect(self._add)
        self.edit_paragraph_button = QPushButton("编辑本段"); self.edit_paragraph_button.clicked.connect(self._edit_paragraph)
        self.undo_button = QPushButton("撤销"); self.undo_button.clicked.connect(self._undo_change)
        for button in (self.add_button, self.edit_paragraph_button, self.undo_button): row.addWidget(button)
        row.addStretch(); layout.addLayout(row)
        splitter = QSplitter(Qt.Horizontal)
        self.pages = QStackedWidget()
        self.text = _ArticlePreview(); self.text.setReadOnly(True)
        self.text.setStyleSheet(self.text.styleSheet() + 'font-size:18px;')
        self.text.document().setDocumentMargin(18)
        self.text.clicked.connect(self._clicked)
        self.text.selectionChanged.connect(self._selection_changed)
        self.pages.addWidget(self.text)
        paragraph_page = QWidget(); paragraph_layout = QVBoxLayout(paragraph_page); paragraph_layout.setContentsMargins(0,0,0,0)
        paragraph_layout.addWidget(QLabel("修改当前段落的英文或中文；应用后请检查蓝色挖空范围。"))
        self.paragraph_edit = PlainTextEdit(); self.paragraph_edit.setStyleSheet('font-size:18px;')
        paragraph_layout.addWidget(self.paragraph_edit)
        paragraph_row = QHBoxLayout()
        self.apply_paragraph_button = QPushButton("应用到预览"); self.apply_paragraph_button.clicked.connect(self._apply_paragraph)
        paragraph_row.addWidget(self.apply_paragraph_button)
        cancel = QPushButton("取消本段修改"); cancel.clicked.connect(lambda: self._leave_paragraph())
        paragraph_row.addWidget(cancel); paragraph_row.addStretch(); paragraph_layout.addLayout(paragraph_row)
        self.pages.addWidget(paragraph_page); splitter.addWidget(self.pages)
        panel = QWidget(); self.target_panel = panel; panel.setMinimumWidth(240); panel.setMaximumWidth(330)
        form = QVBoxLayout(panel); form.setContentsMargins(14, 4, 0, 0); form.setSpacing(12)
        self.location = QLabel(); self.location.setObjectName('editorHint'); form.addWidget(self.location)
        self.target_label = QLabel("点击文章中的词语"); self.target_label.setWordWrap(True)
        self.target_label.setStyleSheet('font-size:20px;font-weight:600;color:#1767b3;'); form.addWidget(self.target_label)
        form.addWidget(QLabel("本句释义")); self.chinese_edit = TextLineEdit(); self.chinese_edit.setPlaceholderText("只修改这个原句中的提示")
        form.addWidget(self.chinese_edit)
        form.addWidget(QLabel("易混备注 · 可不填")); self.note_edit = PlainTextEdit()
        self.note_edit.setPlaceholderText("例如 internal / inherent\n阅读及提交后可见，默写时隐藏")
        self.note_edit.setMaximumHeight(110); form.addWidget(self.note_edit)
        self.hint_button = QPushButton("更新预览"); self.hint_button.clicked.connect(self._apply_hint)
        form.addWidget(self.hint_button)
        self.remove_button = QPushButton("取消此处挖空"); self.remove_button.clicked.connect(self._remove); form.addWidget(self.remove_button)
        self.split_button = QPushButton("拆分短语挖空…"); self.split_button.clicked.connect(self._split); form.addWidget(self.split_button)
        help_text = QLabel("选中更大范围后设为挖空，可合并原有题目。\n\n英文答案取自正文；要更换英文，请编辑本段。")
        help_text.setWordWrap(True); help_text.setObjectName('editorHint'); form.addWidget(help_text); form.addStretch()
        splitter.addWidget(panel); splitter.setSizes([820,280]); layout.addWidget(splitter,1)
        bottom = QHBoxLayout(); self.state_label = QLabel("尚未修改"); self.state_label.setObjectName('editorHint'); bottom.addWidget(self.state_label); bottom.addStretch()
        self.save_button = QPushButton("保存修改"); self.save_button.setObjectName('saveContent'); self.save_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消"); self.cancel_button.clicked.connect(self.reject)
        bottom.addWidget(self.cancel_button); bottom.addWidget(self.save_button); layout.addLayout(bottom)
        for button in self.findChildren(QPushButton): button.setAutoDefault(False)
        self.save_button.setDefault(True)
        if item_id in self.article.item_map:
            self._selected_id = item_id; self._paragraph_index = self.article.item_map[item_id].paragraph_index
        elif self._paragraph_index is None:
            first = next(iter(self.article.word_items), None)
            self._paragraph_index = first.paragraph_index if first else (self.article.paragraphs[0].index if self.article.paragraphs else None)
            self._selected_id = first.id if first else None
        self._render(focus=True)
        if self._partial:
            self.edit_paragraph_button.setEnabled(False); self.remove_button.setEnabled(False); self.split_button.setEnabled(False)

    def _push(self):
        self._undo.append(deepcopy(self.article))
        if len(self._undo)>30: self._undo.pop(0)

    def _render(self, focus=False):
        from src.services.learning_content import note_for
        offset = self.text.verticalScrollBar().value()
        self._loading = True
        self._ranges = []; chunks = []; cursor = 0
        for p in self.article.paragraphs:
            self._ranges.append((cursor, cursor+len(p.text), p.index)); chunks.append(p.text); cursor += len(p.text)+2
        self.text.setPlainText('\n\n'.join(chunks))
        all_text = self.text.toPlainText(); selections=[]
        for item in self.article.word_items:
            base = next(a for a,b,index in self._ranges if index == item.paragraph_index)
            selection = QTextEdit.ExtraSelection(); selection.cursor = self.text.textCursor()
            selection.cursor.setPosition(len(all_text[:base+item.start].encode('utf-16-le'))//2)
            selection.cursor.setPosition(len(all_text[:base+item.end].encode('utf-16-le'))//2,QTextCursor.KeepAnchor)
            selection.format.setBackground(QColor('#c9e1fc' if item.id==self._selected_id else '#edf4fd'))
            selection.format.setForeground(QColor('#145b9c')); selections.append(selection)
        self.text.setExtraSelections(selections)
        item=self.article.item_map.get(self._selected_id)
        self.target_label.setText(item.answer if item else '选中英文可新增挖空')
        self.chinese_edit.setText(item.chinese if item else '')
        self.note_edit.setPlainText(note_for(item) if item else '')
        for widget in (self.chinese_edit,self.note_edit,self.hint_button,self.remove_button): widget.setEnabled(item is not None)
        self.split_button.setEnabled(bool(item and len(item.answer.split())>1))
        if self._partial:
            self.remove_button.setEnabled(False); self.split_button.setEnabled(False)
        self.location.setText(f'第 {self._paragraph_index+1} 段 · 本句修正' if self._paragraph_index is not None else '')
        self.undo_button.setEnabled(bool(self._undo))
        self.state_label.setText(f'预览中 · {len(self._undo)} 步修改，保存后生效' if self._undo else '尚未修改')
        self._loading=False
        self.text.verticalScrollBar().setValue(offset)
        if focus and self._paragraph_index is not None:
            base = next(a for a,b,i in self._ranges if i==self._paragraph_index)
            cur=self.text.textCursor(); cur.setPosition(len(all_text[:base].encode('utf-16-le'))//2)
            self.text.setTextCursor(cur); self.text.ensureCursorVisible()
        self._selection_changed()

    def _selection(self):
        cursor=self.text.textCursor(); raw=self.text.toPlainText().encode('utf-16-le')
        start=len(raw[:cursor.selectionStart()*2].decode('utf-16-le'))
        end=len(raw[:cursor.selectionEnd()*2].decode('utf-16-le'))
        return next(((index,start-a,end-a) for a,b,index in self._ranges if a<=start<end<=b),None)

    def _selection_changed(self):
        if self._loading:return
        self.add_button.setEnabled(not self._partial and self.pages.currentIndex()==0 and self._selection() is not None)

    def _clicked(self, pos):
        raw=self.text.toPlainText().encode('utf-16-le'); pos=len(raw[:pos*2].decode('utf-16-le'))
        location=next(((index,pos-a) for a,b,index in self._ranges if a<=pos<=b),None)
        if not location:return
        index, local=location
        clicked_id=next((i.id for i in self.article.word_items if i.paragraph_index==index and i.start<=local<i.end),None)
        if not self._apply_hint(render=False):return
        self._paragraph_index=index
        self._selected_id=clicked_id
        self._render()

    def _apply_hint(self, checked=False, render=True):
        from src.services.learning_content import edit_hint, note_for
        item=self.article.item_map.get(self._selected_id)
        if not item:return True
        if (item.chinese,note_for(item))==(self.chinese_edit.text().strip(),self.note_edit.toPlainText().strip()):return True
        try:
            candidate=deepcopy(self.article)
            edit_hint(candidate,item.id,self.chinese_edit.text(),self.note_edit.toPlainText())
            self._push(); self.article=candidate
            if render:self._render()
            return True
        except ValueError as exc:
            QMessageBox.information(self,'请检查修改',str(exc)); return False

    def _add(self):
        from src.services.learning_content import add_blank
        selection=self._selection()
        if not selection:return
        old_text=next(p.text for p in self.article.paragraphs if p.index==selection[0])
        if not self._apply_hint(render=False):return
        new_text=next(p.text for p in self.article.paragraphs if p.index==selection[0])
        if old_text != new_text:
            from difflib import SequenceMatcher
            block=next((b for b in SequenceMatcher(None,old_text,new_text,autojunk=False).get_matching_blocks()
                        if b.a<=selection[1] and selection[2]<=b.a+b.size),None)
            if block is None:self._render();return
            selection=(selection[0],selection[1]+block.b-block.a,selection[2]+block.b-block.a)
        try:
            candidate=deepcopy(self.article)
            raw=next(p.text for p in candidate.paragraphs if p.index==selection[0])[selection[1]:selection[2]]
            if any('\u4e00'<=c<='\u9fff' for c in raw):
                from src.services.learning_content import merge_blank, selection_english
                english=selection_english(raw)
                chinese,ok=QInputDialog.getText(self,'合并挖空',f'合并为：{english}\n合并后的本句释义（可留空）')
                if not ok:return
                merge_blank(candidate,*selection,chinese)
            else:
                add_blank(candidate,*selection)
            self._push(); self.article=candidate; self._paragraph_index=selection[0]
            self._selected_id=next((i.id for i in self.article.word_items if i.paragraph_index==selection[0] and i.start>=selection[1] and i.end<=selection[2]),None)
            self._render()
        except ValueError as exc:QMessageBox.information(self,'无法设为挖空',str(exc))

    def _remove(self):
        from src.services.learning_content import cancel_blank
        if not self._selected_id or not self._apply_hint(render=False):return
        self._push(); cancel_blank(self.article,self._selected_id); self._selected_id=None; self._render()

    def _split(self):
        import re
        from src.services.learning_content import cancel_blank, add_blank, edit_hint
        if not self._apply_hint():return
        item=self.article.item_map.get(self._selected_id)
        if not item:return
        raw,ok=QInputDialog.getMultiLineText(self,'拆分短语挖空','每行一处挖空，可写成“英文 | 中文”。\n各行英文须依次组成原短语；不自动翻译。\n原释义：'+item.chinese,'\n'.join(item.answer.split()))
        if not ok:return
        parts=[line.split('|',1) for line in raw.splitlines() if line.strip()]
        if len(parts)<2:return
        cursor=0; ranges=[]
        for part in parts:
            english=part[0].strip(); start=item.answer.find(english,cursor)
            if not english or start<0 or item.answer[cursor:start].strip():
                QMessageBox.information(self,'请检查拆分','各行英文必须按顺序完整覆盖原短语。'); return
            ranges.append((item.start+start,item.start+start+len(english),part[1].strip() if len(part)>1 else ''))
            cursor=start+len(english)
        if item.answer[cursor:].strip():
            QMessageBox.information(self,'请检查拆分','还有英文未包含在拆分范围中。'); return
        candidate=deepcopy(self.article)
        # Remove the old phrase gloss, then add the requested independent hints
        # from right to left so insertion never invalidates an earlier range.
        edit_hint(candidate,item.id,'',''); cancel_blank(candidate,item.id)
        try:
            for start,end,chinese in reversed(ranges):
                add_blank(candidate,item.paragraph_index,start,end)
                child=next(i for i in candidate.word_items if i.paragraph_index==item.paragraph_index and i.start==start)
                edit_hint(candidate,child.id,chinese,'')
        except ValueError as exc:QMessageBox.information(self,'无法拆分',str(exc));return
        self._push();self.article=candidate;self._selected_id=None;self._render()

    def _edit_paragraph(self):
        if not self._apply_hint() or self._paragraph_index is None:return
        p=next(p for p in self.article.paragraphs if p.index==self._paragraph_index)
        self.paragraph_edit.setPlainText(p.text);self.pages.setCurrentIndex(1)
        self.edit_paragraph_button.setEnabled(False);self.add_button.setEnabled(False)
        self.target_panel.setEnabled(False); self.undo_button.setEnabled(False)
        self.paragraph_edit.setFocus()

    def _leave_paragraph(self):
        self.pages.setCurrentIndex(0);self.edit_paragraph_button.setEnabled(True);self._selection_changed()
        self.target_panel.setEnabled(True); self.undo_button.setEnabled(bool(self._undo))

    def _apply_paragraph(self):
        from src.services.learning_content import edit_paragraph
        try:
            candidate=deepcopy(self.article);edit_paragraph(candidate,self._paragraph_index,self.paragraph_edit.toPlainText())
            if candidate!=self.article:self._push();self.article=candidate
            self._leave_paragraph();self._render(focus=True);return True
        except ValueError as exc:QMessageBox.information(self,'请检查正文',str(exc));return False

    def _undo_change(self):
        item=self.article.item_map.get(self._selected_id)
        if item:
            from src.services.learning_content import note_for
            if (item.chinese,note_for(item))!=(self.chinese_edit.text().strip(),self.note_edit.toPlainText().strip()):
                self._render();return
        if self._undo:
            self.article=self._undo.pop();self._leave_paragraph();self._render()

    def accept(self):
        if self.pages.currentIndex()==1 and not self._apply_paragraph():return
        if not self._apply_hint():return
        super().accept()

    def reject(self):
        changed=self.article!=self.original_article or (self.pages.currentIndex()==1 and self.paragraph_edit.document().isModified())
        item=self.article.item_map.get(self._selected_id)
        if item:
            from src.services.learning_content import note_for
            changed=changed or (item.chinese,note_for(item))!=(self.chinese_edit.text().strip(),self.note_edit.toPlainText().strip())
        if changed and QMessageBox.question(self,'取消修改','放弃本次尚未保存的修改？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        super().reject()
