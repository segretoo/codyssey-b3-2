"""AI 출력 검증 + 후처리(추가 API 호출 없이 규칙에 맞게 다듬기).

미션의 '검증 후 재생성 또는 후처리 중 택1' 중 후처리를 택했다 → 요청 횟수가 늘지 않는다.
규칙: 커밋 제목 ≤72자(권장 50), PR 제목 ≤80자, PR 본문은 Why/What/How to Test + 섹션별 불릿 ≥1.
"""
from __future__ import annotations

import re

COMMIT_TITLE_RECOMMENDED = 50
COMMIT_TITLE_MAX = 72
PR_TITLE_MAX = 80
PLACEHOLDER = "- (내용 없음 — 직접 보완해 주세요)"
SECTIONS = [("Why", "## Why"), ("What", "## What"), ("How to Test", "## How to Test")]

_BULLET_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+")
_SECTION_RE = re.compile(r"^\s*#{1,6}\s*(why|what|how[\s_-]*to[\s_-]*test)\b.*$", re.IGNORECASE)
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(#+\s*|[-*]\s+)?(pr\s*title|commit\s*(message|title)|title|제목|PR\s*제목)\s*[:：]\s*", re.IGNORECASE
)


def clean_output(raw: str) -> str:
    """코드펜스 줄을 제거하고 앞뒤 공백을 정리한다."""
    lines = [ln.rstrip() for ln in raw.replace("\r\n", "\n").split("\n") if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def _clean_title(line: str) -> str:
    t = _TITLE_PREFIX_RE.sub("", line.strip())
    t = re.sub(r"^#+\s+", "", t)  # "# 제목" 형태의 마크다운 헤더 제거
    t = t.strip().strip("`\"'").strip()
    return re.sub(r"\s+", " ", t)


def _truncate(title: str, limit: int) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def _to_bullets(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        out.append(ln.rstrip() if _BULLET_RE.match(ln) else f"- {s}")
    return out


def normalize_commit(raw: str) -> tuple[str, str, list[str]]:
    """(제목, 본문, 후처리 안내 목록) 을 돌려준다. 본문은 없으면 빈 문자열."""
    notes: list[str] = []
    lines = clean_output(raw).split("\n")
    title_raw = next((ln for ln in lines if ln.strip()), "")
    idx = lines.index(title_raw) if title_raw else 0
    title = _clean_title(title_raw) or "chore: 변경 사항 정리"
    rest = lines[idx + 1 :]

    if len(title) > COMMIT_TITLE_MAX:
        notes.append(f"커밋 제목이 {len(title)}자라 {COMMIT_TITLE_MAX}자로 줄였습니다.")
        title = _truncate(title, COMMIT_TITLE_MAX)
    elif len(title) > COMMIT_TITLE_RECOMMENDED:
        notes.append(f"커밋 제목이 {len(title)}자입니다. (권장 {COMMIT_TITLE_RECOMMENDED}자 이내, 최대 {COMMIT_TITLE_MAX}자)")

    body_lines = [ln for ln in rest]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    if body_lines and not any(_BULLET_RE.match(ln) for ln in body_lines):
        body_lines = _to_bullets(body_lines)
        notes.append("본문에 불릿이 없어 줄마다 `- ` 불릿으로 바꿨습니다.")
    body = "\n".join(body_lines).strip()
    return title, body, notes


def normalize_pr(raw: str) -> tuple[str, str, list[str]]:
    """(PR 제목, PR 본문, 후처리 안내 목록). 본문은 Why/What/How to Test 순서로 재조립한다."""
    notes: list[str] = []
    lines = clean_output(raw).split("\n")

    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or _SECTION_RE.match(lines[first]):
        title = "(제목을 생성하지 못했습니다 — 직접 입력해 주세요)"
        body_start = first or 0
        notes.append("AI 출력에 PR 제목 줄이 없었습니다.")
    else:
        title = _clean_title(lines[first]) or "(제목 없음)"
        body_start = first + 1

    if len(title) > PR_TITLE_MAX:
        notes.append(f"PR 제목이 {len(title)}자라 {PR_TITLE_MAX}자로 줄였습니다.")
        title = _truncate(title, PR_TITLE_MAX)

    # 섹션별로 줄 모으기
    buckets: dict[str, list[str]] = {name: [] for name, _ in SECTIONS}
    current: str | None = None
    for ln in lines[body_start:]:
        m = _SECTION_RE.match(ln)
        if m:
            key = re.sub(r"[\s_-]+", " ", m.group(1).lower())
            current = {"why": "Why", "what": "What", "how to test": "How to Test"}[key]
            continue
        if current:
            buckets[current].append(ln)

    parts = []
    for name, header in SECTIONS:
        content = _to_bullets(buckets[name])
        if not content:
            notes.append(f"'{name}' 섹션이 비어 있어 안내 문구를 넣었습니다.")
            content = [PLACEHOLDER]
        elif any(not _BULLET_RE.match(ln) for ln in buckets[name] if ln.strip()):
            notes.append(f"'{name}' 섹션의 일반 문장을 불릿으로 바꿨습니다.")
        parts.append(header + "\n" + "\n".join(content))
    return title, "\n\n".join(parts), notes


def validate_pr(title: str, body: str) -> list[str]:
    """규칙 위반 목록 (비어 있으면 통과). 테스트와 자기검증용."""
    problems = []
    if "\n" in title or not title:
        problems.append("PR 제목은 비어 있지 않은 1줄이어야 합니다.")
    if len(title) > PR_TITLE_MAX:
        problems.append(f"PR 제목이 {PR_TITLE_MAX}자를 넘습니다.")
    for name, header in SECTIONS:
        m = re.search(rf"^{re.escape(header)}\s*\n((?:(?!## ).*\n?)*)", body, re.MULTILINE)
        if not m:
            problems.append(f"'{header}' 섹션이 없습니다.")
        elif not any(_BULLET_RE.match(ln) for ln in m.group(1).splitlines()):
            problems.append(f"'{header}' 섹션에 불릿이 없습니다.")
    return problems


def validate_commit(title: str, body: str) -> list[str]:
    problems = []
    if "\n" in title or not title:
        problems.append("커밋 제목은 비어 있지 않은 1줄이어야 합니다.")
    if len(title) > COMMIT_TITLE_MAX:
        problems.append(f"커밋 제목이 {COMMIT_TITLE_MAX}자를 넘습니다.")
    if body and not any(_BULLET_RE.match(ln) for ln in body.splitlines()):
        problems.append("커밋 본문이 있으면 불릿이 1개 이상 필요합니다.")
    return problems
