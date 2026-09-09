# ADR 0010: 답변 버전과 독립 생성 worker

- 상태: 채택
- 날짜: 2026-09-09
- 마이그레이션: `0010_worker_heartbeat`, `0011_answer_versions`

## 배경과 결정

실패한 답변을 재시도하거나 완료된 답변을 다시 생성할 때 질문 원문과 이전 답변·사용량을 보존해야 한다. API 개발 reload가 진행 중인 모델 연결을 종료하는 문제도 분리한다. 새 화면·서버 실행 없이 격리 DB와 Mock 프로세스·SSR 테스트로 검증한다.

## 답변 버전

`POST /api/v1/generations/{id}/regenerate`는 인증·CSRF·Origin 검증과 UUID `Idempotency-Key`를 요구하며 `{options}`만 받는다. 가장 최근 질문의 현재 답변을 본인이 다시 생성할 수 있다. 이전 질문의 편집·분기는 이번 범위에 포함하지 않는다.

새 `GenerationRun`은 기존 `user_message_id`를 재사용하고 새 assistant 메시지·사용량 기록을 만든다. `supersedes_generation_id`는 직전 답변을 가리킨다. 이전 생성의 `is_current`를 false로 바꾸고 새 답변을 저장하는 작업은 같은 트랜잭션에서 처리한다. 질문별 현재 답변의 partial unique index, 사용자·대화의 active 제약, 승인 잠금과 대화 sequence 재검증으로 중복 승인을 막는다. 같은 키 재전송은 기존 작업을 반환한다.

이전 메시지·완료 상태·정산 값은 변경하지 않는다. 새 버전도 정상 생성처럼 종료 후 확인된 사용량을 한 번 차감한다. 새 답변이 실패·중단되어도 현재 버전으로 남아 재시도할 수 있으며 이전 답변은 접힌 기록으로 열어 볼 수 있다. 사용자 질문과 작성 중인 초안은 복제하거나 지우지 않는다.

다음 문맥과 압축에는 `is_current=true`인 종료된 버전만 포함한다. 재생성 승인 시 현재 질문의 옛 답변을 제외하고 질문을 마지막에 한 번 추가한다. 최신 질문만 허용하고 요약 경계에 포함된 질문은 거절하므로 과거 요약의 무효화 없이 문맥을 일관되게 유지한다. 임의의 옛 질문 편집·분기를 추가할 때는 별도의 요약 무효화 설계가 필요하다.

메시지 조회는 `generation_id`, `generation_status`, `is_current`, `can_regenerate`, 명시적 `finish_reason`을 제공한다. 웹 중계가 새 재생성 경로를 허용하며 마지막 질문의 현재 답변에서만 버튼을 표시한다.

## Markdown과 복사

`react-markdown`과 `remark-gfm`으로 목록·표·코드·각주를 렌더링한다. raw HTML을 건너뛰고 URL 프로토콜을 제한하며 이미지는 자동 요청하지 않고 링크로 표시한다. 각주의 ID는 메시지마다 분리한다. 답변과 코드 복사는 Clipboard API를 우선 사용하고 LAN HTTP에서는 기존 복사 API로 보완하며 선택·초점·스크롤을 복원한다. 약 10ms grapheme 타이핑, 즉시 중단과 사용자 스크롤 우선 정책을 유지한다.

## 독립 worker

`python -m backend.app.worker`가 별도 프로세스로 실행된다. `GENERATION_WORKER_ENABLED=false`가 기본이며 true는 API 내장 실행자의 호환 옵션이다. `scripts/dev.py`는 이 값을 false로 고정하고 모델·API·worker·웹을 관리한다. API만 reload되어도 worker의 모델 연결과 작업은 유지된다. worker 코드·환경 변경은 다음 전체 실행부터 반영한다.

worker는 전용 asyncpg 연결의 advisory lock으로 리더 권한을 유지한다. 하나의 리더만 복구·claim·모델 추론을 수행하고 작업 선점에는 `FOR UPDATE SKIP LOCKED`를 사용한다. API 중단 요청은 DB로 전달하며 worker가 계속 조회한다. SIGTERM에는 연결과 확인된 사용량을 정리하고, 강제 종료 후 새 리더는 남은 running을 기존 무차감 실패 정책으로 종료한다. 불명 사용량을 추정하거나 이전 생성을 자동 재실행하지 않는다.

`worker_heartbeats` 단일행은 실행자 UUID·시작/갱신/종료 시각·실행 작업 ID를 저장한다. 리더 연결이 약 2초마다 갱신하며 `/health/worker`는 10초 이내 갱신·미종료면 200, 그렇지 않으면 503을 반환한다. 응답에는 상태와 대기/실행 건수만 노출한다. heartbeat는 관측용이며 만료로 실행 권한을 빼앗거나 작업을 다시 실행하지 않는다.

launcher 종료 순서는 웹 → worker → API → 모델이다. worker가 정산할 시간을 준 후 모델을 종료한다. 프로세스 그룹을 관리해 uvicorn/npm의 자식도 함께 정리한다. 이 개발 launcher는 자동 재시작 supervisor를 대신하지 않는다.

## 짧은 중단과 평가

공백을 제외한 Unicode 코드 포인트가 8개 이하인 중단·실패 원문은 완성 assistant 답변의 길이 예제가 되지 않도록 별도 비신뢰 JSON 참고 기록으로 전달한다. 원본 DB 메시지와 사용자 발언은 유지한다. 명시적 `finish_reason=length`나 더 긴 부분 답변은 assistant 원문으로 남겨 이어쓰기에 사용한다. 문맥 정책 버전은 2이며 요약 정책 버전은 1이다.

`scripts/evaluate_context.py`는 기본적으로 네트워크 없는 계약 검사다. 6개 가상 사례에 이름·직업 정정·수치·미완료 작업·108턴 압축과 length 이어쓰기를 포함한다. 1024 출력 상한 사례는 이미 잘린 부분 코드 fixture를 사용하며 모델을 실제 1024토큰까지 생성한 시험과 다르다. 실제 모델은 `--mode model`에서 기존 loopback 서버만 호출한다. 저장 대화·사용자 예산을 사용하지 않고 모델·정책 버전, 시간·사용량·단어 기반 회상 점수를 보고서에 기록한다. 단어 포함 점수는 부정·모순·코드 의미를 판정하지 않으므로 출력 검토가 필요하다.

## 검증과 롤백

격리 PostgreSQL에서 마이그레이션 왕복·drift와 재생성 원문/문맥/권한/멱등성/정산, 108턴 압축을 검사한다. 별도 Mock worker PID로 API lifespan 재시작·DB 중단·SIGTERM·SIGKILL 복구와 heartbeat의 읽기 전용 동작을 검증한다. 프런트는 상태 머신·Markdown SSR·URL·복사 대체 처리·웹 API 경로를 코드로 검사한다. 실제 브라우저 E2E와 실제 MLX의 장기 의미 품질은 별도 검증 항목이다. 실행별 결과는 [TODO](../../todo.md)에 기록한다.

`0011_answer_versions`는 재생성 기록이 하나라도 있으면 다운그레이드를 거절한다. 이전 스키마가 질문당 답변 하나만 허용하므로 데이터를 삭제해 강제로 맞추지 않는다. `0010_worker_heartbeat` 다운그레이드는 관측 기록만 제거한다. 자동 왕복은 임시 테스트 DB에서만 수행한다.
