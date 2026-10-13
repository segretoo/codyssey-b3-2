"""safe-mode: AI 로 보내기 전에 diff 에서 민감정보를 가리고, 전송량을 제한한다.

- (A) 정규표현식 기반 마스킹  → mask_text()
- (B) 파일/줄 수 제한         → apply_safe_mode() 의 max_files / max_lines
- 민감 파일(.env, *.pem 등)은 diff 본문 자체를 보내지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MASK = "[MASKED]"
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_LINES = 200

# (이름, 정규식). 정규식에 (?P<val>...) 그룹이 있으면 그 부분만, 없으면 매치 전체를 가린다.
DEFAULT_PATTERNS: list[tuple[str, str]] = [
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    (
        "api_key",
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}"
        r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})",
    ),
    ("bearer", r"(?i)\bBearer\s+(?P<val>[A-Za-z0-9._~+/=-]{16,})"),
    # KEY = "값" / "key": "값"  (따옴표로 감싼 값)
    (
        "secret_quoted",
        r"(?i)(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|access[_-]?key)[A-Za-z0-9_.-]*[\"']?"
        r"\s*[:=]\s*(?P<q>[\"'])(?P<val>[^\"'\s]{6,})(?P=q)",
    ),
    # .env 스타일: KEY=값 (줄 끝까지 단순 문자열일 때만 → os.environ["X"] 같은 코드는 건드리지 않음)
    (
        "secret_plain",
        r"(?im)^[+\- ]?\s*[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|access[_-]?key)[A-Za-z0-9_.-]*"
        r"\s*=\s*(?P<val>[A-Za-z0-9_\-/+=]{8,})\s*$",
    ),
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("phone_kr", r"(?<!\d)01[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"),
]

# 이 경로의 diff 본문은 아예 보내지 않는다 (.env.example 같은 샘플은 제외)
SENSITIVE_FILE_RE = re.compile(
    r"(^|/)(\.env(\.(?!example|sample|template).+)?|id_rsa[^/]*|id_ed25519[^/]*|[^/]*\.(pem|key|p12|pfx)|credentials[^/]*\.json)$",
    re.IGNORECASE,
)


@dataclass
class SafeResult:
    text: str
    masked_count: int = 0
    files_total: int = 0
    files_kept: int = 0
    lines_total: int = 0
    lines_kept: int = 0
    redacted_files: list[str] = field(default_factory=list)


def _compile(patterns: list[tuple[str, str]]) -> list[tuple[str, re.Pattern[str]]]:
    return [(name, re.compile(rx)) for name, rx in patterns]


def _replace(m: re.Match[str]) -> str:
    if "val" in m.re.groupindex:
        base = m.start(0)
        s, e = m.span("val")
        return m.group(0)[: s - base] + MASK + m.group(0)[e - base :]
    return MASK


def mask_text(
    text: str,
    extra_patterns: list[str] | None = None,
    use_default: bool = True,
) -> tuple[str, int]:
    """text 의 민감정보를 [MASKED] 로 바꾸고 (결과, 마스킹 횟수)를 돌려준다."""
    patterns = list(DEFAULT_PATTERNS) if use_default else []
    for i, rx in enumerate(extra_patterns or []):
        patterns.append((f"custom{i + 1}", rx))
    total = 0
    for _name, rx in _compile(patterns):
        text, n = rx.subn(_replace, text)
        total += n
    return text, total


def count_sensitive(text: str) -> int:
    """마스킹하지 않고 '민감정보로 보이는 패턴 수'만 센다 (safe-mode OFF 경고용)."""
    return mask_text(text)[1]


def split_diff(diff: str) -> list[str]:
    """`git diff` 출력을 파일 단위 블록 리스트로 나눈다."""
    blocks: list[str] = []
    cur: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git ") and cur:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def block_path(block: str) -> str:
    first = block.split("\n", 1)[0]
    # "diff --git a/x b/x" → 마지막 " b/" 뒤가 경로
    idx = first.rfind(" b/")
    return first[idx + 3 :].strip('"') if idx != -1 else first


def apply_safe_mode(
    diff: str,
    max_files: int = DEFAULT_MAX_FILES,
    max_lines: int = DEFAULT_MAX_LINES,
    extra_patterns: list[str] | None = None,
) -> SafeResult:
    """민감 파일 제외 → 파일 수 제한 → 마스킹 → 줄 수 제한 순서로 diff 를 줄인다."""
    blocks = split_diff(diff)
    res = SafeResult(text="", files_total=len(blocks), lines_total=len(diff.splitlines()))

    kept: list[str] = []
    for block in blocks[:max_files]:
        path = block_path(block)
        if SENSITIVE_FILE_RE.search(path):
            header = block.split("\n", 1)[0]
            kept.append(f"{header}\n[REDACTED: 민감 파일이라 diff 본문을 전송하지 않음]")
            res.redacted_files.append(path)
        else:
            kept.append(block)
    res.files_kept = len(kept)
    omitted_files = len(blocks) - len(kept)

    # 마스킹을 먼저 해야 여러 줄짜리 비밀키 블록이 잘려서 새는 일이 없다
    text, res.masked_count = mask_text("\n".join(kept), extra_patterns)

    lines = text.splitlines()
    omitted_lines = max(0, len(lines) - max_lines)
    if omitted_lines:
        lines = lines[:max_lines] + [f"... (줄 수 제한으로 이하 {omitted_lines}줄 생략)"]
    if omitted_files:
        lines.append(f"... (파일 수 제한으로 {omitted_files}개 파일 생략)")
    res.lines_kept = min(len(text.splitlines()), max_lines)
    res.text = "\n".join(lines)
    return res
