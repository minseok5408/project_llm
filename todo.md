# Qwen Workbench 서비스화 TODO

이 문서는 현재의 로컬 채팅 MVP를 **지속적으로 운영 가능한 하나의 서비스**로 단계적으로 확장하기 위한 실행 순서다. 위 단계의 완료 조건을 통과한 뒤 다음 단계로 넘어간다.

## 현재 상태

- [x] Python 3.12 `.venv` 개발 환경
- [x] React/Vinext 채팅 화면
- [x] FastAPI 게이트웨이
- [x] Mock/MLX provider 교체 구조
- [x] `Qwen3.8-27B-4bit` SSE 스트리밍 연결
- [x] 기본 입력 검증, 오류 표시, 생성 중지
- [x] Mock 기반 API 테스트와 프런트 빌드 검사
- [ ] 데이터베이스와 마이그레이션
- [ ] 회원가입·로그인·사용자별 권한 격리
- [ ] 채팅방·메시지·생성 상태 저장
- [ ] 실제 tokenizer 기반 컨텍스트 관리와 압축
- [ ] 영속 작업 큐, 재연결, 장애 복구
- [ ] HTTPS, 감사 로그, 백업, 관측, 배포 체계

> 현재는 브라우저가 대화의 유일한 원본이고 매 요청마다 전체 메시지 배열을 서버로 보낸다. 새로고침하면 대화가 사라지고, 클라이언트가 `system`/`assistant` 역할을 위조할 수 있으며, 프로세스가 종료되면 생성 상태를 복구할 수 없다. 따라서 아직 운영 가능한 서비스로 배포하면 안 된다.

## 먼저 고정할 원칙

- **진실의 원본은 서버다.** 클라이언트는 새 사용자 메시지만 보내고, 모델 컨텍스트는 서버가 DB에서 조립한다.
- **기본 저장소는 PostgreSQL이다.** 개발만을 위한 SQLite 전용 구현을 만들지 않는다.
- **처음부터 workspace 경계를 둔다.** 모든 계정에 기본 workspace 하나를 만들고, 나중에 공유 workspace를 추가해도 데이터 구조를 바꾸지 않게 한다.
- **가입 정책은 설정으로 바꾼다.** 서비스 코드는 하나로 유지하고 `open`, `invite_only`, `disabled` 중 하나를 운영 설정으로 선택한다.
- **브라우저에 인증 토큰을 저장하지 않는다.** 서버측 opaque session과 `Secure`·`HttpOnly`·`SameSite` 쿠키를 사용한다.
- **MLX 모델 프로세스는 한 개만 둔다.** 현재 M3 Pro에서는 동시 생성 1건과 짧은 bounded queue를 기본값으로 삼는다.
- **모델 서버 `:8080`은 외부에 공개하지 않는다.** 운영에서도 loopback 또는 보호된 내부망에서만 접근한다.
- **요약은 원문을 대체하지 않는다.** 원본 메시지는 보존하고, 요약의 대상 범위와 버전을 별도로 기록한다.
- **RAG는 인증·DB·권한 격리 이후에 한다.** 그렇지 않으면 문서 ACL과 삭제 전파를 다시 설계하게 된다.
- **복구해 보지 않은 백업은 완료가 아니다.** 실제 restore drill을 출시 조건으로 삼는다.

## 목표 구조

```text
Browser
  │  same-origin HTTPS + session cookie
  ▼
Web / FastAPI API
  ├── PostgreSQL: 사용자, 대화, 메시지, 요약, 생성 작업
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

### 구현

- [ ] 개발용 PostgreSQL을 Docker Compose로 추가한다.
- [ ] `SQLAlchemy 2.x async`, `asyncpg`, `Alembic`을 Python 의존성에 추가한다.
- [ ] `DATABASE_URL`, pool 크기, 연결 timeout 설정을 추가하고 비밀값은 Git에 올리지 않는다.
- [ ] 아래와 같이 백엔드 책임을 분리한다.

```text
backend/app/
├── api/v1/           # HTTP 요청·응답과 dependency
├── db/               # engine, session, migration 공통 코드
├── models/           # ORM 모델
├── repositories/     # 권한 범위가 적용된 DB 접근
├── services/         # 대화·생성·컨텍스트 업무 규칙
└── workers/          # 생성·요약·문서 처리 작업
```

- [ ] 요청마다 `AsyncSession`을 열고 반드시 닫는다.
- [ ] SSE가 연결된 긴 시간 동안 DB transaction을 열어두지 않는다.
- [ ] 모든 PK는 UUID, 시간은 PostgreSQL `timestamptz`와 UTC를 사용한다.
- [ ] `/health/live`와 `/health/ready`를 분리한다. DB 또는 모델이 준비되지 않으면 readiness만 `503`을 반환한다.
- [ ] 테스트도 SQLite 대체물이 아니라 실제 PostgreSQL에서 실행한다.

### 초기 데이터 모델

| 테이블 | 핵심 내용 |
|---|---|
| `users` | 이메일, 표시 이름, 상태, 생성·수정·마지막 로그인 시각 |
| `auth_identities` | password/OIDC provider와 provider subject; 한 사용자에게 여러 로그인 수단 연결 가능 |
| `auth_sessions` | session token의 **hash**, 만료·철회 시각, 기기 메타데이터 |
| `workspaces` | 사용자와 공유 그룹의 데이터·권한 경계 |
| `workspace_members` | 사용자, workspace, `owner/admin/member` 역할; 복합 unique |
| `conversations` | workspace, 작성자, 제목, 상태, 모델, 설정 JSON, 마지막 메시지 시각, soft delete |
| `messages` | 대화 내 sequence, 역할, 본문, 상태, 작성자, token 수, 모델·prompt version |
| `generation_runs` | `queued/running/completed/failed/cancelled`, 입출력 메시지, 모델 설정, token·지연 정보 |
| `generation_events` | generation별 순번이 있는 스트림 event; 재연결 시 replay |
| `conversation_summaries` | 요약 대상 시작·끝 sequence, 내용, token 수, 모델·prompt version, source hash |
| `audit_logs` | actor, workspace, action, object, 결과, 시각; 메시지 원문은 저장하지 않음 |

### 필수 제약과 인덱스

- [ ] `users.email`은 정규화 후 unique로 만든다.
- [ ] `(workspace_id, user_id)` membership을 unique로 만든다.
- [ ] `(conversation_id, sequence)` message 순번을 unique로 만든다.
- [ ] `(generation_id, sequence)` event 순번을 unique로 만든다.
- [ ] 대화 목록은 `(workspace_id, status, last_message_at DESC, id DESC)` cursor index를 사용한다.
- [ ] queued 작업은 status와 생성 시각에 partial index를 둔다.
- [ ] FK의 delete 동작과 soft/hard delete 정책을 명시한다.
- [ ] migration 실행 역할과 애플리케이션 runtime DB 역할을 분리할 수 있게 준비한다.

### 테스트·완료 조건

- [ ] 빈 DB에서 `alembic upgrade head`가 성공한다.
- [ ] 한 revision downgrade 후 다시 upgrade할 수 있다.
- [ ] ORM과 실제 migration schema에 drift가 없다.
- [ ] DB 중단 시 liveness는 살아 있고 readiness만 실패한다.
- [ ] API 테스트가 종료된 뒤 열린 connection 또는 transaction이 남지 않는다.

**1단계 Gate:** 깨끗한 환경에서 PostgreSQL을 띄우고 migration과 통합 테스트를 한 명령 흐름으로 재현할 수 있다.

---

## 2단계 — 회원가입·로그인·workspace 권한

**목표:** 모든 데이터가 인증된 사용자와 workspace에 귀속되고, 다른 사용자의 ID를 알아도 접근할 수 없게 한다.

### 가입 정책과 인증 구조

- [ ] 이메일/비밀번호 회원가입, 이메일 확인, 로그인, 비밀번호 재설정을 기본 흐름으로 구현한다.
- [ ] `SIGNUP_MODE=open|invite_only|disabled` 설정으로 가입 범위만 바꾸고 별도 서비스 코드로 나누지 않는다.
- [ ] `invite_only`에서는 만료·사용 횟수가 있는 초대 token을 검증한다.
- [ ] `disabled`에서는 기존 사용자의 로그인만 허용한다.
- [ ] 추후 OIDC/SSO가 필요하면 같은 `auth_identities` 모델에 provider를 추가하고 사용자·권한 모델은 그대로 유지한다.
- [ ] 계정 생성 시 기본 workspace와 owner membership을 한 transaction에서 만든다.

### 세션과 계정 보안

- [ ] 자체 비밀번호는 Argon2id로 hash하고 평문 또는 복호화 가능한 형태로 저장하지 않는다.
- [ ] 이메일 존재 여부를 로그인·가입·재설정 응답에서 드러내지 않는다.
- [ ] 짧은 무작위 session 원문은 쿠키에만 두고 DB에는 hash만 저장한다.
- [ ] 쿠키를 `__Host-session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`로 설정한다.
- [ ] 로그인·권한 변경 때 session ID를 회전하고 로그아웃·계정 비활성화 때 즉시 철회한다.
- [ ] 상태 변경 API에 CSRF token과 `Origin` 검증을 적용한다.
- [ ] 로그인, 가입, 재설정에 IP/계정별 rate limit을 둔다.
- [ ] 운영 만료 정책을 정한다. 초기 예: 유휴 30분, 절대 8시간 후 재인증.

### API와 화면

- [ ] `POST /api/v1/auth/signup` — 현재 `SIGNUP_MODE` 정책을 적용
- [ ] `POST /api/v1/auth/login`
- [ ] `POST /api/v1/auth/logout`
- [ ] `POST /api/v1/auth/logout-all`
- [ ] `GET /api/v1/auth/me`
- [ ] OIDC를 추가하면 login/callback/logout route와 `state`, `nonce`, issuer, audience 검증을 적용한다.
- [ ] 로그인·가입·이메일 확인·재설정 화면을 추가한다.
- [ ] FastAPI 공통 dependency에서 현재 사용자, workspace, 역할을 결정한다.
- [ ] 브라우저가 보낸 `user_id`, `workspace_id`, `role`을 신뢰하지 않는다.
- [ ] 모든 repository 조회가 object ID와 현재 workspace membership을 함께 검사한다.
- [ ] PostgreSQL RLS를 방어 계층으로 추가하고 runtime 역할을 `NOBYPASSRLS` 비소유자로 둔다.

### 테스트·완료 조건

- [ ] 인증되지 않은 업무 API는 `401`을 반환한다.
- [ ] 사용자 A가 사용자 B의 conversation/message ID로 조회·수정·삭제·내보내기를 할 수 없다.
- [ ] CSRF, 잘못된 Origin, 만료·철회된 session이 거부된다.
- [ ] session 회전, 현재 기기 로그아웃, 전체 기기 로그아웃이 동작한다.
- [ ] OIDC를 활성화했다면 위조·만료 token과 재사용 callback이 거부된다.
- [ ] RLS 테스트는 migration owner가 아닌 실제 runtime DB 역할로 실행한다.

**2단계 Gate:** 두 사용자와 두 workspace로 실행한 교차 접근 테스트가 전부 실패하고, 인증 우회 없이 채팅 API를 호출할 수 없다.

---

## 3단계 — 채팅방·메시지·목록 저장

**목표:** 새로고침, 로그아웃, 서버 재시작 뒤에도 사용자의 채팅방과 대화 내용이 복원되게 한다.

### API

```http
POST   /api/v1/conversations
GET    /api/v1/conversations?cursor=&limit=30&status=active
GET    /api/v1/conversations/{conversation_id}
PATCH  /api/v1/conversations/{conversation_id}
DELETE /api/v1/conversations/{conversation_id}
GET    /api/v1/conversations/{conversation_id}/messages?before=&limit=50
```

- [ ] 대화 생성·조회·제목 변경·pin·archive·soft delete를 구현한다.
- [ ] offset 대신 `(last_message_at, id)` cursor로 목록을 페이지네이션한다.
- [ ] 메시지는 `(conversation_id, sequence)` 순서로 cursor pagination한다.
- [ ] 첫 메시지 일부를 임시 제목으로 쓰고 첫 응답 완료 후 비동기로 제목을 생성한다.
- [ ] 기본적으로 메시지를 append-only로 둔다. 편집·분기는 6단계에서 요약 무효화와 함께 추가한다.
- [ ] 삭제한 대화의 유예기간과 hard delete 시점을 정한다.

### 프런트엔드

- [ ] URL을 `/chat/{conversationId}` 형태로 만든다.
- [ ] 왼쪽 sidebar에 새 채팅 버튼과 대화 목록을 추가한다.
- [ ] 목록을 `오늘 / 어제 / 최근 7일 / 이전`으로 나누고 cursor 무한 스크롤을 적용한다.
- [ ] 제목 변경, pin, archive, 삭제 메뉴를 추가한다.
- [ ] 초기 loading, 빈 목록, 재시도, 권한 없음 상태를 각각 표시한다.
- [ ] 브라우저의 `messages[]`를 진실의 원본으로 사용하지 않고 API 응답을 cache한다.

### 테스트·완료 조건

- [ ] 새로고침과 서버 재시작 뒤 같은 채팅이 복원된다.
- [ ] 101개 이상의 대화 목록에서 중복·누락 없이 다음 cursor를 불러온다.
- [ ] 오래된 메시지를 위로 스크롤해 순서대로 불러온다.
- [ ] 동시에 메시지를 추가해도 sequence가 중복되지 않는다.
- [ ] 제목·pin·archive·삭제 상태가 목록에 즉시 반영된다.
- [ ] 삭제·보관된 대화가 일반 목록에 섞이지 않는다.

**3단계 Gate:** 다른 브라우저 세션에서 로그인해도 본인의 채팅 목록과 메시지를 그대로 이어서 볼 수 있다.

---

## 4단계 — 영속 생성 작업·큐·재연결

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

- [ ] 사용자 메시지, assistant placeholder, generation 작업을 한 짧은 transaction에서 만든다.
- [ ] 같은 idempotency key와 같은 본문이면 기존 결과를 반환하고, 같은 key에 다른 본문이면 `409`를 반환한다.
- [ ] `queued → running → completed | failed | cancelled` 상태 전이를 DB에 기록한다.
- [ ] `GET /api/v1/generations/{id}` 상태 조회 API를 만든다.
- [ ] `GET /api/v1/generations/{id}/events` SSE API를 별도로 만든다.
- [ ] `POST /api/v1/generations/{id}/cancel`로 queued/running 작업을 취소한다.
- [ ] SSE event에 연속된 `id:`를 넣고 `Last-Event-ID` 이후를 replay한다.
- [ ] token마다 DB에 쓰지 않고 약 250ms 또는 일정 글자 단위로 chunk를 저장한다.
- [ ] 완료된 assistant 본문은 영구 저장하고 오래된 stream event는 보존기간 후 정리한다.
- [ ] 숨겨진 reasoning/chain-of-thought는 저장하거나 사용자에게 노출하지 않는다.

### worker와 자원 보호

- [ ] API와 모델 worker를 분리한다. 모델 worker 한 개만 MLX 생성 슬롯을 소유한다.
- [ ] PostgreSQL의 `FOR UPDATE SKIP LOCKED` 또는 동등한 lease 방식으로 작업 하나를 claim한다.
- [ ] `lease_expires_at`, heartbeat, attempt count로 죽은 worker의 작업을 복구한다.
- [ ] 초기 active generation은 1건으로 유지한다.
- [ ] 초기 queue 상한은 3건 정도로 작게 시작하고 부하 측정 후 조정한다.
- [ ] 사용자당 active generation 1건과 사용자/workspace별 rate limit·quota를 둔다.
- [ ] queue가 가득 차면 stream을 시작하기 전에 `429`와 `Retry-After`를 반환한다.
- [ ] 공정한 순서 정책을 정해 한 사용자가 queue를 독점하지 못하게 한다.
- [ ] 연결 종료와 취소가 upstream MLX 생성 중단까지 전파되게 한다.
- [ ] API를 여러 worker로 늘리기 전에 semaphore/queue/lease를 공유 저장소로 옮긴다.

### 테스트·완료 조건

- [ ] 같은 요청을 동시에 반복해도 user/assistant 메시지가 각각 하나만 생긴다.
- [ ] SSE를 끊었다 다시 연결해도 event가 중복 없이 이어진다.
- [ ] API 또는 worker 재시작 후 queued/running 작업이 복구되거나 명확히 실패 처리된다.
- [ ] 취소 뒤 생성 슬롯과 upstream connection이 남지 않는다.
- [ ] 생성 실패가 이미 저장된 사용자 메시지를 삭제하지 않는다.
- [ ] 10개 동시 요청에서도 실제 MLX 생성은 최대 1건이고 queue 상한을 넘으면 즉시 거부된다.

**4단계 Gate:** 중복 요청·연결 끊김·worker 강제 종료 시나리오에서 메시지 중복이나 유실이 없고 상태가 항상 하나로 확정된다.

---

## 5단계 — 실제 토큰 예산과 컨텍스트 압축

**목표:** 32K 컨텍스트를 넘지 않으면서 오래된 대화의 핵심 사실을 유지한다.

### 서버 중심 ContextBuilder

- [ ] 공개 API에서 임의의 `messages[]`, `system`, `assistant` 입력을 제거한다.
- [ ] server system prompt는 코드/설정 또는 권한 있는 workspace 설정에서만 만든다.
- [ ] 현재 모델의 chat template와 tokenizer를 이용해 실제 prompt token을 계산한다.
- [ ] 모델마다 `context_window`, tokenizer ID, 출력 상한, 안전 여유를 registry로 관리한다.
- [ ] `LLM_MAX_HISTORY_CHARS`를 token budget 정책으로 대체한다.
- [ ] 실패·취소 메시지는 기본 모델 컨텍스트에서 제외한다.
- [ ] 다음 우선순위로 prompt를 조립한다.

```text
1. 서버 system/policy
2. 현재 사용자 메시지
3. 최신 원문 turn
4. 검증된 rolling summary
5. RAG 또는 장기 memory (추후)
```

초기 예산식은 다음과 같이 두고 실제 benchmark로 조정한다.

```text
input_budget = context_window
             - requested_max_output_tokens
             - max(1024, context_window * 0.05)
```

### 압축 정책

- [ ] input budget 70% 부근을 soft threshold, 85% 부근을 hard threshold의 초기값으로 둔다.
- [ ] soft threshold를 넘으면 완료된 오래된 연속 prefix를 비동기 요약한다.
- [ ] hard threshold에서 유효한 요약이 없으면 생성 전 동기 압축 또는 결정적 truncation을 적용한다.
- [ ] system prompt, 현재 질문, 최신 4~6개 완전한 turn은 원문으로 보존한다.
- [ ] 요약에 목표, 확정 사실, 사용자 선호, 결정, 미해결 질문을 구분해 담는다.
- [ ] 요약의 `source_from_sequence`, `source_through_sequence`, source hash, 모델·prompt version, token 수를 저장한다.
- [ ] 원문 메시지를 삭제하거나 요약으로 덮어쓰지 않는다.
- [ ] 긴 대화는 rolling/hierarchical summary checkpoint로 관리한다.
- [ ] 새 메시지와 요약 작업의 경쟁은 conversation version의 compare-and-swap으로 막는다.
- [ ] 요약 실패 시 최신 turn 우선의 결정적 fallback으로 채팅을 계속한다.
- [ ] 요약은 신뢰된 system instruction이 아니라 **신뢰하지 않는 대화 메모리**로 모델에 전달한다.
- [ ] 압축 전후 token 수, 요약 시간, fallback 횟수를 metrics로 남긴다.

### 테스트·완료 조건

- [ ] 한글·영문·코드 혼합 입력에서도 생성 직전 prompt가 항상 상한 이내다.
- [ ] 100개 이상의 긴 turn에서도 context overflow가 발생하지 않는다.
- [ ] system prompt, 현재 질문, 최신 보존 turn이 변경 없이 남는다.
- [ ] 요약 범위가 겹치거나 비는 구간이 없다.
- [ ] 요약 중 새 메시지가 추가되어도 메시지가 유실되지 않는다.
- [ ] 요약 모델 실패 시에도 채팅이 안전하게 계속된다.
- [ ] 이름, 숫자, 선호, 결정, 미해결 작업을 담은 한국어 golden conversation에서 정한 회상 기준을 통과한다.
- [ ] 원문 삭제 시 summary와 검색 index 등 파생 데이터도 무효화·삭제된다.

**5단계 Gate:** 장기 대화 회귀 테스트에서 token 상한을 한 번도 넘지 않고, 정한 핵심 사실 보존 점수를 충족한다.

---

## 6단계 — 실제 채팅 제품 UX

**목표:** 데모 화면을 매일 사용할 수 있는 저장형 채팅 제품으로 만든다.

- [ ] 메시지 복사, 응답 다시 생성, 실패 재시도, 중지 상태를 구현한다.
- [ ] 사용자 메시지 수정 후 분기와 답변 branch 관계를 저장한다.
- [ ] 편집·삭제·분기 시 영향받은 summary를 무효화한다.
- [ ] 대화 제목 검색을 먼저 만들고, 필요성이 확인되면 메시지 본문 검색을 추가한다.
- [ ] Markdown과 code block을 allowlist 기반으로 안전하게 렌더링하고 XSS를 차단한다.
- [ ] model 준비 중, queue 대기, 생성, 취소, 실패, 재연결 상태를 구분해 표시한다.
- [ ] 진행 중 페이지를 새로고침해도 generation 상태와 부분 결과를 복구한다.
- [ ] 여러 탭에서 같은 대화를 수정할 때 version 충돌을 처리한다.
- [ ] 온도, 출력 길이, thinking 등 허용한 모델 설정만 사용자별로 저장한다.
- [ ] 좋아요/싫어요와 선택적 사유를 품질 feedback으로 저장한다.
- [ ] 키보드 탐색, focus, screen reader label, 색 대비를 점검한다.
- [ ] 모바일, 긴 code/table, 한글 IME 조합 입력을 테스트한다.

**6단계 Gate:** 새 사용자 생성 → 로그인 → 새 채팅 → 응답 중단/재시도 → 목록 복원 → 로그아웃의 핵심 E2E가 브라우저 테스트로 통과한다.

---

## 7단계 — 파일 업로드와 RAG (선택 기능)

**선행 조건:** 1~6단계와 사용자/workspace 권한 격리가 완료되어야 한다.

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

**7단계 Gate:** 다른 workspace 문서는 검색 후보에도 한 건도 들어가지 않고, 사용자는 답변 근거를 원문 위치까지 확인할 수 있다.

---

## 8단계 — 보안·개인정보·관리자 기능 강화

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
- [ ] 사용자/workspace/IP별 요청 속도, 일일 token quota, export 상한을 둔다.
- [ ] 대화 보존기간, soft delete 유예기간, backup 만료 시점을 문서화한다.
- [ ] 사용자 데이터 export, 대화 영구 삭제, 계정 삭제 job을 구현한다.
- [ ] 디스크는 FileVault, 외부 backup은 별도 key로 암호화한다.
- [ ] 저장소 secret scan, dependency scan, SAST, SBOM 생성을 CI에 추가한다.
- [ ] IDOR, XSS, CSRF, session 탈취, prompt injection, 무제한 자원 사용, 노트북 분실 threat model을 작성한다.
- [ ] 관리자 화면에는 사용자 상태, 역할, queue, worker, 모델 상태만 필요한 범위로 표시한다.

**8단계 Gate:** 교차 workspace 권한, CSRF/XSS, session 철회, rate limit, secret scan 테스트를 통과하고 Critical/High 보안 이슈가 남지 않는다.

---

## 9단계 — 관측성·SLO·장애 알림

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

**9단계 Gate:** 모델, DB, queue, disk 장애를 의도적으로 발생시켰을 때 alert가 울리고 request ID로 전체 흐름을 추적할 수 있다.

---

## 10단계 — LLM 평가·자동 테스트·CI/CD

**목표:** 코드, 모델, prompt, 요약, RAG 변경의 품질과 안정성 회귀를 배포 전에 잡는다.

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

**10단계 Gate:** 필수 검사와 품질 기준에 실패한 commit은 운영 배포할 수 없고, staging에서 이전 artifact로 되돌릴 수 있다.

---

## 11단계 — 운영 배포·백업·복구

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
- [ ] 시작/중지, MLX OOM, queue 적체, DB 장애, disk 부족, secret 회전, rollback, restore runbook을 작성한다.
- [ ] 사고 심각도, 담당자, 연락 경로, 전체 session 폐기와 생성 kill switch를 정의한다.

> 현재 노트북 한 대에 API, DB, 모델을 모두 두면 전체 서비스의 단일 장애점이 된다. 요구 가용성이 높아지면 DB/API를 관리되는 별도 host로 옮기고 Mac은 VPN 또는 mTLS 뒤의 전용 추론 worker로 분리한다.

**11단계 Gate:** 재부팅 후 자동 기동하고, backup을 새 환경에 복원하며, 운영자가 runbook만 보고 주요 장애와 rollback을 처리할 수 있다.

---

## 12단계 — 측정에 따른 확장

**선행 조건:** 실제 사용량과 9~11단계의 capacity 지표에서 병목이 확인되어야 한다.

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

**12단계 Gate:** worker 하나가 중단돼도 요청이 다른 worker로 이동하거나 안전하게 대기하고, 확장 후에도 권한·순서·취소·멱등성이 동일하게 동작한다.

---

## 최종 운영 준비 체크리스트

서비스 용도를 구분하지 않고 아래 기준을 모두 통과하면 운영 준비가 끝난 것으로 본다. 7단계 RAG를 사용하지 않는다면 RAG 전용 항목만 제외한다.

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

### RAG를 사용하는 경우

- [ ] 파일 검사, 격리 parsing, ACL retrieval이 완료됐다.
- [ ] 교차 workspace 문서 노출이 0건이다.
- [ ] 근거 표시와 원본·chunk·embedding 삭제 전파가 검증됐다.
- [ ] 품질 기준 미달 시 feature flag로 즉시 중단할 수 있다.

## 바로 진행할 첫 작업 묶음

로드맵을 실제 issue/PR로 옮길 때는 다음 순서를 권장한다.

1. **PR 1:** PostgreSQL Compose, DB 설정, async session, Alembic, live/ready health
2. **PR 2:** users/workspaces/conversations/messages 초기 schema와 repository test
3. **PR 3:** 회원가입·로그인, session, CSRF, 모든 API의 workspace dependency
4. **PR 4:** conversation CRUD, cursor 목록, `/chat/{id}` sidebar
5. **PR 5:** message idempotency, generation state, 단일 worker와 bounded queue
6. **PR 6:** 재연결 가능한 SSE, cancel, worker restart recovery
7. **PR 7:** tokenizer 기반 ContextBuilder와 token budget
8. **PR 8:** rolling summary와 장기 대화 회귀 평가

한 PR에서 DB, 인증, UI, queue를 모두 바꾸지 않는다. 각 PR의 migration과 테스트를 통과한 상태로 main을 항상 실행 가능하게 유지한다.

## 당장 피할 것

- [ ] JWT/session token을 `localStorage` 또는 `sessionStorage`에 저장하지 않는다.
- [ ] 클라이언트가 보낸 전체 대화와 role을 그대로 모델 입력으로 사용하지 않는다.
- [ ] `mlx_vlm.server :8080`을 LAN 또는 인터넷에 직접 노출하지 않는다.
- [ ] 27B 모델을 API worker마다 따로 load하지 않는다.
- [ ] 프로세스 내부 semaphore를 멀티 worker 운영 queue로 간주하지 않는다.
- [ ] context 압축을 위해 원본 메시지를 삭제하지 않는다.
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
