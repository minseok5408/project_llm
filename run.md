# 실행 명령어

현재 기능과 제약의 요약은 [개발 현황](docs/development-status.md), 후속 작업은 [TODO](todo.md)를 참고합니다.

## 프로젝트 이동

```bash
cd /Users/kimseokryu/project/pycharm/project_llm
```

## 최초 설치

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm install
```

## 환경 설정 파일 생성 (선택)

`.env`가 없는 경우에만 복사합니다. 기존 파일이 있으면 필요한 설정을 직접 추가합니다.

```bash
cp -n .env.example .env
```

기본 `DATABASE_ENABLED=false`에서는 로그인·채팅을 사용할 수 없습니다. Mock 모델을 쓰더라도 세션을 저장할 PostgreSQL이 필요합니다. DB 설정은 아래 절차를 따릅니다. 프로젝트 스크립트는 개발용 `.env`를 자동 생성하거나 변경하지 않습니다.

## 로컬 PostgreSQL 설정

로컬 Docker 엔진과 Docker Compose를 설치하고 실행해 둡니다. 개발·테스트 DB는 모두 공식 `postgres:17` 이미지를 사용합니다. 테스트 runner는 Unix socket을 사용하는 로컬 Docker context만 허용합니다.

1. 비밀번호를 한 번 생성합니다.

   ```bash
   .venv/bin/python -c 'import secrets; print(secrets.token_hex(24))'
   ```

2. 생성한 값을 `.env`의 빈 `POSTGRES_PASSWORD=` 뒤에 넣습니다. `DATABASE_URL`도 `postgresql+asyncpg://system:<같은 비밀번호>@127.0.0.1:5432/project_llm` 형식으로 채우고 `DATABASE_ENABLED=true`로 바꿉니다. 생성값은 hex 문자열이라 URL용 추가 인코딩이 필요 없습니다. 실제 비밀번호와 URL은 Git에 올리지 않습니다.
3. 아래 명령으로 개발 DB를 띄우고 migration을 적용합니다.

   ```bash
   docker compose up -d --wait postgres
   .venv/bin/python -m alembic upgrade head
   .venv/bin/python -m alembic check
   ```

기본 DB 이름은 `project_llm`, 로그인 사용자는 `system`, 주소는 `127.0.0.1:5432`입니다. `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PORT`를 바꾸면 `DATABASE_URL`에도 같은 값을 반영합니다. `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`는 빈 데이터 디렉터리의 최초 초기화에 쓰이므로 기존 volume의 DB 이름·사용자·비밀번호는 `.env` 수정만으로 바뀌지 않습니다. 기존 DB 이름이나 로그인 사용자를 바꿀 때는 PostgreSQL에서 실제 이름을 변경한 뒤 환경 설정과 연결 URL을 맞춥니다.

API는 migration을 자동 적용하지 않습니다. Docker 시작과 Alembic 실행은 위 명령으로 처리합니다. `DATABASE_ENABLED=true`이면 API와 독립 worker가 각자의 pool을 준비합니다. 현재 head는 `0012_network_search`입니다. 도메인·관측 테이블은 16개이며 기존 월 무료·플랜 예산을 유지하고 `token_reservations.usage_basis`에 정산 기준을 기록합니다. `conversation_compactions`는 대화 요약과 별도 시스템 유지 사용량을, `user_preferences`는 로컬 전용 설정을, `web_search_runs`는 생성별 검색 상태와 출처를 보관합니다. 로그인, 채팅 저장·복원, 무료 월 지급과 답변 종료 후 토큰 차감이 모두 이 DB를 사용합니다.

### DBeaver에서 테이블 확인

PostgreSQL 연결의 표시 이름을 `docker_project_llm`으로 지정하고 Host `127.0.0.1`, Port `5432`, Database `project_llm`, Username `system`과 `.env`의 `POSTGRES_PASSWORD` 값을 입력합니다. 포트·DB·사용자 설정을 바꿨다면 해당 값을 사용합니다. 연결 후 `docker_project_llm → Schemas → public → Tables`를 새로고침합니다. 연결 표시 이름과 실제 DB 이름은 별도 설정입니다.

`users`, `workspaces`, `workspace_members`, `conversations`, `messages`, `usage_plans`, `token_budgets`, `token_reservations`, `auth_identities`, `auth_sessions`, `generation_runs`, `generation_events`, `conversation_compactions`, `worker_heartbeats`, `user_preferences`, `web_search_runs`와 `alembic_version`이 보여야 합니다. migration 자체는 사용자별 예산을 일괄 생성하지 않습니다. 가입·사용량 조회·생성 요청 때 월 무료 플랜과 해당 회원의 월 예산을 자동 준비합니다. 시스템 계정 준비에만 아래 로컬 초기화 명령을 사용합니다. [월 무료 토큰 정책](docs/adr/0007-monthly-allowances.md)을 참고합니다.

### DB 설정

| 환경 변수                          | 기본값  | 의미                                                           |
| ---------------------------------- | ------- | -------------------------------------------------------------- |
| `DATABASE_ENABLED`                 | `false` | API DB 계층 사용 여부                                          |
| `DATABASE_URL`                     | 빈 값   | API 접속 URL; 활성화하면 필수이며 `postgresql+asyncpg`만 허용  |
| `MIGRATION_DATABASE_URL`           | 빈 값   | Alembic 전용 owner URL; 비어 있으면 `DATABASE_URL` 사용        |
| `DATABASE_POOL_SIZE`               | `5`     | API 프로세스마다 유지할 pool 크기                              |
| `DATABASE_MAX_OVERFLOW`            | `5`     | pool 위에 일시적으로 허용할 추가 연결 수                       |
| `DATABASE_POOL_TIMEOUT_SECONDS`    | `5`     | pool에서 연결을 기다리는 최대 시간                             |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `5`     | PostgreSQL 연결 수립 timeout; migration에도 적용               |
| `DATABASE_HEALTH_TIMEOUT_SECONDS`  | `2`     | pool 대기·연결·`SELECT 1`을 포함하는 DB readiness 전체 timeout |

URL 설정은 `SecretStr`로 보관하고 빈 문자열은 미설정 값으로 처리합니다. 개발 Compose의 PostgreSQL 로그인 사용자 `system`은 초기 owner 계정이며 애플리케이션의 시스템 사용자와 별개입니다. 현재는 migration/runtime URL을 나눌 수 있는 설정만 준비했고, 최소 권한 역할·RLS는 아직 구현하지 않았습니다.

### DB 정지와 재시작

```bash
docker compose stop postgres
docker compose up -d --wait postgres
```

`stop`은 개발 volume의 데이터를 유지합니다. `scripts/dev.py`의 `Ctrl+C`는 웹·worker·API·모델 프로세스만 종료하며 Compose로 시작한 DB는 별도로 정지합니다.

## 시스템 계정과 토큰 예산 관리

일반 계정의 무료 월 20,000토큰은 시스템 계정의 수동 할당 없이 자동 지급합니다. 한국 시간 매월 1일 00시부터 다음 달 1일 00시까지 사용하며 남은 양은 이월하지 않습니다. 월 중 가입한 계정도 그달 20,000토큰 전액을 받습니다. 별도 cron이나 갱신 서버를 띄우지 않고 가입 시, `GET /api/v1/usage`와 생성 승인 요청 때 현재 월의 예산을 멱등하게 생성합니다. 기존 회원도 이 경로를 거치면 이번 달 예산을 받습니다.

기본 무료 플랜은 `free-monthly` / `무료` / 20,000토큰입니다. 플랜 정책을 `TokenBudget`에 기간별 사본으로 저장하므로 새달 지급을 위해 과거 사용량을 초기화하지 않습니다. 별도 `source=plan` 기간 예산이 유효하면 이를 우선하며 무료 예산과 합산하지 않습니다. 플랜이 소진돼도 기간 중 무료로 전환하지 않고, 종료되면 그달 무료 잔여량을 사용합니다. 이전 달·플랜에서 시작한 예약은 원래 예산에서 계속 정산합니다.

입력 약 1,000 + 답변 약 1,000토큰씩이면 월 약 10회 수준입니다. 질문 횟수를 보장하는 한도는 아니며 연속 대화에서는 다시 보내는 이력도 입력에 포함되어 더 빨리 소진될 수 있습니다. 결제 등급별 토큰 지급은 이 플랜·예산 구조에 연결할 예정이며 실제 결제 기능은 아직 없습니다. 아래 명령은 시스템 계정 준비와 로컬 유지보수용으로 남겨 둡니다.

먼저 `alembic upgrade head`를 적용하고 저장소 루트에서 실행합니다. 아래 이메일·기간·토큰량은 예시입니다. 실제 운영 계정과 정책으로 바꿉니다. 명령은 `.env`의 `DATABASE_URL`을 사용하며 로컬 PostgreSQL 연결만 허용합니다. 새 서버를 실행하지 않습니다.

```bash
.venv/bin/python scripts/manage_accounts.py bootstrap-system --email system@example.com
.venv/bin/python scripts/manage_accounts.py balance --email system@example.com
```

첫 명령은 사용자와 기본 작업 공간을 만들거나 지정한 기존 계정을 명시적으로 승격합니다. 반복 실행해도 계정·작업 공간을 중복 생성하지 않으며, 기존 비활성 계정을 다시 활성화하지 않습니다. 공개 가입 경로에는 이 함수를 연결하지 않습니다. 시스템 계정의 표시 이름은 기본 `system`이고 토큰 한도는 면제됩니다. 이 명령은 계정 권한만 준비합니다. 비밀번호는 아래 `set-password` 명령으로 별도 설정합니다.

유지보수 시에는 시스템 계정으로 별도 플랜과 `source=plan` 기간 예산을 만들 수 있습니다. 무료 월 지급에는 이 명령이 필요하지 않으며 플랜 가격이나 실제 결제는 처리하지 않습니다.

```bash
.venv/bin/python scripts/manage_accounts.py create-plan \
  --actor-email system@example.com --code trial-v1 --name '체험 플랜' --token-limit 100000
.venv/bin/python scripts/manage_accounts.py grant-budget \
  --actor-email system@example.com --user-email member@example.com \
  --grant-key trial-member-2026-09 --plan-code trial-v1 \
  --starts-at 2026-09-01T00:00:00Z --ends-at 2026-10-01T00:00:00Z
.venv/bin/python scripts/manage_accounts.py balance --email member@example.com
```

플랜 없이 부여하려면 `--plan-code` 대신 `--token-limit`을 지정합니다. 플랜과 함께 지정하면 해당 기간의 한도만 별도로 정합니다. 같은 `--grant-key`와 같은 요청의 반복은 예산을 한 번만 부여하고, 같은 키의 다른 요청과 같은 source 안에서 겹치는 기간은 거부합니다. 위 10만 토큰은 유지보수 예시이며 자동 지급하는 무료 월 20,000토큰과 별개입니다.

관리 CLI의 이메일 인자는 로컬 운영자가 대상을 고르는 수단입니다. HTTP API에서는 검증된 로그인 세션으로 사용자를 결정합니다. 일반 계정은 자동 무료 예산 또는 우선 적용되는 기간 플랜으로 채팅하고 선택된 예산이 부족하면 `402`로 안내합니다. 시스템 계정에는 무료 예산을 만들지 않고 한도 면제와 사용량 기록을 적용합니다. 공개 관리 API·관리 화면과 실제 결제 연동은 아직 없습니다.

## 로그인과 24시간 세션

회원가입은 기본으로 열려 있습니다(`SIGNUP_MODE=open`). 로그인 화면에서 회원가입을 선택하고 사용자 이름·이메일·비밀번호·비밀번호 확인을 입력합니다. 비밀번호는 8~32자이며 두 입력이 일치해야 합니다. 로그인·비밀번호 변경에도 같은 길이 규칙을 적용합니다. 가입하면 일반 `member` 계정·기본 작업 공간과 이번 달 무료 20,000토큰 예산을 같은 transaction으로 만들고 자동으로 24시간 로그인합니다. 채팅의 사용자 표시는 로그인 계정의 `display_name`을 사용하며 시스템 권한은 자동 부여하지 않습니다.

시스템 계정 등 기존 계정의 첫 비밀번호는 로컬 터미널에서 설정합니다. 다음 이메일을 사용할 실제 계정으로 바꿉니다. `bootstrap-system`으로 이미 준비한 계정이면 이를 다시 실행할 필요는 없습니다.

```bash
.venv/bin/python scripts/manage_accounts.py set-password --email system@example.com
```

새 비밀번호를 두 번 입력합니다(8~32자). 입력 문자는 화면에 표시되지 않으며 명령 기록·설정 파일에도 저장하지 않습니다. DB에는 Argon2id 해시를 저장합니다. 이 명령은 기존 활성 계정만 대상으로 하며, 비밀번호를 변경하면 그 계정의 모든 기존 로그인 세션을 철회합니다. PostgreSQL 접속 비밀번호와 웹 로그인 비밀번호는 별개입니다.

웹 주소에 접속하면 로그인 화면이 먼저 나옵니다. 이메일과 위 비밀번호로 로그인하면 **로그인 시각부터 24시간** 유지됩니다. 새로고침·브라우저 재시작·API 재시작 뒤에도 잔여 시간 동안 유지하며, 사용해도 만료 시각은 연장하지 않습니다. 24시간이 지나거나 로그아웃하면 로그인 화면으로 돌아갑니다. 왼쪽 아래 계정 메뉴의 로그아웃은 현재 브라우저 세션만 종료합니다.

`localhost`와 `192.168.x.x`는 서로 다른 호스트여서 각각 최초 로그인이 필요합니다. 같은 계정으로 로그인하면 서버에 저장된 동일한 작업 공간·대화를 다시 볼 수 있습니다. 새로고침과 다른 기기의 새 로그인에서도 `/chat/{id}`의 메시지와 진행 중 생성 상태를 복원합니다. 만료·로그아웃 시 화면의 대화 상태와 브라우저 스트림을 정리합니다.

| 설정                 | 기본값  | 의미                                                                                                  |
| -------------------- | ------- | ----------------------------------------------------------------------------------------------------- |
| `SIGNUP_MODE`        | `open`  | 일반 사용자 가입 화면/API 활성화. `disabled`로 닫아도 기존 로그인은 유지. 가입자는 항상 `member`      |
| `AUTH_COOKIE_SECURE` | `false` | 현재 로컬·LAN HTTP용 `project_llm_session`. HTTPS에서는 `true`로 바꾸어 `__Host-session; Secure` 사용 |

쿠키는 `HttpOnly`, `SameSite=Lax`, `Path=/`를 사용합니다. HTTP는 전송 암호화를 제공하지 않으므로 현재 허용된 로컬·같은 Wi-Fi 범위에서 사용합니다. 인증된 POST·PATCH·DELETE에는 세션에 연결된 CSRF 토큰과 JSON 본문을 보내고, 웹과 FastAPI가 Origin을 검사합니다. FastAPI는 계속 loopback에만 열어 둡니다. 별도 JWT나 localStorage 토큰 설정은 필요 없습니다.

인증 API는 `/api/v1/auth/config`, `/me`, `/login`, `/signup`, `/logout`입니다. 로그인/가입/me는 사용자 정보·만료 시각·CSRF 토큰을 반환하고 로그인/가입은 세션 쿠키를 설정합니다. 공개 가입을 꺼도 기존 사용자의 로그인은 가능합니다. 이메일 확인·초대 가입·이메일 비밀번호 재설정은 후속 작업이며, 지금 비밀번호를 잊으면 위 로컬 명령을 다시 실행합니다.

## 저장형 채팅과 생성 API

### 화면과 테마 설정

- 왼쪽 위 버튼으로 사이드바를 접고 펼칩니다. 접으면 세로 아이콘 막대가 남고, 좁은 화면에서는 대화 목록이 덮개 형태로 열립니다. **최근 채팅** 제목으로 최근 목록만 접거나 펼칩니다.
- 사이드바 접기 버튼 옆 돋보기 또는 `⌘K`·`Ctrl+K`로 채팅 검색을 엽니다. 현재 작업 공간의 제목과 메시지 본문을 검색하며 보관된 대화도 포함합니다. 기본 작업 공간 선택은 화면에서 생략합니다.
- 선택한 대화의 점 세 개 메뉴에서 제목 변경·고정·보관·삭제를 합니다. 왼쪽 아래 이름·요금제를 누르면 계정 메뉴가 열립니다. **설정**은 중앙 모달로 열리고, **사용량**을 선택해야 토큰 상세 사용량·갱신일이 표시됩니다. 계정 메뉴의 **로그아웃**은 현재 브라우저 세션만 종료합니다.
- 왼쪽 아래 계정 메뉴의 **설정 → 일반 → 화면 테마**는 **시스템 설정**이 기본이며 운영체제의 라이트·다크 변경을 즉시 따릅니다. 라이트 모드나 다크 모드를 직접 고르면 운영체제 변경과 관계없이 유지됩니다.
- 테마는 현재 브라우저의 `project-llm-theme` 값으로 저장하고 같은 출처의 다른 탭에도 반영합니다. 브라우저 저장이 차단된 경우 현재 화면에는 적용되지만 재접속 시 유지되지 않을 수 있습니다. 계정별 DB 설정은 아니므로 다른 기기나 접속 주소에서는 기본 시스템 테마로 시작합니다.
- 설정 모달의 **연결 및 검색**에서 로컬 전용·웹검색 방식을 바꿉니다. 화면 오른쪽 아래 **모델 정보**에서 모델 이름과 연결 상태를 확인합니다. 입력창 **생성 설정**에서 깊이 생각하기를 선택합니다. 최대 출력 토큰은 서버에서 관리하며 화면에는 숫자 선택을 제공하지 않습니다.
- 입력창 오른쪽 화살표로 전송하고 생성 중에는 같은 자리의 사각형으로 **답변 중단**을 요청합니다. 참고 자료는 기본으로 접혀 있고 펼치면 제목 링크만 표시합니다.

### 저장과 API

로그인 후 기본 작업 공간의 대화를 왼쪽에서 선택합니다. 새 대화, 제목 변경, 고정·해제, 보관·복원과 삭제를 지원합니다. 첫 질문의 60자가 초기 제목이 됩니다. 목록은 최근 메시지 시각·UUID 커서, 메시지는 순번 커서로 더 불러옵니다. 고정 정렬은 현재 불러온 목록에만 적용하며 전체 페이지의 고정 우선 정렬은 아직 없습니다. 작업 공간이 없으면 빈 목록을 표시하고 조회 중 새 작업 공간을 만들지 않습니다.

| 요청                                                                                   | 동작                                                                  |
| -------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `GET /api/v1/workspaces`                                                               | 현재 계정의 작업 공간과 역할                                          |
| `GET /api/v1/conversations?workspace_id=<uuid>&status=active&limit=30&cursor=<cursor>` | 대화 목록과 `next_cursor`; 보관 목록은 `status=archived`              |
| `GET /api/v1/conversations?workspace_id=<uuid>&status=all&q=<query>`                   | 제목·본문 부분검색; 최대 200자, 빈 검색어는 전체 목록, 기존 커서 사용 |
| `POST /api/v1/conversations`                                                           | `{workspace_id,title?}`로 생성, `201`                                 |
| `GET /api/v1/conversations/{id}`                                                       | 단건 대화와 `active_generation_id`                                    |
| `PATCH /api/v1/conversations/{id}`                                                     | `title`, `is_pinned`, `status` 중 변경할 값만 전송                    |
| `DELETE /api/v1/conversations/{id}`                                                    | JSON `{}`로 soft delete, `204`                                        |
| `GET /api/v1/conversations/{id}/messages?before=<sequence>&limit=50`                   | 시간순 메시지 한 페이지·`next_cursor`·`active_generation_id`          |
| `GET /api/v1/usage`                                                                    | 자기 계정의 기간 한도·실사용·예약·잔여량; system은 `unlimited=true`   |
| `POST /api/v1/conversations/{id}/messages`                                             | 새 사용자 메시지·생성 옵션·검색 모드 승인, `202`                      |
| `GET /api/v1/generations/{id}`                                                         | 생성 상태와 확정된 입력·출력 사용량                                   |
| `GET /api/v1/generations/{id}/events?after=0`                                          | 저장한 이벤트 재생; `Last-Event-ID`도 지원                            |
| `POST /api/v1/generations/{id}/cancel`                                                 | JSON `{}`로 본인이 시작한 생성 중단 요청                              |
| `POST /api/v1/generations/{id}/regenerate`                                             | 마지막 질문의 현재 답변 재생성; 생성·검색 옵션과 멱등 키, `202`       |

메시지 전송에는 UUID 형식의 `Idempotency-Key` 헤더와 아래 본문을 사용합니다. 같은 키·내용·생성 옵션·검색 모드를 재전송하면 기존 작업을 반환하며 다시 실행하거나 차감하지 않습니다. 같은 키의 다른 요청은 `409`입니다. 기존 `/api/chat`은 `410`을 반환합니다.

```json
{
  "content": "질문",
  "options": { "thinking": false },
  "network_mode": "auto",
  "web_search": "auto"
}
```

재생성도 UUID `Idempotency-Key`가 필요하며 본문은 `{ "options": { "thinking": false }, "network_mode": "auto", "web_search": "auto" }`입니다. 질문은 서버 원문을 사용하고 검색은 현재 선택한 설정으로 새로 수행합니다. `network_mode`는 `auto/local`, `web_search`는 `auto/on/off`를 받으며 생성 옵션과 별도의 최상위 필드입니다. 서버의 사용자별 로컬 전용 설정이 항상 우선합니다. 메시지 조회의 assistant 항목에는 `generation_id`, `generation_status`, `is_current`, `can_regenerate`, `finish_reason`, `search`가 포함됩니다. 이전 답변 버전과 검색 출처는 보존하고 현재 버전만 다음 모델 문맥에 넣습니다.

작성자 또는 작업 공간 owner/admin만 대화 정보를 변경·삭제할 수 있습니다. 다른 작업 공간의 ID는 `404`이며 시스템 계정도 같은 소속 검사를 거칩니다. 생성 중인 대화의 보관·삭제는 `409`로 거부합니다. 서버는 클라이언트의 전체 이력·역할·사용량을 받지 않고, 서버 system prompt·저장된 요약·최근 질문과 답변·현재 질문으로 모델 입력을 만듭니다. 완료·중단·실패·기존 사용량 미확정 작업의 사용자 발언과 실제 부분 답변을 포함하며 빈 답변이나 오류 안내를 모델의 답변으로 만들지 않습니다.

### 생성 설정과 실제 사용량

답변 출력 기본값과 API 허용 상한은 `backend/app/schemas.py`의 `GenerationOptions.max_tokens`에서 **4,096토큰**으로 관리합니다. 프런트는 이 필드를 생략합니다. 직접 API를 호출할 때는 정수 1~4,096을 지정할 수 있지만, 서버의 문맥·잔여 예산 검사를 우회할 수 없습니다. 별도의 답변 출력 한도 환경 변수는 없으며, `LLM_COMPACTION_MAX_TOKENS`는 요약 출력 설정입니다.

| 환경 변수                        | 기본값   | 의미                                                                         |
| -------------------------------- | -------- | ---------------------------------------------------------------------------- |
| `GENERATION_WORKER_ENABLED`      | `false`  | API 내장 worker의 호환 옵션; 개발 launcher는 false로 고정                    |
| `GENERATION_QUEUE_LIMIT`         | `3`      | 전체 queued/running 상한은 이 값 + 실행 슬롯 1개                             |
| `LLM_CONTEXT_WINDOW`             | `32768`  | 실제 입력 토큰 + 요청 최대 출력량의 상한                                     |
| `LLM_MAX_HISTORY_CHARS`          | `200000` | 모델에 전달할 전체 문맥의 글자 수 상한; 초과 시 요약 가능한 과거를 압축      |
| `LLM_MAX_CONCURRENT_GENERATIONS` | `1`      | 현재 worker는 DB당 실제 생성 1건으로 고정; 값을 늘려도 실행자 수는 늘지 않음 |
| `LLM_COMPACTION_TRIGGER_RATIO`   | `0.75`   | 입력 + 요청 최대 출력이 문맥에서 이 비율 이상이면 오래된 대화 압축           |
| `LLM_COMPACTION_TARGET_RATIO`    | `0.55`   | 요약과 최근 원문을 구성할 때의 목표 비율; 시작 비율보다 작아야 함            |
| `LLM_COMPACTION_KEEP_TURNS`      | `4`      | 우선 보존할 최근 질문·답변 쌍 수; 길면 최소 마지막 1쌍까지 조정              |
| `LLM_COMPACTION_MAX_TOKENS`      | `1024`   | 요약 최대 출력량; 실제 값은 문맥 크기의 1/4 이하로 제한                      |

새 요청은 생성 전에 토큰을 예약·차감하지 않습니다. 남은 토큰 안에서 출력 상한만 정하고 답변 완료·중단 후 확인된 사용량을 한 번 차감합니다. 이전 요청 호환용 예약 이력은 유지합니다. [후정산·스크롤 정책](docs/adr/0008-deferred-charging-and-chat-scroll.md)을 참고하세요.

`scripts/dev.py`가 API와 별도의 worker 프로세스를 실행합니다. 개별 실행 시에는 `python -m backend.app.worker`를 함께 실행해야 합니다. worker는 자신의 pool 밖의 asyncpg 연결 하나로 PostgreSQL advisory lock을 유지하므로 같은 DB에서 실제 추론 실행자는 하나입니다. API reload는 진행 중인 생성을 종료하지 않습니다. worker 코드·설정 변경은 전체 launcher를 종료한 뒤 다시 실행할 때 반영됩니다. 사용자는 동시에 하나, 대화도 동시에 하나만 생성할 수 있으며 대기열이 가득 차면 SSE 시작 전에 `429`와 `Retry-After`를 반환합니다.

MLX의 `/v1/responses/input_tokens`로 같은 chat template의 실제 입력량을 계산하고 `include_usage`로 받은 최종 입력·출력량을 정산합니다. 입력량 확인 API가 없는 모델 서버는 생성 전에 `503`으로 거부합니다. 오래된 대화가 길어지면 자동 압축하고 답변 생성 직전에 문맥·사용자 허용량을 다시 확인합니다. 현재 질문·마지막 원문만으로도 모델 한도를 채우는 경우에는 질문을 줄이거나 필요한 내용을 정리해 새 대화에서 이어가야 합니다. 출력 공간만 부족한 경우에는 서버가 실제 출력 상한을 줄입니다. 글자 수 제한도 별도로 유지합니다. 숨겨진 reasoning 본문은 저장하거나 표시하지 않으며 사용량에는 포함됩니다.

자동 압축은 같은 단일 worker에서 답변 생성 전에 실행하며 화면에 `이전 대화를 정리 중…`을 표시합니다. 최근 4쌍을 우선 보존하고 필요한 경우 최소 마지막 1쌍까지 줄이며, 그 이전 범위만 기존 요약과 합쳐 새 요약으로 저장합니다. 기본 요약 출력 상한은 1,024토큰입니다. 원본 메시지를 삭제하지 않고 완성된 요약만 다음 요청에 사용합니다. 비어 있거나 실패하거나 출력 상한으로 잘린 요약은 사용하지 않고 요청을 실패 처리하며 사용자 토큰은 차감하지 않습니다. 자세한 정책은 [자동 압축 ADR](docs/adr/0009-context-compaction-and-continuation.md)을 참고합니다.

요약 중에도 `중단`을 누르면 모델 연결을 닫고 해당 답변 요청을 끝냅니다. 요약의 확인된 입력·출력 사용량은 `conversation_compactions`에 시스템 유지 작업으로 별도 기록하며 사용자 한도에서 차감하지 않습니다. 확인하지 못한 요약 사용량은 0으로 추정하지 않고 NULL로 남깁니다. 여러 배치 중 후속 배치가 실패·중단되어도 이미 완성한 요약은 다음 요청에 재사용할 수 있습니다. 요약 이후 실제 답변을 생성한 경우에만 그 답변의 입력(요약문 포함)과 출력을 기존 완료·중단 후 정산 정책으로 처리합니다.

모델이 `finish_reason=length`를 명시하면 출력 상한에 도달했다는 안내를 표시하고, 최신 질문의 현재 답변에는 **이어서 생성** 버튼을 제공합니다. 버튼은 이어 쓰기 요청을 새 사용자 메시지로 보내며 기존 답변·작성 중인 입력을 보존합니다. 새 답변에도 서버 기본 4,096토큰과 잔여 문맥·사용자 예산에 따른 제한이 적용됩니다. 생성 중·보관된 대화·사용량 부족 상태에서는 버튼을 비활성화합니다. `이어서 말해`를 직접 보내는 방법도 유지합니다. 출력 개수만 보고 잘림을 추정하지 않으며, 이전 생성 작업을 재개하거나 같은 요청 키를 재실행하지 않습니다.

자동 압축 후에도 새 질문·직전 원문·요약이 한도에 들어오지 않거나 요약 자체가 실패할 수 있습니다. 압축이 무한한 대화 기억이나 답변 완성을 보장하지는 않으며 [현재 한계와 실패 처리](docs/adr/0009-context-compaction-and-continuation.md#자동-압축으로도-처리할-수-없는-경우)를 참고합니다.

답변은 한글·이모지 묶음을 보존하는 grapheme 단위로 약 10ms 간격으로 표시합니다. 표시 대기가 쌓이면 따라잡고, 모션 감소 설정에서는 타이핑 지연을 생략합니다. 중단을 누르면 표시 대기열도 끝냅니다. 위로 스크롤하면 자동 따라가기가 멈추고, 아래쪽으로 이동하거나 최신 답변 이동 버튼을 누르면 다시 따라갑니다.

대기 중 중단에는 토큰 차감이 없습니다. 실행 중 중단은 모델 응답 연결을 닫고 정산 후 생성 슬롯을 반환합니다. 최종 실제량을 이미 받았다면 `provider` 기준으로 정산합니다. 최종량이 없으면 서버가 `logprobs`로 확인한 누적 출력이 1토큰 이상일 때 전체 입력과 확인한 출력만 `received`로 정산하고, 확인한 출력이 없으면 `waived` 기준으로 입력·출력 청구량 모두 0으로 면제합니다. 미수신 GPU 사용량을 추정 청구하지 않는 중단 할인 정책이며 정상 완료는 최종 실제량을 사용합니다.

Markdown·표·코드 블록과 답변/코드 복사를 지원합니다. 일반 HTTP LAN에서는 복사 호환 경로를 사용합니다. raw HTML은 렌더링하지 않고 외부 이미지를 자동 로드하지 않습니다. 마지막 질문의 현재 답변에만 다시 생성/다시 시도를 표시하고 이전 버전은 접어서 보존합니다. 재생성은 질문 원문을 그대로 쓰되 새 작업·사용량 기록을 만들며 작성 중인 초안은 유지합니다.

브라우저 연결 종료만으로 생성 작업을 취소하지 않습니다. 다시 열면 이벤트 0부터 재생해 부분 본문을 복구하며 DB의 부분 본문과 중복 합치지 않습니다.

통신 장애·실행자 종료로 최종 사용량을 확인할 수 없는 후정산 요청은 청구량 0의 실패로 끝냅니다. 대화·오류 기록은 보존하고 다음 질문을 허용합니다. 이미 확인된 최종 사용량은 정확히 차감합니다. worker 재기동과 새 요청 승인 시 이전 미정산 후정산 기록도 복구합니다. 기존 사전 예약 방식의 불명 사용량은 예약 보존 정책을 유지합니다. [ADR 0006](docs/adr/0006-persistent-chat-and-usage.md)에 상태·복구 한계를 기록했습니다.

## 온라인과 로컬 전용 모드

로컬 전용 스위치는 기본 OFF이며 설정은 로그인한 사용자별로 DB에 저장합니다. OFF 상태에서 검색 서비스를 사용할 수 있으면 온라인, 연결할 수 없거나 키가 없으면 로컬 모드로 표시합니다. ON으로 바꾸면 서버 연결 검사와 외부 검색을 중단하고 로컬 모델만 사용합니다. 물리적인 Wi-Fi 상태를 판별하는 기능이 아니라 서버에서 검색 서비스에 도달할 수 있는지 확인하는 기능입니다. 다른 기기와 이 Mac 사이의 Wi-Fi/LAN 접속 설정은 그대로 유지합니다.

1. 로컬 `.env`의 `WEB_SEARCH_PROVIDER=tavily`를 확인하고 발급받은 Tavily API 키를 `WEB_SEARCH_API_KEY`에 입력합니다. 기존 `.env`를 예시 파일로 덮어쓰지 않습니다.
2. 스키마 변경을 적용하려면 `.venv/bin/python -m alembic upgrade head`를 실행합니다.
3. 기존 개발 실행을 `Ctrl+C`로 종료하고 `.venv/bin/python scripts/dev.py`를 다시 실행합니다. 독립 worker는 API reload만으로 환경 변수를 다시 읽지 않습니다.
4. 로그인 후 **왼쪽 아래 계정 → 설정 → 연결 및 검색**에서 로컬 전용을 OFF로 두고 연결 상태를 확인합니다. 다시 확인 버튼으로 서버 연결 검사를 요청할 수 있습니다.

| 환경 변수                          | 기본값   | 의미                                                     |
| ---------------------------------- | -------- | -------------------------------------------------------- |
| `WEB_SEARCH_PROVIDER`              | `tavily` | 기본 Tavily, 선택 Brave, `disabled`로 서버 검색 비활성화 |
| `WEB_SEARCH_API_KEY`               | 미설정   | 선택한 공급자의 API 키, 기본 Tavily; 서버에서만 보관     |
| `WEB_SEARCH_TIMEOUT_SECONDS`       | `8`      | 검색 요청 제한 시간(초)                                  |
| `WEB_SEARCH_MAX_RESULTS`           | `5`      | 최대 검색 결과 수                                        |
| `WEB_SEARCH_MAX_QUERY_CHARS`       | `500`    | 외부에 보낼 현재 질문의 최대 글자 수                     |
| `WEB_SEARCH_MAX_CONTEXT_CHARS`     | `12000`  | 모델에 넣을 검색 자료의 글자 수 상한                     |
| `WEB_SEARCH_CHECK_CACHE_SECONDS`   | `30`     | 검색 서비스 연결 확인 결과의 캐시 시간(초)               |
| `WEB_SEARCH_CHECK_TIMEOUT_SECONDS` | `2`      | 연결 확인 요청 제한 시간(초)                             |

키가 없어도 로그인·저장·로컬 답변은 사용할 수 있습니다. 키는 브라우저 설정에 입력하지 않고 `.env`에만 보관합니다. 검색 API 요금·호출 한도는 발급받은 공급자 계정의 조건을 따르며 서비스 토큰 예산과 별개입니다.

2026-09-09 확인 기준 Tavily 무료 플랜은 카드 등록 없이 매월 1,000 API 크레딧을 제공합니다. 검색 크레딧은 앱의 월 무료 LLM 토큰과 다릅니다. 현재 계정 조건은 [Tavily 가격 안내](https://www.tavily.com/pricing)에서 확인합니다.

Tavily 요청은 `search_depth=basic`, `auto_parameters=false`로 고정해 상위 검색으로 자동 전환하지 않습니다. `include_answer`, `include_raw_content`, `include_images`도 false로 보내 검색 요약만 사용하고 최종 답변은 로컬 모델이 생성합니다. [공식 검색 API](https://docs.tavily.com/documentation/api-reference/endpoint/search)를 따릅니다.

Brave 어댑터도 선택 옵션으로 유지합니다. `WEB_SEARCH_PROVIDER=brave`를 사용하려면 `WEB_SEARCH_API_KEY`에 Brave 키를 넣어야 하며, 이 앱은 출처를 DB에 저장하므로 Brave의 [결과 저장 안내](https://brave.com/search/api/)에 따른 저장 권한 계약이 필요합니다. 기본 Tavily 설정에는 이 Brave 전용 조건을 적용하지 않습니다.

웹검색 방식의 기본값은 `자동`입니다. 최신 정보나 명시적인 웹검색 요청에 해당하는 질문만 검색하며, `항상 검색`은 모든 질문에 검색을 시도하고 `검색 안 함`은 해당 답변에 검색을 사용하지 않습니다. 검색 방식은 현재 로그인한 화면의 메모리에 유지하고 생성 작업마다 기록합니다. 설정 미확인·저장 중·API 오류 때 보내는 질문은 로컬 전용으로 제한합니다. 로컬 전용 ON은 웹검색 방식보다 우선합니다. 연결 실패로 자동 로컬 전환해도 사용자의 OFF 선택을 ON으로 바꾸지 않아 다음 질문에서 재연결을 시도할 수 있습니다.

검색에는 현재 질문의 앞부분 기본 최대 500자만 전송합니다. 이전 대화, 문맥 요약, 전체 모델 입력, 사용자 쿠키와 로그인 세션은 전송하지 않습니다. Tavily 응답의 제목·요약·링크를 참고하며 원문 페이지 본문을 직접 가져오거나 JavaScript를 실행하지 않습니다. 외부 검색 자료는 비신뢰 참고 자료로 전달하고 실제 입력 토큰을 다시 계산한 뒤 답변을 생성합니다. 문맥·남은 예산에 맞춰 자료 수와 출력 상한을 줄이며, 자료를 포함하지 못하면 그 사실을 안내하고 로컬 답변으로 진행합니다.

화면은 `검색 중`과 모델 `생성 중`을 구분하고, 답변 아래에 출처 번호·제목 링크·조회 시각을 표시합니다. 검색 실패·오프라인·빈 결과에서는 질문을 보존하고 최신 정보를 확인하지 못했다는 안내와 함께 로컬 답변을 생성합니다. 검색 도중 답변 중단을 누르면 기존 중단 동작으로 전체 생성을 종료합니다. 로컬 전용 설정의 revision이 바뀌면 대기 중이거나 진행 중인 검색은 외부 요청을 중단하고 로컬 답변으로 전환합니다. 이미 완료된 답변의 출처를 삭제하거나 모델 생성을 일시정지하지 않습니다.

검색 자료가 실제 답변 입력에 포함되면 그 토큰도 사용자 입력 사용량에 포함하며, 출력과 함께 완료·중단 후 기존 정책으로 한 번 정산합니다. 생성 전에 토큰을 차감하지 않으며 자동 요약 토큰의 사용자 비차감 정책도 유지합니다.

| 요청                              | 본문·응답                                                                                     |
| --------------------------------- | --------------------------------------------------------------------------------------------- |
| `GET /api/v1/network-mode`        | 인증 필수. `local_only`, `revision`, `mode`, `reason`, `search_configured`, `checked_at` 반환 |
| `PATCH /api/v1/network-mode`      | `{ "local_only": true }` 또는 `false`; 인증·CSRF·Origin 검증 후 저장                          |
| `POST /api/v1/network-mode/check` | JSON `{}`; 인증·CSRF·Origin 검증 후 강제 재확인. 로컬 전용이면 외부 요청하지 않음             |

GET도 저장된 정책에 따라 캐시가 만료되면 짧은 검사를 수행합니다. 화면은 초기 조회와 수동 재확인만 사용하며 자동 폴링하지 않습니다. 서버의 검색 결과 메타로 진행 중 생성의 온라인·오프라인 변화도 표시합니다. 생성 조회·메시지의 `search`에는 상태·사유·공급자·출처 목록이 있고 SSE `meta`에는 `stage=searching/generating/compacting`과 검색 상태가 전달됩니다. 실제 Tavily 호출과 최신 질문의 출처·의미 정확도는 키 등록 뒤 별도로 검증합니다. [설계와 범위](docs/adr/0011-online-local-web-search.md)를 참고하세요.

## 실제 Qwen 모델로 전체 실행

```bash
.venv/bin/python scripts/dev.py
```

## 모델 없이 Mock으로 전체 실행

```bash
.venv/bin/python scripts/dev.py --mock
```

## 같은 Wi-Fi의 다른 기기에서 접속

위 실행 명령을 그대로 사용합니다. 웹 서버는 `0.0.0.0:3000`으로 열리고 시작 로그에 이 Mac의 로컬 주소와 현재 내부 IP 주소가 함께 표시됩니다. 다른 기기의 브라우저에는 `0.0.0.0`이나 `localhost` 대신 Mac의 내부 IP를 입력합니다.

예를 들어 Mac의 IP가 `192.168.0.76`이면 휴대폰·태블릿·다른 PC에서 `http://192.168.0.76:3000`으로 접속합니다. 같은 Wi-Fi를 사용해야 하며, 공유기의 게스트 기기 격리 기능이 켜져 있으면 기기 간 접속이 막힐 수 있습니다. macOS가 웹 서버의 수신 연결 허용을 물으면 사용하는 로컬 네트워크에서 허용합니다. Wi-Fi를 바꾸면 IP가 달라질 수 있으므로 다시 실행한 로그의 주소를 확인합니다.

브라우저는 같은 웹 주소의 `/api/status`, `/api/v1/auth/*`, 대화·생성·사용량 API를 호출하고 웹 서버가 `API_BASE_URL=http://127.0.0.1:8000`으로 전달합니다. 개발 실행과 빌드 후 실행 모두 같은 방식이며 저장된 생성 이벤트의 SSE도 스트리밍으로 전달합니다. `NEXT_PUBLIC_API_BASE_URL`은 제거합니다. 이 구성에서는 LAN IP를 FastAPI의 CORS 목록에 추가할 필요가 없습니다.

웹의 `3000` 포트만 LAN 접속을 허용합니다. FastAPI `8000`, 모델 `8080`, PostgreSQL `5432`는 `127.0.0.1`에 유지합니다. 현재 로컬 개발 환경이므로 사용을 허용한 같은 Wi-Fi/LAN에서 테스트합니다. 인터넷 포트 포워딩이나 외부 배포는 설정하지 않습니다.

기존 웹 서버가 실행 중이면 설정 반영 후 다시 접속합니다. 새로 시작할 때 `3000` 포트가 이미 사용 중이면 다른 포트로 자동 이동하지 않고 오류를 표시합니다.

## 계층별 개별 실행

### 터미널 1: MLX 모델 서버

```bash
.venv/bin/mlx_vlm.server --model mlx-community/Qwen3.8-27B-4bit --host 127.0.0.1 --port 8080 --max-kv-size 32768 --prefill-step-size 512 --max-num-seqs 1
```

### 터미널 2: FastAPI 게이트웨이

```bash
GENERATION_WORKER_ENABLED=false .venv/bin/python -m uvicorn backend.app.main:app --reload --reload-dir backend/app --host 127.0.0.1 --port 8000
```

### 터미널 3: 독립 생성 worker

```bash
.venv/bin/python -m backend.app.worker
```

`DATABASE_ENABLED=true`가 필요합니다. API를 개별 실행할 때 기존 `.env`의 `GENERATION_WORKER_ENABLED`도 `false`로 설정합니다.

### 터미널 4: 채팅 화면

```bash
npm run dev
```

## 검사

폴더 구조 변경 후에도 API 진입점 `backend.app.main:app`, worker 진입점 `backend.app.worker`, npm 실행 명령은 같습니다. 이번 구조 변경에는 DB migration이나 환경 변수 변경이 없습니다. 소스와 테스트는 [tree.md](tree.md)의 새 경로를 사용하며, 구조 결정은 [ADR 0012](docs/adr/0012-feature-runtime-structure.md)에 기록합니다.

DB 없는 빠른 검사입니다. `TEST_DATABASE_URL`이 없으면 PostgreSQL 통합 테스트는 skip으로 표시됩니다.

```bash
npm run test:backend
npm run test:proxy
npm run test:auth
npm run test:chat
npm run lint:python
npm run lint
npx tsc --noEmit
npm run build
```

`test:proxy`는 실제 서버를 띄우지 않고 LAN 요청의 쿠키·Origin·API 전달·SSE·취소·오류·경로 제한을 검사합니다. `test:auth`는 메모리 세션·24시간 만료·401·로그아웃·세션 교체 경쟁을, `test:chat`은 대화 복원·화면 이동·재연결·생성 상태 경쟁과 grapheme 표시·중단 대기열, 재생성·Markdown SSR·복사 대체 처리를 검사합니다. 스크롤 따라가기 검사를 포함한 프런트 테스트 전체를 실행하려면 `node --experimental-strip-types --test tests/*.test.mjs`를 사용합니다. 이 검사는 브라우저 화면을 열거나 실제 UI를 조작하지 않습니다. `backend/tests/test_dev.py`는 내부 IP 선택·프로세스 그룹·worker 분리와 종료 순서를, `test_worker_entrypoint.py`는 독립 worker 진입점과 종료 정리를 검사합니다. PostgreSQL 통합 검사에는 생성 멱등성·정산 기준·중단 후 차감과 기존 예약 해제·SSE 재생·두 백그라운드 worker의 리더 선출과 연결 정리도 포함합니다.

자동 압축 검사는 모델을 호출하지 않는 테스트 provider로 요약 재사용·원문 보존·입력 및 출력 한도·잘린 요약 거부·중단과 장애 복구·권한 경계·사용자 무차감을 확인합니다. 실제 Qwen의 장기 회상 정확도와 문장·코드 이어 쓰기 품질은 별도 평가 항목이며 코드 검사로 보장하지 않습니다. 실행별 결과는 [todo.md](todo.md)에 기록합니다.

검색 기능의 프런트 상태 머신·SSR 검사는 `npm run test:frontend`에 포함됩니다. 가짜 HTTP 공급자와 격리 DB로 외부 통신 차단·실패·중단·출처 저장·모드 권한·토큰 정산을 검사하며, 실제 Tavily 키를 사용하는 호출과 실제 모델의 최신 정보 정확도 검사는 별도로 남깁니다.

기억·이어쓰기의 기본 오프라인 계약 검사는 아래 명령으로 실행합니다. 6개 가상 대화와 108턴 누적 압축을 검사하며 실제 모델의 정답률로 간주하지 않습니다.

```bash
.venv/bin/python scripts/evaluate_context.py --output /private/tmp/context-contract.json
```

기존 로컬 모델의 정량 회상 평가를 명시적으로 실행하려면 다음 명령을 사용합니다. 새 서버는 시작하지 않지만 모델 추론을 수행하며 실제 앱과 함께 실행하지 않습니다. 결과에 모델·정책 버전, 시간·토큰, 단어 기반 회상 점수와 실패를 기록합니다. 전체 사례는 `--case`를 생략합니다. 표현상 동의어나 의미 정확도는 결과를 따로 검토해야 합니다.

```bash
.venv/bin/python scripts/evaluate_context.py --mode model --case short_cancelled_recall --output /private/tmp/context-model.json
```

로컬 Docker 엔진이 실행 중이면 다음 한 명령으로 실제 PostgreSQL과 전체 백엔드 테스트를 검증합니다.

```bash
.venv/bin/python scripts/test_db.py
```

runner는 고유 Compose 프로젝트, 무작위 비밀번호와 loopback 포트, tmpfs 데이터 디렉터리를 사용합니다. `compose.test.yaml`을 독립적으로 실행하며 Compose에는 `--env-file /dev/null`을 전달합니다. 테스트 subprocess의 `DATABASE_URL`, `MIGRATION_DATABASE_URL`, `TEST_DATABASE_URL`은 임시 DB 주소로 덮어씁니다. 개발 DB와 개발용 `.env`는 변경하지 않습니다.

검사 순서는 `alembic upgrade head` → `downgrade base` → `upgrade head` → `check` → 전체 `pytest`입니다. 통합 테스트는 각자 별도의 `qwen_test_<uuid>` DB를 만들고 정리합니다. 실패·중단 시에도 runner가 생성한 임시 Compose 프로젝트만 정리합니다. 웹·API·모델 서버를 띄우지 않으며, 독립 worker 검사는 격리 DB에 연결한 Mock 자식 프로세스만 사용합니다. 외부 배포하지 않으며 첫 실행에는 PostgreSQL 이미지를 내려받을 수 있습니다.

직접 준비한 테스트 PostgreSQL을 사용할 때는 `TEST_DATABASE_URL`을 전용 `qwen_test` 또는 `qwen_test_*` DB로 지정하고 `.venv/bin/python -m pytest`를 실행합니다. 이 계정에는 별도 테스트 DB의 생성·삭제 권한이 필요합니다. URL을 지정했는데 DB에 접속할 수 없으면 통합 테스트가 실패합니다.

## Health API

API가 실행 중일 때 확인할 수 있습니다.

```bash
curl -i http://127.0.0.1:8000/health/live
curl -i http://127.0.0.1:8000/health/ready
curl -i http://127.0.0.1:8000/health/worker
```

`/health/live`와 기존 `/health`는 DB·모델을 조회하지 않고 `200`과 `{"status":"ok"}`를 반환합니다. DB 비활성화 + Mock에서는 `/health/ready`가 `200`과 다음 응답을 반환합니다.

```json
{ "status": "ready", "checks": { "database": "disabled", "model": "ready" } }
```

활성화된 DB가 실패하면 `503`과 다음 응답을 반환합니다. 모델 실패도 `checks.model`을 `unavailable`로 표시하며 `503`이 됩니다.

```json
{
  "status": "not_ready",
  "checks": { "database": "unavailable", "model": "ready" }
}
```

DB와 모델 검사는 병렬로 실행합니다. 모델 검사 timeout은 최대 3초이며 DB는 설정한 health timeout을 사용합니다. readiness 응답은 `Cache-Control: no-store`를 포함합니다. 로그는 `event`, `status_code`, `checks`, `duration_ms`를 가진 JSON으로 기록하고 원본 예외·접속 URL·SQL 인자·프롬프트를 넣지 않습니다. 정상은 INFO, 의존 서비스 실패는 WARNING 수준입니다.

readiness는 의존 서비스 연결 상태만 확인하므로 migration 최신 여부, 생성 worker의 리더 상태나 입력 토큰 계산 API 지원을 보장하지 않습니다. 모델 판정은 `provider.status()`를 사용합니다. MLX는 `/v1/models`의 HTTP 응답 성공만 확인하며 설정한 모델이 실제 목록에 있는지, 추론 warm-up을 마쳤는지는 검사하지 않습니다.

`/health/worker`는 최근 10초 이내의 heartbeat와 종료 상태를 확인하고 `queued`·`running` 건수만 반환합니다. 실행 중이면 `200`, 누락·오래됨·종료·DB 장애이면 `503`, DB 비활성이면 `disabled`와 `200`입니다. heartbeat는 약 2초마다 갱신하며 진단에만 사용합니다. 새 리더의 복구 권한은 DB 세션 잠금으로 결정하고, 시간 만료만으로 생성 작업을 다시 실행하지 않습니다.

## DB 기반 변경 롤백

로그인 도입 후에는 `DATABASE_ENABLED=false`로 바꾸면 로그인·채팅이 중단됩니다. 익명 채팅으로 자동 전환하지 않습니다. 앱을 중단해도 PostgreSQL 테이블과 volume은 보존합니다.

`0012_network_search`는 검색 기록이 있으면 다운그레이드를 거절합니다. 이전 스키마로 되돌리며 이미 제공한 답변의 출처를 삭제하지 않습니다. 기록이 없는 임시 DB에서는 검색 테이블·사용자 모드 설정·요청별 모드 필드를 제거합니다.

`0011_answer_versions`는 재생성 이력이 있으면 다운그레이드를 거절합니다. 이전 스키마가 같은 질문의 여러 답변을 표현하지 못하므로 원문이나 정산 이력을 임의 삭제하지 않습니다. `0010_worker_heartbeat` 다운그레이드는 worker 관측 기록만 제거합니다.

`0009_context_compaction` 다운그레이드는 요약·압축 사용량 기록과 생성 작업의 압축 대기 표시를 제거합니다. 원본 메시지·사용자 토큰 예산·답변 정산 이력은 유지하지만 저장된 요약을 잃으므로, 작업을 종료하고 필요한 요약·유지 사용량을 보존한 뒤 이전 코드와 함께 되돌립니다.

`0008_deferred_charging`은 미정산 deferred 기록이 있으면 다운그레이드를 거절합니다. 기존 reserved 이력과 사용량은 업그레이드에서 유지하며 새 생성부터 예산 예약 없이 종료 후 차감합니다.

`0007_cancellation_usage`의 다운그레이드는 예약·사용 수치를 유지하지만 `usage_basis`를 제거합니다. 기준별 감사 이력이 필요하면 내려가기 전에 이를 보존합니다. 업그레이드는 기존 정산을 `provider`, 반환을 `waived`로 채우며 수치·시각은 변경하지 않습니다.

`0006_monthly_allowances`는 무료 예산 행이 하나라도 있으면 다운그레이드를 중단합니다. 이전 코드가 같은 기간의 무료·플랜 예산을 구분하지 못하므로 데이터가 없는 임시 DB에서만 자동 왕복을 허용합니다. 실제 월 지급 정책을 되돌릴 때는 예산·예약의 보존과 호환 계획이 필요하며 자동 삭제·합산·사용량 초기화로 해결하지 않습니다. 상세 기준은 [ADR 0007](docs/adr/0007-monthly-allowances.md#검증과-이전-구조-호환성)에 있습니다.

`0005_generation_runs`의 다운그레이드는 생성 작업·이벤트와 멱등 실행 이력을 삭제하지만 기존 메시지·토큰 예약은 남깁니다. 진행 작업과 미확정 예약을 자동 정산하지 않으므로 운영 DB의 단순 되돌리기 수단으로 사용하지 않습니다. `0004_auth_sessions`의 다운그레이드는 비밀번호 인증 수단과 로그인 세션을 삭제합니다. `0003_system_token_quotas`는 사용자 플랫폼 권한 열과 토큰 관리 테이블 3개를, `0002_core_chat_schema`는 사용자·작업 공간·소속·대화·메시지와 데이터를 삭제합니다. 마이그레이션 왕복 검사는 `.venv/bin/python scripts/test_db.py`의 임시 DB에서만 실행합니다. 실제 DB를 이전 구조로 되돌리기 전에는 worker를 정지하고 작업·예약 상태 확인, 백업과 복구 계획을 마련합니다.

`0001_database_baseline` 자체의 다운그레이드는 revision 기록만 해제하지만, 현재 head에서 `downgrade base`를 실행하면 먼저 도메인·관측 16개 테이블을 삭제하게 됩니다. 다만 검색·재생성·무료 예산 등 보존 조건에 해당하는 데이터가 있으면 해당 revision에서 거부합니다. 이전 코드의 스키마 비교를 통과시키기 위해 테이블을 지우지 않습니다. 상세 범위는 [대화·생성의 롤백 절차](docs/adr/0006-persistent-chat-and-usage.md#검증과-롤백)를 따릅니다.

## 코드 포맷

```bash
.venv/bin/ruff format backend scripts
npm run format
```

## 빌드된 웹 화면만 실행

```bash
npm run build
npm run start
```

## 종료

```text
Ctrl+C
```
