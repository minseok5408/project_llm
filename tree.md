# 프로젝트 구조

```text
project_llm/
├── app/                       # React/Vinext 로그인·채팅 페이지, 레이아웃, 전역 스타일
│   ├── components/            # 인증 게이트·대화 상태·목록 UI·SSE 재생·grapheme 타이핑
│   ├── chat/[conversationId]/ # 저장된 대화의 직접 접근 페이지
│   └── api/[...path]/route.ts  # 같은 웹 주소의 API 요청을 로컬 FastAPI로 중계
├── backend/
│   ├── app/
│   │   ├── api/              # health·인증·대화·생성 이벤트·자기 사용량 API
│   │   ├── db/               # async engine·session·공통 ORM metadata
│   │   ├── models/           # 계정·인증·채팅·토큰·생성 작업·이벤트·요약 ORM 모델
│   │   ├── repositories/     # 사용자와 소속 범위를 확인하는 저장·조회 코드
│   │   ├── services/         # 인증·월 무료 지급·플랜 예산·문맥·요약·내장 단일 실행자
│   │   ├── main.py           # API 등록, DB와 생성 worker의 시작·종료
│   │   ├── config.py         # 모델·DB 환경 설정과 비밀 URL 검증
│   │   ├── providers.py      # Mock·MLX 입력 계산·스트림·확인 수신량·최종 사용량
│   │   └── schemas.py        # 채팅 요청·응답 검증
│   ├── migrations/
│   │   ├── env.py            # 환경 URL 또는 전달된 연결로 async migration 실행
│   │   ├── script.py.mako    # 새 Alembic revision 템플릿
│   │   └── versions/         # 0001 기반 ~ 0009 대화 압축의 Alembic 이력
│   └── tests/                # Mock API·설정·health·실제 PostgreSQL 통합 테스트
├── components/
│   └── ui/                    # shadcn 기반 재사용 UI 컴포넌트
├── docs/
│   └── adr/                   # DB 수명·health 계약 등 설계 결정
├── hooks/                     # 프런트엔드 공용 React 훅
├── lib/                       # 프런트엔드 공용 유틸리티
├── public/                    # favicon 등 정적 파일
├── tests/                     # 웹 중계·인증·대화 복원·생성 상태의 Node 회귀 테스트
├── scripts/
│   ├── dev.py                # 명시적으로 실행한 웹·API·모델 프로세스 관리
│   ├── manage_accounts.py    # 로컬 시스템 계정·비밀번호·토큰 플랜·기간 예산 관리
│   └── test_db.py            # 임시 PostgreSQL + migration + 전체 백엔드 테스트
├── .idea/                     # PyCharm 프로젝트 설정
├── .venv/                     # [생성] Python 3.12 가상환경
├── node_modules/              # [생성] npm 패키지
├── dist/                      # [생성] 프로덕션 웹 빌드 결과
├── .next/                     # [생성] Next 호환 타입·빌드 데이터
├── .vinext/                   # [생성] Vinext 개발 데이터
├── .pytest_cache/             # [생성] pytest 캐시
├── .ruff_cache/               # [생성] Ruff 캐시
├── qwen_workbench.egg-info/   # [생성] Python editable 설치 메타데이터
├── README.md                  # 프로젝트 개요와 설치 안내
├── run.md                     # 실행·검사 명령 모음
├── todo.md                    # 실제 서비스화를 위한 단계별 개발 로드맵
├── tree.md                    # 프로젝트 폴더와 주요 파일 설명
├── pyproject.toml             # Python 패키지·도구 설정
├── alembic.ini                # migration 위치·로깅 설정; 접속 비밀 없음
├── compose.yaml               # PostgreSQL 17 개발 DB, loopback·영속 volume
├── compose.test.yaml          # 독립 테스트 DB, loopback 임시 포트·tmpfs
├── package.json               # 프런트엔드 패키지와 npm 명령
├── package-lock.json          # npm 의존성 잠금 파일
├── .env.example               # 로컬 환경 변수 예시
├── vite.config.ts             # 로컬 Vinext/Vite 구성
└── tsconfig.json              # TypeScript 설정
```

## 실행 흐름

```text
로컬·같은 Wi-Fi 브라우저
    ↓
웹 0.0.0.0:3000 → /api 중계 → FastAPI 127.0.0.1:8000
    ├── PostgreSQL project_llm :5432 (계정·세션·대화·요약·생성 이벤트·토큰 사용량)
    └── API 내장 worker (별도 DB advisory 연결로 실행자 한 개 선출)
            ↓
        OpenAI 호환 MLX-VLM 서버 :8080 (입력 토큰 계산·추론·실제량 반환)
            ↓
        mlx-community/Qwen3.8-27B-4bit
```

## 소스 폴더

- `app/`: `page.tsx`와 `chat/[conversationId]/page.tsx`가 로그인 게이트와 공통 채팅 화면을 표시합니다. `components/chat-workbench.tsx`는 작업 공간·대화 목록·사용량 UI를, `chat-store.ts`와 `chat-stream.ts`는 API 상태·복원·SSE 재생을 처리합니다. `layout.tsx`와 `globals.css`는 공통 레이아웃과 테마입니다.
- `app/components/grapheme-typer.ts`, `streaming-text.tsx`: 받은 답변을 약 10ms 간격의 한글·이모지 묶음으로 표시합니다. 표시 적체를 줄이고 중단 시 표시를 고정하며 모션 감소 설정을 처리합니다. `tests/chat-store.test.mjs`에서 표시 대기열과 중단·완료를 검사합니다.
- `app/api/[...path]/route.ts`: 개발·빌드 실행 모두에서 브라우저의 같은 출처 API 요청을 내부 FastAPI에 전달합니다. 서버용 `API_BASE_URL`을 사용하고 SSE 응답은 버퍼에 모으지 않고 전달합니다.
- `backend/app/`: 프런트엔드와 모델 사이의 Python 게이트웨이입니다. 권한·저장·허용량을 검증하고 생성 작업을 처리하며, DB에 저장한 이벤트를 브라우저용 SSE로 재생합니다.
- `backend/tests/`: 모델을 로드하지 않고 API·입력 검증·설정·health를 검사합니다. 실제 PostgreSQL에서는 마이그레이션·제약조건·저장·조회·접근 범위·동시 순번·커서 목록·삭제 정책과 세션 정리를 확인합니다.
- `app/components/chat-scroll.ts`: 사용자 스크롤 우선, 최신 답변 따라가기, 이전 메시지 위치 보존을 처리합니다. `tests/chat-scroll.test.mjs`로 경계 동작을 검사합니다.
- `backend/migrations/`: Alembic 마이그레이션 이력입니다. `0001`은 기반, `0002`는 채팅용 5개 테이블, `0003`은 플랫폼 권한과 토큰 관리용 3개 테이블, `0004`는 인증 수단·세션, `0005_generation_runs`는 생성 작업·이벤트, `0006_monthly_allowances`는 예산의 `source` 구분을 추가합니다. `0007_cancellation_usage`는 `usage_basis`, `0008_deferred_charging`은 기존 수치를 보존하면서 `charge_mode`와 사용자별 미정산 제한을 추가합니다. 현재 head `0009_context_compaction`은 대화 요약·시스템 유지 사용량과 생성 작업의 압축 대기 표시를 추가하며 도메인 테이블은 13개입니다.
- `docs/adr/`: DB 연결 기반, 채팅 데이터 구조와 계정·토큰 예산/향후 결제 정책을 각 ADR로 기록합니다.
- `components/ui/`: 버튼, 입력창, 스위치 등 채팅 화면에서 사용하는 UI 기본 컴포넌트입니다.
- `hooks/`: 여러 화면에서 재사용할 수 있는 React 훅을 둡니다.
- `lib/`: 클래스 이름 결합 등 프런트엔드 공용 함수를 둡니다.
- `public/`: 브라우저가 그대로 제공하는 favicon과 정적 자산을 둡니다.
- `scripts/`: `dev.py`는 모델·API·웹 프로세스를 관리하고 DB를 자동 시작하지 않습니다. `test_db.py`는 독립 테스트 PostgreSQL만 시작해 migration과 전체 테스트를 실행한 뒤 임시 리소스를 정리합니다.
- `.idea/`: 프로젝트 인터프리터와 TypeScript 경로 등 PyCharm 설정을 보관합니다.

## 백엔드 주요 파일

- `backend/app/main.py`: health·인증·대화·생성·사용량 router와 `/api/status`를 등록합니다. 기존 `/api/chat`은 `410`입니다. DB 활성화 시 engine을 준비하고 기본 내장 생성 worker를 시작하며, 종료 때 worker 전용 연결과 pool을 정리합니다. 자동 migration은 하지 않습니다.
- `backend/app/api/conversations.py`: 작업 공간·대화 CRUD·커서 목록·이전 메시지를 제공하고, 세션 종료 전에 응답 값을 복사합니다. 생성 중인 대화의 보관·삭제를 차단합니다.
- `backend/app/api/generations.py`: 새 사용자 메시지 승인, 작업 상태·중단 요청과 `after`/`Last-Event-ID` 이후 이벤트 재생을 제공합니다. 스트림 중에도 세션·소속을 다시 확인합니다.
- `backend/app/api/usage.py`: 인증된 사용자 자신의 기간 한도·실사용·예약·잔여량을 제공합니다.
- `backend/app/api/health.py`: `/health/live`, `/health/ready`와 기존 `/health` 별칭을 제공합니다. readiness는 활성화된 DB·모델의 상태를 병렬 확인하고 제한된 JSON 상태·소요시간 로그를 기록합니다.
- `backend/app/db/session.py`: pool과 `AsyncSession`을 관리합니다. 호출자가 명시적으로 commit하고 남은 transaction은 종료 시 rollback합니다. `DBSession`은 function scope여서 SSE 응답 전 닫히며 스트림·백그라운드 작업은 별도 짧은 session을 엽니다.
- `backend/app/db/base.py`: 제약조건 이름 규칙을 가진 공통 선언형 메타데이터입니다.
- `backend/app/models/`: UUID와 UTC 시각을 공유하는 13개 ORM 모델입니다. `quota.py`는 플랜·기간 예산·예약/정산과 `source=plan/free_monthly`, `usage_basis=provider/received/waived` 구분을, `generations.py`는 생성 작업·이벤트와 멱등성·active 제약을 정의합니다. `compactions.py`는 완성된 요약·범위·모델·prompt version·별도 유지 사용량과 실패/중단 상태를 저장합니다. `__init__.py`에서 전체 모델을 Alembic의 비교 대상에 포함합니다.
- `backend/app/repositories/`: 활성 사용자와 작업 공간의 소속을 확인하며 데이터를 저장·조회합니다. 호출자가 커밋하고, 같은 대화의 메시지 순번은 원자적으로 할당합니다.
- `backend/app/services/token_quota.py`: 별도 플랜·기간 예산 관리, 자기 잔여량 조회, 새 생성의 허용량 검사와 종료 후 정산을 처리하고 이전 예약 방식도 보존합니다. 정산 기준과 수치의 멱등성을 검사하고 면제 정산은 0/0만 허용합니다. 같은 source의 기간 중복을 막고 유효한 plan을 무료 예산보다 우선하며 기존 예약은 원래 예산에 정산합니다.
- `backend/app/services/monthly_allowance.py`: `MonthlyAllowanceService`가 한국 시간 월 경계로 무료 플랜·사용자별 월 예산을 준비합니다. 가입·사용량 조회·생성 승인에서 호출하고 사용자·월 키로 중복 지급을 막습니다. 기본 20,000토큰, 미이월이며 별도 cron이나 시스템 대리 계정을 만들지 않습니다.
- `backend/app/services/generations.py`: DB 문맥 구성·실제 토큰 계산·멱등 승인·bounded queue·부분 본문 저장·정산을 연결합니다. 압축이 필요한 작업은 같은 worker에서 요약 후 실제 입력·출력 상한을 확정합니다. 사용자 중단은 상류 연결을 닫고 확인 수신량 할인 또는 0/0 면제로 마무리합니다. 같은 파일의 `GenerationWorker`는 DB advisory lock과 `SKIP LOCKED`로 단일 실행·재시작 처리를 담당합니다. API 밖의 별도 worker 프로세스와 불명 사용량 대조는 후속입니다.
- `backend/app/services/conversation_context.py`: 종료된 생성에 연결된 사용자 발언·실제 부분 답변을 시간순으로 조회합니다. 서버 정책과 요약 참고 정보를 조립하고 최근 미완성 답변의 이어 쓰기 문맥을 유지합니다.
- `backend/app/services/context_compaction.py`: 실제 tokenizer로 최근 원문 보존 수와 요약 배치 크기를 정합니다. 기존 요약·오래된 대화를 참고 자료로 전달하는 한국어 요약 지시를 관리합니다.
- `backend/app/services/compaction_service.py`: 완성된 요약을 재사용하고 압축 시도를 저장합니다. 요약 중에도 중단하며 실패·빈 응답·출력 상한 잘림을 재사용하지 않습니다. 요약 사용량은 별도 시스템 유지 기록으로 저장하고 사용자 예산을 차감하지 않습니다.
- `backend/app/services/system_accounts.py`: 명시적인 로컬 관리 명령에서 호출하는 시스템 계정 초기화입니다. 공개 가입·앱 시작 때 사용하지 않습니다.
- `backend/app/providers.py`: `count_input`과 `ProviderDelta`로 실제 입력 토큰·본문·최종 사용량·명시적 `finish_reason`을 전달하고 유효한 `logprobs`에서 확인한 누적 수신 토큰을 계산합니다. 숨겨진 reasoning 본문은 표시·저장에서 제외하되 확인된 토큰 수는 전달합니다.
- `backend/app/config.py`: 모델 설정, 압축 시작·목표 비율·최근 원문 수·요약 출력 상한, DB 활성화 여부, pool·timeout을 읽습니다. `DATABASE_URL`과 migration 전용 URL은 `SecretStr`로 보관하며 빈 값은 미설정으로 처리합니다.
- `backend/app/schemas.py`: 채팅 메시지와 생성 옵션의 데이터 구조 및 검증 규칙입니다.
- `backend/tests/test_api.py`: Mock 상태 조회·공개 health와 구형 `/api/chat`의 `410`을 검사합니다.
- `backend/tests/test_config.py`, `backend/tests/test_health.py`: DB 설정 검증과 비밀값 마스킹, liveness·readiness 계약을 검사합니다.
- `backend/tests/conftest.py`: 실제 PostgreSQL에 테스트마다 고유 DB를 만들고 정리하는 공통 도구입니다. `TEST_DATABASE_URL`이 없으면 PostgreSQL 전용 검사를 건너뜁니다.
- `backend/tests/test_database.py`: 마이그레이션 왕복·스키마 비교와 세션·상태 검사·앱 종료 동작을 확인합니다.
- `backend/tests/test_models.py`, `backend/tests/test_repositories.py`: 데이터 제약과 저장·조회, 접근 범위, 순번·커서·삭제 정책을 검사합니다.
- `backend/tests/test_token_quota.py`, `backend/tests/test_system_accounts.py`, `backend/tests/test_account_cli.py`: 기존 데이터 보존, 시스템 권한·명시적 초기화, 토큰 예약·정산·동시성, 로컬 접속 제한을 검사합니다.
- `backend/tests/test_conversation_api.py`, `backend/tests/test_generation_api.py`: 작업 공간 권한·대화 복원·페이지 조회·생성 승인·SSE 재생·다른 기기 복원·자기 사용량을 검사합니다.
- `backend/tests/test_generations.py`, `backend/tests/test_generation_worker.py`: 생성 원자성·멱등성·취소·정산·중단 복구와 실제 백그라운드 worker 두 개의 리더 선출·연결 정리를 검사합니다.
- `backend/tests/test_provider_usage.py`: 모델 서버 없이 실제 입력 계산 요청·usage 파싱·reasoning 제외·상류 실패를 검증합니다.
- `backend/tests/test_conversation_context.py`, `backend/tests/test_context_compaction.py`: 중단된 사용자 발언·부분 답변의 문맥 구성과 실제 토큰 기준 요약 배치·최근 원문 보존을 검사합니다.
- `backend/tests/test_compaction_generation.py`: 테스트 provider와 격리 PostgreSQL로 압축부터 답변 생성까지의 통합 흐름을 검사합니다. 요약 재사용·원문 보존·실패·중단·권한 변경과 사용자 예산 정산을 확인하며 실제 모델의 회상 품질 평가는 별도입니다.

`backend/app/api/auth.py`는 `/api/v1/auth/*`와 공통 인증 의존성을, `backend/app/services/auth.py`는 비밀번호·24시간 세션과 가입 시 월 무료 예산 준비를 처리합니다. `models/auth.py`에 인증 수단·세션을 정의하고 `test_auth_service.py`, `test_auth_api.py`로 검증합니다. 일반 계정은 자동 월 무료 또는 별도 기간 plan을 사용하며 시스템 계정은 무료 지급 없이 한도 면제·사용량 기록을 유지합니다.

## 자동 생성 폴더

`[생성]`으로 표시한 폴더는 설치, 테스트, 개발 서버 또는 빌드 과정에서 다시 만들어집니다. 애플리케이션 기능을 변경할 때는 이 폴더보다 `app/`, `backend/`, `components/`, `hooks/`, `lib/`, `public/`, `scripts/`를 수정합니다.
