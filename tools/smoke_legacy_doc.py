"""可选的本机冒烟测试：需要 Microsoft Word，验证 .doc 只读转换与导入。"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".test-deps"))
sys.path.insert(0, str(ROOT / ".test-deps" / "win32"))
sys.path.insert(0, str(ROOT / ".test-deps" / "win32" / "lib"))
sys.path.insert(0, str(ROOT / ".test-deps" / "win32comext"))
sys.path.insert(0, str(ROOT))
_dll_directory = os.add_dll_directory(str(ROOT / ".test-deps" / "pywin32_system32"))

import pythoncom
import win32com.client

from src.database.repository import Repository
from src.services.library import ArticleLibrary


def main() -> int:
    folder = Path(tempfile.mkdtemp(prefix="english_dictation_doc_smoke_"))
    source = folder / "legacy sample.doc"
    word = document = None
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Add()
        document.Content.Text = "I have ambition（抱负，雄心）."
        document.Range(7, 15).Bold = True
        document.SaveAs2(str(source), FileFormat=0, AddToRecentFiles=False)
        document.Close(SaveChanges=0); document = None
        word.Quit(SaveChanges=0); word = None

        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        article = ArticleLibrary(Repository(folder / "data" / "test.db")).import_file(source)
        assert [item.answer for item in article.word_items] == ["ambition"]
        assert article.filename == source.name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        print(".doc smoke test passed: answer=ambition, original unchanged")
        return 0
    finally:
        if document is not None:
            try: document.Close(SaveChanges=0)
            except Exception: pass
        if word is not None:
            try: word.Quit(SaveChanges=0)
            except Exception: pass
        pythoncom.CoUninitialize()
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
