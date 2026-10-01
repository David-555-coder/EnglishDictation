"""Validated, replace-only recovery. Called with the window closed and lock held."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
from datetime import datetime
import zipfile
from dataclasses import dataclass
from contextlib import closing

from src.services.library import backup_library
from src.services.serialization import article_from_dict, session_from_dict

MANIFEST = "learning-backup.json"
FORMAT_VERSION = 1
MAX_FILES = 5000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
RECOVERY_MARKER = ".restore-recovery.json"

# Columns created by the original schema; later optional columns are migrated
# by Repository on restart. Do not initialise a backup just to validate it.
REQUIRED_COLUMNS = {
    "wrong_words": "id normalized_answer answer chinese error_count occurrence_count first_error_at last_error_at",
    "word_examples": "id wrong_word_id source_article source_path sentence occurrence_count",
    "practice_sessions": "id submitted_at article_name source_path practice_type target_count correct_count wrong_count accuracy viewed_answer",
    "practice_answers": "id session_id item_id answer normalized_answer user_answer is_correct is_blank chinese sentence",
    "article_library": "id source_path filename added_at",
    "practice_drafts": "session_key article_path payload updated_at",
    "app_settings": "key value",
}


@dataclass
class PreparedRestore:
    directory: Path
    database_path: Path
    source: Path
    warnings: tuple[str, ...] = ()

    def close(self):
        # A failed rollback must remain available for startup recovery.
        marker = self.database_path.parent / RECOVERY_MARKER
        if marker.exists():
            try:
                if json.loads(marker.read_text(encoding="utf-8"))["stage"] == self.directory.name:
                    return
            except (ValueError, KeyError, OSError):
                return
        if self.directory.exists():
            try:
                shutil.rmtree(self.directory)
            except OSError:
                logging.getLogger(__name__).warning("临时恢复目录清理失败：%s", self.directory, exc_info=True)


def _safe_member(name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or any(part in {".", ".."} for part in name.rstrip("/").split("/"))):
        raise ValueError("备份包含不安全的文件路径")
    if name in {"data/", "data/articles/", "data/english_dictation.db", "恢复说明.txt", MANIFEST}:
        return True
    if len(path.parts) == 3 and path.parts[:2] == ("data", "articles") and path.suffix.lower() == ".docx":
        stem = path.name.split(".")[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
            raise ValueError("备份包含无效的文件名")
        if path.name.endswith((" ", ".")) or any(char in path.name for char in '<>"|?*'):
            raise ValueError("备份包含无效的文件名")
        return True
    raise ValueError("这不是软件支持的完整学习备份")


def _validate_database(path, articles):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("备份中的学习数据库已损坏")
        if connection.execute("PRAGMA user_version").fetchone()[0] > FORMAT_VERSION:
            raise ValueError("备份来自较新的软件版本，请先升级软件")
        tables = {name for name, kind in connection.execute("SELECT name, type FROM sqlite_master") if kind == "table"}
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                raise ValueError(f"备份缺少学习数据表：{table}")
            present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not set(columns.split()).issubset(present):
                raise ValueError(f"备份数据库结构不兼容：{table}")
        faults = connection.execute("PRAGMA foreign_key_check").fetchall()
        # Early releases could leave orphan examples after removing a wrong
        # word. Keep those inert legacy rows verbatim, not a destructive repair.
        # Broken grade/answer associations are still rejected.
        if any(row[0] != "word_examples" or row[2] != "wrong_words" for row in faults):
            raise ValueError("备份中的学习记录关联不完整")
        warnings = (f"备份含 {len(faults)} 条无对应错词的旧例句，将原样保留，不会自动删除或合并。",) if faults else ()
        referenced = set()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(article_library)")}
        if "snapshot_json" in columns:
            for (payload,) in connection.execute("SELECT snapshot_json FROM article_library WHERE snapshot_json != ''"):
                article = article_from_dict(json.loads(payload))
                name = article.metadata.get("copy_name")
                if name:
                    _safe_member("data/articles/" + name)
                    referenced.add(name)
        for (payload,) in connection.execute("SELECT payload FROM practice_drafts"):
            session = session_from_dict(json.loads(payload))
            name = session.article.metadata.get("copy_name")
            if name:
                _safe_member("data/articles/" + name)
                referenced.add(name)
        if any(not (articles / name).is_file() for name in referenced):
            raise ValueError("备份缺少文章副本，不能完整恢复")
        return warnings


def prepare_library_restore(repository, source):
    """Extract only recognised members into a private same-volume staging area."""
    source = Path(source).resolve()
    directory = Path(tempfile.mkdtemp(prefix=".ed_restore_", dir=repository.database_path.parent.parent))
    prepared = PreparedRestore(directory, repository.database_path.resolve(), source)
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > MAX_FILES or sum(member.file_size for member in members) > MAX_TOTAL_BYTES:
                raise ValueError("备份过大，超出安全恢复限制")
            seen = set()
            for member in members:
                _safe_member(member.filename)
                key = member.filename.casefold()
                if key in seen:
                    raise ValueError("备份中存在重复文件")
                seen.add(key)
                if (member.file_size > MAX_FILE_BYTES or member.flag_bits & 1
                        or stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK):
                    raise ValueError("备份包含过大的文件、加密文件或链接")
                target = directory.joinpath(*PurePosixPath(member.filename).parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as input_file, target.open("xb") as output_file:
                        shutil.copyfileobj(input_file, output_file)
            manifest = directory / MANIFEST
            if manifest.exists():
                metadata = json.loads(manifest.read_text(encoding="utf-8"))
                if metadata.get("format") != "EnglishDictation" or metadata.get("version") != FORMAT_VERSION:
                    raise ValueError("备份格式版本不兼容")
        database = directory / "data" / "english_dictation.db"
        if not database.is_file():
            raise ValueError("备份中没有学习数据库")
        articles = directory / "data" / "articles"
        articles.mkdir(parents=True, exist_ok=True)
        prepared.warnings = _validate_database(database, articles)
        for article in articles.iterdir():
            with zipfile.ZipFile(article) as document:
                if ("word/document.xml" not in document.namelist()
                        or sum(info.file_size for info in document.infolist()) > MAX_TOTAL_BYTES
                        or document.testzip() is not None):
                    raise ValueError("备份中的文章副本已损坏")
        return prepared
    except Exception as exc:
        prepared.close()
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"备份无法读取或内容不完整：{exc}") from exc


def recover_interrupted_restore(database_path):
    """Rollback an interrupted multi-file swap before any repository opens."""
    database_path = Path(database_path).resolve()
    data_dir = database_path.parent
    marker = data_dir / RECOVERY_MARKER
    if not marker.exists():
        return False
    metadata = json.loads(marker.read_text(encoding="utf-8"))
    name = metadata["stage"]
    if not isinstance(name, str) or Path(name).name != name or not name.startswith(".ed_restore_"):
        raise ValueError("恢复日志路径无效，原数据未被改动")
    stage = data_dir.parent / name
    if stage.is_symlink() or not stage.is_dir():
        raise ValueError("恢复现场缺失，请使用恢复前自动备份")
    previous = stage / "previous"
    for suffix in ("", "-wal", "-shm", "-journal"):
        old = previous / (database_path.name + suffix)
        if old.exists():
            os.replace(old, Path(str(database_path) + suffix))
    articles = data_dir / "articles"
    old_articles = previous / "articles"
    if old_articles.exists():
        if articles.exists():
            os.replace(articles, stage / "failed-articles")
        os.replace(old_articles, articles)
    elif not metadata["had_articles"] and articles.exists():
        os.replace(articles, stage / "failed-articles")
    marker.unlink()
    shutil.rmtree(stage)
    return True


def restore_library(repository, prepared, safety_destination=None):
    """Back up current state, verify that backup, then swap with rollback journal.

    Caller must own the app instance lock and close all UI/data writers first.
    No database object is created here, so recovery cannot prune existing rows.
    """
    database_path = repository.database_path.resolve()
    if prepared.database_path != database_path:
        raise ValueError("恢复目标与校验时的数据目录不一致")
    data_dir = database_path.parent
    marker = data_dir / RECOVERY_MARKER
    if marker.exists():
        raise ValueError("上一次恢复尚未完成，请重新启动软件")
    if safety_destination is None:
        safety_destination = data_dir.parent / "backups" / "recovery" / f"恢复前学习备份-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
    safety_destination = Path(safety_destination).resolve()
    if safety_destination == prepared.source or safety_destination.exists():
        raise ValueError("恢复前备份不能覆盖已有文件")
    safety = backup_library(repository, safety_destination)
    checked = prepare_library_restore(repository, safety)
    checked.close()  # Never replace live data unless the safety backup validates.
    previous = prepared.directory / "previous"
    previous.mkdir()
    articles = data_dir / "articles"
    marker.write_text(json.dumps({"stage": prepared.directory.name, "had_articles": articles.exists()},
                                 ensure_ascii=False), encoding="utf-8")
    try:
        for suffix in ("", "-wal", "-shm", "-journal"):
            current = Path(str(database_path) + suffix)
            if current.exists():
                os.replace(current, previous / current.name)
        if articles.exists():
            os.replace(articles, previous / "articles")
        os.replace(prepared.directory / "data" / "articles", articles)
        os.replace(prepared.directory / "data" / "english_dictation.db", database_path)
        marker.unlink()  # Commit point. Only staged old files are removed below.
    except Exception:
        recover_interrupted_restore(database_path)
        raise
    finally:
        prepared.close()
    return safety
