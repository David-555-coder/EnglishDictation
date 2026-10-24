"""Offline source-only visual and responsiveness measurements for the prototype."""
import argparse
import json
import os
from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import psutil

from tools.translation_prototype.engine import Query
from tools.translation_prototype.ui import PrototypeWindow


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    app=QApplication([])
    for name in ('msyh.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name))
    process=psutil.Process();before=process.memory_info().rss;start=time.perf_counter()
    window=PrototypeWindow();window.show();app.processEvents();construct=(time.perf_counter()-start)*1000
    idle=process.memory_info().rss;assert window.client._manager is None and window.client.network_requests==0
    window.grab().save(str(output/'reader.png'));rows=[]
    for text in ('utterly','spill the beans','charge','as far as I am concerned','looked up to','stress-free'):
        started=time.perf_counter();window.start_lookup(Query(text,text))
        window.input.setFocus();window.input.insert('abc');until=time.monotonic()+3
        while not window.popup.lookup_done and time.monotonic()<until:app.processEvents();QTest.qWait(1)
        assert window.popup.lookup_done
        app.processEvents()
        dimensions=(window.popup.width(),window.popup.height())
        screen=window.popup.screen().availableGeometry()
        rows.append({'text':text,'milliseconds':round((time.perf_counter()-started)*1000,3),
                     'width':dimensions[0],'height':dimensions[1],
                     'inside_screen':screen.contains(window.popup.frameGeometry()),
                     'displayed':window.popup.result.toPlainText()})
        filename=text.replace(' ','-')+'.png';window.popup.grab().save(str(output/filename))
    cached_start=time.perf_counter();window.start_lookup(Query('utterly','utterly'))
    until=time.monotonic()+1
    while not window.popup.lookup_done and time.monotonic()<until:app.processEvents();QTest.qWait(1)
    cached_ms=(time.perf_counter()-cached_start)*1000
    after=process.memory_info().rss
    summary={'normal_learning_database_opened':False,'external_network_used':False,
             'network_requests':window.client.network_requests,'network_manager_created':window.client._manager is not None,
             'scale':os.environ.get('QT_SCALE_FACTOR','1'),'construct_ms':round(construct,2),
             'rss_idle_bytes':idle,'rss_after_bytes':after,'rss_before_bytes':before,
             'cached_lookup_ms':round(cached_ms,2),'input_preserved':window.input.text()=='abc'*6,
             'input_probe':'programmatic insert; real key events covered by unit tests',
             'rows':rows,'scope':'Offscreen prototype only; not native desktop or integrated performance evidence'}
    (output/'smoke.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    window.close();app.processEvents()
    assert summary['input_preserved'] and all(row['inside_screen'] for row in rows)


if __name__=='__main__':main()
