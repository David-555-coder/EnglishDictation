"""Build the standalone index ahead of time, never at application startup."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import zlib

from tools.translation_prototype.dictionary import normalize


def build(source,output):
    source,output=Path(source),Path(output)
    if output.exists(): raise FileExistsError('Refusing to overwrite an existing dictionary')
    output.parent.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter(); connection=sqlite3.connect(output)
    connection.executescript('''
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE entries(key TEXT PRIMARY KEY,word TEXT,phonetic TEXT,meaning BLOB,rank INTEGER) WITHOUT ROWID;
        CREATE TABLE forms(form TEXT,lemma TEXT,rank INTEGER,PRIMARY KEY(form,lemma)) WITHOUT ROWID;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID;
    ''')
    total=skipped=0
    with source.open(encoding='utf-8-sig',newline='') as handle:
        for row in csv.DictReader(handle):
            total+=1
            word=(row.get('word') or '').strip();key=normalize(word)
            meaning=(row.get('translation') or '').replace('\\n','\n').strip()
            if not key or len(key)>160 or not re.search('[a-z]',key) or not re.search('[\u3400-\u9fff]',meaning):
                skipped+=1; continue
            tags=(row.get('tag') or '').split()
            rank=(600 if any(tag in tags for tag in ('cet4','cet6','gk')) else 0)
            rank+=300 if row.get('oxford')=='1' else 0
            rank+=int(row.get('collins') or 0)*20
            connection.execute('''INSERT INTO entries VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                word=excluded.word,phonetic=excluded.phonetic,meaning=excluded.meaning,rank=excluded.rank
                WHERE excluded.rank>entries.rank''',
                (key,word,row.get('phonetic') or '',zlib.compress(meaning.encode(),6),rank))
            for pair in (row.get('exchange') or '').split('/'):
                if ':' not in pair: continue
                kind,values=pair.split(':',1)
                if kind not in ('p','d','i','3','s','r','t'): continue
                for value in values.split(','):
                    form=normalize(value)
                    if form and form!=key and len(form)<=160:
                        connection.execute('INSERT OR IGNORE INTO forms VALUES(?,?,?)',(form,key,rank))
    with source.open('rb') as source_bytes: source_hash=hashlib.file_digest(source_bytes,'sha256').hexdigest()
    summary={'source_rows':total,'skipped_without_chinese_or_invalid':skipped,
             'entries':connection.execute('SELECT count(*) FROM entries').fetchone()[0],
             'forms':connection.execute('SELECT count(*) FROM forms').fetchone()[0],
             'source_sha256':source_hash}
    for key,value in summary.items(): connection.execute('INSERT INTO metadata VALUES(?,?)',(key,str(value)))
    connection.commit(); assert connection.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    connection.close()
    summary.update(seconds=round(time.perf_counter()-started,3),bytes=output.stat().st_size)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(); print(json.dumps(build(args.source,args.output),ensure_ascii=False),flush=True)
