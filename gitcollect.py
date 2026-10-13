"""Git 변경 사항 수집. 범위는 `git status` 와 `git diff` 로만 제한한다."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


class GitError(Exception):
    """git 실행/저장소 문제. 사용자에게 보여줄 메시지를 담는다."""


@dataclass
class Changes:
    branch: str
    status_lines: list[str]  # 변경 파일 목록 (브랜치 줄 제외)
    diff: str

    @property
    def file_count(self) -> int:
        return len(self.status_lines)

    @property
    def diff_line_count(self) -> int:
        return len(self.diff.splitlines())


def run_git(args: list[str], cwd: str | None = None) -> str:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as e:
        raise GitError("git 이 설치되어 있지 않거나 PATH 에 없습니다.") from e
    if proc.returncode != 0:
        msg = proc.stderr.strip() or f"git {' '.join(args)} 실패 (exit {proc.returncode})"
        if "not a git repository" in msg:
            msg = "Git 저장소가 아닙니다. `git init` 된 프로젝트 루트에서 실행하세요."
        raise GitError(msg)
    return proc.stdout


def parse_branch(line: str) -> str:
    """`git status --porcelain --branch` 첫 줄(`## main...origin/main [ahead 1]`)에서 브랜치명을 뽑는다."""
    s = line[3:] if line.startswith("## ") else line
    if s.startswith("No commits yet on "):
        return s[len("No commits yet on "):].strip()
    if s.startswith("HEAD (no branch)"):
        return "(detached HEAD)"
    return s.split("...")[0].split(" ")[0]


def collect_changes(cwd: str | None = None, staged_only: bool = False) -> Changes:
    """git status / git diff 결과를 모아 Changes 로 돌려준다."""
    raw_status = run_git(["status", "--porcelain=v1", "--branch"], cwd)
    lines = [ln for ln in raw_status.splitlines() if ln.strip()]
    branch = "(알 수 없음)"
    status_lines: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            branch = parse_branch(ln)
        else:
            status_lines.append(ln)

    if not status_lines:
        return Changes(branch, [], "")

    parts: list[str] = []
    staged = run_git(["diff", "--cached", "--no-color", "--no-ext-diff"], cwd)
    if staged.strip():
        parts.append(staged.rstrip("\n"))
    if not staged_only:
        unstaged = run_git(["diff", "--no-color", "--no-ext-diff"], cwd)
        if unstaged.strip():
            parts.append(unstaged.rstrip("\n"))
    return Changes(branch, status_lines, "\n".join(parts))
