"""Utility helpers for conversation-oriented handlers."""

from __future__ import annotations

from typing import Any, List, Optional


def normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        items: List[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                candidate = next(
                    (
                        item.get(key)
                        for key in ("question", "text", "content", "value")
                        if isinstance(item.get(key), str) and item.get(key).strip()
                    ),
                    None,
                )
                text = candidate if isinstance(candidate, str) else str(item)
            else:
                text = str(item)
            text = text.strip()
            if text:
                items.append(text)
        return items
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def summarize_original_action(original_action: Any) -> Optional[str]:
    if not isinstance(original_action, dict):
        return None

    action_type = original_action.get("type")
    raw_params = original_action.get("params")
    params = raw_params if isinstance(raw_params, dict) else {}

    action_label = ""
    if isinstance(action_type, str) and action_type:
        action_label = action_type.replace("_", " ").strip()

    request_text = params.get("request")
    if isinstance(request_text, str):
        request_text = request_text.strip()
    else:
        request_text = None

    target_file = params.get("file_path")
    if isinstance(target_file, str):
        target_file = target_file.strip()
    else:
        target_file = None

    details: List[str] = []
    if action_label:
        details.append(action_label)
    if request_text:
        details.append(request_text)
    elif target_file:
        details.append(f"work involving {target_file}")

    return " - ".join(details) if details else None


def build_discuss_fallback_response(
    questions: List[str],
    unclear_aspects: List[str],
    understood_summary: Optional[str],
) -> str:
    intro_parts: List[str] = []
    if understood_summary:
        intro_parts.append(f"I'd love to help with {understood_summary}")
    else:
        intro_parts.append("I'd love to help out")
    if unclear_aspects:
        intro_parts.append("but I need a quick clarification first")
    intro = " ".join(intro_parts) + "."

    question_block = "\n".join(f"- {question}" for question in questions) if questions else "- Could you share a bit more detail?"

    closing = "Once I have these details I'll jump right back into it."

    clarification_note = ""
    if unclear_aspects:
        clarification_note = "I'm specifically unsure about: " + "; ".join(unclear_aspects) + "\n\n"

    return f"{intro}\n\n{clarification_note}To get moving, could you help me with:\n{question_block}\n\n{closing}"

