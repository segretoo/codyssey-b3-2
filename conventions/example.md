## 우리 팀 커밋/PR 컨벤션 (예시 — 팀 규칙에 맞게 고쳐 쓰세요)

### 커밋
- 제목 형식: `type(scope): 요약`  (type 은 feat / fix / docs / refactor / test / chore 만 사용)
- scope 는 변경된 모듈명(예: cli, git, safety, ai)을 쓴다.
- 제목은 현재형 동사 어미 없이 명사형으로 끝낸다. (예: "옵션 추가", "오류 처리 개선")
- 본문 불릿은 "무엇을"이 아니라 "왜"가 드러나게 쓴다.

### PR
- 제목 앞에 type 을 붙인다. (예: `feat: safe-mode 마스킹 규칙 확장`)
- Why 에는 문제 상황 1줄, What 에는 파일 단위 변경, How to Test 에는 실행 명령어를 쓴다.
- How to Test 에는 항상 `python -m unittest discover -s tests` 를 포함한다.
