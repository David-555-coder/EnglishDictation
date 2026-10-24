"""Compact non-modal dictionary card; no learning or network ownership."""
from html import escape
from math import ceil
import re
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLayout, QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget)
from src.ui.widgets.text_menus import bind_text_menus

POPUP_STYLE="""
QDialog{background:transparent;}
QFrame#lookupPanel{background:white;border:1px solid #dfe5ed;border-radius:10px;}
QLabel{background:transparent;color:#27394d;font-family:'Microsoft YaHei UI';}
QLabel#lookupTitle{font-size:18px;font-weight:600;color:#253b52;}
QLabel#lookupMeta{font-size:12px;color:#8490a0;}
QLabel#lookupSource{font-size:11px;color:#8692a0;}
QLabel#netLabel{font-size:12px;color:#77889b;}
QWidget#networkSection,QTextBrowser QWidget{background:transparent;}
QTextBrowser{background:transparent;border:none;padding:0;color:#2c4056;font-family:'Microsoft YaHei UI';font-size:15px;selection-background-color:#d8e9fa;selection-color:#163e68;}
QToolButton{background:transparent;border:none;border-radius:4px;padding:4px 5px;font-family:'Microsoft YaHei UI';font-size:12px;color:#507ba5;}
QToolButton:hover{background:#edf4fc;}
QToolButton:disabled{color:#acb6c2;}
QToolButton#closeButton{font-size:17px;color:#96a1ae;padding:0;}
QToolButton#closeButton:hover{color:#425a76;background:#f0f3f7;}
QFrame#rule{background:#edf0f4;border:none;max-height:1px;}
"""

FORM_NOTE = re.compile(r'[（(]\s*([A-Za-z][A-Za-z\'-]*(?: [A-Za-z\'-]+)*)的'
                      r'(过去式和过去分词|过去式|过去分词|现在分词|复数|第三人称单数形式)\s*[）)]')


class TranslationPopup(QDialog):
    def __init__(self,owner,parent=None):
        super().__init__(parent or owner,Qt.Tool|Qt.FramelessWindowHint)
        self.owner=owner;self._point=QPoint(400,200);self._lines=[];self._expanded=False
        self._copy_text='';self.lookup_done=False;self.net_state='closed'
        self._online_text='';self._preferred_width=344;self._outside_filter_installed=False
        self.setWindowTitle('划词释义');self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_ShowWithoutActivating);self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(POPUP_STYLE)
        outer=QVBoxLayout(self);outer.setContentsMargins(8,8,8,8)
        outer.setSizeConstraint(QLayout.SetNoConstraint)
        self.panel=QFrame();self.panel.setObjectName('lookupPanel');outer.addWidget(self.panel)
        shadow=QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(16);shadow.setOffset(0,3);shadow.setColor(QColor(35,56,79,25))
        self.panel.setGraphicsEffect(shadow)
        layout=QVBoxLayout(self.panel);layout.setContentsMargins(14,12,14,10);layout.setSpacing(6)
        head=QHBoxLayout();head.setSpacing(8)
        self.title=QLabel();self.title.setObjectName('lookupTitle');self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.PlainText);head.addWidget(self.title,1)
        self.close_button=QToolButton();self.close_button.setText('×');self.close_button.setObjectName('closeButton')
        self.close_button.setFixedSize(22,22);self.close_button.setToolTip('关闭（也可点击外部或按 Esc）');self.close_button.setAccessibleName('关闭释义')
        self.close_button.clicked.connect(self.close);head.addWidget(self.close_button,0,Qt.AlignTop)
        layout.addLayout(head)
        self.meta=QLabel();self.meta.setObjectName('lookupMeta');self.meta.setTextFormat(Qt.PlainText)
        self.meta.setWordWrap(True);layout.addWidget(self.meta)
        self.result=self._browser();layout.addWidget(self.result)
        self.more=QToolButton();self.more.setText('展开全部释义');self.more.clicked.connect(self._toggle_more)
        layout.addWidget(self.more,0,Qt.AlignLeft)
        self.network=QWidget();self.network.setObjectName('networkSection')
        net=QVBoxLayout(self.network);net.setContentsMargins(0,3,0,2);net.setSpacing(5)
        rule=QFrame();rule.setObjectName('rule');rule.setFixedHeight(1);net.addWidget(rule)
        net_head=QHBoxLayout();net_head.setSpacing(5)
        self.net_label=QLabel('原句译文');self.net_label.setObjectName('netLabel');net_head.addWidget(self.net_label)
        net_head.addStretch()
        self.net_copy=QToolButton();self.net_copy.setText('复制译文');self.net_copy.setToolTip('复制本次联网译文')
        self.net_copy.clicked.connect(lambda:QApplication.clipboard().setText(self._online_text))
        net_head.addWidget(self.net_copy);net.addLayout(net_head)
        self.net_text=self._browser();net.addWidget(self.net_text)
        self.net_note=QLabel();self.net_note.setObjectName('lookupSource');self.net_note.setWordWrap(True)
        self.net_note.setTextFormat(Qt.PlainText);net.addWidget(self.net_note)
        layout.addWidget(self.network)
        footer=QHBoxLayout();footer.setSpacing(5)
        self.notice=QLabel('本地查询');self.notice.setObjectName('lookupSource')
        self.notice.setToolTip('本地词典的通用释义，不自动判断本句唯一词义。')
        self.notice.setWordWrap(True);footer.addWidget(self.notice,1)
        self.copy=QToolButton();self.copy.setText('复制释义');self.copy.setToolTip('复制完整中文释义')
        self.copy.clicked.connect(lambda:QApplication.clipboard().setText(self._copy_text));footer.addWidget(self.copy)
        self.retry=QToolButton();self.retry.setText('联网参考');self.retry.clicked.connect(owner.online_action)
        footer.addWidget(self.retry);layout.addLayout(footer)
        self.network.hide();self.net_copy.hide();self.more.hide();self.copy.setEnabled(False)
        self.escape=QShortcut(QKeySequence('Escape'),self);self.escape.activated.connect(self.close)
        bind_text_menus(self)

    @staticmethod
    def _browser():
        browser=QTextBrowser();browser.setOpenExternalLinks(False);browser.setOpenLinks(False)
        browser.setFrameShape(QFrame.NoFrame);browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        browser.document().setDocumentMargin(0)
        return browser

    def _body(self,browser,html,minimum=28,maximum=190):
        browser.setProperty('lookupHtml',html);browser.setProperty('lookupMinimum',minimum)
        browser.setProperty('lookupMaximum',maximum);self._measure_body(browser)

    def _measure_body(self,browser):
        html=browser.property('lookupHtml') or ''
        if browser.property('renderedLookupHtml')!=html:
            browser.setHtml('<div style="line-height:135%;">'+html+'</div>')
            browser.setProperty('renderedLookupHtml',html)
        browser.document().setTextWidth(max(160,self.width()-16-28-4))
        required=ceil(browser.document().size().height())+3
        maximum=browser.property('lookupMaximum') or 190
        browser.setFixedHeight(min(maximum,max(browser.property('lookupMinimum') or 22,required)))
        browser.setProperty('lookupOverflow',required>maximum)

    def _fit(self):
        screen=QApplication.screenAt(self._point) or QApplication.primaryScreen();bounds=screen.availableGeometry()
        self.setFixedWidth(min(self._preferred_width,max(240,bounds.width()-16)))
        self._measure_body(self.result)
        if not self.network.isHidden():self._measure_body(self.net_text)
        self.more.setVisible(len(self._lines)>3 or bool(self.result.property('previewTrimmed')) or
                             bool(self.result.property('lookupOverflow')) or self._expanded)
        # Invalidate inside-out after changing browser heights. Otherwise Qt can
        # retain a transient network-section minimum and stretch the title/meta
        # to fill a much taller window even though the text itself is short.
        def natural_height():
            for layout in (self.network.layout(),self.panel.layout(),self.layout()):
                layout.invalidate();layout.activate()
            return self.layout().totalSizeHint().height()
        height=natural_height();overflow=height-(bounds.height()-20)
        if overflow>0:
            self.result.setFixedHeight(max(28,self.result.height()-overflow));height=natural_height()
        overflow=height-(bounds.height()-20)
        if overflow>0 and not self.network.isHidden():
            self.net_text.setFixedHeight(max(28,self.net_text.height()-overflow));height=natural_height()
        self.setFixedHeight(height)
        self.move(max(bounds.left()+8,min(self._point.x()+12,bounds.right()-self.width()-8)),
                  max(bounds.top()+8,min(self._point.y()+12,bounds.bottom()-self.height()-8)))

    def present(self,query,point):
        self._point=point;self.lookup_done=False;self.net_state='closed';self._expanded=False;self._lines=[]
        self._copy_text=self._online_text='';self.copy.setEnabled(False);self.network.hide();self.net_copy.hide();self.more.hide()
        self.title.setText(query.selected);self.meta.setText('');self.meta.hide()
        self.notice.setText('本地查询');self.retry.setText('联网参考');self.retry.setEnabled(True)
        self._preferred_width=392 if len(query.selected)>28 else 344
        self.setFixedWidth(self._preferred_width);self._body(self.result,'<span style="color:#8b97a6">正在查词…</span>')
        self.show();self._fit()

    def show_lookup(self,result,error=''):
        self.lookup_done=True
        if error:
            self._body(self.result,escape(error));self.notice.setText('词库不可用');self._fit();return
        if not result.entries:
            self._body(self.result,'<span style="color:#687b8e">未收录这个完整表达。</span>')
            self.notice.setText('不拆词拼译 · 可选联网参考');self._fit();return
        first=result.entries[0]
        form_note=('词形参考（多种可能）  ' if len(result.entries)>1 else '词形还原：'+first.word+'  ') if result.match=='inflected' else ''
        notes=[note for note in (form_note.strip(),('/'+first.phonetic.strip('/')+'/') if first.phonetic else '') if note]
        self._lines=[];copy_parts=[]
        for entry in result.entries:
            if len(result.entries)>1:self._lines.append('<b>'+escape(entry.word)+'</b>')
            seen=set()
            for line in entry.translation.splitlines():
                line=line.strip()
                if line and line not in seen:
                    display=line
                    matches=list(FORM_NOTE.finditer(line)) if len(result.entries)==1 else []
                    if len(matches)==1:
                        match=matches[0]
                        notes.append(match[1]+' · '+match[2].replace('和','／'))
                        display=line[:match.start()]+line[match.end():]
                    seen.add(line);self._lines.append(escape(display))
            copy_parts.append(entry.translation)
        self._copy_text='\n\n'.join(copy_parts);self.copy.setEnabled(True);self.copy.setToolTip('复制完整中文释义')
        self.notice.setText('补充释义' if getattr(first,'source','ECDICT')!='ECDICT' else 'ECDICT')
        self.meta.setText('  ·  '.join(dict.fromkeys(notes)));self.meta.setVisible(bool(notes))
        self._render_lines()

    def _render_lines(self):
        lines=self._lines if self._expanded else self._lines[:3]
        self.result.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if self._expanded else Qt.ScrollBarAlwaysOff)
        self.result.setProperty('renderedLookupHtml',None)
        self.result.setProperty('previewTrimmed',False)
        self._body(self.result,'<br>'.join(lines),maximum=208 if self._expanded else 84)
        if not self._expanded:
            document=self.result.document();block=document.begin();ends=[]
            while block.isValid():
                layout=block.layout()
                for index in range(layout.lineCount()):
                    line=layout.lineAt(index)
                    ends.append(block.position()+line.textStart()+line.textLength())
                block=block.next()
            if len(ends)>3:
                cursor=QTextCursor(document);cursor.setPosition(ends[2])
                cursor.movePosition(QTextCursor.PreviousCharacter,QTextCursor.MoveAnchor,4)
                cursor.movePosition(QTextCursor.End,QTextCursor.KeepAnchor)
                cursor.removeSelectedText();cursor.insertText('…')
                self.result.setProperty('previewTrimmed',True)
        self.more.setText('收起释义' if self._expanded else '展开释义')
        self._fit()

    def _toggle_more(self):
        self._expanded=not self._expanded;self._render_lines()

    def show_online(self,query,state,text=''):
        self.net_state=state;self.network.show();self.net_copy.setVisible(state=='done')
        if state=='consent':
            self.net_label.setText('联网前确认')
            self._body(self.net_text,escape(query.text),minimum=30,maximum=100)
            self.net_note.setText('只发送上方英文；MyMemory 可能留存请求，习语可能误译。')
            self.retry.setText('同意并查询');self.retry.setEnabled(True)
        elif state=='loading':
            self.net_label.setText('原句译文')
            self._body(self.net_text,'<span style="color:#8b97a6">正在查询，可继续阅读…</span>',maximum=100)
            self.net_note.setText('MyMemory · 仅作参考');self.retry.setText('查询中');self.retry.setEnabled(False)
        else:
            self.net_label.setText('原句译文' if state=='done' else '暂未查到译文')
            self._body(self.net_text,escape(text).replace('\n','<br>'),maximum=140)
            self.net_note.setText('MyMemory · 仅供参考')
            self.retry.setText('重试' if state=='error' else '收起译文');self.retry.setEnabled(True)
            if state=='done':
                self._online_text=text
        self._fit()

    def showEvent(self,event):
        if not self._outside_filter_installed:
            QApplication.instance().installEventFilter(self);self._outside_filter_installed=True
        super().showEvent(event)

    def eventFilter(self,watched,event):
        # Temporary listener only while shown. Returning False preserves the
        # same original click: no synthetic replay, focus stealing or polling.
        kind=event.type()
        if kind in (QEvent.MouseButtonPress,QEvent.TouchBegin,QEvent.NonClientAreaMouseButtonPress):
            if isinstance(watched,QWidget):
                # QWidget.isAncestorOf stops at owned top-level menus. Walk the
                # QObject ownership chain so the card's copy menu stays inside.
                target=watched
                while target is not None and target is not self:target=target.parent()
                if target is None:self.hide()
        elif kind==QEvent.ApplicationDeactivate:
            self.hide()
        return False

    def hideEvent(self,event):
        if self._outside_filter_installed:
            QApplication.instance().removeEventFilter(self);self._outside_filter_installed=False
        self.owner.cancel_queries();super().hideEvent(event)
