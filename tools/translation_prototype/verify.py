"""Packaged dictionary and loopback smoke; no real learning data or internet."""
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from tools.translation_prototype.engine import MyMemory,Query,TranslationClient
from tools.translation_prototype.ui import PrototypeWindow


def main(output):
    app=QApplication([])
    for name in ('msyh.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name))
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    checks={};state={'stage':0,'ticks':0};errors=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            body=json.dumps({'responseStatus':200,'responseData':{'translatedText':'她坐在河岸边，看着河水流淌。'}},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    provider=MyMemory();provider.endpoint=f'http://127.0.0.1:{server.server_port}/'
    client=TranslationClient(provider=provider);window=PrototypeWindow(client);window.show()
    checks['no_network_on_start']=client.network_requests==0 and client._manager is None
    checks['no_learning_modules']=not any(name.startswith(('src.database','src.services.fsrs','src.services.memory_scheduler')) for name in sys.modules)
    window.start_lookup(Query('utterly','utterly'));window.input.insert('independent typing')
    timer=QTimer();timer.setInterval(10)
    def finish():
        timer.stop();window.close();client.cancel();server.shutdown();server.server_close()
        report={'checks':checks,'passed':sum(checks.values()),'failed':[k for k,v in checks.items() if not v],
                'errors':errors,'normal_learning_database_opened':False,'external_network_used':False}
        (output/'packaged-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        app.exit(0 if not report['failed'] and not errors else 1)
    def tick():
        state['ticks']+=1
        if state['ticks']>600:errors.append('verification timeout');finish();return
        if state['stage']==0 and window.popup.lookup_done:
            checks['offline_word']='完全地' in window.popup.result.toPlainText()
            checks['local_lookup_no_network']=client.network_requests==0 and client._manager is None
            checks['compact_single_word_popup']=window.popup.height()<280
            checks['input_during_lookup']=window.input.text()=='independent typing'
            window.popup.grab().save(str(output/'packaged-word.png'))
            window.grab().save(str(output/'packaged-reader.png'))
            window.start_lookup(Query('spill the beans','spill the beans'));state['stage']=1
        elif state['stage']==1 and window.popup.lookup_done:
            checks['whole_idiom']='说漏嘴' in window.popup.result.toPlainText()
            window.popup.grab().save(str(output/'packaged-phrase.png'))
            window.start_lookup(Query('bank','bank'),Query('bank','She sat on the bank and watched the river flow.',True))
            state['stage']=2
        elif state['stage']==2 and window.popup.lookup_done:
            window.popup.retry.click()
            checks['inline_consent_before_network']=window.popup.net_state=='consent' and client.network_requests==0
            window.popup.retry.click();state['stage']=3
        elif state['stage']==3 and window.popup.net_state in ('done','error'):
            checks['network_reference']='河岸' in window.popup.net_text.toPlainText()
            checks['non_modal_popup']=not window.popup.isModal()
            checks['network_count_once']=client.network_requests==1
            window.repeat_query();state['stage']=4
        elif state['stage']==4 and window.popup.net_state in ('done','error'):
            checks['repeat_uses_cache']=client.network_requests==1
            window.article.setCurrentIndex(1)
            checks['switch_hides_popup']=not window.popup.isVisible()
            before=window.dictionary.lookups
            window.mode.setCurrentIndex(3);window.start_lookup(Query('bank','bank'));window.start_query(Query('bank','bank'))
            checks['independent_practice_disabled']=client.network_requests==1 and window.dictionary.lookups==before and not window.popup.isVisible()
            finish()
    timer.timeout.connect(tick);timer.start();result=app.exec();thread.join(timeout=1);return result
