#!/usr/bin/env python3
"""AI 기반 Git 커밋 메시지 / PR 초안 생성기 (CLI).

    python main.py commit [옵션]
    python main.py pr [옵션]

흐름: git status/diff 수집 → (safe-mode) 마스킹·제한 → 프롬프트 구성 → AI API 1회 호출
      → 출력 형식 검증·후처리 → 터미널 출력.  push / PR 생성 같은 원격 반영은 하지 않는다.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import postprocess
from ai_client import DEFAULT_BASE_URLS, DEFAULT_MODELS, PROVIDERS, AIClient, AIConfig, AIError, is_reasoning_model
from gitcollect import GitError, collect_changes
from prompts import build_messages
from safety import DEFAULT_MAX_FILES, DEFAULT_MAX_LINES, apply_safe_mode, count_sensitive

DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 600
# safe-mode OFF 여도 비용 폭주 방지를 위해 적용하는 최소한의 상한(문자 수)
HARD_DIFF_CHAR_LIMIT = 60000

EXIT_OK, EXIT_ERROR, EXIT_GIT = 0, 1, 2


# ---------------------------------------------------------------- 출력 헬퍼
def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


def error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- CLI 정의
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    api = common.add_argument_group("AI API 옵션 (기본값은 환경변수 → 내장 기본값 순)")
    api.add_argument("--provider", choices=PROVIDERS, help="AI 제공자 (env: AI_PROVIDER, 기본 openai)")
    api.add_argument("--model", help="모델명 (env: AI_MODEL, 기본: provider 별 기본 모델)")
    api.add_argument("--temperature", type=float, help=f"무작위성 0~2 (env: AI_TEMPERATURE, 기본 {DEFAULT_TEMPERATURE})")
    api.add_argument("--max-tokens", type=int, help=f"응답 최대 토큰 (env: AI_MAX_TOKENS, 기본 {DEFAULT_MAX_TOKENS})")
    api.add_argument("--base-url", help="API 기본 주소 (env: AI_API_BASE_URL, OpenAI 호환 서버용)")

    git = common.add_argument_group("Git 입력 옵션")
    git.add_argument("--staged", action="store_true", help="staged 변경(git diff --cached)만 사용")

    safe = common.add_argument_group("보안 옵션 (safe-mode)")
    safe.add_argument("--safe-mode", action="store_true", help="diff 의 민감정보를 마스킹하고 전송량을 제한")
    safe.add_argument("--max-files", type=int, help=f"safe-mode: 전송할 최대 파일 수 (기본 {DEFAULT_MAX_FILES})")
    safe.add_argument("--max-lines", type=int, help=f"safe-mode: 전송할 최대 diff 줄 수 (기본 {DEFAULT_MAX_LINES})")
    safe.add_argument(
        "--mask-pattern", action="append", default=[], metavar="REGEX",
        help="safe-mode: 추가로 가릴 정규식 (여러 번 지정 가능)",
    )

    misc = common.add_argument_group("기타")
    misc.add_argument("--convention", metavar="FILE", help="팀 커밋/PR 컨벤션 텍스트 파일 (프롬프트에 주입)")
    misc.add_argument("--dry-run", action="store_true", help="API 를 호출하지 않고 전송될 프롬프트만 출력 (호출 0회)")

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="git 변경 사항으로 커밋 메시지 / PR 초안을 AI 로 생성합니다. (결과는 초안이며 검토 후 사용하세요)",
        epilog="API Key 는 환경변수 AI_API_KEY 로만 전달합니다.  예) export AI_API_KEY=\"YOUR_KEY\"",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{commit,pr}")
    sub.add_parser("commit", parents=[common], help="커밋 메시지 초안 생성")
    sub.add_parser("pr", parents=[common], help="PR 제목/본문 초안 생성")
    return parser


def _env_float(name: str) -> float | None:
    v = os.environ.get(name)
    return float(v) if v else None


def _env_int(name: str) -> int | None:
    v = os.environ.get(name)
    return int(v) if v else None


def resolve_config(args: argparse.Namespace, api_key: str) -> AIConfig:
    """우선순위: CLI 옵션 > 환경변수 > 내장 기본값."""
    provider = args.provider or os.environ.get("AI_PROVIDER") or "openai"
    if provider not in PROVIDERS:
        raise ValueError(f"지원하지 않는 AI_PROVIDER: {provider} (지원: {', '.join(PROVIDERS)})")
    try:
        temperature = args.temperature if args.temperature is not None else _env_float("AI_TEMPERATURE")
        max_tokens = args.max_tokens if args.max_tokens is not None else _env_int("AI_MAX_TOKENS")
    except ValueError as e:
        raise ValueError(f"환경변수 값을 숫자로 읽을 수 없습니다: {e}") from e
    temperature = DEFAULT_TEMPERATURE if temperature is None else temperature
    max_tokens = DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens

    upper = 1.0 if provider == "anthropic" else 2.0
    if not 0 <= temperature <= upper:
        raise ValueError(f"--temperature 는 0~{upper:g} 범위여야 합니다. (입력: {temperature})")
    if max_tokens < 1:
        raise ValueError("--max-tokens 는 1 이상이어야 합니다.")

    return AIConfig(
        provider=provider,
        model=args.model or os.environ.get("AI_MODEL") or DEFAULT_MODELS[provider],
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        base_url=args.base_url or os.environ.get("AI_API_BASE_URL") or DEFAULT_BASE_URLS[provider],
    )


# ---------------------------------------------------------------- 본 흐름
def prepare_diff(args: argparse.Namespace, diff: str) -> str:
    """safe-mode 여부에 따라 AI 로 보낼 diff 텍스트를 만든다."""
    if args.safe_mode:
        max_files = args.max_files or DEFAULT_MAX_FILES
        max_lines = args.max_lines or DEFAULT_MAX_LINES
        res = apply_safe_mode(diff, max_files, max_lines, args.mask_pattern)
        info(
            f"safe-mode ON: 민감정보 {res.masked_count}건 마스킹 · "
            f"파일 {res.files_total}→{res.files_kept}개 · 줄 {res.lines_total}→{res.lines_kept}줄"
        )
        if res.redacted_files:
            info(f"safe-mode: 민감 파일 {len(res.redacted_files)}개의 diff 본문 제외 ({', '.join(res.redacted_files)})")
        return res.text

    if args.max_files or args.max_lines or args.mask_pattern:
        warn("--max-files/--max-lines/--mask-pattern 은 --safe-mode 와 함께 쓸 때만 적용됩니다.")
    hits = count_sensitive(diff)
    if hits:
        warn(f"diff 에 민감정보로 보이는 패턴이 {hits}건 있습니다. 외부 AI 로 전송되므로 --safe-mode 사용을 권장합니다.")
    if len(diff) > HARD_DIFF_CHAR_LIMIT:
        warn(f"diff 가 {len(diff):,}자라 앞 {HARD_DIFF_CHAR_LIMIT:,}자만 전송합니다. (비용 방지)")
        diff = diff[:HARD_DIFF_CHAR_LIMIT] + "\n... (길이 제한으로 이하 생략)"
    return diff


def print_block(title: str, text: str) -> None:
    print(f"\n--- {title} ---")
    print(text)
    print("-" * (len(title) + 8))


def run(args: argparse.Namespace) -> int:
    # 1) API Key (코드에 하드코딩하지 않고 환경변수로만 받는다)
    api_key = os.environ.get("AI_API_KEY", "").strip()
    if not api_key and not args.dry_run:
        error("AI_API_KEY 환경변수가 설정되지 않았습니다.")
        print('      예) export AI_API_KEY="YOUR_KEY"', file=sys.stderr)
        return EXIT_ERROR

    try:
        cfg = resolve_config(args, api_key)
    except ValueError as e:
        error(str(e))
        return EXIT_ERROR

    convention = None
    if args.convention:
        try:
            convention = Path(args.convention).read_text(encoding="utf-8-sig")  # BOM 있는 UTF-8 도 허용
        except UnicodeDecodeError:
            error(f"컨벤션 파일은 UTF-8 텍스트여야 합니다: {args.convention} (메모장이면 '다른 이름으로 저장' → 인코딩 UTF-8)")
            return EXIT_ERROR
        except OSError as e:
            error(f"컨벤션 파일을 읽을 수 없습니다: {args.convention} ({e.strerror or e})")
            return EXIT_ERROR

    for rx in args.mask_pattern:
        try:
            re.compile(rx)
        except re.error as e:
            error(f"--mask-pattern 정규식이 올바르지 않습니다: {rx!r} ({e})")
            return EXIT_ERROR

    # 2) Git 변경 사항 수집 (git status / git diff 만 사용)
    if not os.path.exists(".git"):
        warn("현재 디렉토리에 .git 이 없습니다. 프로젝트 루트에서 실행하는 것을 권장합니다.")
    try:
        changes = collect_changes(staged_only=args.staged)
    except GitError as e:
        error(str(e))
        return EXIT_GIT

    info(f"현재 브랜치: {changes.branch}")
    if not changes.status_lines:
        target = "커밋 메시지를" if args.command == "commit" else "PR 초안을"
        info(f"변경 사항이 없습니다. {target} 생성하지 않고 종료합니다.")
        return EXIT_OK
    info(f"Git status 수집 완료: {changes.file_count}개 파일 변경 감지")
    info(f"Git diff 수집 완료: {changes.diff_line_count}줄" + (" (staged 만)" if args.staged else ""))
    if not changes.diff.strip():
        warn("diff 가 비어 있습니다 (새 파일만 있는 경우). 파일 목록만으로 요약하므로 품질이 낮을 수 있습니다. `git add -N <파일>` 로 diff 에 포함시킬 수 있습니다.")

    # 3) 전송할 diff 준비 → 프롬프트
    diff_text = prepare_diff(args, changes.diff)
    system, user = build_messages(args.command, changes, diff_text, convention)

    if args.dry_run:
        info("--dry-run: AI API 를 호출하지 않습니다. (호출 0회)")
        print_block("System Prompt", system)
        print_block("User Prompt (AI 로 전송될 내용)", user)
        return EXIT_OK

    # 4) AI API 호출 (명령당 1회)
    client = AIClient(cfg)
    temp_label = "모델 기본값" if is_reasoning_model(cfg.provider, cfg.model) else f"{cfg.temperature:g}"
    info(f"AI API 요청 중... (provider={cfg.provider}, model={cfg.model}, temperature={temp_label}, max_tokens={cfg.max_tokens})")
    try:
        raw = client.generate(system, user)
    except AIError as e:
        error(f"AI API 호출 실패: {e}")
        info(f"AI API 호출 횟수: {client.calls}회")
        return EXIT_ERROR

    # 5) 형식 검증 + 후처리 → 출력
    if args.command == "commit":
        title, body, notes = postprocess.normalize_commit(raw)
        print("[DONE] 커밋 메시지 생성 완료")
        print_block("Commit Message", title + (f"\n\n{body}" if body else ""))
    else:
        title, body, notes = postprocess.normalize_pr(raw)
        print("[DONE] PR 초안 생성 완료")
        print_block("PR Title", title)
        print_block("PR Body", body)
    for n in notes:
        warn(n)
    info(f"AI API 호출 횟수: {client.calls}회")
    info("생성된 문구는 초안입니다. 반드시 검토 후 사용하세요.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 터미널에서 한글이 깨지지 않도록
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        error("사용자가 중단했습니다.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
