"""Small no-key adapter and cancellable Qt network client. No learning storage."""
from collections import OrderedDict
from dataclasses import dataclass
from html import unescape
import json
import re
import time
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QTimer, QUrl, Signal


class TranslationError(ValueError):
    pass


@dataclass(frozen=True)
class Query:
    selected: str
    text: str
    contextual: bool = False


def clean_english(text):
    # Remove only explicitly bracketed Chinese glosses, not English parentheses.
    text = re.sub(r'（[^）]*[\u3400-\u9fff][^）]*）|\([^)]*[\u3400-\u9fff][^)]*\)', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def prepare_query(document, start, end, use_context=True):
    """Offsets are Python code points; the UI adapter converts Qt UTF-16 first.

    MyMemory has no context-only parameter. Translate the real enclosing sentence
    and label the answer as a sentence translation, never invent a word gloss.
    """
    start, end = sorted((max(0, start), min(len(document), end)))
    if re.search(r'\n\s*\n', document[start:end]):
        raise TranslationError('请一次选择同一段内的一两句话；免费服务不接受跨段翻译。')
    selected = clean_english(document[start:end])
    if not re.search('[A-Za-z]', selected):
        raise TranslationError('请先选中英文单词、词组或短句。')
    if len(selected.encode('utf-8')) > 500:
        raise TranslationError('这次选择较长，请缩短到一两句话（最多 500 字节），不会自动截断。')
    if not use_context:
        return Query(selected, selected)
    boundaries = [0]
    for match in re.finditer(r'[.!?][\"\u201d\u2019\']?(?=\s|$)|\n\s*\n', document):
        before = document[:match.end()]
        if re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|e\.g|i\.e|[A-Z])\.$', before):
            continue
        boundaries.append(match.end())
    boundaries.append(len(document))
    left = max((p for p in boundaries if p <= start), default=0)
    right = min((p for p in boundaries if p >= end), default=len(document))
    context = clean_english(document[left:right])
    if context and len(context.encode('utf-8')) <= 500 and context != selected:
        return Query(selected, context, True)
    return Query(selected, selected)


class MyMemory:
    name = 'MyMemory · 免费联网'
    endpoint = 'https://api.mymemory.translated.net/get'

    def url(self, text):
        if re.search(r'\n\s*\n', text):
            raise TranslationError('请一次选择同一段内的一两句话。')
        if not text.strip() or len(text.encode('utf-8')) > 500:
            raise TranslationError('请选择不超过 500 字节的英文。')
        return self.endpoint + '?' + urlencode({'q': text, 'langpair': 'en|zh-CN'})

    @staticmethod
    def parse(body):
        try:
            value = json.loads(body)
            if value.get('quotaFinished') or str(value.get('responseStatus')) == '429':
                raise TranslationError('免费服务今日额度已用完，请稍后或明天再试。')
            if str(value.get('responseStatus')) != '200':
                raise TranslationError('翻译服务暂时无法处理这段文字，请稍后再试。')
            result = value.get('responseData', {}).get('translatedText')
            if not isinstance(result, str) or not result.strip():
                raise TranslationError('没有收到有效译文，请稍后再试。')
            return unescape(result).strip()
        except (ValueError, TypeError, AttributeError) as error:
            if isinstance(error, TranslationError):
                raise
            raise TranslationError('翻译服务返回了无法识别的内容，请稍后再试。') from None


class TranslationClient(QObject):
    """One pending request, bounded RAM cache, lazy network import, no retries.

    completed(token, translated_text, friendly_error, cached). Cancellation also
    invalidates already-queued cache hits. Never log URLs or selected text.
    """
    completed = Signal(int, str, str, bool)

    def __init__(self, parent=None, provider=None, timeout_ms=8000, cache_size=128, ttl=3600):
        super().__init__(parent)
        self.provider = provider or MyMemory()
        self.timeout_ms, self.cache_size, self.ttl = timeout_ms, cache_size, ttl
        self._manager = None
        self._active = None
        self._serial = 0
        self._cache = OrderedDict()
        self.network_requests = 0

    def cancel(self):
        self._serial += 1
        active, self._active = self._active, None
        if active is not None:
            active['timer'].stop()
            active['reply'].abort()

    def request(self, text):
        self.cancel()
        token = self._serial
        try:
            url = self.provider.url(text)
        except TranslationError as error:
            message = str(error)
            QTimer.singleShot(0, lambda: self._deliver(token, '', message, False))
            return token
        key = (self.provider.endpoint, text, 'zh-CN')
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.ttl:
            self._cache.move_to_end(key)
            QTimer.singleShot(0, lambda: self._deliver(token, cached[1], '', True))
            return token
        self._cache.pop(key, None)
        # This import and the manager are deliberately absent during app startup.
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
        if self._manager is None:
            self._manager = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'Accept', b'application/json')
        request.setRawHeader(b'User-Agent', b'EnglishDictation-ReadingDictionary/1.0')
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        reply = self._manager.get(request)
        reply.setReadBufferSize(65537)
        timer = QTimer(self)
        timer.setSingleShot(True)
        active = {'token': token, 'reply': reply, 'timer': timer, 'body': bytearray(),
                  'timeout': False, 'oversize': False, 'key': key}
        self._active = active
        self.network_requests += 1
        reply.readyRead.connect(lambda: self._read(active))
        reply.finished.connect(lambda: self._finish(active))
        timer.timeout.connect(lambda: self._timeout(active))
        timer.start(self.timeout_ms)
        return token

    def _deliver(self, token, text, error, cached):
        if token == self._serial:
            self.completed.emit(token, text, error, cached)

    def _read(self, active):
        if active is not self._active:
            return
        active['body'].extend(bytes(active['reply'].read(max(0, 65537-len(active['body'])))))
        if len(active['body']) > 65536:
            active['oversize'] = True
            active['reply'].abort()

    def _timeout(self, active):
        if active is self._active:
            active['timeout'] = True
            active['reply'].abort()

    def _finish(self, active):
        from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
        active['timer'].stop()
        reply = active['reply']
        if active is not self._active:
            reply.deleteLater()
            active['timer'].deleteLater()
            return
        active['body'].extend(bytes(reply.read(max(0, 65537-len(active['body'])))))
        active['oversize'] = active['oversize'] or len(active['body']) > 65536
        self._active = None
        error, text = '', ''
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if active['timeout']:
            error = '查询超时；你可以继续阅读，稍后再试。'
        elif active['oversize']:
            error = '翻译服务返回内容异常，已停止查询。'
        elif status == 429:
            error = '免费服务请求过多或额度不足，请稍后再试。'
        elif reply.error() != QNetworkReply.NoError or status != 200:
            error = '暂时无法连接翻译服务，请检查网络或稍后再试。'
        else:
            try:
                text = self.provider.parse(bytes(active['body']))
                self._cache[active['key']] = (time.monotonic(), text)
                self._cache.move_to_end(active['key'])
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            except TranslationError as problem:
                error = str(problem)
        reply.deleteLater()
        active['timer'].deleteLater()
        self._deliver(active['token'], text, error, False)
