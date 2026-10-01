"""Compare releases in isolated data copies; never instantiate Repository on user data.

Usage: python tools/profile_performance.py SOURCE_ROOT USER_DB
Prints numbers only, not document text. It may produce a screenshot in its temp directory.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
source_root, user_db = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
before = hashlib.sha256(user_db.read_bytes()).hexdigest()
root = Path(tempfile.mkdtemp(prefix="EnglishDictationProfile_"))
db = root / "data" / "english_dictation.db"
db.parent.mkdir()
source = sqlite3.connect(user_db.as_uri() + "?mode=ro", uri=True)
target = sqlite3.connect(db)
source.backup(target); target.close(); source.close()
sys.path.insert(0, str(source_root))
started = time.perf_counter()
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from src.database.repository import Repository
from src.ui.main_window import MainWindow


class Memory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


def memory_mb():
    counters = Memory(); counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    if not fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return round(counters.PrivateUsage / 1048576, 2)


app = QApplication([]); app.setStyle("Fusion")
for name in ("msyh.ttc", "msyhbd.ttc"):
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
    if font.exists(): QFontDatabase.addApplicationFont(str(font))
repo = Repository(db)
window = MainWindow(repo); window.show()
deadline = time.monotonic() + 30
while not window.articles and time.monotonic() < deadline:
    app.processEvents(); QTest.qWait(10)
QTest.qWait(150)
startup = time.perf_counter() - started
window._save_active_draft()
memory_before = memory_mb()
calls = []
save = repo.save_draft
def counted(session): calls.append(1); return save(session)
repo.save_draft = counted
start = time.perf_counter()
for _ in range(100): window._save_active_draft()
save_ms = (time.perf_counter() - start) * 1000
QDialog.exec = lambda self: QDialog.Rejected
for _ in range(12):
    window.show_history(); window.show_wrong_words()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
memory_after = memory_mb()
window.grab().save(str(root / "window.png"))
result = {"source": str(source_root), "startup_seconds_offscreen": round(startup, 3),
          "articles": len(window.articles), "drafts": len(window._drafts),
          "full_articles_loaded": sum(type(a).__name__ == 'Article' for a in window.articles),
          "unchanged_save_calls": len(calls), "unchanged_save_100_ms": round(save_ms, 2),
          "private_mb_before_dialogs": memory_before, "private_mb_after_dialogs": memory_after,
          "remaining_dialogs": len(window.findChildren(QDialog)), "screenshot": str(root / "window.png")}
window.close(); window.deleteLater(); app.processEvents()
assert hashlib.sha256(user_db.read_bytes()).hexdigest() == before
result["original_database_unchanged"] = True
print(json.dumps(result, ensure_ascii=False), flush=True)
