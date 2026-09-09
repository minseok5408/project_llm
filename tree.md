# 프로젝트 구조

```text
project_llm/
├── app/                         # React/Vinext 페이지·레이아웃·전역 스타일
│   ├── chat/[conversationId]/   # 저장된 대화의 직접 접근 페이지
│   └── api/[...path]/route.ts   # 같은 출처의 API·SSE를 로컬 FastAPI로 중계
├── features/                    # 기능별 화면·상태·자료형
│   ├── auth/
│   │   ├── components/         # 로그인·회원가입 게이트
│   │   └── state/              # 24시간 인증 세션
│   ├── chat/
│   │   ├── components/         # 작업 화면·사이드바·채팅 검색·입력창·메시지·모델 정보
│   │   ├── state/              # 대화·검색·생성 복원과 API 상태
│   │   ├── stream/             # SSE 해석·한글/이모지 표시 대기열
│   │   └── scroll/             # 사용자 스크롤 우선·이전 메시지 위치 보존
│   ├── network/
│   │   ├── components/         # 로컬 모드 선택·검색 출처
│   │   ├── state/              # 서버 모드·연결 상태·설정 revision
│   │   └── types.ts            # 모드·검색 자료형
│   ├── preferences/
│   │   ├── components/         # 설정 모달·사용량 화면·테마 선택과 동기화
│   │   └── state/              # 시스템·라이트·다크 설정과 브라우저 저장
│   └── usage/
│       ├── components/         # 계정 메뉴·요금제 표시·설정 모달 연결
│       └── types.ts            # 토큰 사용량 자료형
├── components/
│   ├── content/                # 공통 Markdown·복사·링크 정책
│   └── ui/                     # shadcn 기반 UI 기본 요소
├── backend/
│   ├── app/
│   │   ├── api/                # 인증·대화·생성·사용량·모드·health API
│   │   ├── runtime/
│   │   │   ├── worker.py       # 영속 큐·리더 선출·단일 실행·복구
│   │   │   └── cancellation.py # 검색·압축·생성 공통 중단 처리
│   │   ├── llm/
│   │   │   ├── protocol.py     # 모델 규격·본문/사용량 응답·오류
│   │   │   ├── registry.py     # 설정에 맞는 기존 모델 공급자 생성
│   │   │   └── providers/     # MLX·Mock 구현과 공통 스트림 처리
│   │   ├── context/
│   │   │   ├── builder.py      # 현재 답변 버전·부분 답변·요약 문맥 구성
│   │   │   ├── policy.py       # 짧은 중단 원문 전달 정책
│   │   │   ├── compaction.py   # 실제 토큰 예산·요약 배치 계획
│   │   │   └── service.py      # 요약 생성·저장·사용자 비차감 기록
│   │   ├── tools/
│   │   │   └── web_search/    # 검색 정책·결과·Tavily/Brave 어댑터
│   │   ├── services/
│   │   │   ├── generations/
│   │   │   │   ├── service.py    # 생성 서비스의 공통 의존성·본문 저장
│   │   │   │   ├── admission.py  # 질문·재생성 승인·멱등성·큐 상한
│   │   │   │   ├── settlement.py # 완료·중단·복구 후 토큰 정산
│   │   │   │   └── events.py     # 영속 이벤트·응답 구성
│   │   │   └── …              # 인증·월 무료·토큰 예산·모드·시스템 계정
│   │   ├── db/                 # DB 연결·세션·공통 ORM metadata
│   │   ├── models/             # 계정·대화·요약·토큰·검색·worker 기록
│   │   ├── repositories/       # 사용자·작업 공간 권한을 확인하는 저장소
│   │   ├── main.py             # 기존 API 진입점·DB pool 수명
│   │   ├── worker.py           # 기존 독립 worker CLI 진입점
│   │   ├── config.py           # 모델·DB·검색 환경 설정
│   │   └── schemas.py          # API 요청·응답 검증
│   ├── migrations/versions/    # 기존 Alembic 이력, head 0012_network_search
│   └── tests/                  # 모델 없는 코드 검사·격리 PostgreSQL 통합 검사
├── docs/adr/                    # 설계 결정과 구조 변경의 범위
├── hooks/                       # 공통 React 훅
├── lib/                         # 공통 프런트 유틸리티
├── public/                      # favicon 등 정적 파일
├── tests/                       # 화면 없이 실행하는 Node·React SSR 회귀 검사
├── scripts/
│   ├── dev.py                  # 명시적으로 실행한 웹·API·worker·모델 관리
│   ├── manage_accounts.py      # 로컬 시스템 계정·플랜·예산 관리
│   ├── evaluate_context.py     # 오프라인 문맥 계약·선택형 실제 모델 평가
│   └── test_db.py              # 임시 PostgreSQL·migration·전체 백엔드 검사
├── .venv/                      # [생성] Python 가상환경
├── node_modules/               # [생성] npm 패키지
├── dist/                       # [생성] 웹 빌드 결과
├── .next/                      # [생성] 호환 타입·빌드 데이터
├── .vinext/                    # [생성] Vinext 개발 데이터
├── README.md                   # 프로젝트 개요·설치
├── run.md                      # 실행·검사 명령
├── todo.md                     # 최우선 개발 기능·기존 단계별 로드맵
├── tree.md                     # 현재 폴더와 책임
├── pyproject.toml              # Python 패키지·검사 도구
├── alembic.ini                 # migration 설정
├── compose.yaml                # 개발 PostgreSQL, loopback·영속 volume
├── compose.test.yaml           # 격리 테스트 PostgreSQL, 임시 포트·tmpfs
├── package.json                # 프런트 의존성·명령
├── package-lock.json           # npm 의존성 잠금
├── .env.example                # 환경 변수 예시
├── vite.config.ts              # 로컬 웹 구성
└── tsconfig.json               # TypeScript 설정
```

현재 구현된 파일만 표시합니다. 도구 반복 실행, 사용자 장기 기억, 문서 검색용 빈 폴더는 만들지 않습니다. 후속 개발 순서는 [todo.md](todo.md), 경로 변경과 호환 범위는 [ADR 0012](docs/adr/0012-feature-runtime-structure.md)를 따릅니다.

## 실행 흐름

```text
로컬·같은 Wi-Fi 브라우저
    ↓
웹 0.0.0.0:3000 → /api 중계 → FastAPI 127.0.0.1:8000
    ├── PostgreSQL project_llm :5432 (계정·세션·대화·요약·생성 이벤트·토큰 사용량)
    └── PostgreSQL 작업 큐 ← 독립 worker (DB advisory 연결로 실행자 한 개 선출)
            ├── 사용자 모드·revision 확인 → 허용된 검색만 Tavily API (기본) (현재 질문 일부)
            ├── 검색 제목·요약·출처 → 문맥/토큰 재계산
            ↓
        OpenAI 호환 MLX-VLM 서버 :8080 (입력 토큰 계산·추론·실제량 반환)
            ↓
        mlx-community/Qwen3.8-27B-4bit
```

## 소스 폴더

- `app/`: `page.tsx`와 `chat/[conversationId]/page.tsx`가 기능 모듈의 공통 채팅 화면을 표시합니다. `layout.tsx`와 `globals.css`는 공통 레이아웃과 테마이며, API 중계 경로도 이 폴더에 유지합니다.
- `features/auth/`: 로그인·회원가입 게이트와 메모리 인증 세션을 관리합니다.
- `features/chat/`: 작업 화면, 대화 목록·입력창·메시지 컴포넌트와 기존 `state/chat-store.ts`를 둡니다. 이번 이동에서는 대화별 초안 보존이나 전역 작업 상태 같은 새 기능을 추가하지 않습니다.
- `features/network/`, `features/usage/`: 네트워크 설정·검색 출처와 토큰 사용량 화면을 각각 관리합니다.
- `features/preferences/`: 브라우저별 화면 테마와 운영체제 변경·탭 간 동기화를 관리합니다. `app/layout.tsx`의 초기 스크립트가 첫 화면 전에 테마를 적용하며 계정·토큰·네트워크 정책과 독립적입니다.
- `components/content/`: Markdown 렌더링·링크 정책·복사와 HTTP 환경의 복사 대체 처리를 공유합니다.
- `features/chat/stream/grapheme-typer.ts`, `features/chat/components/streaming-text.tsx`: 받은 답변을 약 10ms 간격의 한글·이모지 묶음으로 표시합니다. 표시 적체를 줄이고 중단 시 표시를 고정하며 모션 감소 설정을 처리합니다. `tests/chat-store.test.mjs`에서 표시 대기열과 중단·완료를 검사합니다.
- `app/api/[...path]/route.ts`: 개발·빌드 실행 모두에서 브라우저의 같은 출처 API 요청을 내부 FastAPI에 전달합니다. 서버용 `API_BASE_URL`을 사용하고 SSE 응답은 버퍼에 모으지 않고 전달합니다.
- `backend/app/`: 프런트엔드와 모델 사이의 Python 게이트웨이입니다. 권한·저장·허용량을 검증하고 생성 작업을 처리하며, DB에 저장한 이벤트를 브라우저용 SSE로 재생합니다.
- `backend/tests/`: 모델을 로드하지 않고 API·입력 검증·설정·health를 검사합니다. 실제 PostgreSQL에서는 마이그레이션·제약조건·저장·조회·접근 범위·동시 순번·커서 목록·삭제 정책과 세션 정리를 확인합니다.
- `features/chat/scroll/chat-scroll.ts`: 사용자 스크롤 우선, 최신 답변 따라가기, 이전 메시지 위치 보존을 처리합니다. `tests/chat-scroll.test.mjs`로 경계 동작을 검사합니다.
- `backend/migrations/`: Alembic 마이그레이션 이력입니다. `0001`은 기반, `0002`는 채팅용 5개 테이블, `0003`은 플랫폼 권한과 토큰 관리용 3개 테이블, `0004`는 인증 수단·세션, `0005_generation_runs`는 생성 작업·이벤트, `0006_monthly_allowances`는 예산의 `source` 구분을 추가합니다. `0007_cancellation_usage`는 `usage_basis`, `0008_deferred_charging`은 기존 수치를 보존하면서 `charge_mode`와 사용자별 미정산 제한을 추가합니다. `0009_context_compaction`은 대화 요약·시스템 유지 사용량과 압축 대기 표시를 추가합니다. `0010_worker_heartbeat`는 worker 관측, `0011_answer_versions`는 답변 버전, 현재 head `0012_network_search`는 사용자별 로컬 전용 설정·요청별 모드·검색 상태와 출처를 추가하며 도메인·관측 테이블은 16개입니다.
- `docs/adr/`: DB 연결 기반, 채팅 데이터 구조와 계정·토큰 예산/향후 결제 정책을 각 ADR로 기록합니다.
- `components/ui/`: 버튼, 입력창, 스위치 등 채팅 화면에서 사용하는 UI 기본 컴포넌트입니다.
- `hooks/`: 여러 화면에서 재사용할 수 있는 React 훅을 둡니다.
- `lib/`: 클래스 이름 결합 등 프런트엔드 공용 함수를 둡니다.
- `public/`: 브라우저가 그대로 제공하는 favicon과 정적 자산을 둡니다.
- `scripts/`: `dev.py`는 모델·API·worker·웹 프로세스를 관리하고 DB를 자동 시작하지 않습니다. `test_db.py`는 독립 테스트 PostgreSQL만 시작해 migration과 전체 테스트를 실행한 뒤 임시 리소스를 정리합니다.
- `.idea/`: 프로젝트 인터프리터와 TypeScript 경로 등 PyCharm 설정을 보관합니다.

## 백엔드 주요 파일

### 온라인·로컬 전용과 웹검색

```text
features/network/
├── state/network-mode-store.ts      # 서버 설정·연결 상태·요청 모드
├── components/network-mode-switch.tsx # 로컬 전용·웹검색 방식·다시 확인
├── components/search-sources.tsx    # 검색 단계·실패 안내·접고 펼치는 출처 제목 링크
└── types.ts                        # 모드·검색 자료형

backend/app/
├── api/network_mode.py             # 본인 모드 조회·저장·연결 재확인
├── models/user_preferences.py     # 사용자별 local_only·revision
├── models/web_search.py            # 생성별 검색 상태·출처 기록
├── services/network_mode.py        # 외부 통신 허용·공급자 확인·revision 감시
└── tools/web_search/
    ├── provider.py                # 검색 공급자 규격·공통 결과·제한된 오류
    ├── service.py                 # 검색·중단·문맥/예산 재검사·저장
    ├── context.py                 # 검색 필요성 판단·비신뢰 참고 자료 구성
    └── adapters/
        ├── tavily.py              # 기본 Tavily basic 제목·요약 검색
        ├── brave.py               # 선택 가능한 Brave 어댑터
        └── common.py              # URL·텍스트 정규화·크기 제한
```

`generation_runs`에 질문 승인 당시 모드와 사용자 설정 revision을 기록합니다. 검색은 기본 Tavily 또는 선택 Brave 어댑터로 같은 독립 worker에서 답변 직전에 실행하며, 로컬 전용 설정 변경은 대기·진행 검색에 반영합니다. 검색 결과의 제목·요약·링크만 사용하고 임의 웹페이지의 본문 수집기는 아직 없습니다. 자세한 계약은 [ADR 0011](docs/adr/0011-online-local-web-search.md)에 있습니다.

### 기존 생성·저장 기반

- `backend/app/main.py`: health·인증·대화·생성·사용량·네트워크 모드 router와 `/api/status`를 등록합니다. 기존 `/api/chat`은 `410`입니다. DB 활성화 시 engine을 준비하고 종료 때 pool을 정리합니다. 내장 worker는 기본 비활성이며 별도 worker 진입점에서 생성 수명을 관리합니다. 자동 migration은 하지 않습니다.
- `backend/app/api/conversations.py`: 작업 공간·대화 CRUD·커서 목록·이전 메시지를 제공하고, 세션 종료 전에 응답 값을 복사합니다. 생성 중인 대화의 보관·삭제를 차단합니다.
- `backend/app/api/generations.py`: 새 사용자 메시지·마지막 질문 재생성 승인, 작업 상태·중단 요청과 `after`/`Last-Event-ID` 이후 이벤트 재생을 제공합니다. 스트림 중에도 세션·소속을 다시 확인합니다.
- `backend/app/api/usage.py`: 인증된 사용자 자신의 기간 한도·실사용·예약·잔여량을 제공합니다.
- `backend/app/api/health.py`: `/health/live`, `/health/ready`, `/health/worker`와 기존 `/health` 별칭을 제공합니다. readiness는 활성화된 DB·모델의 상태를 병렬 확인하고 제한된 JSON 상태·소요시간 로그를 기록합니다.
- `backend/app/db/session.py`: pool과 `AsyncSession`을 관리합니다. 호출자가 명시적으로 commit하고 남은 transaction은 종료 시 rollback합니다. `DBSession`은 function scope여서 SSE 응답 전 닫히며 스트림·백그라운드 작업은 별도 짧은 session을 엽니다.
- `backend/app/db/base.py`: 제약조건 이름 규칙을 가진 공통 선언형 메타데이터입니다.
- `backend/app/models/`: UUID와 UTC 시각을 쓰는 도메인 15개 및 고정 name 키의 worker 관측 모델입니다. `quota.py`는 플랜·기간 예산·예약/정산과 `source=plan/free_monthly`, `usage_basis=provider/received/waived` 구분을, `generations.py`는 생성 작업·이벤트와 멱등성·active 제약을 정의합니다. `compactions.py`는 완성된 요약·범위·모델·prompt version·별도 유지 사용량과 실패/중단 상태를 저장합니다. `__init__.py`에서 전체 모델을 Alembic의 비교 대상에 포함합니다.
- `backend/app/repositories/`: 활성 사용자와 작업 공간의 소속을 확인하며 데이터를 저장·조회합니다. 호출자가 커밋하고, 같은 대화의 메시지 순번은 원자적으로 할당합니다.
- `backend/app/services/token_quota.py`: 별도 플랜·기간 예산 관리, 자기 잔여량 조회, 새 생성의 허용량 검사와 종료 후 정산을 처리하고 이전 예약 방식도 보존합니다. 정산 기준과 수치의 멱등성을 검사하고 면제 정산은 0/0만 허용합니다. 같은 source의 기간 중복을 막고 유효한 plan을 무료 예산보다 우선하며 기존 예약은 원래 예산에 정산합니다.
- `backend/app/services/monthly_allowance.py`: `MonthlyAllowanceService`가 한국 시간 월 경계로 무료 플랜·사용자별 월 예산을 준비합니다. 가입·사용량 조회·생성 승인에서 호출하고 사용자·월 키로 중복 지급을 막습니다. 기본 20,000토큰, 미이월이며 별도 cron이나 시스템 대리 계정을 만들지 않습니다.
- `backend/app/services/generations/`: `admission.py`의 승인, `settlement.py`의 정산, `events.py`의 이벤트를 `service.py`에서 연결합니다. DB 문맥 구성·실제 토큰 계산·멱등 승인·bounded queue·부분 본문 저장·정산을 연결합니다. 압축이 필요한 작업은 같은 worker에서 요약 후 실제 입력·출력 상한을 확정합니다. 사용자 중단은 상류 연결을 닫고 확인 수신량 할인 또는 0/0 면제로 마무리합니다. 최신 질문의 재생성은 기존 사용자 메시지를 재사용하고 원자적으로 현재 답변을 교체합니다. 실행은 별도 `runtime/worker.py`의 `GenerationWorker`가 담당합니다.
- `backend/app/worker.py`, `backend/app/runtime/worker.py`: 종료 신호·DB advisory lock·`SKIP LOCKED`·heartbeat·단일 실행·중단 정산과 재시작 복구를 관리합니다. API reload와 별도로 동작합니다.
- `backend/app/context/builder.py`: 종료된 현재 답변 버전에 연결된 사용자 발언·실제 부분 답변을 시간순으로 조회합니다. 서버 정책과 요약 참고 정보를 조립하고 최근 미완성 답변의 이어 쓰기 문맥을 유지합니다.
- `backend/app/context/policy.py`: 8자 이하 중단 원문을 비신뢰 참고 JSON으로 유지해 완성 답변 길이 예제로 전달하지 않습니다.
- `backend/context_evaluation.py`, `backend/evaluation_cases.py`: 고정 가상 사례·108턴 반복 압축 계약과 선택형 모델 회상 점수를 구분합니다.
- `backend/app/context/compaction.py`: 실제 tokenizer로 최근 원문 보존 수와 요약 배치 크기를 정합니다. 기존 요약·오래된 대화를 참고 자료로 전달하는 한국어 요약 지시를 관리합니다.
- `backend/app/context/service.py`: 완성된 요약을 재사용하고 압축 시도를 저장합니다. 요약 중에도 중단하며 실패·빈 응답·출력 상한 잘림을 재사용하지 않습니다. 요약 사용량은 별도 시스템 유지 기록으로 저장하고 사용자 예산을 차감하지 않습니다.
- `backend/app/services/system_accounts.py`: 명시적인 로컬 관리 명령에서 호출하는 시스템 계정 초기화입니다. 공개 가입·앱 시작 때 사용하지 않습니다.
- `backend/app/llm/protocol.py`, `backend/app/llm/registry.py`: 모델 공급자 계약과 설정 기반 생성 함수를 정의합니다. 현재 Mock·MLX 선택을 분리한 것이며 다중 모델 능력 registry는 후속입니다.
- `backend/app/runtime/cancellation.py`: `GenerationCancelled`와 공통 비동기 중단 처리를 검색·압축·생성에서 공유합니다.
- `backend/app/llm/providers/mlx.py`: `count_input`과 `ProviderDelta`로 실제 입력 토큰·본문·최종 사용량·명시적 `finish_reason`을 전달하고 유효한 `logprobs`에서 확인한 누적 수신 토큰을 계산합니다. 숨겨진 reasoning 본문은 표시·저장에서 제외하되 확인된 토큰 수는 전달합니다.
- `backend/app/config.py`: 모델 설정, 압축 시작·목표 비율·최근 원문 수·요약 출력 상한, DB 활성화 여부, pool·timeout과 검색 공급자·시간/자료 상한을 읽습니다. `DATABASE_URL`과 migration 전용 URL, `WEB_SEARCH_API_KEY`는 `SecretStr`로 보관하며 빈 값은 미설정으로 처리합니다.
- `backend/app/schemas.py`: 채팅 메시지와 생성 옵션의 데이터 구조 및 검증 규칙입니다.
- `backend/tests/test_api.py`: Mock 상태 조회·공개 health와 구형 `/api/chat`의 `410`을 검사합니다.
- `backend/tests/test_config.py`, `backend/tests/test_health.py`: DB 설정 검증과 비밀값 마스킹, liveness·readiness 계약을 검사합니다.
- `backend/tests/conftest.py`: 실제 PostgreSQL에 테스트마다 고유 DB를 만들고 정리하는 공통 도구입니다. `TEST_DATABASE_URL`이 없으면 PostgreSQL 전용 검사를 건너뜁니다.
- `backend/tests/test_database.py`: 마이그레이션 왕복·스키마 비교와 세션·상태 검사·앱 종료 동작을 확인합니다.
- `backend/tests/test_models.py`, `backend/tests/test_repositories.py`: 데이터 제약과 저장·조회, 접근 범위, 순번·커서·삭제 정책을 검사합니다.
- `backend/tests/test_token_quota.py`, `backend/tests/test_system_accounts.py`, `backend/tests/test_account_cli.py`: 기존 데이터 보존, 시스템 권한·명시적 초기화, 토큰 예약·정산·동시성, 로컬 접속 제한을 검사합니다.
- `backend/tests/test_conversation_api.py`, `backend/tests/test_generation_api.py`: 작업 공간 권한·대화 복원·페이지 조회·생성 승인·SSE 재생·다른 기기 복원·자기 사용량을 검사합니다.
- `backend/tests/test_generations.py`, `backend/tests/test_generation_worker.py`: 생성 원자성·멱등성·취소·정산·중단 복구와 실제 백그라운드 worker 두 개의 리더 선출·연결 정리를 검사합니다.
- `backend/tests/test_answer_versions.py`: 재생성 원문·문맥·권한·멱등성·종료 후 정산을 검사합니다.
- `backend/tests/test_worker_entrypoint.py`: 독립 worker 설정·종료 신호·오류 시 자원 정리와 비밀값 비노출을 검사합니다.
- `tests/chat-render.test.mjs`, `tests/clipboard.test.mjs`: Markdown SSR·버전 버튼·안전한 링크·각주 ID·복사와 초점/선택 복원을 검사합니다.
- `backend/tests/test_worker_process.py`: 격리 DB와 Mock 자식 프로세스로 API 재시작 독립성·DB 중단 요청·SIGTERM/SIGKILL 복구를 검사합니다.
- `backend/tests/test_context_evaluation.py`: 오프라인 계약·실패 점수·경계·108턴 가상 이력을 검사합니다.
- `backend/tests/test_network_mode.py`, `test_network_mode_api.py`, `test_network_schema.py`: 사용자 설정·권한·동시 변경·연결 검사 정책과 migration 제약을 검사합니다.
- `backend/tests/test_web_search_provider.py`, `test_tavily_search_provider.py`, `test_web_search_context.py`, `test_web_search_service.py`, `test_web_search_generation.py`, `test_web_search_api.py`: 가짜 HTTP·모델 공급자로 검색 정규화·문맥 상한·모드 변경·생성/정산·출처 조회를 검사합니다.
- `backend/tests/test_search_source_unicode.py`: 잘못된 Unicode 출처를 거절하고 한글·이모지를 보존하는지 검사합니다.
- `tests/network-mode.test.mjs`, `tests/chat-render.test.mjs`: 외부 전송의 기본 차단·설정 응답 경합·검색 상태·출처 링크와 스위치를 화면 없이 검사합니다.
- `backend/tests/test_provider_usage.py`: 모델 서버 없이 실제 입력 계산 요청·usage 파싱·reasoning 제외·상류 실패를 검증합니다.
- `backend/tests/test_conversation_context.py`, `backend/tests/test_context_compaction.py`: 중단된 사용자 발언·부분 답변의 문맥 구성과 실제 토큰 기준 요약 배치·최근 원문 보존을 검사합니다.
- `backend/tests/test_compaction_generation.py`: 테스트 provider와 격리 PostgreSQL로 압축부터 답변 생성까지의 통합 흐름을 검사합니다. 요약 재사용·원문 보존·실패·중단·권한 변경과 사용자 예산 정산을 확인하며 실제 모델의 회상 품질 평가는 별도입니다.

`backend/app/api/auth.py`는 `/api/v1/auth/*`와 공통 인증 의존성을, `backend/app/services/auth.py`는 비밀번호·24시간 세션과 가입 시 월 무료 예산 준비를 처리합니다. `models/auth.py`에 인증 수단·세션을 정의하고 `test_auth_service.py`, `test_auth_api.py`로 검증합니다. 일반 계정은 자동 월 무료 또는 별도 기간 plan을 사용하며 시스템 계정은 무료 지급 없이 한도 면제·사용량 기록을 유지합니다.

## 자동 생성 폴더

`[생성]`으로 표시한 폴더는 설치, 테스트, 개발 서버 또는 빌드 과정에서 다시 만들어집니다. 애플리케이션 기능을 변경할 때는 이 폴더보다 `app/`, `features/`, `backend/`, `components/`, `hooks/`, `lib/`, `public/`, `scripts/`를 수정합니다.
