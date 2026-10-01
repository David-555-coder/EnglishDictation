"""Validate an upgrade against a disposable copy of a learning database."""
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.database.repository import Repository
from src.services.library import ArticleLibrary
from src.services.practice_service import new_saved_wrong_session


def main():
    original = Path(sys.argv[1]).resolve()
    before_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    root = Path(tempfile.mkdtemp(prefix="EnglishDictationUpgrade_"))
    target_path = root / "data" / "english_dictation.db"
    target_path.parent.mkdir()
    source = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    tables = ("article_library", "wrong_words", "word_examples", "practice_sessions", "practice_answers")
    before = {t: source.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    try: source.backup(target)
    finally: target.close(); source.close()
    repo = Repository(target_path)
    with repo.connection() as connection:
        after = {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert after == before, (before, after)
    histories = repo.list_wrong_practice_history()
    for record in histories:
        session = new_saved_wrong_session(record, repo.get_wrong_answers(record["id"]))
        assert session.total == record["wrong_count"]
        assert all(item.answer.strip() for item in session.article.word_items)
    articles, paths = repo.load_articles()
    library = ArticleLibrary(repo)
    report = {"preserved_rows": before, "restorable_wrong_sessions": len(histories), "cached_articles": len(articles),
              "imported_articles": 0, "total_targets": 0, "missing_sources": [], "failed_sources": []}
    for path in paths:
        if not Path(path).is_file():
            report["missing_sources"].append(Path(path).name); continue
        try:
            article = library.import_file(path)
            report["imported_articles"] += 1
            report["total_targets"] += article.target_count
        except Exception as exc:
            report["failed_sources"].append({"filename": Path(path).name, "error": type(exc).__name__})
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before_hash
    report["original_database_unchanged"] = True
    report["test_directory"] = str(root)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 1 if report["failed_sources"] else 0


if __name__ == "__main__": raise SystemExit(main())
