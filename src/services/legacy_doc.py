from __future__ import annotations

from pathlib import Path


def convert_doc_to_docx(source: str | Path, destination: str | Path) -> Path:
    """使用本机 Microsoft Word 将旧 .doc 只读转换为新的 .docx 副本。"""
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source.suffix.casefold() != ".doc":
        raise ValueError("待转换文件必须是 .doc")
    if source == destination:
        raise ValueError("转换目标不能覆盖原文件")

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("读取 .doc 需要本机 Microsoft Word 和程序内置的 Word 转换组件。") from exc

    word = document = None
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(
            str(source), ConfirmConversions=False, ReadOnly=True,
            AddToRecentFiles=False, Visible=False, OpenAndRepair=True,
        )
        document.SaveAs2(str(destination), FileFormat=16, AddToRecentFiles=False)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Word 未生成可用的 .docx 副本")
        return destination
    except Exception as exc:
        raise RuntimeError(f"无法读取旧式 .doc：{exc}") from exc
    finally:
        if document is not None:
            try:
                document.Close(SaveChanges=0)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit(SaveChanges=0)
            except Exception:
                pass
        pythoncom.CoUninitialize()
