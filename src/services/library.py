import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path

from src.models.rules import RecognitionRules


class ArticleLibrary:
    def __init__(self, repository):
        self.repository = repository
        self.directory = repository.database_path.parent / "articles"

    def import_file(self, path, rules=None):
        from src.parser.docx_parser import DocxParser
        source = Path(path).resolve()
        suffix = source.suffix.casefold()
        if suffix not in {".docx", ".doc"}: raise ValueError("只支持 .docx 和 .doc 文件")
        data = source.read_bytes()
        self.directory.mkdir(parents=True, exist_ok=True)
        copy_path = self.directory / (hashlib.sha256(data).hexdigest() + ".docx")
        if not copy_path.exists():
            temporary = self.directory / f".{copy_path.stem}.{uuid.uuid4().hex}.tmp.docx"
            try:
                if suffix == ".doc":
                    from src.services.legacy_doc import convert_doc_to_docx
                    convert_doc_to_docx(source, temporary)
                else:
                    with temporary.open("wb") as output: output.write(data)
                os.replace(temporary, copy_path)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        article = DocxParser().parse(copy_path, rules=rules or RecognitionRules(), source_path=source)
        article.metadata["copy_name"] = copy_path.name
        article.metadata["source_format"] = suffix
        article = self.repository.apply_learning_content(article)
        self.repository.save_article(article)
        return article

    def bind_copy(self, article):
        name = article.metadata.get("copy_name")
        if name and Path(name).name == name:
            article.metadata["document_path"] = str(self.directory / name)
        return article

    def reparse(self, article, rules):
        from src.parser.docx_parser import DocxParser
        self.bind_copy(article)
        source = article.metadata.get("document_path") or article.source_path
        updated = DocxParser().parse(source, rules=rules, source_path=article.source_path)
        if "copy_name" in article.metadata: updated.metadata["copy_name"] = article.metadata["copy_name"]
        updated = self.repository.apply_learning_content(updated)
        self.repository.save_article(updated)
        return updated


def backup_library(repository, destination):
    """Consistent SQLite snapshot + local Word copies, written atomically to a ZIP."""
    destination = Path(destination).resolve()
    if destination.suffix.lower() != ".zip": raise ValueError("备份文件必须使用 .zip 扩展名")
    data_dir = repository.database_path.parent.resolve()
    if destination.is_relative_to(data_dir): raise ValueError("请将备份保存在 data 文件夹之外")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ed_backup_") as temp:
        snapshot = Path(temp) / "english_dictation.db"
        with repository.connection() as source:
            target = sqlite3.connect(snapshot)
            try: source.backup(target)
            finally: target.close()
        fd, archive = tempfile.mkstemp(suffix=".zip", dir=destination.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                output.write(snapshot, "data/english_dictation.db")
                for path in (data_dir / "articles").glob("*.docx"):
                    output.write(path, "data/articles/" + path.name)
                output.writestr("learning-backup.json", json.dumps({"format": "EnglishDictation", "version": 1}))
                output.writestr("恢复说明.txt", "在软件中选择“导入 → 恢复学习备份”。恢复会先自动备份当前学习数据，再整体替换并重新启动；不支持合并。旧版软件可关闭后先保留原 data 文件夹，再将备份中的 data 放到实际数据目录。")
            os.replace(archive, destination)
        finally:
            if os.path.exists(archive): os.unlink(archive)
    return destination
