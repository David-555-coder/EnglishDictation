"""Daily admission and explicit append limits; never use normal learning storage."""
from datetime import timedelta

import pytest

from src.database.repository import Repository
from src.services.review_continuity import status_text
from src.ui.daily_review_dialog import ReviewQuantityDialog
from test_library_ui import ui
from test_review_continuity import setup, enroll, add_old


def memory_rows(repo):
    with repo.connection() as db:
        return [tuple(r) for r in db.execute("SELECT * FROM wrong_words ORDER BY id")]


def admission_counts(repo):
    with repo.connection() as db:
        return dict(db.execute("""SELECT selection_reason,count(*) FROM daily_review_items
            WHERE day=? GROUP BY selection_reason""", (repo.today().isoformat(),)))


def test_today_182_plus_future_59_still_admits_18_old_words(setup):
    repo, clock = setup
    enroll(repo, 241)
    with repo.connection() as db:
        for start, end, offset in ((183, 201, 1), (202, 206, 2), (207, 241, 3)):
            db.execute("UPDATE wrong_words SET review_due_date=? WHERE id BETWEEN ? AND ?",
                       ((clock[0] + timedelta(days=offset)).isoformat(), start, end))
    add_old(repo, 80)
    before = memory_rows(repo)
    repo.configure_review_capacity(200, 40)
    ids = repo.today_review_ids()
    assert len(ids) == 200
    assert set(range(1, 183)).issubset(ids)
    assert not set(range(183, 242)).intersection(ids)
    assert admission_counts(repo) == {"scheduled_return": 182, "old_intake": 18}
    assert memory_rows(repo) == before


def test_future_peak_warns_without_blocking_today_old_intake(setup):
    repo, clock = setup
    enroll(repo, 95, (clock[0] + timedelta(days=1)).isoformat())
    add_old(repo, 50)
    repo.configure_review_capacity(80, 20)
    assert len(repo.today_review_ids()) == 20
    status = repo.continuity_status()
    assert status["future_days"] == [
        {"day": (clock[0] + timedelta(days=i)).isoformat(), "count": 95 if i == 1 else 0}
        for i in (1, 2, 3)
    ]
    assert "超过日常上限" in status_text(status)
    assert "明天95词" in status_text(status)


def test_real_today_overload_keeps_priority_and_original_dates(setup):
    repo, _ = setup
    enroll(repo, 95)
    add_old(repo, 100)
    before = memory_rows(repo)
    repo.configure_review_capacity(80, 40)
    assert len(repo.today_review_ids()) == 80
    assert admission_counts(repo) == {"scheduled_return": 80}
    assert memory_rows(repo) == before
    assert "今日名额已满" in status_text(repo.continuity_status())


def begun_plan_with_unused_old_slots(repo):
    enroll(repo, 130)
    add_old(repo, 150)
    repo.configure_review_capacity(200, 33)
    ids = repo.today_review_ids(start=True)
    assert len(ids) == 163
    # Represent an already begun plan made under the former future-load guard:
    # 33 old words were selected, although the saved old-word cap is 40.
    with repo.connection() as db:
        db.execute("UPDATE daily_review_days SET old_limit=40 WHERE day=?", (repo.today().isoformat(),))
        db.execute("UPDATE app_settings SET value='40' WHERE key='review_old_limit'")
    return ids


@pytest.mark.parametrize("batch_total,batch_old", [(20, 20), (20, 4), (20, 0), (7, 7)])
def test_append_cannot_spend_unused_slots_from_previous_plan(setup, batch_total, batch_old):
    repo, _ = setup
    original = begun_plan_with_unused_old_slots(repo)
    before = memory_rows(repo)
    added = repo.append_review_capacity(batch_total, batch_old)
    assert added == min(batch_total, batch_old)
    ids = repo.today_review_ids()
    assert ids[:len(original)] == original
    assert len(ids) == len(original) + added
    assert len(ids) == len(set(ids))
    assert repo.review_capacity() == (200, 40)
    assert memory_rows(repo) == before
    reopened = Repository(repo.database_path)
    assert reopened.today_review_ids() == ids


def test_append_fills_returning_words_before_this_batch_old_limit(setup):
    repo, _ = setup
    original = begun_plan_with_unused_old_slots(repo)
    with repo.connection() as db:
        # Five old words became active elsewhere after this daily plan began.
        rows = [r[0] for r in db.execute("""SELECT id FROM wrong_words WHERE review_active=0
            AND id NOT IN (SELECT word_id FROM daily_review_items) ORDER BY id LIMIT 5""")]
        db.executemany("UPDATE wrong_words SET review_active=1 WHERE id=?", [(wid,) for wid in rows])
    assert repo.append_review_capacity(20, 4) == 9
    ids = repo.today_review_ids()
    assert ids[:len(original)] == original
    assert ids[len(original):len(original)+5] == rows
    assert admission_counts(repo) == {"scheduled_return": 135, "old_intake": 37}


def test_append_without_existing_plan_still_adds_at_most_requested_count(setup):
    repo, _ = setup
    enroll(repo, 100)
    # No preview has been generated. An explicit add request must not silently
    # generate the entire regular plan before adding this batch.
    assert repo.append_review_capacity(7, 0) == 7
    assert len(repo.today_review_ids()) == 7
    text = status_text(repo.continuity_status())
    assert "尚未加入当前清单" in text and "暂缺可用原句" not in text


def test_repeated_explicit_append_respects_each_batch_and_never_duplicates(setup):
    repo, _ = setup
    original = begun_plan_with_unused_old_slots(repo)
    for count in (1, 2):
        assert repo.append_review_capacity(20, 20) == 20
        ids = repo.today_review_ids()
        assert len(ids) == len(original)+20*count
        assert len(ids) == len(set(ids))
    assert repo.review_capacity() == (200, 40)


def test_invalid_append_is_atomic(setup):
    repo, _ = setup
    original = begun_plan_with_unused_old_slots(repo)
    capacity = repo.review_capacity(today=True)
    for total, old in ((0, 0), (20, 21), (True, 0), (10000, 0)):
        with pytest.raises(ValueError):
            repo.append_review_capacity(total, old)
        assert repo.today_review_ids() == original
        assert repo.review_capacity(today=True) == capacity


def test_new_admission_version_does_not_repeat_enrollment_migration(setup):
    from src.services.review_continuity import POLICY, ADMISSION_POLICY
    repo, clock = setup
    enroll(repo, 3)
    add_old(repo, 5)
    repo.configure_review_capacity(80, 20)
    repo.today_review_ids(start=True)
    with repo.connection() as db:
        db.execute("UPDATE daily_review_days SET policy_version=?", (POLICY,))
        db.execute("UPDATE daily_review_items SET policy_version=?", (POLICY,))
        original = [tuple(r) for r in db.execute("SELECT * FROM daily_review_items ORDER BY rowid")]
    before = memory_rows(repo)
    reopened = Repository(repo.database_path)
    reopened.enable_review_continuity()
    assert memory_rows(reopened) == before
    with reopened.connection() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM daily_review_items ORDER BY rowid")] == original
    clock[0] += timedelta(days=1)
    reopened.today_review_ids()
    with reopened.connection() as db:
        assert db.execute("SELECT policy_version FROM daily_review_days WHERE day=?",
                          (clock[0].isoformat(),)).fetchone()[0] == ADMISSION_POLICY


def test_finished_plan_does_not_auto_refill_or_rewrite_on_restart(setup):
    repo, _ = setup
    begun_plan_with_unused_old_slots(repo)
    with repo.connection() as db:
        db.execute("UPDATE daily_review_items SET completed=1")
        original = [tuple(r) for r in db.execute("SELECT * FROM daily_review_items ORDER BY rowid")]
    assert Repository(repo.database_path).today_review_ids() == []
    with repo.connection() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM daily_review_items ORDER BY rowid")] == original
    assert "保留原清单" in status_text(repo.continuity_status())


def test_status_explains_today_composition_and_future_counts(setup):
    repo, _ = setup
    enroll(repo, 74)
    add_old(repo, 100)
    repo.configure_review_capacity(80, 20)
    status = repo.continuity_status()
    assert status["planned_return"] == 74 and status["planned_old"] == 6
    text = status_text(status)
    assert "到期复习74词" in text and "重新学习旧词6词" in text
    assert "今日名额已满" in text
    assert "今日作答后会变化" in text


def test_future_warning_uses_default_capacity_not_today_extra(setup):
    repo, clock = setup
    enroll(repo, 90, (clock[0] + timedelta(days=1)).isoformat())
    repo.configure_review_capacity(80, 0)
    repo.configure_review_capacity(120, 0, today_only=True)
    status = repo.continuity_status()
    assert status["total_limit"] == 120 and status["daily_default_limit"] == 80
    assert "超过日常上限" in status_text(status)


def test_dialog_describes_real_batch_caps_and_nonbinding_future_counts(setup, ui):
    repo, _ = setup
    begun_plan_with_unused_old_slots(repo)
    dialog = ReviewQuantityDialog(20, 4, repo.continuity_status(), append=True)
    try:
        assert "本次最多追加20词" in dialog.summary.text()
        assert "其中旧词最多4词" in dialog.summary.text()
        assert "明天" in dialog.future_note.text()
        assert "不自动补齐" in dialog.append_note.text()
    finally:
        dialog.close()
