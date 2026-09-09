# Qwen Workbench 서비스화 TODO

이 문서는 현재의 로컬 채팅 MVP를 **지속적으로 운영 가능한 하나의 서비스**로 단계적으로 확장하기 위한 실행 순서다. 위 단계의 완료 조건을 통과한 뒤 다음 단계로 넘어간다.

## 현재 상태

- [x] Python 3.12 `.venv` 개발 환경
- [x] React/Vinext 채팅 화면
- [x] FastAPI 게이트웨이
- [x] Mock/MLX provider 교체 구조
- [x] `Qwen3.8-27B-4bit` SSE 스트리밍 연결
- [x] 기본 입력 검증, 오류 표시, 생성 중단
- [x] 빠른 생성 중단·확인 수신량 정산·한글/이모지 단위 10ms 타이핑
- [x] 사용자 스크롤 우선·최신 답변 이동·답변 완료/중단 후 토큰 차감
- [x] 서버 종료 후 후정산 미확정 요청의 무차감 종료·기존 차단 기록 복구·개발 자동 재시작 범위 제한
- [x] Mock 기반 API 테스트와 프런트 빌드 검사
- [x] 같은 Wi-Fi/LAN 웹 접속 — 웹 `0.0.0.0:3000`, 동일 출처 `/api` 중계, 실행 로그의 현재 내부 IP 안내
- [x] PostgreSQL·마이그레이션·계정·인증·대화·토큰·생성 작업·이벤트·요약 — 현재 도메인 13개 테이블, 감사·결제 테이블은 후속
- [x] 시스템 계정과 토큰 플랜·기간 예산·답변 종료 후 실제 사용량 차감 — 월 무료 20,000토큰 자동 지급·자기 사용량 API·화면 연결
- [x] 24시간 서버 세션·로그인/로그아웃·로그인 시작 화면
- [x] 사용자별 작업 공간·대화·생성 API 권한 검사 — system도 소속 검사 유지
- [ ] 회원가입 정책 완성 — 기본 활성인 일반 가입 API/화면·가입 후 자동 로그인 구현, 이메일 확인·초대·RLS 후속
- [x] 채팅방·메시지·생성 상태 저장과 `/chat/{id}` 복원·목록·고정·보관·삭제
- [x] 모델 서버의 실제 tokenizer 입력량과 문맥 상한 검사
- [x] 중단·미완성 답변 문맥 유지와 `이어서 말해` 이어 쓰기
- [x] 장기 대화 자동 압축·요약 — 원문 보존·최근 대화 유지·시스템 유지 사용량 분리
- [x] 영속 작업 큐·DB당 단일 실행자·SSE 재생·중단 작업 상태 정리
- [ ] 불명 실제 사용량의 자동 대조·정산 복구와 독립 worker 운영
- [ ] HTTPS, 감사 로그, 백업, 관측, 배포 체계

> 현재 대화의 원본은 PostgreSQL이며 브라우저는 새 사용자 메시지만 전송한다. 로그인·권한·저장·실제 토큰 정산과 이벤트 재생을 연결했다. 공개 가입은 기본 활성(`SIGNUP_MODE=open`)이며 일반 계정에 한국 시간 기준 월 무료 20,000토큰을 자동 지급한다. 중단·미완성 대화를 다음 문맥에 포함하고 오래된 대화는 자동 요약한다. 이메일 확인·RLS·실제 결제·불명 사용량 대조·운영 기반은 남아 있다. 저장·생성은 [ADR 0006](docs/adr/0006-persistent-chat-and-usage.md), 월 지급은 [ADR 0007](docs/adr/0007-monthly-allowances.md), 압축·이어 쓰기는 [ADR 0009](docs/adr/0009-context-compaction-and-continuation.md)에 정리했다.

## 먼저 고정할 원칙

- **진실의 원본은 서버다.** 클라이언트는 새 사용자 메시지만 보내고, 모델 컨텍스트는 서버가 DB에서 조립한다.
- **기본 저장소는 PostgreSQL이다.** 개발만을 위한 SQLite 전용 구현을 만들지 않는다.
- **처음부터 workspace 경계를 둔다.** 모든 계정에 기본 workspace 하나를 만들고, 나중에 공유 workspace를 추가해도 데이터 구조를 바꾸지 않게 한다.
- **운영자는 `system`, 일반 가입자는 `member`다.** 서비스 계정 권한을 작업 공간 역할·모델 메시지 역할과 분리하며, 시스템 승격은 명시적인 운영 절차로만 수행한다.
- **시스템 계정은 사용량 한도를 면제하고 사용량은 기록한다.** 모델 컨텍스트·메모리·동시성 제한까지 없애지는 않는다.
- **일반 계정의 토큰 예산은 사용자 단위다.** 여러 작업 공간에서 같은 예산을 공유하고, 사용자별 미정산 1건·실제 입력/출력 후정산으로 동시 초과 사용을 막는다.
- **결제와 사용량을 분리한다.** 무료는 월 20,000토큰·한국 시간 매월 1일 갱신·미이월로 제공한다. 향후 검증된 결제 이벤트가 등급별 기간 예산을 발급하도록 연결하고 기존 사용량·발급 당시 한도를 덮어쓰지 않는다. 결제사와 유료 가격은 후속이다.
- **가입 정책은 설정으로 바꾼다.** 서비스 코드는 하나로 유지하고 `open`, `invite_only`, `disabled` 중 하나를 운영 설정으로 선택한다.
- **인증 토큰은 HttpOnly 쿠키로만 전달한다.** localStorage·sessionStorage에는 저장하지 않는다. 서버측 opaque session은 절대 24시간이며 `SameSite=Lax`를 사용한다. 현재 로컬 HTTP에서는 `AUTH_COOKIE_SECURE=false`, 추후 HTTPS에서는 `Secure`와 `__Host-session`을 적용한다.
- **MLX 모델 프로세스는 한 개만 둔다.** 현재 M3 Pro에서는 동시 생성 1건과 짧은 bounded queue를 기본값으로 삼는다.
- **모델 서버 `:8080`은 외부에 공개하지 않는다.** 운영에서도 loopback 또는 보호된 내부망에서만 접근한다.
- **요약은 원문을 대체하지 않는다.** 원본 메시지는 보존하고, 요약의 대상 범위와 버전을 별도로 기록한다.
- **자동 요약 비용은 시스템 유지 작업으로 처리한다.** 사용자 한도에서 차감하지 않고 별도 사용량으로 남긴다. 실제 답변에 포함된 요약문은 해당 답변의 입력 사용량으로 정산한다.
- **RAG는 인증·DB·권한 격리 이후에 한다.** 그렇지 않으면 문서 ACL과 삭제 전파를 다시 설계하게 된다.
- **복구해 보지 않은 백업은 완료가 아니다.** 실제 restore drill을 출시 조건으로 삼는다.

## 목표 구조

```text
Browser
  │  same-origin HTTPS + session cookie
  ▼
Web / FastAPI API
  ├── PostgreSQL: 계정 권한, 대화, 메시지, 토큰 예산·정산, 요약, 생성 작업
  ├── TokenQuotaService: system 면제 / 일반 사용자 종료 후 차감
  ├── 결제 연동 (후속): 검증된 구독·결제 이벤트 → 기간 예산 부여
  ├── Redis: rate limit·짧은 캐시 (필요 시 도입)
  └── Generation queue
          │
          ▼
      단일 MLX worker ──► mlx_vlm.server :8080 (loopback only)
          │
          └── 영속 generation event ──► 재연결 가능한 SSE
```

초기에는 PostgreSQL 작업 큐로 충분하다. 실제 측정에서 여러 API/worker가 필요해질 때 Redis 또는 전용 큐를 도입한다.

## 모든 단계의 공통 완료 규칙

기능 하나를 완료할 때마다 다음을 함께 처리한다.

- [ ] 성공, 입력 오류, 인증 없음, 권한 없음, 중복 요청, 의존 서비스 장애 테스트를 추가한다.
- [ ] DB 변경에는 Alembic migration과 upgrade/downgrade 검증을 추가한다.
- [ ] 구조화 로그와 운영 지표를 추가하되 프롬프트·답변 원문과 인증정보는 기본 로그에서 제외한다.
- [ ] 설정값을 `.env.example`, 실행법을 `run.md`, 구조 변경을 `tree.md`에 반영한다.
- [ ] API 계약 또는 중요한 설계 결정은 `docs/adr/`에 짧게 기록한다.
- [ ] 미완성 기능은 feature flag 뒤에 두고 기본값을 비활성화한다.
- [ ] 롤백 방법과 데이터 호환 범위를 적는다.

---

## 1단계 — PostgreSQL과 데이터 계층 만들기

**목표:** 재시작해도 데이터를 잃지 않는 기반을 만들고, 이후 인증·채팅 기능이 같은 저장 규칙을 사용하게 한다.

**2026-09-09 진행:** PR 1의 연결·세션·migration·health 기반과 PR 2의 핵심 5개 테이블·repository 이후 계정·토큰 정책을 확장했다. 토큰 기반 revision은 `0003_system_token_quotas`이며 채팅용 5개 테이블에 `users.platform_role`과 토큰 관리용 3개 테이블을 추가한다. `DATABASE_ENABLED=false`가 기본이다. 인증은 `0004_auth_sessions`, 생성 작업·이벤트는 `0005_generation_runs`, 대화 요약은 현재 head `0009_context_compaction`에서 추가했다. 감사·결제 테이블은 후속이다. 실행법은 `run.md`, 생성·압축 설계와 롤백 범위는 [ADR 0006](docs/adr/0006-persistent-chat-and-usage.md)과 [ADR 0009](docs/adr/0009-context-compaction-and-continuation.md)를 따른다.

### 구현

- [x] 개발용 PostgreSQL을 Docker Compose로 추가한다.
- [x] `SQLAlchemy 2.x async`, `asyncpg`, `Alembic`을 Python 의존성에 추가한다.
- [x] `DATABASE_URL`, pool 크기, 연결 timeout 설정을 추가하고 비밀값은 Git에 올리지 않는다.
- [ ] 아래 목표처럼 별도 worker 책임을 분리한다. — HTTP `/api/v1`은 `api/` 모듈에, 채팅·생성 서비스와 내장 worker는 `services/generations.py`에 구현; 독립 worker 패키지·프로세스는 후속

```text
backend/app/
├── api/v1/           # HTTP 요청·응답과 dependency
├── db/               # engine, session, migration 공통 코드
├── models/           # ORM 모델
├── repositories/     # 권한 범위가 적용된 DB 접근
├── services/         # 대화·생성·컨텍스트 업무 규칙
└── workers/          # 생성·요약·문서 처리 작업
```

- [x] DB 요청은 dependency 또는 서비스의 짧은 `AsyncSession` context에서 처리하고 반드시 닫는다.
- [x] SSE가 연결된 긴 시간 동안 DB transaction을 열어두지 않는다. — 함수 scope에서 응답 전에 닫고 실제 PostgreSQL 테스트로 확인
- [x] 현재 도메인 13개 테이블의 PK는 UUID, 시간은 PostgreSQL `timestamptz`와 UTC를 사용한다. — 후속 테이블에도 같은 규칙 적용
- [x] `/health/live`와 `/health/ready`를 분리한다. DB 또는 모델이 준비되지 않으면 readiness만 `503`을 반환한다. — 비활성 DB는 검사 생략, 모델은 기존 provider 상태 기준
- [x] 테스트도 SQLite 대체물이 아니라 실제 PostgreSQL에서 실행한다.

### 초기 데이터 모델

현재 `users`, `workspaces`, `workspace_members`, `conversations`, `messages`, `usage_plans`, `token_budgets`, `token_reservations`, `auth_identities`, `auth_sessions`, `generation_runs`, `generation_events`, `conversation_compactions`를 구현했다. 아래 감사·결제 테이블은 해당 기능을 구현할 때 추가한다.

| 테이블                                               | 핵심 내용                                                                                                            |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `users`                                              | 이메일, 표시 이름, 상태, `platform_role=member/system`, 생성·수정·마지막 로그인 시각                                 |
| `usage_plans`                                        | 사용량 플랜의 기본 토큰량과 사용 가능 상태                                                                           |
| `token_budgets`                                      | 사용자별 유효기간, 발급 당시 한도, 사용·예약량, 멱등 키, `source=plan/free_monthly`                                  |
| `token_reservations`                                 | 사용자별 요청 키, 허용 상한·기존 예약량, 입력/출력 청구량, 차감 방식·상태·기준                                       |
| `billing_customers / subscriptions / payment_events` | 후속 결제사 고객·구독·검증된 이벤트와 예산 부여 이력                                                                 |
| `auth_identities`                                    | 현재 password 로그인 해시·사용자/provider unique; OIDC provider subject 확장은 후속 migration                        |
| `auth_sessions`                                      | session token의 **hash**, 절대 24시간 만료·철회 시각; 기기 메타데이터는 후속                                         |
| `workspaces`                                         | 사용자와 공유 그룹의 데이터·권한 경계                                                                                |
| `workspace_members`                                  | 사용자, workspace, `owner/admin/member` 역할; 복합 unique                                                            |
| `conversations`                                      | workspace, 작성자, 제목, 상태, 모델, 설정 JSON, 마지막 메시지 시각, soft delete                                      |
| `messages`                                           | 대화 내 sequence, 역할, 본문, 상태, 작성자, token 수, 모델·prompt version                                            |
| `generation_runs`                                    | `queued/running/completed/failed/cancelled/usage_pending`, 입출력 메시지·예약 참조, 요청 키·해시·설정·이벤트 순번    |
| `generation_events`                                  | generation별 순번이 있는 스트림 event; 재연결 시 replay                                                              |
| `conversation_compactions`                           | 요약 대상 마지막 sequence, 완료 요약, 작업 상태·오류, 모델·prompt version, 사용자에게 차감하지 않는 입력·출력 사용량 |
| `audit_logs`                                         | actor, workspace, action, object, 결과, 시각; 메시지 원문은 저장하지 않음                                            |

### 필수 제약과 인덱스

- [x] `users.email`은 정규화 후 unique로 만든다.
- [x] `(workspace_id, user_id)` membership을 unique로 만든다.
- [x] `(conversation_id, sequence)` message 순번을 unique로 만든다.
- [x] `(generation_id, sequence)` event 순번을 unique로 만든다.
- [x] 대화 목록은 `(workspace_id, status, last_message_at DESC, id DESC)` cursor index를 사용한다. — 삭제된 행 제외
- [x] queued 작업은 생성 시각·UUID partial index를 둔다. 사용자·대화별 active unique index도 적용한다.
- [x] 핵심 5개 테이블 FK의 delete 동작과 soft/hard delete 정책을 명시한다. — ADR 0002; 자동 영구 삭제의 유예기간은 후속 작업
- [x] migration 실행 역할과 애플리케이션 runtime DB 역할을 분리할 수 있게 준비한다. — `MIGRATION_DATABASE_URL` 분리 지원; 최소 권한 역할 자체는 후속 작업

### 테스트·완료 조건

- [x] 빈 DB에서 `alembic upgrade head`가 성공한다.
- [x] 한 revision downgrade 후 다시 upgrade할 수 있다.
- [x] ORM과 실제 migration schema에 drift가 없다. — 도메인 테이블·인덱스·기본값 비교 및 CHECK 제약 통합 테스트
- [x] DB 중단 시 liveness는 살아 있고 readiness만 실패한다. — 실제 DB 연결 차단·기존 연결 종료·재허용으로 장애와 복구 검증
- [x] API 테스트가 종료된 뒤 열린 connection 또는 transaction이 남지 않는다.

**1단계 Gate:** 깨끗한 환경에서 PostgreSQL을 띄우고 migration과 통합 테스트를 한 명령 흐름으로 재현할 수 있다.

검증 명령: `.venv/bin/python scripts/test_db.py`. PR 2 기준으로 migration 왕복·drift 검사와 전체 백엔드 **47개 테스트**를 통과했고 임시 DB를 정리했다. 사용자 생성 원자성, 교차 workspace 접근 차단, 구성원·관리자 삭제 권한, 105개 대화 cursor, 메시지 동시 순번·rollback, soft delete와 DB 제약을 실제 PostgreSQL에서 검증했다. Ruff 검사·포맷 검사도 통과했다. 프런트 빌드는 PR 1에서 통과했으며 이번 PR 2에는 프런트 변경이 없다. 위 검증은 PR 2 시점의 기록이다. 이후 인증·생성 작업·이벤트·요약 테이블을 추가했으며 감사 테이블은 남아 있다.

계정·토큰 확장 시점 검증: 전체 백엔드 **73개 테스트**, 마이그레이션 왕복·drift 검사, Ruff·포맷 검사를 통과했다. 로컬 `project_llm` DB에도 `0003_system_token_quotas` 업그레이드와 스키마 비교를 완료하고, 운영자가 지정한 시스템 계정 1개와 기본 작업 공간·owner 소속을 생성했다. 잔여량 조회에서 `unlimited=true`를 확인했다. 가격·일반 사용자 예산·테스트 메시지는 넣지 않았다. DBeaver의 `docker_project_llm → Schemas → public → Tables`를 새로고침해 확인한다.

개발 접속 명칭은 DBeaver `docker_project_llm`, 데이터베이스 `project_llm`, PostgreSQL 로그인 `system`으로 통일했다. 기존 DB·역할 식별자와 전체 테이블 내용·비밀번호·Docker 데이터 볼륨을 보존했고, 설정 변경 후 스키마 비교와 DBeaver 실제 재접속을 확인했다.

---

## 1.5단계 — 시스템 계정과 일반 사용자 토큰 정책

**목표:** 시스템 계정은 서비스 한도 없이 사용하고 일반 사용자는 자동 월 무료 20,000토큰과 향후 결제 등급별 기간 예산을 사용한다. 수동 관리 명령은 유지보수용이다. 기본 정산은 [ADR 0003](docs/adr/0003-system-accounts-and-token-quotas.md), 월 정책은 [ADR 0007](docs/adr/0007-monthly-allowances.md)을 따른다.

**월 무료 정책(2026-09-09):** `0006_monthly_allowances`에서 기존 예산을 `plan`으로 보존하고 `free_monthly`를 구분한다. 한국 시간 매월 1일 갱신·미이월이며 월 중 가입도 20,000토큰 전액을 받는다. 가입·사용량 조회·생성 승인에서 사용자·월 키로 필요할 때 지급하므로 기존 회원도 적용되고 별도 cron은 필요 없다. 약 10회는 입력 1,000 + 답변 1,000토큰 기준의 목표이며 이력·답변 길이에 따라 달라진다. 개발 DB 업그레이드와 drift 검사를 완료했다.

**월 무료 정책 검증 완료:** 임시 PostgreSQL migration 왕복·drift를 포함한 백엔드 **211개**(98.94초), 프런트 **36개**·빌드·TypeScript·oxlint·Ruff·변경 형식 검사를 통과했다. 기존 회원·새 가입에 무료 20,000토큰이 적용되고 반복 조회도 같은 예산을 유지했다. 브라우저의 무료량·한국 시간 10월 1일 갱신·미이월 안내와, 수동 할당 없이 실제 MLX 입력 48 + 출력 5 = 53토큰 정산·잔여 19,947·예약 0을 확인했다. 검증용 임시 계정과 관련 데이터를 정리하고 기존 사용자 데이터는 보존했다.

- [x] 기존 사용자와 일반 생성의 기본 권한을 `member`로 두고 시스템 계정은 로컬 관리 명령으로 준비한다.
- [x] `system` 계정은 토큰 한도를 면제하지만 요청·실제 사용 기록을 남긴다.
- [x] 플랜, 기간 예산, 예약/정산 기록을 분리한다. 플랜 변경으로 기존 예산을 초기화하지 않는다.
- [x] 일반 사용자의 작업 공간들이 같은 사용자 예산을 공유한다. 월 무료 예산은 자동 준비하며 system에는 발급하지 않는다.
- [x] 무료 UsagePlan 정책을 기간별 TokenBudget 사본으로 만들고 같은 사용자·월의 중복 지급을 막는다.
- [x] source별 활성 예산을 하나로 제한하고 유효한 plan은 무료보다 우선한다. 소진돼도 기간 중 무료로 전환하지 않으며 종료 후 그달 무료 잔여량을 사용한다.
- [x] 월·플랜 전환 후에도 이전 예약은 원래 예산에 정산한다.
- [x] 새 생성은 허용량만 검사하고 완료·중단 후 차감한다. 기존 요청 호환을 위한 예약·정산 서비스는 보존한다.
- [x] 관리자 권한, 기간 경계·중복, 멱등성, 동시 예약과 데이터 보존을 PostgreSQL에서 검사한다.
- [x] 준비된 시스템 계정에 로컬 숨김 입력으로 비밀번호를 설정하고 로그인·세션으로 실제 사용자를 결정한다. — 비밀번호 직접 설정 1회 필요
- [x] 일반 사용자에게 남은 토큰·기간·한도 초과 안내를 표시한다. 시스템 계정에는 사용량과 무제한 표시를 제공한다.
- [x] 실제 모델 입력 tokenizer와 provider 출력 사용량을 연결한다. 클라이언트가 보낸 토큰 수로 정산하지 않는다.
- [x] 생성 작업과 예약 키를 연결해 요청 재시도가 모델을 중복 실행하지 못하게 한다.
- [x] 모델 미실행이 확실하면 차감 없이 끝낸다. 정상 완료·실사용이 확인된 실패는 최종 사용량, 사용자 중단은 확인 수신량 또는 0/0 면제로 차감한다. 후정산의 불명 사용량 실패는 무차감 종료해 다음 질문을 허용하고, 이전 사전 예약 방식만 `usage_pending`을 유지한다.
- [ ] worker 종료를 확인한 후 고아 예약을 복구한다. 시간 만료만으로 실행 중인 예약을 해제하지 않는다.

**결제 추가 시:** 고객·구독·결제 이벤트를 별도로 저장하고 서명 검증·이벤트 중복 방지 후 등급별 `plan` 기간 예산을 발급한다. 플랜 변경·추가 구매·환불은 사용 이력을 지우지 않는 조정 이력으로 설계한다. 무료 월 20,000토큰 정책은 확정했고 결제사·유료 가격·실제 결제는 아직 남아 있다.

**완료 Gate:** 인증된 시스템 계정은 서비스 한도 때문에 차단되지 않고, 일반 사용자는 동시 요청·재시도·실패에도 실제 허용량을 초과하거나 중복 차감되지 않는다. HTTP·모델 통합과 실제 사용량 정산을 검증했다. 최종 사용량을 확인하지 못한 예약의 자동 복구는 아직 없으며 임의 추정 차감·환급은 하지 않는다.

---

## 2단계 — 회원가입·로그인·workspace 권한

**목표:** 모든 데이터가 인증된 사용자와 workspace에 귀속되고, 다른 사용자의 ID를 알아도 접근할 수 없게 한다.

**2026-09-09 진행:** `0004_auth_sessions`에서 Argon2id 비밀번호와 서버 세션을 추가했다. 로그인 시각부터 24시간 유지하고 비로그인·만료 시 로그인 화면을 표시한다. 공개 가입은 현재 기본 활성이고 시스템 계정 비밀번호는 로컬 관리 명령으로 설정한다. HTTP 요청의 인증·CSRF·Origin과 웹 쿠키 중계를 연결했다. 일반 계정은 유효한 예산 안에서 채팅할 수 있고 작업 공간 권한·대화 저장·실제 사용량 조회를 연결했다. 이메일 확인·초대·RLS는 남아 있으므로 2단계 전체 완료는 아니다. 인증은 [ADR 0005](docs/adr/0005-authentication-and-24-hour-sessions.md), 후속 통합은 [ADR 0006](docs/adr/0006-persistent-chat-and-usage.md)을 따른다.

**가입 정책 수정(2026-09-09):** 로그인·가입·비밀번호 설정·변경 모두 8~32자를 사용한다. 가입 폼은 사용자 이름·이메일·비밀번호·확인을 받으며 가입 성공 후 자동으로 24시간 로그인한다. 채팅에는 계정의 `display_name`을 표시한다. 신규 `member`는 시스템 권한 없이 기본 작업 공간과 이번 달 무료 20,000토큰 예산을 함께 준비한다.

**월 무료 지급 도입 전 가입 정책 검증:** 임시 PostgreSQL migration 왕복·drift와 백엔드 **188개**(75.79초), 프런트 **36개**·빌드·TypeScript·oxlint·Ruff를 통과했고, 실제 localhost에서 기본 가입 활성·8자 비밀번호 가입과 로그인·자동 24시간 세션·member/기본 작업 공간/당시 예산 0 및 가입 폼·사용자 이름 표시를 확인했다.

**인증 도입 시점 검증:** 임시 PostgreSQL 마이그레이션 왕복·스키마 비교 및 전체 백엔드 **100개**, 프록시/프런트 세션 **22개** 테스트 통과. Ruff·프런트 lint·production build 통과. 로컬 `project_llm`에 `0004_auth_sessions`를 적용했고, 기존 실행 서버의 localhost/LAN 주소에서 임시 계정으로 로그인 → 쿠키 유지·재접속 → CSRF 거부 → 로그아웃을 확인한 뒤 임시 데이터를 정리했다. 브라우저 로그인 시작 화면도 확인했다. 실제 시스템 계정의 비밀번호는 운영자가 로컬 `set-password`로 직접 설정한다.

### 가입 정책과 인증 구조

- [ ] 이메일/비밀번호 회원가입, 이메일 확인, 로그인, 비밀번호 재설정을 기본 흐름으로 구현한다.
- [x] `SIGNUP_MODE=open|disabled`로 공개 가입을 제어한다. 기본은 `open`이다.
- [ ] `invite_only` 설정과 초대 검증을 추가한다.
- [ ] `invite_only`에서는 만료·사용 횟수가 있는 초대 token을 검증한다.
- [x] `disabled`에서는 기존 사용자의 로그인만 허용한다.
- [ ] 추후 OIDC/SSO가 필요하면 같은 `auth_identities` 모델에 provider를 추가하고 사용자·권한 모델은 그대로 유지한다.
- [x] 계정 생성 시 기본 workspace와 owner membership을 한 transaction에서 만든다. — 일반 회원가입 API도 같은 원자적 생성 함수를 사용
- [x] 이미 준비된 시스템 계정은 로컬 `set-password` 인증 초기화 흐름으로 연결한다. 같은 이메일의 공개 가입으로 계정을 가져가거나 승격할 수 없게 한다.

### 세션과 계정 보안

- [x] 자체 비밀번호는 Argon2id로 hash하고 평문 또는 복호화 가능한 형태로 저장하지 않는다.
- [ ] 이메일 존재 여부를 로그인·가입·재설정 응답에서 드러내지 않는다.
- [x] 고엔트로피 무작위 session 원문은 쿠키에만 두고 DB에는 hash만 저장한다.
- [x] `HttpOnly`, `SameSite=Lax`, `Path=/` 쿠키를 사용한다. 현재 로컬 HTTP에서는 `project_llm_session`, `AUTH_COOKIE_SECURE=true`이면 `__Host-session; Secure`를 사용한다. HTTPS 자체 준비는 후속이다.
- [x] 로그인마다 새 session을 발급하고 시스템 승격·비밀번호 변경·로그아웃 때 기존 대상 세션을 철회한다. 비활성 계정은 매 요청에서 거부한다.
- [x] 인증된 상태 변경 API에 CSRF token과 `Origin` 검증을 적용한다. 로그인/가입은 Origin·JSON을 검사한다.
- [x] 로그인·가입에 단일 API 프로세스의 계정별·연결 IP별 rate limit을 둔다. 웹 중계 사용 시 연결 IP 한도는 공유된다.
- [ ] 재설정·다중 API 프로세스 확장 때 공유 rate limit을 추가한다.
- [x] 로그인 후 절대 24시간 만료를 DB와 쿠키에서 함께 검사한다. 활동·새로고침으로 연장하지 않는다.

### API와 화면

- [x] `POST /api/v1/auth/signup` — 현재 `SIGNUP_MODE` 정책을 적용
- [x] `POST /api/v1/auth/login`
- [x] `POST /api/v1/auth/logout`
- [x] `POST /api/v1/auth/logout-all`
- [x] `GET /api/v1/auth/me`
- [x] `GET /api/v1/auth/config` — 공개 가입 정책과 세션 유지 시간
- [ ] OIDC를 추가하면 login/callback/logout route와 `state`, `nonce`, issuer, audience 검증을 적용한다.
- [x] 로그인·선택적 가입 화면, 계정 헤더, 로그아웃·전체 로그아웃, 만료·장애·재시도 화면을 추가한다.
- [ ] 이메일 확인·이메일 재설정 화면을 추가한다.
- [x] FastAPI 공통 dependency에서 현재 사용자와 플랫폼 역할을 세션으로 결정한다.
- [x] workspace·대화·생성 업무 API에 세션 사용자·membership·역할 검사를 연결한다.
- [x] `user_id`·`role`을 클라이언트에서 받지 않고 `workspace_id`는 현재 사용자의 소속 범위로 검증한다.
- [x] `platform_role`은 로그인한 서버 계정에서 읽는다. 시스템 계정도 작업 공간 데이터 접근 검사를 유지한다.
- [x] 인증된 자기 토큰 잔여량 API를 제공한다. 시스템 전용 플랜·예산 관리는 로컬 CLI에 유지한다.
- [ ] 공개 관리 API·관리 화면이 필요해지면 별도 권한·감사 정책으로 추가한다.
- [x] 웹의 `/api` 중계에 인증 경로와 Cookie·Set-Cookie·Origin·CSRF 헤더 전달을 함께 추가하고 검사한다.
- [x] 현재 repository 조회가 object ID와 활성 사용자·workspace membership을 함께 검사한다. — workspace·대화·생성 API에 연결 완료
- [ ] PostgreSQL RLS를 방어 계층으로 추가하고 runtime 역할을 `NOBYPASSRLS` 비소유자로 둔다.

### 테스트·완료 조건

- [x] 인증되지 않은 업무 API는 `401`을 반환한다.
- [x] 사용자 A가 소속 밖 conversation/message/generation ID로 조회·수정·삭제·생성을 할 수 없다. system 교차 접근도 차단한다.
- [ ] 내보내기 기능 추가 시 같은 권한 경계를 적용한다.
- [x] CSRF, 잘못된 Origin, 만료·철회된 session이 거부된다.
- [x] session 회전, 현재 기기 로그아웃, 전체 기기 로그아웃이 동작한다.
- [ ] OIDC를 활성화했다면 위조·만료 token과 재사용 callback이 거부된다.
- [ ] RLS 테스트는 migration owner가 아닌 실제 runtime DB 역할로 실행한다.

**2단계 Gate:** 두 사용자와 두 workspace로 실행한 교차 접근 테스트가 전부 실패하고, 인증 우회 없이 채팅 API를 호출할 수 없다.

---

## 3단계 — 채팅방·메시지·목록 저장

**목표:** 새로고침, 로그아웃, 서버 재시작 뒤에도 사용자의 채팅방과 대화 내용이 복원되게 한다.

**2026-09-09 진행:** PR 2의 저장소를 workspace·대화·메시지 API, `/chat/{id}` 목록 화면과 연결했다. 제목·고정·보관·삭제와 다른 기기 로그인 복원을 지원한다. 날짜별 그룹·자동 무한 스크롤·LLM 제목·영구 삭제 정책은 후속이다.

**저장·생성 통합 검증:** 임시 PostgreSQL의 upgrade → downgrade base → upgrade → `alembic check`와 전체 백엔드 **178개 테스트**를 통과했다(70.66초, provider 검증 38개 포함). 프런트 **35개 테스트**, 빌드·TypeScript·oxlint·Ruff·변경 형식 검사도 통과했다. 개발 `project_llm` DB에 `0005_generation_runs`를 적용하고 drift가 없음을 확인했다. 실제 MLX의 일반/thinking 입력 계산 39/75가 최종 입력 사용량과 일치했다. 브라우저에서 로그인 → 생성 → 입력 49·출력 4 정산 → 새로고침 후 로그인·대화 유지 → 제목 수정·고정을 확인했다. 다른 세션의 조회도 일치했고 같은 요청 키의 재전송은 기존 생성만 반환하며 메시지·차감을 중복하지 않았다.

저장·생성 통합 당시에는 진행 중 새로고침으로 부분 응답을 복원하고 기존 수신 완료 후 최종 취소·예약 반환까지 실제 모델로 확인했다. 로그아웃 후 해당 검증용 임시 계정·작업 공간·대화·생성·예산·세션을 정리했다. 이후 빠른 중단·타이핑 정책의 검증은 아래 진행 기록과 구분한다.

### API

```http
POST   /api/v1/conversations
GET    /api/v1/conversations?workspace_id=<uuid>&cursor=&limit=30&status=active
GET    /api/v1/conversations/{conversation_id}
PATCH  /api/v1/conversations/{conversation_id}
DELETE /api/v1/conversations/{conversation_id}
GET    /api/v1/conversations/{conversation_id}/messages?before=&limit=50
```

- [x] 대화 생성·조회·제목 변경·pin·archive·soft delete를 구현한다.
- [x] offset 대신 `(last_message_at, id)` cursor로 목록을 페이지네이션한다.
- [x] 메시지는 `(conversation_id, sequence)` 순서로 cursor pagination한다.
- [x] 첫 질문의 앞 60자를 초기 제목으로 쓴다.
- [ ] 첫 응답 완료 후 LLM으로 제목을 생성한다.
- [x] 공개 API는 새 사용자 메시지 추가만 허용하고 완료 원문 편집·분기는 제공하지 않는다. assistant 생성 중 본문·상태 갱신은 서버가 담당한다.
- [ ] 삭제한 대화의 유예기간과 hard delete 시점을 정한다.

### 프런트엔드

- [x] URL을 `/chat/{conversationId}` 형태로 만든다.
- [x] 왼쪽 sidebar에 새 채팅 버튼과 대화 목록을 추가한다.
- [x] 대화 목록과 이전 메시지의 커서 “더 불러오기”를 제공한다. 고정 정렬은 불러온 목록 안에 적용한다.
- [ ] 날짜별 그룹과 자동 무한 스크롤·전체 고정 우선 정렬을 추가한다.
- [x] 제목 변경, pin, archive, 삭제 메뉴를 추가한다.
- [x] 초기 loading, 빈 목록, 재시도, 권한 없음 상태를 각각 표시한다.
- [x] 브라우저의 `messages[]`를 진실의 원본으로 사용하지 않고 API 응답을 cache한다.

### 테스트·완료 조건

- [x] 새로고침과 서버 재시작 뒤 같은 채팅이 복원된다.
- [x] 101개 이상의 대화 목록에서 중복·누락 없이 다음 cursor를 불러온다.
- [x] “이전 메시지 불러오기”로 순서대로 이어 붙인다. 자동 스크롤 로딩은 후속이다.
- [x] 동시에 메시지를 추가해도 sequence가 중복되지 않는다.
- [x] 제목·pin·archive·삭제 상태가 목록에 즉시 반영된다.
- [x] 삭제·보관된 대화가 일반 목록에 섞이지 않는다.

**3단계 Gate:** 다른 브라우저 세션에서 로그인해도 본인의 채팅 목록과 메시지를 그대로 이어서 볼 수 있다.

---

## 4단계 — 영속 생성 작업·큐·재연결

**2026-09-09 진행:** `0005_generation_runs`에서 작업·이벤트, 멱등 승인·실제 사용량 예약/정산과 API 내장 worker를 연결했다. `GENERATION_WORKER_ENABLED=true`, 기본 대기 상한은 3+실행 1건이다. 상세 상태·취소·복구 한계는 [ADR 0006](docs/adr/0006-persistent-chat-and-usage.md)을 따른다.

**빠른 중단·표시 정책 수정:** 중단 시 모델 응답 연결을 즉시 닫는다. 최종 사용량을 이미 받았다면 `provider`로 정산하고, 그렇지 않으면 확인된 출력이 있을 때 입력+수신 출력을 `received`로 정산하며 없으면 `waived` 0/0으로 면제한다. `0007_cancellation_usage`가 기준과 기존 이력을 보존한다. 받은 텍스트는 약 10ms 간격의 grapheme 단위로 표시하며 대기가 쌓이면 따라잡고 중단 때 표시도 멈춘다.

**빠른 중단 검증 완료:** 임시 PostgreSQL migration 왕복·drift를 포함한 백엔드 **242개**(97.55초), 프런트 **44개**, 빌드·TypeScript·oxlint·Ruff·형식 검사를 통과했다. 토큰이 오지 않는 제공자 연결 종료, 다른 API 인스턴스의 취소 감지, 취소·전송 오류 경합, 확인 수신량/면제 정산과 다음 질문 허용을 검증했다. 기존 로컬 앱의 실제 MLX 일반 모드에서 3개 출력 토큰 후 중단했을 때 API 직접 연결은 요청 응답 16ms·종료 이벤트 59ms, 웹 경유 재검사 2회는 요청 응답 18/19ms·종료 이벤트 61/64ms였다. 입력 50+출력 3의 수신량 정산·예약 0을 확인했고, 중단 직후 다음 짧은 질문은 1.62초에 정상 완료했다. 추론 모드의 숨김 출력도 본문 노출 없이 집계했다. 이 수치는 짧은 로컬 검사 결과이며 긴 입력의 GPU 연산 중 중단 지연을 보장하지 않는다. 개발 파일 수정이 진행되던 최초 웹 검사에서는 취소 요청 응답이 약 83초 지연됐으나 이후 같은 경로에서 재현되지 않았고 원인은 확정하지 못했다. 브라우저 자동화 연결은 사용할 수 없어 화면 동작은 프런트 테스트로 검증했다. 임시 검증 계정·관련 데이터와 검사 파일은 정리했다.

**후정산·스크롤 수정:** 새 생성은 예산 예약·차감 없이 허용량만 검사하고 완료·중단 후 확인된 사용량만 차감한다. 진행 중 미정산 사용량의 중복 실행은 제한하고, 서버 장애로 종료된 후정산 요청은 무차감 복구한다. 위로 스크롤하면 자동 따라가기를 해제하고, 아래쪽 또는 최신 답변 이동 버튼으로 다시 활성화한다. 중단은 모델 연결을 종료하며 재개 기능이 아니다. [ADR 0008](docs/adr/0008-deferred-charging-and-chat-scroll.md)에 현재 정책과 기존 이력 호환을 기록했다.

**후정산·스크롤 검증 완료:** PostgreSQL migration 왕복·drift와 백엔드 **262개**(117.88초), 프런트 **50개**·빌드·TypeScript·lint를 통과했다. 실제 웹→MLX 검사에서 생성 중 20,000토큰을 그대로 유지하고, 중단 후 입력 49+출력 3=52토큰만 차감해 19,948토큰이 됐다. 다음 생성 중에도 잔액이 유지됐으며 정상 완료 후 입력 44+출력 3=47토큰만 추가 차감했다. 중단 재요청은 중복 차감하지 않았고 이번 중단 종료 이벤트는 49ms에 확인됐다. 임시 검증 계정·관련 데이터·파일을 정리했다. 브라우저 자동화 연결이 없어 실제 스크롤 조작은 실행하지 못했으며 스크롤 동작은 프런트 회귀 테스트로 검증했다.

**서버 종료 후 차단 복구 검증:** 개발 중 `worker_stopped`로 끝난 후정산 요청이 `usage_pending`에 남아 새 질문을 계속 막는 문제를 수정했다. 최종 사용량을 확인하지 못한 후정산 실패는 무차감으로 종료하며, 기존 미정산 후정산 요청은 worker 시작·새 질문 승인 전에 복구한다. 대화·오류 코드·원래 종료 시각을 보존하고 반복·동시 복구를 멱등 처리한다. 기존 사전 예약 방식은 종전 정책을 유지한다. 마이그레이션 왕복·drift와 백엔드 **270개**(114.06초), Ruff·형식 검사를 통과했다. 시험 계정의 남은 미정산 건수 0과 기존 사용량 보존을 확인했다. 개발 감시는 앱 코드에 한정해 테스트 파일 변경으로 인한 불필요한 재시작을 줄였으며 해당 실행 옵션은 다음 개발 서버 실행부터 적용된다.

**목표:** 생성 요청의 중복, 연결 끊김, 취소, API/worker 재시작을 데이터 유실 없이 처리한다.

### 요청 계약

기존처럼 전체 history를 받지 않고 새 사용자 메시지만 받는다.

```http
POST /api/v1/conversations/{conversation_id}/messages
Idempotency-Key: <uuid>
Content-Type: application/json

{
  "content": "질문",
  "options": { "thinking": false, "max_tokens": 1024 }
}
```

```json
{
  "user_message_id": "...",
  "assistant_message_id": "...",
  "generation_id": "...",
  "events_url": "/api/v1/generations/.../events"
}
```

- [x] 사용자 메시지, assistant placeholder, generation 작업과 허용량 기록을 한 짧은 transaction에서 만든다. 입력·최대 출력 상한만 정하고 사용량·잔여량은 생성 중 변경하지 않는다.
- [x] 같은 idempotency key와 같은 본문이면 기존 결과를 반환하고, 같은 key에 다른 본문이면 `409`를 반환한다.
- [x] `queued → running → completed | failed | cancelled | usage_pending` 상태를 DB에 기록한다.
- [x] `GET /api/v1/generations/{id}` 상태 조회 API를 만든다.
- [x] `GET /api/v1/generations/{id}/events` SSE API를 별도로 만든다.
- [x] `POST /api/v1/generations/{id}/cancel`의 JSON `{}`로 중단을 요청한다. queued는 차감 없이 종료하고 running은 표시와 상류 연결을 끝낸 뒤 확인 수신량만 차감한다.
- [x] SSE event에 연속된 `id:`를 넣고 `Last-Event-ID` 이후를 replay한다.
- [x] token마다 DB에 쓰지 않고 약 40ms 또는 4096자 단위로 chunk를 저장한다.
- [x] 받은 답변을 약 10ms 간격의 grapheme 단위로 표시하고 표시 적체·중단·모션 감소 설정을 처리한다.
- [x] 완료·실패·취소된 메시지와 저장된 부분 본문·이벤트를 보존한다.
- [ ] 오래된 stream event의 보존기간과 정리 작업을 정한다.
- [x] 모델의 실제 입력·출력 토큰을 생성 종료 후 한 번 차감한다. 시스템 계정도 사용량을 기록한다.
- [x] 회계 기록의 멱등 반환과 worker 실행 선점을 구분한다. 같은 요청 키의 생성 작업은 하나만 실행한다.
- [x] 숨겨진 reasoning/chain-of-thought는 저장하거나 사용자에게 노출하지 않는다.

### worker와 자원 보호

- [x] API 내장 worker 후보 중 DB advisory lock을 보유한 한 개만 실제 추론을 수행한다.
- [ ] worker를 API 밖의 독립 프로세스로 분리한다.
- [x] PostgreSQL의 `FOR UPDATE SKIP LOCKED` 또는 동등한 lease 방식으로 작업 하나를 claim한다.
- [ ] `lease_expires_at`, heartbeat, attempt count로 죽은 worker의 작업을 복구한다.
- [x] DB당 실제 추론은 1건으로 유지한다. `LLM_MAX_CONCURRENT_GENERATIONS`를 높여도 현재 실행자 수는 늘지 않는다.
- [x] `GENERATION_QUEUE_LIMIT=3`을 기본으로 queued/running 합계를 실행 슬롯 포함 4건으로 제한한다. 부하 측정 후 조정한다.
- [x] 사용자·대화별 active를 각각 1건으로 제한한다. 일반 계정은 사용자 예산을 공유하고 system도 실행·문맥 제한을 따른다.
- [ ] 생성 API의 시간당 요청 속도 제한을 추가한다.
- [x] queue가 가득 차면 stream을 시작하기 전에 `429`와 `Retry-After`를 반환한다.
- [x] 사용자당 active 1건과 생성 시각·UUID FIFO를 적용한다. 세밀한 공정성 정책은 후속이다.
- [x] 사용자 중단은 상류 연결을 즉시 닫고 서버가 `logprobs`로 확인한 누적 출력과 입력량만 정산한다. 확인한 출력이 없으면 청구량을 면제하며 미수신 GPU 소모를 추정 청구하지 않는다. 브라우저 종료만으로 생성이 취소되지는 않는다.
- [x] 승인 상한·작업·이벤트와 단일 리더 잠금을 PostgreSQL에서 공유한다. 독립 worker lease·heartbeat는 후속이다.

### 테스트·완료 조건

- [x] 같은 요청을 동시에 반복해도 user/assistant 메시지가 각각 하나만 생긴다.
- [x] SSE를 끊었다 다시 연결해도 event가 중복 없이 이어진다.
- [x] API 또는 worker 재시작 후 queued/running 작업이 복구되거나 명확히 실패 처리된다.
- [x] 중단 시 상류 연결을 닫고 확인 수신량 정산이 끝나면 실행 슬롯을 반환한다.
- [x] 생성 실패가 이미 저장된 사용자 메시지를 삭제하지 않는다.
- [ ] 10개 동시 요청에서도 실제 MLX 생성은 최대 1건이고 queue 상한을 넘으면 즉시 거부된다.

**4단계 Gate:** 중복 요청·연결 끊김·worker 강제 종료 시나리오에서 메시지 중복이나 유실이 없고 상태가 항상 하나로 확정된다. 현재 멱등성·재생·중단 작업 상태 처리는 구현했다. 강제 종료 직전 미저장 조각과 불명 실제 사용량 자동 복구, 독립 worker 운영이 남아 있어 Gate 전체 완료로 표시하지 않는다.

---

## 5단계 — 실제 토큰 예산과 컨텍스트 압축

**목표:** 32K 컨텍스트를 넘지 않으면서 오래된 대화의 핵심 사실을 유지한다.

### 서버 중심 ContextBuilder

서버 system prompt + 완성된 요약 + 최근 원문 질문/답변 + 새 질문을 조립하고 실제 입력 토큰 + 최대 출력량을 검사한다. 완료·실패·중단·기존 `usage_pending` 작업의 사용자 발언과 실제로 받은 부분 답변을 포함한다. 빈 placeholder는 답변으로 넣지 않는다. 명시적인 `finish_reason=length`는 출력 상한 안내로 연결하고, `이어서 말해` 요청에는 최근 부분 답변을 제공한다. 새 요청에도 설정한 출력 상한을 적용한다.

**2026-09-09 구현:** `0009_context_compaction`을 개발 DB에 적용했다. 대화 요약과 별도 시스템 유지 사용량을 저장하며 사용자에게 요약 생성 비용을 차감하지 않는다. 격리 PostgreSQL의 migration 왕복·drift를 포함한 백엔드 **329개 테스트**(130.83초), 프런트 **56개**, 빌드·TypeScript·oxlint·Ruff 검사를 통과했다. 실제 MLX의 합성 대화 요약은 입력 298·출력 23토큰으로 완료됐고 요약만으로 이름·직업을 다시 답했다. 검증용 계정·대화는 모두 정리했다. 한 글자에서 중단한 원문 문맥에서는 모델이 이름만 답하고 직업을 누락하는 사례가 있었으며, 독립 재전송에서도 실제 EOS로 종료했다. 원문 전달 실패나 중단 신호 잔류로 확인되지 않았으므로 이 경우의 회상 품질 개선은 후속 평가에 남긴다.

- [x] 공개 API에서 임의의 `messages[]`, `system`, `assistant` 입력을 제거한다.
- [x] server system prompt는 코드/설정 또는 권한 있는 workspace 설정에서만 만든다.
- [x] 현재 모델의 chat template와 tokenizer를 이용해 실제 prompt token을 계산한다.
- [ ] 모델마다 `context_window`, tokenizer ID, 출력 상한, 안전 여유를 registry로 관리한다.
- [x] 실제 tokenizer 예산을 사용하면서 `LLM_MAX_HISTORY_CHARS`를 별도 입력 크기 방어로 유지한다.
- [x] 종료된 작업의 사용자 발언·부분 답변을 원문으로 포함하고 미완성 상태를 참고 정보로 전달한다.
- [x] 서버 정책·현재 질문·최근 원문을 유지하고 완성된 rolling summary로 오래된 문맥을 보충한다.

```text
서버 system/policy + 신뢰하지 않는 과거 요약 참고 정보
  → 최근 원문 질문·답변(시간순)
  → 현재 사용자 메시지
```

현재 기본 기준은 다음과 같다. 요약의 목표 비율은 절대 상한이 아니며 최종 생성 직전 실제 토큰 수를 다시 검사한다.

```text
압축 시작: 실제 입력 + 요청 최대 출력 >= context_window * 0.75
압축 목표: 요약과 최근 원문을 포함한 입력 + 최대 출력 <= context_window * 0.55
요약 최대 출력: min(1024, context_window / 4)
```

### 압축 정책

- [x] 모델 문맥의 75% 또는 별도 글자 수 제한에 도달하면 요약 가능한 오래된 원문을 압축한다.
- [x] 같은 단일 worker에서 답변 전에 요약을 실행하고, 여러 배치면 이전 요약을 다음 배치와 통합한다.
- [x] 서버 정책·현재 질문·최근 4쌍을 우선 보존하고 길이에 따라 최소 마지막 1쌍까지 조정한다.
- [x] 요약 지시에서 이름·직업·선호·수치·결정·미해결 질문·코드 식별자와 사실 정정을 보존하도록 한다.
- [x] `through_sequence`, 모델·prompt version, 상태·오류, 입력·출력량과 산정 근거를 기록한다.
- [x] 원문 메시지를 삭제하거나 요약으로 덮어쓰지 않는다.
- [x] 완성된 rolling summary checkpoint 뒤의 원문만 다음 문맥에 조립한다.
- [x] 대화별 active 작업 제한과 저장 직전 실행 상태·권한 확인으로 요약과 생성 충돌을 막는다.
- [x] 빈 요약·실패·명시적 출력 상한 잘림은 재사용하지 않고 해당 요청을 무차감 실패로 종료한다.
- [x] 요약 중 중단은 모델 연결을 닫고 답변 요청을 종료한다. 부분 요약은 사용하지 않는다.
- [x] 요약은 신뢰된 system instruction이 아니라 **신뢰하지 않는 대화 메모리**로 모델에 전달한다.
- [x] 요약 생성 사용량은 시스템 유지 기록에만 남긴다. 실제 답변의 입력(요약문 포함)·출력만 기존 후정산 정책으로 사용자에게 차감한다.
- [ ] 메시지 편집·분기를 도입할 때 요약 source hash와 파생 자료 무효화를 추가한다.
- [ ] 압축 전후 token 수, 요약 시간, fallback 횟수를 metrics로 남긴다.

### 테스트·완료 조건

**2026-09-09 코드 오류 검증:** 화면·브라우저·새 앱 서버를 열지 않고 코드 검사만 수행했다. 요약 사용량 누락·입력량 불일치·출력량 상한 초과 3개, 압축 후 예산 부족 1개, 요약 중 계정 비활성화·소속 철회 2개 오류 테스트를 추가했다. 격리 PostgreSQL의 migration 왕복·스키마 drift 검사와 백엔드 **335개 테스트**(148.23초), 프런트 **56개**, TypeScript·oxlint·Ruff·형식 검사·프로덕션 빌드를 통과했다. 원문 보존·사용자 무차감·실패한 요약 재사용 차단·정상 공급자로 복구 후 재질문을 확인했고 임시 테스트 DB를 제거했다. 실제 MLX 회상 품질은 이번 코드 검사 대상에 포함하지 않았으며 위의 짧은 중단 문맥 문제와 100턴 이상 평가를 완료로 표시하지 않는다.

- [x] 요약의 최종 사용량 누락·입력량 불일치·출력량 초과를 차단하고 원문·사용자 예산을 보존한다.
- [x] 압축 후 예산이 부족하면 실제 답변을 시작하지 않고 무차감 종료하며 완성된 요약은 재사용할 수 있게 남긴다.
- [x] 요약 중 계정 비활성화·소속 철회 시 요약 내용을 버리고 답변 실행·사용자 차감을 차단한다.
- [ ] 한글·영문·코드 혼합 입력에서도 생성 직전 prompt가 항상 상한 이내다.
- [ ] 100개 이상의 긴 turn에서도 context overflow가 발생하지 않는다.
- [x] 서버 기본 정책·현재 질문·최신 원문을 유지하고, 미완성 상태 안내와 요약을 별도 참고 정보로 전달한다.
- [x] 연속 배치와 다음 요청의 요약 재사용에서 원문 범위가 겹치거나 빠지지 않는다.
- [ ] 요약 중 새 메시지가 추가되어도 메시지가 유실되지 않는다.
- [x] 빈 요약·길이 제한·요약 중단·실행자 종료 시 요청을 무차감 종료하고 다음 질문을 허용한다.
- [ ] 이름, 숫자, 선호, 결정, 미해결 작업을 담은 한국어 golden conversation에서 정한 회상 기준을 통과한다.
- [ ] 한 글자 등 매우 짧은 중단 답변 뒤에도 복수 사실을 빠뜨리지 않는지 실제 모델 평가를 보강한다.
- [ ] 원문 삭제 시 summary와 검색 index 등 파생 데이터도 무효화·삭제된다.

**5단계 Gate:** 장기 대화 회귀 테스트에서 token 상한을 한 번도 넘지 않고, 정한 핵심 사실 보존 점수를 충족한다. 기본 자동 압축·이어 쓰기는 구현했으며, 100턴 이상 부하와 실제 모델의 정량 회상 평가 등 전체 Gate 검증은 후속이다.

---

## 6단계 — 실제 채팅 제품 UX

**목표:** 데모 화면을 매일 사용할 수 있는 저장형 채팅 제품으로 만든다.

- [ ] 메시지 복사, 응답 다시 생성, 실패 재시도, 중단 상태를 구현한다.
- [ ] 사용자 메시지 수정 후 분기와 답변 branch 관계를 저장한다.
- [ ] 편집·삭제·분기 시 영향받은 summary를 무효화한다.
- [ ] 대화 제목 검색을 먼저 만들고, 필요성이 확인되면 메시지 본문 검색을 추가한다.
- [ ] Markdown과 code block을 allowlist 기반으로 안전하게 렌더링하고 XSS를 차단한다.
- [ ] model 준비 중, queue 대기, 생성, 취소, 실패, 재연결 상태를 구분해 표시한다.
- [x] 진행 중 페이지를 새로고침해도 generation 상태와 부분 결과를 복구한다.
- [ ] 여러 탭에서 같은 대화를 수정할 때 version 충돌을 처리한다.
- [ ] 온도, 출력 길이, thinking 등 허용한 모델 설정만 사용자별로 저장한다.
- [ ] 좋아요/싫어요와 선택적 사유를 품질 feedback으로 저장한다.
- [ ] 키보드 탐색, focus, screen reader label, 색 대비를 점검한다.
- [ ] 모바일, 긴 code/table, 한글 IME 조합 입력을 테스트한다.

**6단계 Gate:** 새 사용자 생성 → 로그인 → 새 채팅 → 응답 중단/재시도 → 목록 복원 → 로그아웃의 핵심 E2E가 브라우저 테스트로 통과한다.

---

## 7단계 — 인터넷 검색 (선택 기능)

**선행 조건:** 5단계의 실제 토큰 예산과 6단계의 채팅 설정·오류 UX가 완료되어야 한다. 8단계 파일 RAG와는 독립적으로 켜고 끌 수 있어야 한다.

**목표:** 사용자가 명시적으로 허용한 대화에서만 인터넷의 최신 정보를 가져오고, 로컬 모델이 출처를 구분해 답변하도록 한다. 검색과 문서 수집만 외부 통신을 사용하고 모델 추론은 계속 로컬에서 실행한다.

### 권한과 화면 동작

- [ ] `깊이 생각하기` 아래에 같은 형태의 `인터넷 검색 사용` 스위치를 추가한다.
- [ ] 기본값은 꺼짐으로 두고 대화별 설정 `web_search_enabled`로 저장한다.
- [ ] 스위치를 켤 때 브라우저의 `navigator.onLine`만 믿지 않고, FastAPI가 설정된 검색 공급자에 짧은 실제 연결 검사를 수행한다.
- [ ] 연결 검사에 실패하면 스위치를 즉시 끄고 `인터넷이 연결되어 있지 않습니다.` 알림을 표시한다.
- [ ] 검색 도중 연결이 끊기면 구조화된 오류를 반환하고, 작성 중인 질문을 보존한 채 연결 후 재시도할 수 있게 한다.
- [ ] 검색 중, 본문 확인 중, 출처 정리 중 상태를 일반 모델 생성 상태와 구분해 표시한다.
- [ ] 스위치가 꺼져 있을 때는 검색, 연결 검사, 외부 페이지 요청이 한 건도 발생하지 않게 한다.
- [ ] 검색어와 방문한 주소가 외부 서비스에 전달될 수 있지만 대화 전체와 로컬 모델 입력은 보내지 않는다는 점을 설정 옆에 안내한다.

### 검색과 본문 수집

- [ ] 검색 공급자를 교체할 수 있는 `WebSearchProvider` 인터페이스를 만들고 API key, timeout, 결과 수를 환경 설정으로 관리한다.
- [ ] 검색엔진 결과 HTML을 직접 긁는 불안정한 방식은 사용하지 않고, 약관상 허용된 검색 API 또는 사용자가 선택한 검색 공급자를 사용한다.
- [ ] 첫 구현은 현재 질문으로 최대 3~5개 결과를 검색하고, 제목·URL·요약·조회 시각을 정규화한다.
- [ ] 명시적으로 최신 정보나 인터넷 검색을 요구한 질문은 반드시 검색하고, 그 외에는 검색 필요성 판단 결과를 로그에 남긴다.
- [ ] 페이지 본문은 정적 HTML의 읽을 수 있는 텍스트만 추출하며 JavaScript 실행, 로그인, CAPTCHA 우회는 초기 범위에서 제외한다.
- [ ] 요청별·전체 timeout, 최대 응답 크기, 허용 MIME, redirect 횟수와 총 수집 문자 수를 제한한다.
- [ ] 중복 URL과 사실상 같은 문서를 제거하고 검색 실패, `429`, 차단, 빈 본문을 출처별로 격리한다.
- [ ] 수집 본문을 chunk로 나누고 관련 구간만 선택해 5단계 `ContextBuilder`의 전용 인터넷 자료 예산 안에 넣는다.
- [ ] 답변에는 주장과 대응하는 출처 번호, 문서 제목, 원문 URL, 조회 시각을 표시하고 근거가 부족하면 부족하다고 답하게 한다.

### 보안과 개인정보

- [ ] 허용 프로토콜을 `http`와 `https`로 제한하고 loopback, 사설망, link-local, metadata endpoint, 파일 경로 접근을 차단한다.
- [ ] 최초 URL뿐 아니라 DNS 해석 결과와 모든 redirect 대상에도 SSRF 차단 규칙을 다시 적용한다.
- [ ] 외부 페이지의 명령문은 신뢰하지 않는 자료로 표시하고 system prompt나 도구 권한을 덮어쓰지 못하게 한다.
- [ ] 외부 요청에는 사용자 cookie, session, 인증 header와 내부 주소를 전달하지 않는다.
- [ ] 검색어와 원문을 일반 로그에 남기지 않고 공급자 오류에서도 API key와 내부 네트워크 정보를 가린다.
- [ ] 공급자별 개인정보 정책, 비용, 요청 한도와 장애 시 동작을 기록한 뒤 기본 공급자를 결정한다.

### 테스트·완료 조건

- [ ] 스위치가 꺼진 E2E에서 외부 요청이 0건인지 검사한다.
- [ ] 인터넷이 끊긴 상태에서 스위치를 켜면 정확한 알림이 나오고 꺼진 상태로 돌아가는지 검사한다.
- [ ] 검색 중 연결 끊김, timeout, `429`, 잘못된 인증, 빈 결과에서도 질문이 유실되지 않는지 검사한다.
- [ ] 가짜 검색 공급자와 고정 HTML fixture로 검색 순서, 본문 추출, 중복 제거, 토큰 상한을 결정적으로 검사한다.
- [ ] 사설 IP, redirect 우회, DNS 변경을 이용한 SSRF 회귀 테스트를 추가한다.
- [ ] 웹 문서 안의 prompt injection이 모델 지침과 도구 권한을 바꾸지 못하는지 평가한다.
- [ ] 최신성이 필요한 한국어 질문 평가셋으로 출처 정확도, 근거 없는 주장 비율, 검색 지연을 측정한다.

**7단계 Gate:** 스위치가 꺼졌을 때 외부 통신이 0건이고, 오프라인 알림·검색 실패 복구·SSRF 차단 테스트를 통과하며, 스위치를 켠 대표 질문의 답변이 토큰 상한 안에서 확인 가능한 출처를 제공한다.

---

## 8단계 — 파일 업로드와 RAG (선택 기능)

**선행 조건:** 1~6단계와 사용자/workspace 권한 격리가 완료되어야 한다. 7단계 인터넷 검색과는 독립적으로 선택할 수 있다.

**목표:** 권한 있는 업로드 문서를 검색하고 원문 근거가 보이는 답변을 제공한다.

- [ ] 원본 파일은 S3 호환 object storage에 두고 로컬 디스크 저장은 개발용 adapter로 제한한다.
- [ ] PostgreSQL에 `documents`, `document_versions`, `chunks`, `attachments` 메타데이터를 추가한다.
- [ ] 첫 vector store는 `pgvector`를 검토해 인프라 수를 줄인다.
- [ ] `uploaded → scanning → parsing → indexing → ready | failed` 상태를 저장한다.
- [ ] 확장자뿐 아니라 MIME와 magic bytes를 검사하고 크기·페이지·압축 해제 상한을 둔다.
- [ ] 악성 파일 검사와 격리된 parser worker를 사용한다.
- [ ] parsing/embedding 재시도 상한과 dead-letter 상태를 둔다.
- [ ] 원본 hash로 중복을 찾고 문서·embedding 모델 version을 기록한다.
- [ ] chunk 크기/overlap은 추측하지 말고 실제 문서 평가셋으로 결정한다.
- [ ] keyword + vector hybrid search와 reranker를 비교 평가한다.
- [ ] 검색 **전**에 workspace/user/document ACL을 적용한다.
- [ ] 검색 문서 안의 명령은 신뢰하지 않는 데이터로 다뤄 prompt injection을 방어한다.
- [ ] 답변에 문서명과 page/chunk 근거를 표시한다.
- [ ] 파일 삭제 시 원본, chunk, embedding, cache까지 삭제를 전파한다.

**8단계 Gate:** 다른 workspace 문서는 검색 후보에도 한 건도 들어가지 않고, 사용자는 답변 근거를 원문 위치까지 확인할 수 있다.

---

## 9단계 — 보안·개인정보·관리자 기능 강화

**목표:** 저장된 데이터와 운영 권한을 보호하고 누가 무엇을 했는지 추적한다.

- [ ] 프런트와 API를 동일 HTTPS origin으로 제공한다.
- [ ] reverse proxy만 외부에 노출하고 FastAPI와 MLX는 loopback/internal network에 둔다.
- [ ] HSTS, CSP, `frame-ancestors`, `nosniff`, Referrer-Policy, trusted host를 설정한다.
- [ ] 운영 CORS는 정확한 origin allowlist만 허용한다.
- [ ] 운영에서 `/docs`, debug error와 내부 model 주소를 일반 사용자에게 노출하지 않는다.
- [ ] 운영 secret을 `.env` 대신 secret manager에 두고 단일 Mac 운영 환경에서는 Keychain을 사용할 수 있게 한다.
- [ ] DB, OIDC, session key를 분리하고 회전 절차를 작성한다.
- [ ] prompt·response 원문, cookie, token, 비밀번호, DB URL을 로그와 오류 도구에서 redaction한다.
- [ ] 로그인 실패, 권한 변경, 대화 export/delete, 관리자 조회를 append-only audit log로 남긴다.
- [ ] 관리자도 원문 대화를 열람할 수 있는 조건과 승인 기록을 제한한다.
- [ ] 사용자/workspace/IP별 요청 속도와 export 상한을 둔다. 서비스 토큰 quota는 1.5단계 정책을 적용하고 system의 한도 면제와 실행 자원 보호를 구분한다.
- [ ] 대화 보존기간, soft delete 유예기간, backup 만료 시점을 문서화한다.
- [ ] 사용자 데이터 export, 대화 영구 삭제, 계정 삭제 job을 구현한다.
- [ ] 디스크는 FileVault, 외부 backup은 별도 key로 암호화한다.
- [ ] 저장소 secret scan, dependency scan, SAST, SBOM 생성을 CI에 추가한다.
- [ ] IDOR, XSS, CSRF, session 탈취, prompt injection, 무제한 자원 사용, 노트북 분실 threat model을 작성한다.
- [ ] 관리자 화면에는 사용자 상태, 역할, queue, worker, 모델 상태만 필요한 범위로 표시한다.

**9단계 Gate:** 교차 workspace 권한, CSRF/XSS, session 철회, rate limit, secret scan 테스트를 통과하고 Critical/High 보안 이슈가 남지 않는다.

---

## 10단계 — 관측성·SLO·장애 알림

**목표:** 신고가 오기 전에 문제를 발견하고 request ID 하나로 원인을 추적한다.

- [ ] JSON 구조화 로그에 `request_id`, `conversation_id`, `generation_id`, model/prompt version을 연결한다.
- [ ] user ID는 내부 식별자 또는 hash로 기록하고 메시지 원문은 넣지 않는다.
- [ ] queue depth/wait, time-to-first-token, generation time, tokens/sec, 입출력 token, 취소율, 오류율을 측정한다.
- [ ] DB pool, worker heartbeat, MLX 상태, process RSS, macOS memory pressure, disk 여유를 측정한다.
- [ ] OpenTelemetry trace로 API → DB → queue → worker → provider 구간을 연결한다.
- [ ] API, DB, queue, model worker dashboard를 만든다.
- [ ] 내부 서비스의 가용성·지연·오류율 SLO를 baseline 측정 후 숫자로 정한다.
- [ ] 모델 중단, queue 적체, DB 장애, backup 실패, disk 부족, 401/403/429 급증 alert를 만든다.
- [ ] alert 전송 자체를 정기적으로 시험하고 noisy alert를 조정한다.

**10단계 Gate:** 모델, DB, queue, disk 장애를 의도적으로 발생시켰을 때 alert가 울리고 request ID로 전체 흐름을 추적할 수 있다.

---

## 11단계 — LLM 평가·자동 테스트·CI/CD

**목표:** 코드, 모델, prompt, 요약, 인터넷 검색, RAG 변경의 품질과 안정성 회귀를 배포 전에 잡는다.

### 테스트 피라미드

- [ ] repository/service unit test를 추가한다.
- [ ] 실제 PostgreSQL 기반 migration·transaction·RLS integration test를 추가한다.
- [ ] auth, 대화 CRUD, idempotency, queue, cancel, SSE 재연결 API test를 추가한다.
- [ ] Playwright 등으로 가입/로그인/채팅/복원 핵심 E2E를 추가한다.
- [ ] SSE 동시 접속, 32K 근접 입력, 반복 취소, worker 강제 종료 load test를 만든다.
- [ ] 4시간 이상 soak test로 memory leak과 처리량 저하를 확인한다.

### LLM 평가

- [ ] 실제 사용 사례를 익명화한 한국어 golden dataset을 만든다.
- [ ] 일반 대화, 도메인 용어, code, 긴 문맥, 숫자/고유명사, 거절·안전 사례를 포함한다.
- [ ] summary 사실 보존, hallucination, prompt injection 회귀 평가를 추가한다.
- [ ] 인터넷 검색을 켰다면 검색 필요성 판단, 최신성, 출처 정확도, 근거 없는 주장 비율을 평가한다.
- [ ] RAG를 켰다면 retrieval recall, groundedness, citation 정확도를 평가한다.
- [ ] model/prompt/generation 설정별 품질, 지연, memory 결과를 version별로 저장한다.
- [ ] 운영 version을 baseline으로 지정하고 허용 회귀 폭을 숫자로 정한다.
- [ ] 기준을 넘긴 회귀가 있으면 배포를 차단한다.

### CI/CD

- [ ] Python lint/format/test와 프런트 lint/typecheck/build/test를 GitHub Actions에 추가한다.
- [ ] 빈 DB와 직전 운영 schema 양쪽에서 migration을 검사한다.
- [ ] secret/dependency/security scan을 필수 검사로 만든다.
- [ ] Python/npm lockfile과 version 있는 web/API/worker artifact를 만든다.
- [ ] 개발 `--reload`, `scripts/dev.py`, `npm run dev`를 운영 실행에서 제거한다.
- [ ] staging에서 migration, 배포, rollback을 실제로 검증한다.
- [ ] DB 변경은 가능한 한 expand → migrate → contract 순서로 배포한다.

**11단계 Gate:** 필수 검사와 품질 기준에 실패한 commit은 운영 배포할 수 없고, staging에서 이전 artifact로 되돌릴 수 있다.

---

## 12단계 — 운영 배포·백업·복구

**목표:** 개발 터미널이 아니라 재현 가능하고 복구 가능한 운영 방식으로 실행한다.

### 권장 배포 형태

```text
사용자 또는 보호된 네트워크
        │
    HTTPS reverse proxy
        │
  Web + FastAPI API ── PostgreSQL / Redis
        │
    private network 또는 mTLS
        │
  전용 Apple Silicon MLX worker
```

- [ ] 개발·test·staging·prod 설정과 DB를 분리한다.
- [ ] production build와 process supervisor(`launchd` 등)로 API/worker를 자동 시작·재시작한다.
- [ ] migration은 앱 worker마다 실행하지 않고 배포 중 한 번만 실행한다.
- [ ] 모델 warm-up과 readiness를 만들고 model 파일은 Git이 아닌 cache/checksum으로 관리한다.
- [ ] TLS certificate 갱신, log rotation, disk capacity 경고를 자동화한다.
- [ ] 운영 사용자 수, 동시 접속, prompt/output token 분포를 정의해 capacity 문서로 남긴다.
- [ ] 목표 부하에 여유를 둔 load/soak test를 통과한 queue 길이와 context 크기를 운영값으로 사용한다.
- [ ] PostgreSQL 일일 encrypted backup을 운영 host와 다른 장치/저장소에 둔다.
- [ ] 더 짧은 RPO가 필요하면 base backup + WAL archive 기반 PITR을 구성한다.
- [ ] backup 계정과 backup 삭제 권한을 runtime 앱과 분리한다.
- [ ] 월 1회 깨끗한 환경으로 restore하고 메시지 수, checksum, RLS, migration version을 검증한다.
- [ ] 시작/중단, MLX OOM, queue 적체, DB 장애, disk 부족, secret 회전, rollback, restore runbook을 작성한다.
- [ ] 사고 심각도, 담당자, 연락 경로, 전체 session 폐기와 생성 kill switch를 정의한다.

> 현재 노트북 한 대에 API, DB, 모델을 모두 두면 전체 서비스의 단일 장애점이 된다. 요구 가용성이 높아지면 DB/API를 관리되는 별도 host로 옮기고 Mac은 VPN 또는 mTLS 뒤의 전용 추론 worker로 분리한다.

**12단계 Gate:** 재부팅 후 자동 기동하고, backup을 새 환경에 복원하며, 운영자가 runbook만 보고 주요 장애와 rollback을 처리할 수 있다.

---

## 13단계 — 측정에 따른 확장

**선행 조건:** 실제 사용량과 10~12단계의 capacity 지표에서 병목이 확인되어야 한다.

- [ ] API를 stateless하게 만들고 여러 인스턴스가 같은 DB/queue/object storage를 사용하게 한다.
- [ ] 분산 lease, heartbeat, fencing token으로 중복 generation을 막는다.
- [ ] 여러 model worker의 model, 상태, 가용 slot을 등록한다.
- [ ] model 종류, workspace quota, queue priority 기반 routing을 추가한다.
- [ ] parsing/embedding worker를 inference worker와 분리한다.
- [ ] 지속적인 queue wait와 SLO 위반을 장비 증설 신호로 정한다.
- [ ] 전용 Apple Silicon 장비 또는 GPU server로 이전한다.
- [ ] 기존 provider interface를 통해 vLLM 등 다른 OpenAI 호환 backend를 연결한다.
- [ ] worker 장애 시 보조 provider 또는 제한 모드를 준비한다.
- [ ] Kubernetes 같은 복잡한 platform은 실제 요구가 확인된 뒤 도입한다.

**13단계 Gate:** worker 하나가 중단돼도 요청이 다른 worker로 이동하거나 안전하게 대기하고, 확장 후에도 권한·순서·취소·멱등성이 동일하게 동작한다.

---

## 최종 운영 준비 체크리스트

서비스 용도를 구분하지 않고 아래 기준을 모두 통과하면 운영 준비가 끝난 것으로 본다. 7단계 인터넷 검색 또는 8단계 RAG를 사용하지 않는다면 해당 선택 기능의 전용 항목만 제외한다.

### 기능과 데이터

- [ ] 회원가입, 이메일 확인, 로그인, 로그아웃, session 철회가 동작한다.
- [ ] 가입 정책을 설정만으로 `open`, `invite_only`, `disabled`로 바꿀 수 있다.
- [ ] 채팅방·메시지·생성 상태가 새로고침과 재시작 후 복원된다.
- [ ] 중복 요청, 취소, SSE 재연결, worker 장애가 데이터 중복·유실 없이 처리된다.
- [ ] tokenizer 기반 context 예산과 압축이 장기 대화 평가를 통과한다.
- [ ] 사용자/workspace 간 데이터 접근이 모든 API와 DB 정책에서 차단된다.

### 보안과 개인정보

- [ ] HTTPS, secure cookie, CSRF, 보안 header, 정확한 CORS와 rate limit이 적용됐다.
- [ ] 비밀값과 원문 대화가 source, 일반 로그, 오류 화면에 노출되지 않는다.
- [ ] 감사 로그와 데이터 보존·export·영구 삭제 정책이 동작한다.
- [ ] Critical/High 보안 이슈가 남지 않았고 예외는 담당자·만료일·완화책을 기록했다.

### 품질과 운영

- [ ] SLO, LLM 품질 기준, capacity 상한이 숫자로 정해졌다.
- [ ] CI의 lint, build, migration, unit, integration, E2E, security 검사가 통과한다.
- [ ] 목표 부하의 load/soak test와 model·DB·disk 장애 test를 통과한다.
- [ ] dashboard와 alert로 API → DB → queue → worker 흐름을 추적할 수 있다.
- [ ] backup을 새 환경에 실제 복원하고 RPO/RTO를 충족했다.
- [ ] 재부팅 자동 기동, 배포 rollback, secret 회전, 장애 대응 runbook을 검증했다.

### 인터넷 검색을 사용하는 경우

- [ ] 사용자가 스위치를 켠 경우에만 외부 요청이 발생한다.
- [ ] 오프라인 감지, 검색 실패 복구, 요청 상한과 SSRF 차단이 검증됐다.
- [ ] 답변의 주요 외부 사실에 확인 가능한 출처와 조회 시각이 표시된다.
- [ ] 검색 공급자 장애나 품질 기준 미달 시 기능을 즉시 끌 수 있다.

### RAG를 사용하는 경우

- [ ] 파일 검사, 격리 parsing, ACL retrieval이 완료됐다.
- [ ] 교차 workspace 문서 노출이 0건이다.
- [ ] 근거 표시와 원본·chunk·embedding 삭제 전파가 검증됐다.
- [ ] 품질 기준 미달 시 feature flag로 즉시 중단할 수 있다.

## 바로 진행할 첫 작업 묶음

로드맵을 실제 issue/PR로 옮길 때는 다음 순서를 권장한다.

1. **PR 1 (완료, 2026-09-09):** PostgreSQL Compose, DB 설정, async session, Alembic, live/ready health
2. **PR 2 (완료, 2026-09-09):** users/workspaces/workspace_members/conversations/messages 초기 schema와 repository test
3. **계정·토큰 정책 확장 (완료, 2026-09-09):** system/member, 플랜·기간 예산·예약/정산, 명시적 시스템 계정 초기화 — 내부 서비스 범위
4. **PR 3 핵심 완료:** 로그인·선택적 일반 가입·시스템 비밀번호·24시간 세션·CSRF·workspace 업무 API 권한·자기 사용량 조회. 이메일 확인·초대·RLS는 후속, 운영자 예산 관리는 CLI 유지
5. **PR 4 핵심 완료:** conversation CRUD·커서·`/chat/{id}` 목록·대화 복원·잔여량·무제한 표시. 날짜 그룹·LLM 제목·전체 고정 정렬은 후속
6. **PR 5 핵심 완료:** 실제 입력 계산·종료 후 사용량 차감·메시지 멱등성·원자적 허용량 승인·generation state·API 내장 단일 worker·bounded queue
7. **PR 6 핵심 완료:** SSE 재생·대기/실행 중 중단·확인 수신량 정산·후정산 불명 사용량 무차감 종료와 차단 복구. 독립 worker·공급자 사용량 대조·이벤트 보존 정책은 후속
8. **PR 7 핵심 완료:** 서버 ContextBuilder·실제 문맥 예산 검사·중단/미완성 원문 유지·이어 쓰기. 다중 모델 registry와 혼합 언어 평가는 후속
9. **PR 8 핵심 완료:** 자동 rolling summary·원문 보존·사용자 비차감·오류 회귀 검사. 100턴 이상 부하와 실제 모델의 정량 회상 평가는 후속
10. **월 무료 정책 완료:** 한국 시간 월 20,000토큰·미이월·요청 시 자동 지급·plan 우선·기존 예산 정산 보존. 백엔드 211개·프런트 36개와 실제 가입·MLX 정산 검증 통과
11. **PR 9 (결제 도입 시):** 결제사·유료 등급·가격, 구독·검증된 webhook·멱등 plan 예산 발급, 추가 구매·환불 조정 이력

위 순서는 기능을 나누기 위한 기준이며 이번에는 이미 준비한 인증·저장소·토큰 기반을 대화·생성 API와 화면에 연결했다. 이후에도 각 변경의 migration과 테스트를 통과한 상태로 main을 실행 가능하게 유지한다.

## 당장 피할 것

- [ ] JWT/session token을 `localStorage` 또는 `sessionStorage`에 저장하지 않는다.
- [ ] 클라이언트가 보낸 전체 대화와 role을 그대로 모델 입력으로 사용하지 않는다.
- [ ] `mlx_vlm.server :8080`을 LAN 또는 인터넷에 직접 노출하지 않는다.
- [ ] 27B 모델을 API worker마다 따로 load하지 않는다.
- [ ] 프로세스 내부 semaphore를 멀티 worker 운영 queue로 간주하지 않는다.
- [x] context 압축을 위해 원본 메시지를 삭제하지 않는다.
- [ ] token마다 DB commit하지 않는다.
- [ ] 인증·ACL 전에 RAG와 파일 업로드를 공개하지 않는다.
- [ ] backup restore와 교차 사용자 권한 테스트 없이 서비스를 운영 완료로 표시하지 않는다.

## 참고 문서

- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [FastAPI Deployment Concepts](https://fastapi.tiangolo.com/deployment/concepts/)
- [SQLAlchemy AsyncIO](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [OWASP REST Security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html)
- [PostgreSQL Row Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [PostgreSQL Backup and Restore](https://www.postgresql.org/docs/current/backup.html)
- [PostgreSQL Point-in-Time Recovery](https://www.postgresql.org/docs/current/continuous-archiving.html)
- [pgvector](https://github.com/pgvector/pgvector)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)
