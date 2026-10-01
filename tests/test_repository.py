import sqlite3

import pytest

from src.database.repository import Repository


def test_delete_practice_session_cascades_answers_but_not_wrong_words(tmp_path):
    repository = Repository(tmp_path / "test.db")
    with repository.connection() as connection:
        cursor = connection.execute("""INSERT INTO practice_sessions
            (submitted_at,article_name,source_path,practice_type,target_count,correct_count,wrong_count,accuracy,viewed_answer)
            VALUES ('2026-01-01','a.docx','a.docx','整篇文章',1,0,1,0,0)""")
        session_id = cursor.lastrowid
        connection.execute("""INSERT INTO practice_answers
            (session_id,item_id,answer,normalized_answer,user_answer,is_correct,is_blank,chinese,sentence)
            VALUES (?, 'x', 'word', 'word', 'bad', 0, 0, '', 'word.')""", (session_id,))
        connection.execute("""INSERT INTO wrong_words
            (normalized_answer,answer,error_count,occurrence_count,first_error_at,last_error_at)
            VALUES ('word','word',1,1,'2026-01-01','2026-01-01')""")
    assert repository.delete_practice_session(session_id)
    with repository.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM practice_answers").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM wrong_words").fetchone()[0] == 1


def test_article_library_is_persistent_and_removable(tmp_path):
    repository = Repository(tmp_path / "test.db")
    article = tmp_path / "中文 article.docx"
    article.write_bytes(b"placeholder")
    repository.save_article_path(article)
    repository.save_article_path(article)
    repository = Repository(tmp_path / "test.db")
    assert repository.list_article_paths() == [str(article.resolve())]
    assert repository.delete_article_path(article)
    assert repository.list_article_paths() == []


def test_connection_is_closed_and_failed_transaction_is_rolled_back(tmp_path):
    repository = Repository(tmp_path / "test.db")
    with pytest.raises(RuntimeError):
        with repository.connection() as connection:
            connection.execute("INSERT INTO article_library(source_path, filename, added_at) VALUES ('x', 'x', 'now')")
            raise RuntimeError("simulate interrupted write")
    assert repository.list_article_paths() == []
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_existing_history_survives_article_library_migration(tmp_path):
    path = tmp_path / "old.db"
    repository = Repository(path)
    with repository.connection() as connection:
        connection.execute("DROP TABLE article_library")
        connection.execute("""INSERT INTO practice_sessions
            (submitted_at,article_name,source_path,practice_type,target_count,correct_count,wrong_count,accuracy)
            VALUES ('2026-01-01','old.docx','old.docx','整篇文章',1,0,1,0)""")
    reopened = Repository(path)
    assert reopened.list_article_paths() == []
    assert reopened.list_history()[0]["article_name"] == "old.docx"


def test_only_ten_newest_article_slots_are_retained(tmp_path):
    path = tmp_path / "drafts.db"
    repository = Repository(path)
    with repository.connection() as connection:
        for index in range(12):
            connection.execute(
                "INSERT INTO practice_drafts VALUES (?, ?, ?, ?)",
                (f"draft-{index:02d}", f"article{index}.docx", '{"mode":"整篇文章","submitted":false}', f"2026-01-01T00:00:{index:02d}"))

    Repository(path)  # Startup cleanup applies the retention policy.
    with repository.connection() as connection:
        keys = [row[0] for row in connection.execute(
            "SELECT session_key FROM practice_drafts ORDER BY updated_at")]
    assert keys == [f"draft-{index:02d}" for index in range(2, 12)]


def test_wrong_history_natural_sort_and_atomic_batch_delete(tmp_path):
    repository = Repository(tmp_path / "history.db")
    ids = []
    with repository.connection() as connection:
        for index, name in enumerate(("djci10.docx", "djci2.docx", "djci1.docx")):
            cursor = connection.execute("""INSERT INTO practice_sessions
                (submitted_at,article_name,source_path,practice_type,target_count,
                 correct_count,wrong_count,accuracy,viewed_answer)
                VALUES (?, ?, ?, '整篇文章', 2, 1, 1, 50, 0)""",
                (f"2026-01-01T00:00:0{index}", name, name))
            ids.append(cursor.lastrowid)
        connection.execute("""INSERT INTO wrong_words
            (normalized_answer,answer,error_count,occurrence_count,first_error_at,last_error_at)
            VALUES ('word','word',1,1,'2026-01-01','2026-01-01')""")

    records = repository.list_wrong_practice_history(order_by="article")
    assert [record["article_name"] for record in records] == ["djci1.docx", "djci2.docx", "djci10.docx"]
    assert repository.delete_practice_sessions([ids[0], ids[2]]) == 2
    assert [record["id"] for record in repository.list_history()] == [ids[1]]
    assert len(repository.list_wrong_words()) == 1
