"""Read-only English-Chinese lexicon. Never opens the learning database."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import json
import re
import sqlite3
import sys
import unicodedata
import zlib

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, Qt


def normalize(text):
    text = unicodedata.normalize('NFKC', text).translate(str.maketrans({'’':"'",'‘':"'",'‑':'-','‐':'-','–':'-'}))
    text = re.sub(r'\s+', ' ', text).strip(' \t\r\n.,!?;:"“”()[]{}')
    if len(text)>1 and text[0]==text[-1]=="'": text=text[1:-1]
    return text.casefold()


def dictionary_path():
    if getattr(sys,'frozen',False):
        return Path(sys.executable).resolve().parent/'assets'/'dictionary.sqlite3'
    return Path(__file__).resolve().parents[2]/'assets'/'dictionary.sqlite3'


@dataclass(frozen=True)
class Entry:
    word: str
    phonetic: str
    translation: str
    source: str = 'ECDICT'
    reference: str = ''


@dataclass(frozen=True)
class Lookup:
    selected: str
    entries: tuple = ()
    match: str = 'missing'


class Dictionary:
    def __init__(self, path=None, supplements=None):
        self.path = Path(path) if path else dictionary_path()
        self.supplement_file=dictionary_path().with_name('dictionary_supplements.json') if supplements is None else supplements
        self._supplements=None

    def lookup(self, text):
        key=normalize(text)
        if not key or len(key)>160 or not re.search('[a-z]',key): return Lookup(text)
        if self._supplements is None:
            if self.supplement_file and Path(self.supplement_file).exists():
                self._supplements=json.loads(Path(self.supplement_file).read_text(encoding='utf-8'))
            else:self._supplements={}
        # immutable is appropriate: this packaged lexicon is a closed, static DB.
        connection=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
        try:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA cache_size=-512')
            def find(word):
                if word in self._supplements:
                    extra=self._supplements[word]
                    return Entry(extra['word'],'',extra['meaning'],'补充释义',extra['reference'])
                row=connection.execute('SELECT word,phonetic,meaning FROM entries WHERE key=?',(word,)).fetchone()
                return Entry(row[0],row[1],zlib.decompress(row[2]).decode('utf-8')) if row else None
            exact=find(key)
            if exact:
                # Some exact inflection entries only say "give 的过去式".
                # Keep that note, and append an attested base entry, not a guessed suffix rule.
                meanings=[exact.translation]
                for line in exact.translation.splitlines():
                    reference=re.fullmatch(r"([A-Za-z][A-Za-z' -]*)的(?:过去式|过去分词|现在分词|复数|第三人称单数形式)[。. ]?",line.strip())
                    if reference:
                        base=find(normalize(reference[1]))
                        if base and base.word!=exact.word:meanings.append('原形 '+base.word+'：\n'+base.translation)
                if len(meanings)>1:exact=Entry(exact.word,exact.phonetic,'\n'.join(meanings),exact.source,exact.reference)
                return Lookup(text,(exact,),'exact')
            parts=key.split(' ')
            forms=connection.execute('SELECT lemma FROM forms WHERE form=? ORDER BY rank DESC,lemma LIMIT 6',(parts[0],)).fetchall()
            entries=[]
            for (lemma,) in forms:
                candidate=lemma+(' '+' '.join(parts[1:]) if len(parts)>1 else '')
                entry=find(candidate)
                if entry and entry not in entries: entries.append(entry)
            return Lookup(text,tuple(entries[:3]),'inflected' if entries else 'missing')
        finally:
            connection.close()


class _Signals(QObject):
    done=Signal(int,object,str)


class _LookupJob(QRunnable):
    def __init__(self,token,store,text):
        super().__init__(); self.token,self.store,self.text=token,store,text
        self.signals=_Signals()

    def run(self):
        result,error=None,''
        try: result=self.store.lookup(self.text)
        except Exception: error='本地词库暂不可用，请确认软件 assets 文件夹中的词库文件完整。'
        self.signals.done.emit(self.token,result,error)


class DictionaryClient(QObject):
    completed=Signal(int,object,str,bool)

    def __init__(self,parent=None,store=None,cache_size=128):
        super().__init__(parent)
        self.store=store or Dictionary(); self.cache_size=cache_size
        self._serial=0; self._jobs={}; self._cache=OrderedDict()
        self._pool=None; self._pending=None
        self.lookups=0

    def cancel(self):
        self._serial+=1
        self._pending=None

    def request(self,text):
        from PySide6.QtCore import QTimer
        self.cancel(); token=self._serial; key=normalize(text)
        if key in self._cache:
            value=self._cache[key]; self._cache.move_to_end(key)
            # Keep this selection's spelling even on a normalised-key cache hit.
            result=Lookup(text,value.entries,value.match)
            QTimer.singleShot(0,lambda:self._deliver(token,result,'',True))
        elif self._jobs:
            # One running read plus only the newest pending selection. Repeated
            # clicks cannot fill the global pool or build an unbounded queue.
            self._pending=(token,text)
        else:
            self._start(token,text)
        return token

    def _start(self,token,text):
        if self._pool is None:
            self._pool=QThreadPool(self); self._pool.setMaxThreadCount(1)
            self._pool.setExpiryTimeout(15000)
        job=_LookupJob(token,self.store,text)
        job.signals.done.connect(self._receive,Qt.QueuedConnection)
        self._jobs[token]=job; self.lookups+=1
        self._pool.start(job)

    @Slot(int,object,str)
    def _receive(self,token,result,error):
        self._jobs.pop(token,None)
        if token==self._serial and result is not None and not error:
            self._cache[normalize(result.selected)]=result
            self._cache.move_to_end(normalize(result.selected))
            while len(self._cache)>self.cache_size: self._cache.popitem(last=False)
        self._deliver(token,result,error,False)
        pending,self._pending=self._pending,None
        if pending and pending[0]==self._serial:
            self._start(*pending)

    def _deliver(self,token,result,error,cached):
        if token==self._serial: self.completed.emit(token,result,error,cached)
