import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zlib

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from tools.translation_prototype.test_prototype import APP, make_client, service, wait_until
from tools.translation_prototype.dictionary import Dictionary, DictionaryClient, Entry, Lookup, normalize
from tools.translation_prototype.engine import Query
from tools.translation_prototype.ui import PrototypeWindow


@pytest.fixture
def lexicon(tmp_path):
    path=tmp_path/'dictionary.sqlite3';connection=sqlite3.connect(path)
    connection.executescript('''CREATE TABLE entries(key TEXT PRIMARY KEY,word TEXT,phonetic TEXT,meaning BLOB,rank INTEGER);
        CREATE TABLE forms(form TEXT,lemma TEXT,rank INTEGER,PRIMARY KEY(form,lemma));''')
    rows={'bank':('bæŋk','n. 银行；河岸'), 'utterly':('ʌtəli','adv. 完全地'),
          'outdo':('','vt. 超越；胜过'),'outdid':('','outdo的过去式'),
          'spill the beans':('','泄露秘密'), 'look up to':('','敬仰'),
          'run out of':('','用完'), 'leaf':('','n. 树叶'),'leave':('','v. 离开'),
          'long':('','n. 一行\nn. 二行\nn. 三行\nn. 四行\nn. 五行')}
    for word,(phonetic,meaning) in rows.items():
        connection.execute('INSERT INTO entries VALUES(?,?,?,?,?)',(word,word,phonetic,zlib.compress(meaning.encode()),0))
    for form,lemma in [('outdid','outdo'),('looked','look'),('ran','run'),('leaves','leaf'),('leaves','leave')]:
        connection.execute('INSERT INTO forms VALUES(?,?,0)',(form,lemma))
    connection.commit();connection.close();return path


@pytest.mark.parametrize('text,expected',[(' Bank, ','bank'),('“utterly”','utterly'),('Looked   up to','looked up to'),
    ('stress‑free','stress-free'),("I'm","i'm"),("'bank'",'bank')])
def test_normalization_is_limited_to_punctuation_and_typography(text,expected):
    assert normalize(text)==expected


def test_whole_phrase_lookup_never_stitches_single_word_meanings(lexicon):
    store=Dictionary(lexicon,supplements=False)
    assert store.lookup('spill the beans').entries[0].translation=='泄露秘密'
    assert store.lookup('spill beans').match=='missing'
    assert store.lookup('utterly bank').match=='missing'


def test_dictionary_attested_inflection_preserves_particles_and_ambiguity(lexicon):
    store=Dictionary(lexicon,supplements=False)
    assert store.lookup('looked up to').entries[0].word=='look up to'
    assert store.lookup('ran out of').entries[0].translation=='用完'
    assert {entry.word for entry in store.lookup('leaves').entries}=={'leaf','leave'}
    assert store.lookup('banked').match=='missing'  # no unsafe suffix chopping


def test_exact_inflection_reference_includes_actual_chinese_base_meaning(lexicon):
    result=Dictionary(lexicon,supplements=False).lookup('outdid')
    assert result.entries[0].word=='outdid'
    assert '过去式' in result.entries[0].translation and '超越' in result.entries[0].translation


def test_no_creation_when_missing_and_no_writes_to_existing_lexicon(lexicon,tmp_path):
    before=hashlib.sha256(lexicon.read_bytes()).hexdigest()
    store=Dictionary(lexicon,supplements=False)
    for word in ('bank','outdid','looked up to','unknown'):store.lookup(word)
    assert hashlib.sha256(lexicon.read_bytes()).hexdigest()==before
    missing=tmp_path/'missing.sqlite3'
    with pytest.raises(sqlite3.OperationalError):Dictionary(missing,supplements=False).lookup('word')
    assert not missing.exists() and not Path(str(lexicon)+'-wal').exists()


def test_supplements_are_explicit_separate_entries_not_changes_to_source(lexicon,tmp_path):
    supplement=tmp_path/'supplement.json'
    supplement.write_text(json.dumps({'bank':{'word':'bank','meaning':'校对说明','reference':'https://example.com/source'}}),encoding='utf-8')
    result=Dictionary(lexicon,supplements=supplement).lookup('bank')
    assert result.entries[0].source=='补充释义' and result.entries[0].reference.endswith('/source')
    assert Dictionary(lexicon,supplements=False).lookup('bank').entries[0].translation=='n. 银行；河岸'


def test_async_cache_and_cancelled_queued_hit(lexicon):
    client=DictionaryClient(store=Dictionary(lexicon,supplements=False),cache_size=2)
    results=[];client.completed.connect(lambda *args:results.append(args))
    assert client.lookups==0 and not client._jobs
    client.request('bank');wait_until(lambda:len(results)==1)
    client.request('BANK');wait_until(lambda:len(results)==2)
    assert results[-1][3] and results[-1][1].selected=='BANK' and client.lookups==1
    client.request('bank');client.cancel();QTest.qWait(30);assert len(results)==2
    for word in ('utterly','outdo'):
        count=len(results);client.request(word);wait_until(lambda:len(results)>count)
    assert len(client._cache)==2
    client.deleteLater();APP.processEvents()


def test_cancelled_slow_lookup_cannot_replace_new_selection():
    class Slow:
        def lookup(self,text):
            if text=='slow':time.sleep(.12)
            return Lookup(text,(Entry(text,'','意思 '+text),),'exact')
    client=DictionaryClient(store=Slow());rows=[];client.completed.connect(lambda *row:rows.append(row))
    client.request('slow');token=client.request('new');wait_until(lambda:bool(rows));QTest.qWait(180)
    assert len(rows)==1 and rows[0][0]==token and rows[0][1].selected=='new'
    client.deleteLater();APP.processEvents()


@pytest.fixture
def reader(lexicon,service):
    network,_=make_client(service)
    local=DictionaryClient(store=Dictionary(lexicon,supplements=False))
    window=PrototypeWindow(network,local);window.show();APP.processEvents()
    yield window
    window.close();window.deleteLater();network.deleteLater();local.deleteLater();APP.processEvents()


def test_offline_is_default_and_consent_is_not_a_configuration_block(reader):
    assert not reader.online_allowed.isVisible()
    reader.start_lookup(Query('utterly','utterly'))
    wait_until(lambda:reader.popup.lookup_done)
    assert '完全地' in reader.popup.result.toPlainText()
    assert reader.client._manager is None and reader.client.network_requests==0
    assert reader.popup.height()<280 and not reader.popup.network.isVisible()
    assert len([b for b in reader.popup.findChildren(type(reader.popup.copy)) if b.isVisible()])<=3


def test_phrase_not_found_does_not_automatically_send_text(reader):
    reader.start_lookup(Query('not a real fixed phrase','not a real fixed phrase'))
    wait_until(lambda:reader.popup.lookup_done)
    assert '未收录' in reader.popup.result.toPlainText() and not reader.popup.copy.isEnabled()
    assert reader.client.network_requests==0


def test_inline_consent_then_explicit_online_and_return_to_dictionary(reader):
    reader.start_lookup(Query('bank','bank'),Query('bank','The bank is open.',True))
    wait_until(lambda:reader.popup.lookup_done)
    reader.popup.retry.click()
    assert reader.popup.net_state=='consent' and reader.client.network_requests==0
    assert 'The bank is open.' in reader.popup.net_text.toPlainText()
    reader.popup.retry.click();wait_until(lambda:reader.popup.net_state=='done')
    assert reader.client.network_requests==1 and '译文：The bank is open.'==reader.popup.net_text.toPlainText()
    reader.popup.retry.click()
    assert not reader.popup.network.isVisible() and '银行' in reader.popup.result.toPlainText()


def test_long_glosses_expand_without_losing_full_copy_text(reader):
    reader.start_lookup(Query('long','long'));wait_until(lambda:reader.popup.lookup_done)
    assert reader.popup.more.isVisible() and '五行' not in reader.popup.result.toPlainText()
    before=APP.clipboard().text()
    try:
        reader.popup.copy.click();assert '五行' in APP.clipboard().text()
    finally:APP.clipboard().setText(before)
    reader.popup.more.click();assert '五行' in reader.popup.result.toPlainText()
    reader.popup.more.click();assert '五行' not in reader.popup.result.toPlainText()


def test_close_escape_switch_and_independent_mode_cancel_local_callbacks(reader):
    reader.start_lookup(Query('bank','bank'));reader.popup.close();QTest.qWait(40)
    assert not reader.popup.isVisible()
    reader.start_lookup(Query('utterly','utterly'));reader.article.setCurrentIndex(1);QTest.qWait(40)
    assert not reader.popup.isVisible()
    reader.mode.setCurrentIndex(3);reader.start_lookup(Query('bank','bank'))
    assert not reader.popup.isVisible() and reader.client.network_requests==0


def test_glosses_are_plain_text_even_if_the_data_contains_html(reader):
    reader.popup.present(Query('word','word'),QPoint(100,100))
    reader.popup.show_lookup(Lookup('word',(Entry('word','','<script>bad</script> & text'),),'exact'))
    assert '<script>bad</script>' in reader.popup.result.toPlainText()
