"""Compact dictionary-first reading prototype, isolated from learning storage."""
from html import escape
import re
import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QPushButton,
    QScrollArea, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from src.ui.widgets.flow_layout import FlowLayout
from src.ui.widgets.reading_selection import ReadingSelection, piece
from src.ui.widgets.text_menus import TextLabel, text_menu
from tools.translation_prototype.dictionary import DictionaryClient
from tools.translation_prototype.engine import Query, TranslationClient, TranslationError, prepare_query


ARTICLES=[
    ('校园与日常',[
        'She sat on the bank and watched the river flow.',
        'The bank refused to lend him any money.',
        'Please charge your phone before you leave. They will charge us ten dollars for delivery.',
        'The rules must be fair to every student. We bought fresh fruit at the school fair.',
        'I was utterly exhausted after the long journey.',
        'She looked up the word in a dictionary. He made up an excuse for missing the class.',
        'The more carefully you review, the fewer mistakes you make.',
        'Lily missed the last bus. Instead of complaining, she walked home.',
    ]),
    ('词组与习语',[
        'Please do not spill the beans about the surprise party.',
        'We only see each other once in a blue moon.',
        'Before the interview, she got cold feet and almost went home.',
        'The new laptop cost an arm and a leg.',
        'She told a joke to break the ice at the meeting.',
        'The exam was a piece of cake because she had prepared well.',
        "Do not take your parents' support for granted.",
        'She looked up to her teacher. He ran out of time.',
    ]),
]

STYLE="""
QMainWindow{background:#f4f6f9;}
QWidget{font-family:'Microsoft YaHei UI';font-size:14px;color:#26384b;}
QLabel{background:transparent;}
QLabel#muted{color:#7b8797;font-size:12px;}
QLabel#pageTitle{font-size:23px;font-weight:600;color:#20364d;}
QFrame#card{background:white;border:1px solid #e1e7ef;border-radius:10px;}
QScrollArea{border:none;background:transparent;}
QComboBox,QLineEdit,QPushButton{background:white;border:1px solid #dce3ec;border-radius:7px;padding:7px 11px;}
QPushButton:hover{background:#eef4fb;border-color:#bbd2eb;}
QLineEdit:focus{border-color:#8eb5dd;}
QStatusBar{color:#7b8797;font-size:12px;}
"""

from src.ui.widgets.translation_popup import TranslationPopup


class PrototypeLabel(TextLabel):
    def __init__(self,text,owner):super().__init__(text);self.owner=owner
    def contextMenuEvent(self,event):
        menu=text_menu(self);selected=self.owner.selection.selected_text()
        if selected:
            menu.addSeparator();action=menu.addAction('翻译')
            action.setEnabled(self.owner.mode.currentIndex()!=3 and bool(re.search('[A-Za-z]',selected)))
            action.triggered.connect(lambda:self.owner.translate_current(event.globalPos()))
        try:menu.exec(event.globalPos())
        finally:menu.deleteLater()
        event.accept()


class PrototypeWindow(QMainWindow):
    def __init__(self,client=None,dictionary_client=None):
        super().__init__();self.setWindowTitle('随读词典 · 独立试用（不修改学习软件）');self.resize(1040,720)
        self.setStyleSheet(STYLE)
        self.client=client or TranslationClient(self)
        self.dictionary=dictionary_client or DictionaryClient(self)
        self.client.completed.connect(self._completed);self.dictionary.completed.connect(self._lookup_completed)
        self.popup=TranslationPopup(self);self.query=None;self.online_query=None;self._lookup=None
        self._token=None;self._lookup_token=None;self._point=QPoint(500,240);self._custom=None
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central)
        layout.setContentsMargins(26,22,26,18);layout.setSpacing(12)
        head=QHBoxLayout();title=QLabel('随读词典');title.setObjectName('pageTitle');head.addWidget(title)
        badge=QLabel('独立试用');badge.setObjectName('muted');head.addWidget(badge);head.addStretch()
        paste=QPushButton('粘贴英文');paste.clicked.connect(self.paste_text);head.addWidget(paste);layout.addLayout(head)
        tip=QLabel('划选单词或词组，右键“翻译”。本地优先，不用注册。');tip.setObjectName('muted');layout.addWidget(tip)
        row=QHBoxLayout();self.article=QComboBox();self.article.addItems([a[0] for a in ARTICLES]);self.article.setFixedWidth(180)
        self.article.currentIndexChanged.connect(self._article_changed)
        self.mode=QComboBox();self.mode.addItems(['原文阅读','错句阅读（演示）','提交结果（演示）','独立默写（翻译禁用）'])
        self.mode.currentIndexChanged.connect(self.rebuild);row.addWidget(self.article);row.addWidget(self.mode);row.addStretch();layout.addLayout(row)
        self.online_allowed=QCheckBox(central);self.online_allowed.hide();self.online_allowed.toggled.connect(self._privacy_changed)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);layout.addWidget(self.scroll,1)
        self.content=QWidget();self.content_layout=QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0,0,6,0);self.content_layout.setSpacing(14);self.scroll.setWidget(self.content)
        self.selection=ReadingSelection(self.content,self.scroll)
        self.input=QLineEdit();self.input.setPlaceholderText('可在这里输入，测试查询时的响应（不保存）');layout.addWidget(self.input)
        self.statusBar().showMessage('本地查词不联网 · 不读取学习数据 · 不改变默写与复习记录')
        self.rebuild()

    def cancel_queries(self):self.client.cancel();self.dictionary.cancel()
    def _privacy_changed(self,allowed):
        if not allowed:self.client.cancel();self.popup.network.hide()
    def _article_changed(self,*_):self._custom=None;self.rebuild()
    def paste_text(self):
        text,ok=QInputDialog.getMultiLineText(self,'临时试读','只在本窗口显示，不保存；本地查询不会上传文字。')
        if ok and text.strip():self._custom=[p.strip() for p in re.split(r'\n\s*\n',text) if p.strip()];self.rebuild()
    def rebuild(self,*_):
        self.cancel_queries();self.popup.hide();self.selection.reset()
        while self.content_layout.count():
            item=self.content_layout.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        for paragraph in self._custom or ARTICLES[self.article.currentIndex()][1]:
            card=QFrame();card.setObjectName('card');card.setProperty('paragraphText',paragraph)
            flow=FlowLayout(card,margin=18,h_spacing=0,v_spacing=8)
            for token in re.findall(r'\S+\s*',paragraph):
                label=PrototypeLabel(token,self);label.setTextFormat(Qt.PlainText)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse|Qt.TextSelectableByKeyboard)
                label.setStyleSheet('font-size:23px;');label.setProperty('flowText',token);flow.addWidget(label)
            self.content_layout.addWidget(card)
        self.content_layout.addStretch();self.selection.bind()

    def translate_current(self,point=None):
        if self.mode.currentIndex()==3 or not self.selection.selected_text():return
        lo,hi=sorted((self.selection._anchor,self.selection._position));document=self.selection._text
        start=len(piece(document,0,lo));end=len(piece(document,0,hi))
        try:
            query=prepare_query(document,start,end,False)
            online=prepare_query(document,start,end,True)
        except TranslationError as error:self.statusBar().showMessage(str(error),6000);return
        if point is not None:self._point=point
        self.start_lookup(query,online)

    def start_lookup(self,query,online=None):
        if self.mode.currentIndex()==3:return
        self.cancel_queries();self.query=query;self.online_query=online or query;self._lookup=None
        self.popup.present(query,self._point);self._lookup_token=self.dictionary.request(query.selected)

    def _lookup_completed(self,token,result,error,cached):
        if token!=self._lookup_token or not self.popup.isVisible():return
        self._lookup=result;self.popup.show_lookup(result,error)

    def online_action(self):
        if not self.query or self.mode.currentIndex()==3:return
        state=self.popup.net_state
        if state=='done':
            self.popup.network.hide();self.popup.net_state='closed';self.popup.retry.setText('联网参考')
            if self._lookup:self.popup.show_lookup(self._lookup)
            self.popup._fit();return
        if not self.online_allowed.isChecked() and state!='consent':
            self.popup.show_online(self.online_query,'consent');return
        self.online_allowed.setChecked(True);self.start_query(self.online_query)

    def start_query(self,query):
        """Explicit network action only; never the default selection lookup."""
        if self.mode.currentIndex()==3 or not self.online_allowed.isChecked():return
        self.online_query=query
        if not self.popup.isVisible():
            self.query=query;self.online_query=query;self.popup.present(query,self._point)
        self.popup.show_online(query,'loading');self._token=self.client.request(query.text)

    def repeat_query(self):
        if self.online_query:self.start_query(self.online_query)
    def _completed(self,token,text,error,cached):
        if token!=self._token or not self.popup.isVisible():return
        self.popup.show_online(self.online_query,'error' if error else 'done',error or text)
    def closeEvent(self,event):
        self.cancel_queries();self.popup.close();self.selection.reset();super().closeEvent(event)


def main():
    app=QApplication(sys.argv);app.setFont(QFont('Microsoft YaHei UI',10))
    window=PrototypeWindow();window.show();return app.exec()


if __name__=='__main__':raise SystemExit(main())
