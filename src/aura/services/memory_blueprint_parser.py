"""Blueprint parsing utilities for Aura's project memory system."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


ARCHITECTURE_ALIASES: Dict[str, str] = {
    "framework": "Framework",
    "backend": "Backend",
    "frontend": "Frontend",
    "database": "Database",
    "db": "Database",
    "storage": "Storage",
    "cache": "Caching",
    "message broker": "Messaging",
    "queue": "Messaging",
    "auth": "Authentication",
    "authentication": "Authentication",
    "authorization": "Authorization",
    "api": "API Style",
    "architecture": "Architecture",
    "pattern": "Architecture Pattern",
    "language": "Language",
    "infra": "Infrastructure",
    "deployment": "Deployment",
    "hosting": "Hosting",
}

PATTERN_SECTION_HINTS = ("pattern", "convention", "guideline", "best practice")
NEXT_STEP_HINTS = ("next step", "roadmap", "timeline", "milestone", "plan")
ISSUE_HINTS = ("risk", "issue", "concern", "tech debt", "limitation")


@dataclass
class ParsedDecision:
    """Structured architectural decision extracted from blueprint content."""

    category: str
    decision: str
    rationale: str


@dataclass
class ParsedPattern:
    """Structured code pattern or convention."""

    category: str
    description: str
    example: Optional[str] = None


@dataclass
class ParsedIssue:
    """Known issue or risk extracted from the blueprint."""

    description: str
    severity: str = "medium"


@dataclass
class BlueprintParseResult:
    """Aggregate of blueprint parsing output."""

    decisions: List[ParsedDecision]
    patterns: List[ParsedPattern]
    next_steps: List[str]
    summary_notes: List[str]
    issues: List[ParsedIssue]
    state_updates: Dict[str, Any]


class BlueprintParser:
    """Parses architect blueprints (markdown or JSON) into structured memory."""

    def parse(self, payload: Dict[str, Any]) -> BlueprintParseResult:
        blueprint = payload.get("blueprint")
        metadata = payload.get("metadata") or {}

        text_fragments = self._collect_text_fragments(blueprint, metadata, payload)
        sections = self._partition_sections(text_fragments)

        decisions = self._extract_decisions(sections, blueprint)
        patterns = self._extract_patterns(sections, blueprint)
        next_steps = self._extract_next_steps(sections, blueprint)
        issues = self._extract_issues(sections, blueprint)
        summary_notes = self._build_summary(decisions, next_steps)
        state_updates = self._compile_state_updates(payload, decisions, patterns, next_steps, issues)

        return BlueprintParseResult(
            decisions=self._deduplicate_decisions(decisions),
            patterns=self._deduplicate_patterns(patterns),
            next_steps=self._deduplicate_preserve_order(next_steps),
            summary_notes=summary_notes,
            issues=self._deduplicate_issues(issues),
            state_updates=state_updates,
        )

    # --------------------------------------------------------------------- parsing

    def _collect_text_fragments(
        self,
        blueprint: Any,
        metadata: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> List[str]:
        fragments: List[str] = []

        def _append(value: Optional[str]) -> None:
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    fragments.append(cleaned)

        if isinstance(blueprint, str):
            _append(blueprint)
        elif isinstance(blueprint, dict):
            fragments.extend(self._collect_strings_from_mapping(blueprint))
        elif isinstance(blueprint, (list, tuple)):
            for item in blueprint:
                if isinstance(item, str):
                    _append(item)

        for key in ("blueprint_markdown", "design_notes", "summary"):
            _append(metadata.get(key))
        _append(payload.get("prompt"))
        _append(payload.get("request"))
        return fragments

    def _collect_strings_from_mapping(self, data: Dict[str, Any]) -> List[str]:
        collected: List[str] = []
        for value in data.values():
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    collected.append(cleaned)
            elif isinstance(value, dict):
                collected.extend(self._collect_strings_from_mapping(value))
            elif isinstance(value, list):
                for inner in value:
                    if isinstance(inner, str):
                        cleaned_inner = inner.strip()
                        if cleaned_inner:
                            collected.append(cleaned_inner)
                    elif isinstance(inner, dict):
                        collected.extend(self._collect_strings_from_mapping(inner))
        return collected

    def _partition_sections(self, fragments: Sequence[str]) -> Dict[str, List[str]]:
        sections: Dict[str, List[str]] = {"__root__": []}
        current = "__root__"
        in_code_block = False

        for fragment in fragments:
            for raw_line in fragment.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    continue
                if line.startswith("#"):
                    current = line.lstrip("#").strip().lower() or "__root__"
                    sections.setdefault(current, [])
                    continue
                sections.setdefault(current, []).append(line)
        return sections

    def _extract_decisions(
        self,
        sections: Dict[str, List[str]],
        blueprint: Any,
    ) -> List[ParsedDecision]:
        decisions: List[ParsedDecision] = []

        for lines in sections.values():
            for key, value in self._extract_key_value_pairs(lines):
                decisions.append(self._build_decision_from_pair(key, value))

        if isinstance(blueprint, dict):
            decisions.extend(self._extract_decisions_from_mapping(blueprint))

        return [d for d in decisions if d.decision]

    def _extract_patterns(
        self,
        sections: Dict[str, List[str]],
        blueprint: Any,
    ) -> List[ParsedPattern]:
        patterns: List[ParsedPattern] = []

        for name, lines in sections.items():
            if not any(hint in name for hint in PATTERN_SECTION_HINTS):
                continue
            for bullet in self._extract_bullets(lines):
                patterns.append(ParsedPattern(category=name.title(), description=bullet))

        if isinstance(blueprint, dict):
            patterns.extend(self._extract_patterns_from_mapping(blueprint))

        return [p for p in patterns if p.description]

    def _extract_next_steps(
        self,
        sections: Dict[str, List[str]],
        blueprint: Any,
    ) -> List[str]:
        steps: List[str] = []

        for name, lines in sections.items():
            if any(hint in name for hint in NEXT_STEP_HINTS):
                steps.extend(self._extract_bullets(lines))

        if isinstance(blueprint, dict):
            raw_steps = blueprint.get("next_steps") or blueprint.get("milestones") or blueprint.get("timeline")
            if isinstance(raw_steps, list):
                for step in raw_steps:
                    if isinstance(step, str) and step.strip():
                        steps.append(step.strip())

        return steps

    def _extract_issues(
        self,
        sections: Dict[str, List[str]],
        blueprint: Any,
    ) -> List[ParsedIssue]:
        issues: List[ParsedIssue] = []

        for name, lines in sections.items():
            if any(hint in name for hint in ISSUE_HINTS):
                for bullet in self._extract_bullets(lines):
                    severity = self._extract_severity(bullet)
                    issues.append(ParsedIssue(description=self._strip_severity(bullet), severity=severity))

        if isinstance(blueprint, dict):
            for key in ("risks", "issues", "tech_debt"):
                raw = blueprint.get(key)
                if isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict):
                            description = str(item.get("description") or item.get("risk") or "").strip()
                            severity = str(item.get("severity") or "medium").lower()
                            if description:
                                issues.append(ParsedIssue(description=description, severity=severity))
                        elif isinstance(item, str) and item.strip():
                            severity = self._extract_severity(item)
                            issues.append(ParsedIssue(description=self._strip_severity(item), severity=severity))

        return issues

    # --------------------------------------------------------------------- helpers

    def _extract_key_value_pairs(self, lines: Iterable[str]) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        pattern = re.compile(r"^(?:[-*\d\.]+\s*)?(?P<key>[\w\s\/&\(\)\-]+?)\s*[:\-–—]\s*(?P<value>.+)$")
        for line in lines:
            match = pattern.match(line)
            if match:
                pairs.append((match.group("key").strip(), match.group("value").strip()))
        return pairs

    def _extract_bullets(self, lines: Iterable[str]) -> List[str]:
        bullets: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("-") or stripped.startswith("*"):
                bullets.append(stripped.lstrip("-* ").strip())
            elif re.match(r"^\d+[).\s]", stripped):
                bullets.append(re.sub(r"^\d+[).\s]+", "", stripped).strip())
        return bullets

    def _build_decision_from_pair(self, raw_key: str, raw_value: str) -> ParsedDecision:
        category = self._normalise_category(raw_key)
        decision, rationale = self._split_rationale(raw_value)
        return ParsedDecision(
            category=category,
            decision=decision,
            rationale=rationale or "Captured from design blueprint.",
        )

    def _extract_decisions_from_mapping(self, blueprint: Dict[str, Any]) -> List[ParsedDecision]:
        decisions: List[ParsedDecision] = []
        for key, value in blueprint.items():
            if not value:
                continue
            if key in ("files", "project_name", "project_slug"):
                continue
            if isinstance(value, str):
                decisions.append(self._build_decision_from_pair(key, value))
            elif isinstance(value, dict):
                nested = value.get("decision") or value.get("choice")
                rationale = value.get("rationale") or value.get("reason") or ""
                if nested:
                    decisions.append(
                        ParsedDecision(
                            category=self._normalise_category(key),
                            decision=str(nested).strip(),
                            rationale=rationale.strip() or "Captured from design blueprint.",
                        )
                    )
                else:
                    decisions.extend(self._extract_decisions_from_mapping(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        decision = item.get("decision") or item.get("value") or item.get("choice")
                        rationale = item.get("rationale") or item.get("reason") or ""
                        if decision:
                            decisions.append(
                                ParsedDecision(
                                    category=self._normalise_category(item.get("category") or key),
                                    decision=str(decision).strip(),
                                    rationale=rationale.strip() or "Captured from design blueprint.",
                                )
                            )
                    elif isinstance(item, str):
                        decisions.append(self._build_decision_from_pair(key, item))
        return decisions

    def _extract_patterns_from_mapping(self, blueprint: Dict[str, Any]) -> List[ParsedPattern]:
        patterns: List[ParsedPattern] = []
        for key in ("patterns", "code_patterns", "conventions", "guidelines"):
            section = blueprint.get(key)
            if isinstance(section, list):
                for item in section:
                    if isinstance(item, dict):
                        description = item.get("pattern") or item.get("description") or item.get("value")
                        example = item.get("example")
                        if description:
                            patterns.append(
                                ParsedPattern(
                                    category=self._normalise_category(item.get("category") or key),
                                    description=str(description).strip(),
                                    example=str(example).strip() if isinstance(example, str) else None,
                                )
                            )
                    elif isinstance(item, str) and item.strip():
                        patterns.append(
                            ParsedPattern(
                                category=self._normalise_category(key),
                                description=item.strip(),
                            )
                        )
        return patterns

    def _split_rationale(self, value: str) -> Tuple[str, str]:
        if "(" in value and value.endswith(")"):
            head, _, tail = value.partition("(")
            return head.strip(), tail.rstrip(")").strip()
        for separator in (" - ", " — ", " – ", " -- "):
            if separator in value:
                decision, rationale = value.split(separator, 1)
                return decision.strip(), rationale.strip()
        return value.strip(), ""

    def _normalise_category(self, key: Optional[str]) -> str:
        if not key:
            return "Architecture"
        lowered = key.strip().lower()
        for alias, canonical in ARCHITECTURE_ALIASES.items():
            if alias in lowered:
                return canonical
        return key.strip().title()

    def _extract_severity(self, text: str) -> str:
        match = re.search(r"\[(critical|high|medium|low)\]", text, re.IGNORECASE)
        if match:
            return match.group(1).lower()
        match = re.search(r"\b(critical|high|medium|low)\b", text, re.IGNORECASE)
        if match:
            return match.group(1).lower()
        return "medium"

    def _strip_severity(self, text: str) -> str:
        cleaned = re.sub(r"\[(critical|high|medium|low)\]\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\b(critical|high|medium|low)\b[\s:–—-]*", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def _build_summary(
        self,
        decisions: Sequence[ParsedDecision],
        next_steps: Sequence[str],
    ) -> List[str]:
        notes: List[str] = []
        if decisions:
            preview = ", ".join(f"{d.category}: {d.decision}" for d in decisions[:5])
            notes.append(preview)
        if next_steps:
            steps_preview = "; ".join(next_steps[:3])
            notes.append(f"Next steps: {steps_preview}")
        return notes

    def _compile_state_updates(
        self,
        payload: Dict[str, Any],
        decisions: Sequence[ParsedDecision],
        patterns: Sequence[ParsedPattern],
        next_steps: Sequence[str],
        issues: Sequence[ParsedIssue],
    ) -> Dict[str, Any]:
        state: Dict[str, Any] = {}
        if payload.get("project_name"):
            state["project_name"] = payload["project_name"]
        if payload.get("request"):
            state["latest_request"] = payload["request"]
        metadata = payload.get("metadata") or {}
        if metadata.get("generated_at"):
            state["blueprint_generated_at"] = metadata["generated_at"]

        if decisions:
            state["architecture_overview"] = [f"{d.category}: {d.decision}" for d in decisions]
            for decision in decisions:
                lowered = decision.category.lower()
                if "framework" in lowered:
                    state.setdefault("framework", decision.decision)
                elif "database" in lowered or "storage" in lowered:
                    state.setdefault("database", decision.decision)
                elif "auth" in lowered:
                    state.setdefault("authentication", decision.decision)
                elif "api" in lowered:
                    state.setdefault("api_style", decision.decision)
        if patterns:
            state["code_patterns_summary"] = [f"{p.category}: {p.description}" for p in patterns[:5]]
        if next_steps:
            state["next_steps"] = list(next_steps)
        if issues:
            state["open_risks"] = [f"[{issue.severity}] {issue.description}" for issue in issues]

        planned = self._extract_planned_files(payload.get("blueprint"))
        if planned:
            state["planned_files"] = planned
        files_to_watch = payload.get("files_to_watch")
        if isinstance(files_to_watch, list):
            state["files_to_watch"] = [f for f in files_to_watch if isinstance(f, str)]
        return state

    def _extract_planned_files(self, blueprint: Any) -> List[str]:
        files: List[str] = []
        if not isinstance(blueprint, dict):
            return files
        file_specs = blueprint.get("files")
        if isinstance(file_specs, list):
            for spec in file_specs:
                if not isinstance(spec, dict):
                    continue
                path = spec.get("file_path") or spec.get("path")
                description = spec.get("description") or ""
                if isinstance(path, str) and path.strip():
                    descriptor = path.strip()
                    if isinstance(description, str) and description.strip():
                        descriptor = f"{descriptor} — {description.strip()}"
                    files.append(descriptor)
        return files

    # ------------------------------------------------------------------- deduping

    def _deduplicate_decisions(self, decisions: Iterable[ParsedDecision]) -> List[ParsedDecision]:
        deduped: Dict[Tuple[str, str], ParsedDecision] = {}
        for decision in decisions:
            key = (decision.category.lower(), decision.decision.lower())
            if key in deduped:
                if decision.rationale and decision.rationale != deduped[key].rationale:
                    deduped[key].rationale = decision.rationale
            else:
                deduped[key] = decision
        return list(deduped.values())

    def _deduplicate_patterns(self, patterns: Iterable[ParsedPattern]) -> List[ParsedPattern]:
        deduped: Dict[Tuple[str, str], ParsedPattern] = {}
        for pattern in patterns:
            key = (pattern.category.lower(), pattern.description.lower())
            if key not in deduped:
                deduped[key] = pattern
        return list(deduped.values())

    def _deduplicate_issues(self, issues: Iterable[ParsedIssue]) -> List[ParsedIssue]:
        deduped: Dict[str, ParsedIssue] = {}
        for issue in issues:
            key = issue.description.lower()
            deduped.setdefault(key, issue)
        return list(deduped.values())

    def _deduplicate_preserve_order(self, items: Iterable[str]) -> List[str]:
        seen: set = set()
        ordered: List[str] = []
        for item in items:
            if item not in seen:
                ordered.append(item)
                seen.add(item)
        return ordered

