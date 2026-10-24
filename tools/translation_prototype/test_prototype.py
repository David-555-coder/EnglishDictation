"""Deterministic tests use only a loopback fake service, never public quota."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from tools.translation_prototype.engine import MyMemory, Query, TranslationClient, TranslationError, prepare_query
from tools.translation_prototype.ui import PrototypeLabel, PrototypeWindow
from tools.translation_prototype import ui as prototype_ui


APP = QApplication.instance() or QApplication([])


def wait_until(condition, timeout=2500):
    until = time.monotonic()+timeout/1000
    while not condition() and time.monotonic() < until:
        APP.processEvents(); QTest.qWait(5)
    assert condition()


@pytest.fixture(scope='module')
def service():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query).get('q', [''])[0]
            requests.append(query)
            if query.startswith('slow'): time.sleep(.18)
            status = 429 if query == 'http429' else 503 if query == 'offline' else 302 if query == 'redirect' else 200
            value = {'responseStatus':200, 'quotaFinished':False, 'responseData':{'translatedText':'译文：'+query}}
            if query == 'quota': value['quotaFinished'] = True
            body = b'not json' if query == 'bad' else (b'x'*100000 if query == 'large' else json.dumps(value,ensure_ascii=False).encode())
            try:
                self.send_response(status)
                if status == 302: self.send_header('Location', '/?q=redirected')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            except (ConnectionError, OSError): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}/', requests
    server.shutdown(); server.server_close(); thread.join(timeout=2)


def make_client(service, **kwargs):
    provider = MyMemory(); provider.endpoint = service[0]
    client = TranslationClient(provider=provider, **kwargs)
    rows = []; client.completed.connect(lambda *args: rows.append(args))
    return client, rows


@pytest.mark.parametrize('selected,context', [
    ('bank', 'She sat on the bank and watched the river.'),
    ('charge', 'Please charge your phone.'),
    ('looked up', 'She looked up a word in a dictionary.'),
    ('fair', 'We went to a fair.'),
])
def test_enclosing_sentence_is_not_presented_as_a_word_gloss(selected, context):
    start = context.index(selected)
    query = prepare_query(context,start,start+len(selected))
    assert query == Query(selected,context,True)


def test_context_respects_abbreviation_and_paragraph_boundary():
    text = 'Dr. Smith helped her.\n\nShe sat by the river.'
    query = prepare_query(text,4,9)
    assert query.text == 'Dr. Smith helped her.'
    assert 'river' not in query.text


def test_no_context_mode_and_two_sentences_in_one_paragraph():
    text = 'She charged her phone. Then she left.'
    assert prepare_query(text,4,11,False).text == 'charged'
    assert prepare_query(text,0,len(text)).text == text


@pytest.mark.parametrize('text', ['中文','  ', 'word '*110, 'First.\n\nSecond.'])
def test_invalid_and_multi_paragraph_queries_are_not_silently_truncated(text):
    with pytest.raises(TranslationError): prepare_query(text,0,len(text))


def test_existing_chinese_gloss_is_not_sent_as_english_context():
    text = 'She read（阅读） the story.'
    assert prepare_query(text,4,8).text == 'She read the story.'


def test_oversize_context_falls_back_to_selection_without_truncation():
    text = 'She ' + 'carefully '*65 + 'read the story.'
    start = text.index('read')
    assert prepare_query(text,start,start+4) == Query('read','read',False)


@pytest.mark.parametrize('payload', [b'bad', b'[]', b'null', b'{}', b'{"responseStatus":200,"responseData":{}}'])
def test_invalid_payload_is_friendly(payload):
    with pytest.raises(TranslationError): MyMemory.parse(payload)


def test_html_entities_are_decoded_but_never_executed_as_rich_text():
    data = json.dumps({'responseStatus':200,'responseData':{'translatedText':'Tom &amp; Lily <b>文字</b>'}})
    assert MyMemory.parse(data) == 'Tom & Lily <b>文字</b>'


def test_lazy_start_and_bounded_cache(service):
    client, rows = make_client(service,cache_size=2)
    assert client._manager is None and client.network_requests == 0
    for text in ('one','two','three'):
        client.request(text); wait_until(lambda:len(rows)==client.network_requests)
    assert len(client._cache)==2
    count = client.network_requests
    client.request('three'); wait_until(lambda:len(rows)==4)
    assert client.network_requests==count and rows[-1][3]
    client.cancel(); client.deleteLater()


def test_cache_hit_can_be_cancelled_before_delivery(service):
    client, rows = make_client(service)
    client.request('cache-cancel'); wait_until(lambda:len(rows)==1)
    client.request('cache-cancel'); client.cancel(); QTest.qWait(30)
    assert len(rows)==1
    client.deleteLater()


def test_expired_cache_does_not_hide_a_new_request(service):
    client, rows = make_client(service,ttl=0)
    client.request('expired'); wait_until(lambda:len(rows)==1)
    client.request('expired'); wait_until(lambda:len(rows)==2)
    assert client.network_requests==2 and not rows[-1][3]
    client.deleteLater()


@pytest.mark.parametrize('query,message', [('http429','请求过多'),('offline','无法连接'),('bad','无法识别'),
                                         ('quota','额度'),('large','异常'),('redirect','无法连接')])
def test_network_and_service_failures_are_safe(service,query,message):
    client, rows = make_client(service)
    before = len(service[1]); client.request(query); wait_until(lambda:bool(rows))
    assert message in rows[0][2] and not client._cache and client._active is None
    if query=='redirect': assert 'redirected' not in service[1][before:]
    client.deleteLater()


def test_timeout_and_no_automatic_retry(service):
    client, rows = make_client(service,timeout_ms=40)
    client.request('slow-timeout'); wait_until(lambda:bool(rows)); QTest.qWait(220)
    assert '超时' in rows[0][2] and client.network_requests==1 and len(rows)==1
    client.deleteLater()


def test_superseded_request_cannot_deliver_old_result(service):
    client, rows = make_client(service)
    client.request('slow-old'); QTest.qWait(25); token=client.request('new')
    wait_until(lambda:bool(rows)); QTest.qWait(230)
    assert rows==[(token,'译文：new','',False)]
    client.deleteLater()


@pytest.fixture
def window(service):
    client, _ = make_client(service)
    reader = PrototypeWindow(client); reader.show(); APP.processEvents()
    yield reader
    reader.close(); reader.deleteLater(); client.deleteLater(); APP.processEvents()


def test_window_never_connects_on_start_and_requires_consent(window):
    assert window.client._manager is None and window.client.network_requests==0
    window.start_query(Query('bank','bank'))
    assert window.client.network_requests==0 and not window.popup.isVisible()


def test_close_popup_discards_reply_and_copy_is_explicit(window):
    window.online_allowed.setChecked(True)
    window.start_query(Query('slow-window','slow-window')); window.popup.close(); QTest.qWait(230)
    assert not window.popup.isVisible() and not window.popup.copy.isEnabled()
    window.start_query(Query('new-window','new-window')); wait_until(lambda:window.popup.net_state=='done')
    previous=APP.clipboard().text()
    try:
        window.popup.net_copy.click(); assert APP.clipboard().text()=='译文：new-window'
        assert not window.popup.copy.isEnabled()
    finally: APP.clipboard().setText(previous)


@pytest.mark.parametrize('change',['article','mode','consent'])
def test_page_mode_and_privacy_change_cancel_old_requests(window,change):
    window.online_allowed.setChecked(True)
    window.start_query(Query('slow-page','slow-page'))
    if change=='article': window.article.setCurrentIndex(1)
    elif change=='mode': window.mode.setCurrentIndex(3)
    else: window.online_allowed.setChecked(False)
    QTest.qWait(230)
    assert window.client._active is None
    if change!='consent':assert not window.popup.isVisible()
    else:assert not window.popup.network.isVisible()
    if change=='mode':
        before=window.client.network_requests
        window.start_query(Query('word','word'))
        assert window.client.network_requests==before


def test_input_and_scroll_remain_responsive_during_pending_request(window):
    window.online_allowed.setChecked(True)
    window.start_query(Query('slow-input','slow-input'))
    window.input.setFocus(); QTest.keyClicks(window.input,'typing works')
    window.scroll.verticalScrollBar().setValue(80)
    assert window.input.text()=='typing works' and window.client._active is not None
    wait_until(lambda:window.popup.net_state=='done')


def test_real_cross_label_selection_produces_contextual_query(window):
    window.online_allowed.setChecked(True)
    selection = window.selection
    selection.select_all()
    text = selection._text; start=text.index('bank'); selection._anchor=start; selection._position=start+4
    selection._apply(); window.translate_current(QPoint(100,100))
    assert not window.query.contextual and window.query.selected=='bank'
    assert window.query.text=='bank'
    assert window.online_query.text=='She sat on the bank and watched the river flow.'
    wait_until(lambda:window.popup.copy.isEnabled())
    assert window.popup.title.text()=='bank' and window.client.network_requests==0


def test_escape_closes_floating_window_and_cancels_query(window):
    window.online_allowed.setChecked(True)
    window.start_query(Query('slow-escape','slow-escape'))
    QTest.keyClick(window.popup,Qt.Key_Escape); QTest.qWait(230)
    assert not window.popup.isVisible() and window.client._active is None


@pytest.mark.parametrize('mode',[0,1,2,3])
def test_actual_context_event_keeps_copy_and_adds_translation_in_safe_modes(window,monkeypatch,mode):
    window.mode.setCurrentIndex(mode); window.online_allowed.setChecked(True)
    APP.processEvents()
    window.selection.select_all()
    window.selection._anchor=window.selection._text.index('bank')
    window.selection._position=window.selection._anchor+4
    window.selection._apply()
    label=next(w for w in window.content.findChildren(PrototypeLabel) if w.isVisibleTo(window.content) and 'bank' in w.text())
    seen=[]
    def execute(menu,point):
        titles=[action.text() for action in menu.actions() if not action.isSeparator()]
        assert titles==['复制','全选','翻译']
        action=next(action for action in menu.actions() if action.text()=='翻译')
        seen.append(action.isEnabled())
        if action.isEnabled(): action.trigger()
    original_menu=prototype_ui.text_menu
    def make_menu(widget):
        menu=original_menu(widget)
        menu.exec=lambda point: execute(menu,point)
        return menu
    monkeypatch.setattr(prototype_ui,'text_menu',make_menu)
    event=QContextMenuEvent(QContextMenuEvent.Mouse,QPoint(3,3),label.mapToGlobal(QPoint(3,3)))
    QApplication.sendEvent(label,event)
    assert seen==[mode!=3]
    if mode==3:
        assert window.client.network_requests==0
    else:
        wait_until(lambda:window.popup.copy.isEnabled())
        assert window.query.selected=='bank' and not window.query.contextual
        assert window.client.network_requests==0
