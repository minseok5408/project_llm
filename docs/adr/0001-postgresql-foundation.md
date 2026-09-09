# ADR 0001: 로컬 PostgreSQL 기반과 세션·health 계약

날짜: 2026-09-09\
상태: 채택\
범위: `todo.md` PR 1 — DB 연결·migration 기반·health

## 배경

기존 채팅 MVP는 브라우저에만 대화를 보관한다. 사용자·workspace 권한과 채팅 저장을 추가하기 전에 모든 기능이 공유할 DB 연결, transaction 수명과 migration 실행 방식을 고정한다. 프로젝트는 로컬 전용으로 유지하며 호스팅 설정·원격 미리보기·외부 배포를 추가하지 않는다.

## 결정

### PostgreSQL과 활성화

개발·통합 테스트에서 PostgreSQL 17과 SQLAlchemy 2.x async, asyncpg, Alembic을 사용한다. 개발 Compose는 `127.0.0.1`에만 포트를 열고 `/var/lib/postgresql/data`를 named volume에 저장한다. 해당 경로는 PostgreSQL 17 공식 이미지의 데이터 저장 경로다. 테스트 Compose는 독립 프로젝트와 tmpfs를 사용한다. [Docker 공식 PostgreSQL 이미지](https://hub.docker.com/_/postgres)

`DATABASE_ENABLED=false`가 기본이다. 비활성화 상태에서는 DB engine을 만들지 않고 기존 Mock 채팅을 그대로 사용할 수 있다. URL은 선택적 `SecretStr`이며 빈 값은 미설정으로 처리한다. 활성화 시 `DATABASE_URL`을 요구하고 `postgresql+asyncpg` scheme, host, database를 검증한다.

DB 활성화 시 application lifespan마다 engine과 제한된 pool을 준비하고 종료 시 dispose한다. 이때 실제 DB 연결이나 migration을 실행하지 않는다. DB가 일시적으로 중단돼도 API 프로세스가 시작할 수 있고 readiness에서 장애를 관찰한다. 연결의 timezone은 UTC다.

### 세션과 transaction

공통 `Database.session()`이 독립적인 `AsyncSession`을 제공한다. 같은 session을 여러 동시 작업에서 공유하지 않는다. 쓰기를 완료하려면 호출자가 `await session.commit()`을 명시적으로 실행한다. 정상 반환·예외·취소 시 남은 transaction을 rollback하고 session을 닫는다. 비동기 작업별 session 분리와 engine의 명시적 dispose는 SQLAlchemy AsyncIO 수명 관리 지침에 따른다. [SQLAlchemy AsyncIO](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

FastAPI에서 요청 session은 `DBSession = Annotated[AsyncSession, Depends(get_session, scope="function")]`으로 받는다. 함수 scope의 yield dependency는 응답 전 종료되므로 SSE가 이어지는 동안 요청 transaction을 유지하지 않는다. 이를 위해 FastAPI 최소 버전을 0.121로 올린다. [FastAPI yield dependency scope](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/)

종료한 session은 다시 열리지 않도록 `close_resets_only=False`를 적용한다. SSE iterator나 백그라운드 작업에서 DB가 필요하면 별도 짧은 session을 열고 스트림 전송·모델 대기 전에 닫는다. 현재 채팅 API에는 DB 저장을 연결하지 않았다.

### Migration과 역할

공통 `Base.metadata`와 제약 이름 규칙을 두고 Alembic이 해당 metadata를 비교한다. 첫 revision `0001_database_baseline`은 도메인 테이블을 만들지 않는다. `upgrade head` 후 존재하는 관리 테이블은 `alembic_version`뿐이다. 사용자·workspace·대화·메시지 schema는 PR 2에서 추가한다.

Alembic은 `MIGRATION_DATABASE_URL`이 있으면 사용하고 없으면 `DATABASE_URL`로 접속한다. 접속 URL은 `alembic.ini`에 저장하지 않는다. migration engine은 `NullPool`을 사용하고 작업 뒤 dispose한다. runtime과 migration을 별도 역할로 운영할 수 있도록 URL만 분리했다. 현재 개발 Compose의 초기 `qwen` 계정은 owner이며 제한된 runtime 역할, RLS와 권한 격리는 PR 2 이후의 작업이다.

### Health API

| 요청                | 검사                                                 | 응답                                              |
| ------------------- | ---------------------------------------------------- | ------------------------------------------------- |
| `GET /health/live`  | 의존 서비스 조회 없음                                | `200`, `{"status":"ok"}`                          |
| `GET /health`       | 기존 경로 호환용 liveness 별칭                       | `200`, `{"status":"ok"}`                          |
| `GET /health/ready` | 활성화된 DB의 `SELECT 1`과 provider 상태를 병렬 확인 | 모두 사용 가능하면 `200`, 하나라도 실패하면 `503` |

readiness 응답은 `{"status":"ready|not_ready","checks":{"database":"ready|disabled|unavailable","model":"ready|unavailable"}}` 구조다. 비활성 DB는 접속을 생략하고 `disabled`를 반환한다. 모델은 DB 활성화 여부와 관계없이 확인한다. DB 검사는 pool 대기·접속·query 전체에 `DATABASE_HEALTH_TIMEOUT_SECONDS`를 적용하고 모델 검사는 최대 3초로 제한한다.

응답은 `Cache-Control: no-store`를 포함한다. 실패 시 원본 예외나 접속 정보를 응답에 담지 않는다. 로그에는 허용된 `event=readiness_check`, HTTP 상태, dependency 상태, `duration_ms`만 JSON으로 기록한다. 정상 상태는 INFO, 준비 실패는 WARNING이다. 별도 metric exporter는 아직 추가하지 않았다.

readiness는 연결 가능 여부를 나타내며 migration revision, 사용자 권한 또는 채팅 저장 완료를 보증하지 않는다.

모델 판정에는 기존 `provider.status()`를 유지한다. 현재 MLX provider는 `/v1/models`의 HTTP 응답 성공을 확인하며 응답 목록에 설정한 모델이 존재하는지 또는 실제 추론이 가능한지는 검증하지 않는다. 모델 warm-up과 실행 준비 검증은 로드맵 12단계에 남아 있다.

## 검증 방식

`scripts/test_db.py`를 한 명령으로 실행하면 이미 실행 중인 로컬 Docker 엔진에서 임시 PostgreSQL만 시작한다. 고유 Compose 프로젝트, 무작위 비밀번호, loopback 임시 포트와 tmpfs를 사용한다. Compose에 `--env-file /dev/null`을 전달하고 테스트 subprocess의 DB URL을 새 주소로 덮어써 개발 DB를 격리한다.

runner는 migration upgrade/downgrade/upgrade, Alembic metadata drift 검사, 전체 pytest를 실행한다. PostgreSQL 테스트는 각각 고유 DB를 생성하고 정리한다. 테스트에 사용하는 임시 테이블은 애플리케이션 metadata에 섞지 않는다. 세션 commit·rollback·취소·SSE 수명, DB 장애·복구와 application shutdown을 실제 PostgreSQL에서 검사한다.

테스트가 끝나거나 중단되면 runner가 생성한 프로젝트만 정리한다. 개발용 `.env`나 volume은 변경하지 않고 웹·API·모델 서버를 따로 시작하지 않는다. Docker를 자동 설치하거나 시작하지 않으며 사용할 수 없으면 명확히 실패한다. DB URL 없는 기본 pytest에서는 PostgreSQL 전용 테스트만 skip한다.

## 롤백과 호환 범위

현재 PR은 기존 채팅 요청·응답 계약과 데이터 저장 방식을 변경하지 않는다. `DATABASE_ENABLED=false`로 API를 다시 시작하면 DB 기반을 끌 수 있고 이전 코드로 복귀할 수 있다. 개발 DB는 `docker compose stop postgres`로 정지하고 volume은 유지한다.

baseline만 적용된 상태에서 `alembic downgrade base`는 revision 기록을 해제하며 도메인 데이터를 변경하지 않는다. 빈 `alembic_version` 테이블은 남을 수 있다. 다시 `upgrade head`할 수 있다. 이 호환성 판단은 빈 baseline에 한정하며 후속 도메인 migration에는 별도 데이터 호환·rollback 계획이 필요하다.

## 남은 작업

이 단계에는 사용자·workspace·대화·메시지 schema, 인증, RLS, 저장 API와 영속 생성 큐가 없다. 새로고침 후 대화를 복원하는 기능도 아직 없다. PR 2부터 도메인 schema와 repository를 추가하고, 이후 인증·권한 격리와 저장 흐름을 순서대로 구현한다.
