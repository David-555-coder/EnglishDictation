"""Application-observable evidence, separate from ordinary grading.

Reading is not a successful recall. A closed-book correction after a recorded
attempt is short-term relearning, not a second independent recall.
"""
from datetime import datetime, timezone
import json

SCHEMA = 1


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def as_utc(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.astimezone(timezone.utc)


def begin(session, phase="first", parent=None):
    session.view_state["memory"] = {
        "schema": SCHEMA, "phase": phase, "episode": (
            parent.view_state.get("memory", {}).get("episode", parent.session_key) if parent else session.session_key),
        "parent_key": parent.session_key if parent else None,
        "began_at": utc_now(), "exposed": {}, "protected": {}, "answered_at": {},
    }
    return session


def expose(session, item_ids=None):
    evidence = session.view_state.get("memory")
    if not evidence or evidence.get("schema") != SCHEMA:
        return
    at = utc_now()
    for item_id in item_ids or session.target_ids:
        if item_id not in evidence["exposed"]:
            answer = session.answers.get(item_id, "")
            if answer.strip():
                evidence["protected"][item_id] = answer
            evidence["exposed"][item_id] = at


def answer_changed(session, item_id):
    evidence = session.view_state.get("memory")
    if evidence and evidence.get("schema") == SCHEMA:
        evidence["answered_at"][item_id] = utc_now()
        # Editing a protected answer after revelation invalidates that evidence,
        # even if the user eventually changes it back to the original spelling.
        evidence["protected"].pop(item_id, None)


def kind_for_item(session, item_id):
    evidence = session.view_state.get("memory", {})
    if evidence.get("schema") != SCHEMA:
        return "unknown"
    exposed = item_id in evidence.get("exposed", {})
    if exposed and evidence.get("protected", {}).get(item_id) != session.answers.get(item_id, ""):
        return "exposed"
    return evidence.get("phase", "unknown")


def first_round_result(session):
    """Carry the original result through corrections without merging rounds."""
    original = session.view_state.get("first_round_result")
    if isinstance(original, dict):
        return dict(original)
    item_map = session.article.item_map
    recorded = session.view_state.get("evidence_outcomes", {})
    independent = [r for r in session.results if recorded.get(
        item_map[r.item_id].normalized_answer, {}).get("kind", kind_for_item(session, r.item_id)) == "first"]
    return {
        "label": "首轮回忆" if independent else "阅读后答题",
        "correct": sum(r.is_correct for r in independent) if independent else session.correct_count,
        "total": len(independent) if independent else session.total,
    }


def correction_context(session):
    """Persist only the relationship needed for a later closed-book correction.

    Answer/exposure timestamps belong to the submitted round; never reuse them
    as evidence for a new round. Old history without this context stays conservative.
    """
    memory = session.view_state.get("memory", {})
    if memory.get("schema") != SCHEMA or not isinstance(memory.get("episode"), str):
        return {}
    context = {"schema": SCHEMA, "episode": memory["episode"],
               "first_round_result": first_round_result(session)}
    anchors = {word: result["relearning_anchor"]
               for word, result in session.view_state.get("evidence_outcomes", {}).items()
               if "relearning_anchor" in result}
    if anchors:
        context["relearning_anchors"] = anchors
    if session.view_state.get("consolidation"):
        context["consolidation"] = True
    return context


def restore_correction_context(session, raw, parent_key):
    """Restore an episode, not a successful recall or a fresh first attempt.

    Eligibility still checks each word's current episode and elapsed time at
    submission, so an old/superseded history cannot grant model credit.
    """
    try:
        context = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    if not (isinstance(context, dict) and type(context.get("schema")) is int
            and context["schema"] == SCHEMA and isinstance(context.get("episode"), str)
            and 0 < len(context["episode"]) <= 200 and isinstance(parent_key, str) and parent_key):
        return False
    memory = session.view_state["memory"]
    memory.update(phase="relearning", episode=context["episode"], parent_key=parent_key)
    if context.get("consolidation") is True:
        session.view_state["consolidation"] = True
    original = context.get("first_round_result")
    if (isinstance(original, dict) and original.get("label") in {"首轮回忆", "阅读后答题"}
            and type(original.get("correct")) is int and type(original.get("total")) is int
            and 0 <= original["correct"] <= original["total"]):
        session.view_state["first_round_result"] = dict(original)
    return True


def result_summary(session):
    item_map = session.article.item_map
    recorded = session.view_state.get("evidence_outcomes", {})
    kinds = {r.item_id: recorded.get(item_map[r.item_id].normalized_answer, {}).get("kind", kind_for_item(session, r.item_id)) for r in session.results}
    independent = [r for r in session.results if kinds[r.item_id] == "first"]
    if session.view_state.get("memory", {}).get("phase") == "relearning":
        label = "小组再练结果" if session.view_state.get("consolidation") else "纠错后结果"
        return f"{label}：{session.correct_count}/{session.total}\n不作为新的首轮回忆正确率"
    if independent:
        correct = sum(r.is_correct for r in independent)
        other = f"\n其余{session.total-len(independent)}题不计入首轮回忆" if len(independent)<session.total else ""
        return f"首轮回忆：{correct}/{len(independent)}（{correct / len(independent):.1%}）\n本轮答题正确：{session.correct_count}/{session.total}" + other
    return f"阅读后答题：{session.correct_count}/{session.total}\n待后续独立回忆确认"
