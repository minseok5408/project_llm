# 프로젝트 구조

```text
project_llm/
├── app/                         # React/Vinext 페이지·레이아웃·전역 스타일
│   ├── chat/[conversationId]/   # 저장된 대화의 직접 접근 페이지
│   └── api/[...path]/route.ts   # 같은 출처 API·SSE를 로컬 FastAPI로 중계
├── features/                    # 기능별 화면·상태·자료형
│   ├── auth/
│   │   ├── components/         # 로그인·회원가입 게이트
│   │   └── state/              # 24시간 인증 세션
│   ├── chat/
│   │   ├── components/         # 화면 조합·사이드바·검색·입력창·메시지·질문 카드
│   │   ├── hooks/              # 스크롤·라우팅·모델 폴링·WebMCP·메뉴·모션 구독
│   │   ├── state/              # 계정별 작업·대화 제어·생성 이벤트·스트림·자료형
│   │   ├── stream/             # SSE 해석·한글/이모지 표시 대기열
│   │   └── scroll/             # 사용자 스크롤 우선·이전 메시지 위치 계산
│   ├── files/
│   │   ├── components/         # 첨부 선택·상태·재시도·삭제·근거 펼치기
│   │   ├── state/              # 대화별 파일 요청·폴링·늦은 응답 차단
│   │   └── types.ts            # 첨부·근거·파일 상태 자료형
│   ├── network/
│   │   ├── components/         # 데이터 사용 ON/OFF·검색 출처
│   │   ├── state/              # 서버 설정·연결 상태·생성 요청 정책
│   │   └── types.ts            # 모드·검색 자료형
│   ├── memory/
│   │   ├── components/         # 개인 기억 조회·명시적 저장·수정·삭제
│   │   └── state/              # 기억 목록·세대·요청 경합·계정 전환 정리
│   ├── preferences/
│   │   ├── components/         # 설정 모달·탭·사용량 상세·테마 선택
│   │   └── state/              # 시스템·라이트·다크 테마와 브라우저 저장
│   └── usage/
│       ├── components/         # 계정 메뉴·요금제 표시·설정 모달 연결
│       └── types.ts            # 토큰 사용량 자료형
├── components/
│   ├── content/                # 공통 Markdown·복사·링크 정책·코드 색상/줄바꿈
│   └── ui/                     # shadcn 기반 UI 기본 요소
├── backend/
│   ├── app/
│   │   ├── api/                # 인증·대화·생성·사용량·모드·기억·파일·health API
│   │   ├── runtime/
│   │   │   ├── worker.py       # 영속 큐·리더 선출·실행 조합·종료·복구
│   │   │   ├── contracts.py    # GenerationJob·ModelExecution 실행 계약
│   │   │   ├── steps.py        # 단계별 실행·예산·확정 사용량 기록
│   │   │   ├── progress.py     # 내부 실행 단계·종료 상태
│   │   │   └── cancellation.py # 검색·압축·생성 공통 중단 처리
│   │   ├── llm/
│   │   │   ├── protocol.py     # 모델 규격·본문/사용량 응답·도구 호출·오류
│   │   │   ├── registry.py     # 설정에 맞는 모델 공급자 생성
│   │   │   └── providers/     # MLX·Mock·공통 입력 정규화와 스트림 처리
│   │   ├── context/
│   │   │   ├── builder.py      # 현재 답변 버전·부분 답변·요약 문맥 구성
│   │   │   ├── language.py     # 현재 질문·명시적 번역 요청에 따른 언어 지침
│   │   │   ├── local.py        # 개인 기억·원문 발췌 반영과 실제 입력 한도
│   │   │   ├── recall.py       # 같은 대화의 압축 이전 원문 검색·발췌 위치
│   │   │   ├── dependencies.py # 기억·문서 세대와 파생 문맥의 재사용 판정
│   │   │   ├── policy.py       # 짧은 중단 원문 전달 정책
│   │   │   ├── compaction.py   # 실제 토큰 예산·요약 배치 계획
│   │   │   ├── service.py      # 요약 생성·저장·사용자 비차감 기록
│   │   │   └── status.py       # 문맥 측정·압축 상태·요약 반영 범위
│   │   ├── files/              # 원본·격리 파싱·로컬 임베딩·검색·삭제 표시
│   │   ├── tools/
│   │   │   ├── questions.py   # 질문 카드 도구·검증·실제 입력 한도
│   │   │   ├── calculation/
│   │   │   │   ├── evaluator.py # 외부 접근 없는 제한 산술·Python AST 해석
│   │   │   │   ├── planning.py  # 질문 의도·수치 근거·완전한 코드 원문 검증
│   │   │   │   └── service.py   # 준비 단계·실제 사용량·최종 문맥·중단 연결
│   │   │   └── web_search/    # 검색 계획·제한 재검색·Tavily/Brave 어댑터
│   │   ├── services/
│   │   │   ├── generations/  # 생성 승인·본문 저장·정산·영속 이벤트
│   │   │   ├── conversations.py # 대화·첨부 삭제 트랜잭션 조합
│   │   │   ├── memories.py    # 개인 기억 CRUD·충돌·관련 생성 중단
│   │   │   └── …             # 인증·월 무료·토큰 예산·모드·시스템 계정
│   │   ├── repositories/       # 명시적 진입점·권한·계정·대화·메시지 저장소
│   │   ├── db/                 # DB 연결·세션·공통 ORM metadata
│   │   ├── models/             # 도메인·worker 관측 테이블 21개
│   │   ├── main.py             # API 진입점·DB pool 수명
│   │   ├── worker.py           # 독립 worker CLI 진입점
│   │   ├── config.py           # 모델·DB·검색·파일 환경 설정
│   │   └── schemas.py          # API 요청·응답 검증
│   ├── evaluation/             # 합성 답변 평가 자료·실행·사람 채점·기준선 비교
│   │   ├── fixtures/           # 고정 질문·참고 자료·0~2점 채점 기준
│   │   ├── schema.py           # 자료 형식·loopback 평가 주소 검증
│   │   ├── runner.py           # 기존 입력 정책 재사용·실행 결과·실사용량 기록
│   │   ├── calculation.py      # 제품과 같은 계산 근거·준비 모델 사용량의 독립 평가
│   │   └── scoring.py          # 답변에 결합한 채점·미평가 구분·회귀 판정
│   ├── migrations/versions/    # Alembic 이력, head 0017_schema_roles
│   └── tests/                  # 모델 없는 코드 검사·격리 PostgreSQL 통합 검사
├── docs/
│   ├── development-status.md   # 구현 현황·최근 변경·한계·검증 범위
│   ├── answer-quality-evaluation.md # 실제 모델 답변 품질 기준·로컬 평가 절차
│   ├── adr/                    # 기능별 결정과 당시 검증 기록
│   └── evaluations/            # 실제 모델·문서 검색의 합성 평가 결과
├── hooks/                       # 여러 화면에서 쓰는 공통 React 훅
├── lib/
│   ├── http.ts                 # RequestFn·JsonRequest·API 오류 처리 공통 계약
│   └── utils.ts                # 공통 클래스 이름 결합
├── public/                      # favicon 등 정적 파일
├── tests/                       # Node·React SSR·상태/수명 회귀 검사
├── scripts/
│   ├── dev.py                  # 명시적으로 실행한 웹·API·worker·모델 관리
│   ├── manage_accounts.py      # 로컬 시스템 계정·플랜·예산 관리
│   ├── setup_rag.py            # 고정 로컬 임베딩 모델 설치
│   ├── evaluate_answers.py     # 답변 자료 검증·실제 모델 실행·채점·기준선 비교
│   ├── evaluate_file_rag.py    # 문서 검색 품질 비교
│   ├── evaluate_answer_language.py # 입력 언어·번역·혼용 합성 평가
│   ├── evaluate_context.py     # 문맥 계약·선택형 실제 모델 평가
│   └── test_db.py              # 임시 PostgreSQL·migration·백엔드 검사
├── data/documents/              # 로컬 첨부 원본, Git 제외
├── models/                      # 로컬 모델, Git 제외
├── .venv/                       # [생성] Python 가상환경
├── node_modules/                # [생성] npm 패키지
├── dist/                        # [생성] 웹 빌드 결과
├── .next/                       # [생성] 호환 타입·빌드 데이터
├── .vinext/                     # [생성] Vinext 개발 데이터
├── README.md                    # 프로젝트 개요·설치
├── run.md                       # 실행·검사 명령
├── todo.md                      # 개발 우선순위·분야별 요구와 검증 이력
├── tree.md                      # 현재 폴더와 책임
├── AGENTS.md                    # 프로젝트 작업 규칙
├── pyproject.toml               # Python 패키지·검사 도구
├── alembic.ini                  # migration 설정
├── compose.yaml                # 개발 PostgreSQL, loopback·영속 volume
├── compose.test.yaml           # 격리 테스트 PostgreSQL, 임시 포트·tmpfs
├── package.json                # 프런트 의존성·명령
├── package-lock.json           # npm 의존성 잠금
├── .env.example                # 환경 변수 예시, 새 키는 실제 .env에도 함께 반영
├── vite.config.ts              # 로컬 웹 구성
└── tsconfig.json               # TypeScript 설정
```

현재 기능의 현황과 제약은 [개발 현황](docs/development-status.md), 개발 순서는 [TODO](todo.md)를 따릅니다. 초기 기능별 이동은 [ADR 0012](docs/adr/0012-feature-runtime-structure.md), 코드 역할 분리는 [ADR 0019](docs/adr/0019-responsibility-separation.md), DB 역할 정리는 [ADR 0020](docs/adr/0020-schema-roles.md)에 기록합니다. `[생성]` 폴더는 설치·검사·빌드 과정에서 다시 만들어지며 직접 기능을 구현하는 위치가 아닙니다.

## 실행 흐름

```text
로컬·허용된 같은 Wi-Fi/LAN 브라우저
    ↓
웹 0.0.0.0:3000 → 같은 출처 /api 중계 → FastAPI 127.0.0.1:8000
    ├── PostgreSQL 127.0.0.1:5432 (계정·대화·문서·생성·이벤트·실제 사용량)
    └── 영속 작업 큐 ← 독립 worker (DB advisory 연결로 단일 실행자 선출)
            ├── 필요 시 문맥 압축 → 허용된 웹검색·제한 재검색
            ├── 같은 대화 원문·개인 기억·첨부 문서의 로컬 검색
            ├── 필요한 숫자 계산·원문 Python의 제한 추적
            ├── 실제 입력량·권한·예산 확인 → 질문 카드 또는 답변
            ↓
        MLX-VLM 서버 127.0.0.1:8080 (입력 토큰 계산·추론·실제량 반환)
            ↓
        mlx-community/Qwen3.8-27B-4bit
```

외부 검색은 데이터 사용 ON의 `network_mode=auto`·`web_search=auto` 요청을 기존 키워드 규칙이 검색 대상으로 고르고 서버 정책이 허용할 때 Tavily 또는 선택한 Brave API에 필요한 검색어를 전달합니다. 검색 키워드가 없는 일반 질문은 검색 준비를 생략하며, OFF는 `local/off`로 제한합니다. 키워드가 포함된 번역 등 경계 오판의 한계는 [ADR 0011](docs/adr/0011-online-local-web-search.md)을 따릅니다. 모델 추론·첨부 원본·임베딩은 로컬에 유지합니다. 별도 개발 서버나 원격 미리보기를 구조 정리 과정에서 시작하지 않습니다.

## 프런트의 역할 분리

| 파일                                          | 책임                                                                       |
| --------------------------------------------- | -------------------------------------------------------------------------- |
| `features/chat/components/chat-workbench.tsx` | 화면 조합, 전송 가능 여부, 사용자 행동을 저장소·훅에 연결                  |
| `features/chat/state/chat-store.ts`           | 계정 세션의 전역 작업·알림·대화 선택과 대화별 세션 조합                    |
| `features/chat/state/conversation-session.ts` | 대화별 초안·목록·사용자 요청을 제어하고 파일·생성 연결을 조합              |
| `features/files/state/conversation-files.ts`  | 첨부 조회·업로드·삭제·재시도와 폴링, 대화 전환 뒤 늦은 응답 차단           |
| `features/chat/state/generation-events.ts`    | 원본 상태를 바꾸지 않고 이벤트를 검증해 상태 변경분·재생 커서를 계산       |
| `features/chat/state/generation-stream.ts`    | SSE 연결·재연결·중단·종료 후 갱신, 현재 생성에 필요한 상태·콜백만 전달받음 |
| `features/chat/state/conversation-types.ts`   | 대화·메시지·생성·페이지·화면 상태 자료형                                   |
| `lib/http.ts`                                 | 기능별 상태 구현에 의존하지 않는 요청 함수 자료형과 응답 오류 처리         |

`ConversationSession`의 공개 메서드와 기존 자료형 import는 유지합니다. 파일·스트림 객체에는 필요한 조회·갱신·취소 함수만 전달하며 세션 구현 전체를 넘기지 않습니다. `stream/chat-stream.ts`의 SSE 해석, `stream/grapheme-typer.ts`의 한글·이모지 표시 대기열은 각각의 책임을 유지합니다.

| Workbench 훅             | 책임                                                               |
| ------------------------ | ------------------------------------------------------------------ |
| `use-chat-session.ts`    | 계정별 저장소 수명, 주소 이동, 뒤로가기·화면 복귀 갱신             |
| `use-chat-scroll.ts`     | 최신 답변 따라가기, 위로 읽기 우선, 이전 메시지 앵커와 비동기 복원 |
| `use-model-status.ts`    | 모델 상태 폴링, 요청 취소와 늦은 응답 폐기                         |
| `use-web-mcp.ts`         | 현재 전송 함수를 사용하는 도구 등록과 화면 종료 시 해제            |
| `use-workbench-menus.ts` | 검색 단축키, 외부 클릭·Escape, 메뉴와 초점 복귀                    |
| `use-reduced-motion.ts`  | 운영체제 모션 설정 구독과 해제                                     |

위 훅은 `features/chat/hooks/`에 있습니다. `hooks/`에는 여러 화면이 공유할 훅을 둡니다. Workbench의 계정 키가 바뀌면 이전 저장소·요청·이벤트 구독을 정리합니다.

`features/preferences/components/account-settings-dialog.tsx`는 설정 모달·탭과 사용량 상세를 조합하고, 테마 컴포넌트는 브라우저 테마를 관리합니다. `features/usage/components/token-usage-panel.tsx`는 계정 메뉴·요금제 표시와 설정 모달 연결을 맡습니다. 데이터 사용 설정과 개인 기억 관리는 각각 `features/network/`, `features/memory/`에 있습니다.

상단 작업 메뉴·입력창 아래 문맥 메뉴와 본문의 진행 계획·처리 과정 목록은 표시하지 않습니다. 내부 작업·문맥·진행 기록과 SSE는 유지하며, 다른 대화의 실행 상태 배너에는 `generation-activity.tsx`의 상태 문구를 사용합니다. 생성 중 입력은 가능하고 전송만 차단합니다. 질문 카드는 계속 표시합니다.

## 채팅 UI와 정확한 메시지 이동

| 파일                                                                                                | 책임                                                              |
| --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `features/chat/components/conversation-row-actions.tsx`                                             | 대화 행 메뉴와 제목 변경·고정·보관/복원·삭제 선택                 |
| `features/chat/components/conversation-management-dialog.tsx`                                       | 현재 선택과 독립된 대상 대화 ID의 제목 입력·삭제 확인·비동기 오류 |
| `features/chat/components/conversation-sidebar-surface.tsx`, `features/chat/hooks/sidebar-focus.ts` | 데스크톱/모바일 사이드바 표면, Base UI Dialog·초점·배경 조작·복귀 |
| `features/chat/components/user-message-text.tsx`, `composer-expansion.tsx`                          | 원문을 보존하는 장문 접기와 입력 요소를 유지하는 확대·축소        |
| `features/chat/state/message-versions.ts`, `features/chat/components/message-version-group.tsx`     | 같은 질문의 인접한 답변 그룹·선택·검색 공개와 버전별 표시         |
| `features/chat/components/conversation-search-result.tsx`                                           | 검색 발췌·리터럴 강조와 대화/메시지 이동 대상 전달                |
| `components/content/code-block.tsx`, `code-block.css`                                               | 언어·복사·줄바꿈·색상 토큰의 안전한 React 표시                    |
| `components/content/code-highlight.ts`, `code-highlight-engine.ts`                                  | 지연 로딩·별칭·입력 상한·수명 관리와 lowlight 문법 분석           |

표에서 앞 파일만 경로를 표시한 항목의 뒤 파일은 같은 디렉터리에 있습니다. `ConversationSession`은 검색 표적의 `around` 조회와 `before`·`after` 페이지를 조합하고 `use-chat-scroll.ts`는 정확한 표적과 이전 페이지 앵커를 복원합니다. 검색 API는 권한 있는 대화의 최신 일치 메시지를 최대 282자로 발췌합니다. 답변 그룹은 API의 `user_message_id`를 사용하되 DB 원문·현재 버전·다음 문맥의 정책은 유지합니다. 상세 UI·API 계약과 검증은 [ADR 0021](docs/adr/0021-chat-ui-navigation.md)을 따릅니다.

## 저장소와 삭제 트랜잭션

| 파일                               | 책임                                                                       |
| ---------------------------------- | -------------------------------------------------------------------------- |
| `backend/app/repositories/core.py` | 같은 세션·사용자로 역할별 저장소에 명시적으로 위임하는 `Repository` 진입점 |
| `access.py`                        | 활성 사용자·작업 공간 소속 확인, 대화 접근 범위와 쓰기 잠금 순서           |
| `accounts.py`                      | 사용자·기본 작업 공간 생성과 계정·소속 조회                                |
| `conversations.py`                 | 대화 생성·조회·검색·수정과 삭제 표시                                       |
| `messages.py`                      | 메시지 저장·순번 할당·조회와 완료 상태                                     |
| `validation.py`, `pagination.py`   | 입력 검증과 대화 목록 커서 변환                                            |
| `operations.py`                    | DB 오류 변환, 고정 작업명·결과·소요시간의 공통 기록                        |
| `types.py`, `errors.py`            | 페이지·계정 결과 자료형과 저장소 오류                                      |

표의 짧은 파일명은 `backend/app/repositories/` 기준입니다. 저장소는 호출자가 전달한 세션을 사용하며 직접 커밋하지 않습니다. `Repository`는 동적 속성 전달 대신 기존 조회·저장 메서드를 명시적으로 제공합니다.

대화 삭제 API는 `services/conversations.py`의 `ConversationService`를 호출합니다. 이 서비스는 대화 저장소의 권한·잠금·삭제 표시와 `files/deletion.py`의 문서 삭제 표시·검색 조각 제거를 **같은 트랜잭션**으로 조합합니다. API가 커밋한 뒤 기존 파일 정리 경로에서 원본을 제거합니다. 같은 트랜잭션의 파일 표시·검색 조각 제거가 실패하면 대화 삭제 표시도 롤백하며, 저장소가 파일 구현이나 생성 정산 서비스를 직접 불러오지 않습니다.

## 생성 실행 계약

`backend/app/runtime/contracts.py`의 `GenerationJob`은 실행자가 DB에서 인수한 작업 ID·문맥·옵션·네트워크 정책 등 작업 스냅샷을 명시합니다. `ModelExecution`은 DB·모델 공급자·환경 설정만 전달하며 생성 승인·중단·정산 메서드를 포함하지 않습니다.

`runtime/worker.py`가 압축·검색·로컬 기억/원문·파일 검색·로컬 계산·질문 카드·최종 답변을 조합합니다. 문맥·파일·질문 기능은 필요한 실행 의존성을 받고, 웹검색은 검색 공급자와 네트워크 정책을 별도로 받습니다. `StepService`는 DB·설정만 받아 실제 단계·사용량을 기록합니다. `services/generations/`의 승인·본문 저장·정산·영속 이벤트 책임은 유지합니다.

`tools/calculation/evaluator.py`는 DB·모델·파일·네트워크에 접근하지 않는 제한 AST 해석기입니다. `planning.py`가 현재 질문의 의도·수치 근거·완전한 코드 원문을 검증하고, `service.py`가 산술 계획 또는 코드 직접 추적을 기존 단계·후정산·취소·권한·최종 문맥 예산에 연결합니다. Python 원문 직접 추적은 계획 모델 호출을 생략하며 최종 답변의 일반 모드·생각하기 선택은 유지합니다. 계산된 식과 질문 조건의 의미 일치는 구분합니다. [ADR 0023](docs/adr/0023-local-calculation-verification.md)에 지원 문법·자원 한도·실패 정책을 기록합니다.

`evaluation/calculation.py`는 같은 판단·해석·참고 지침을 합성 평가에서 재사용하고 준비 사용량을 최종 답변과 분리합니다. `scripts/evaluate_answers.py run`은 기본으로 이 경로를 사용하며 `--no-local-calculation`으로 비교용 비활성화를 명시합니다. 독립 평가는 사용자 DB·계정 정산을 사용하지 않습니다.

## 파일·언어·DB

`backend/app/files/`는 로컬 원본 저장, 격리 파싱, MLX 임베딩, 권한에 따른 검색과 문맥 반영을 담당합니다. PDF·TXT·Markdown·CSV·JSON을 지원하며 파일 출처·상태·삭제 정책은 [ADR 0017](docs/adr/0017-local-file-rag.md)을 따릅니다. 로컬 모델 설치와 검색 품질 비교는 `scripts/setup_rag.py`, `scripts/evaluate_file_rag.py`에서 수행합니다.

`context/language.py`와 공급자 공통 입력 변환은 현재 질문·입력 언어를 따르고 명시한 번역 언어를 해당 답변에 우선 적용합니다. 저장 원문·외부 검색 후보를 바꾸지 않으며 사용자별 언어 선택은 없습니다. [ADR 0018](docs/adr/0018-answer-language-policy.md), [합성 모델 평가](docs/evaluations/answer-language-smoke.json)를 참고합니다.

코드와 실제 개발 DB의 migration head는 **`0017_schema_roles`**이며 **도메인·관측 테이블 21개**와 `alembic_version`을 사용합니다. 실제 DB 적용 전후 보존 대상의 모든 행 해시가 일치하며 최종 검사 상태는 [개발 현황](docs/development-status.md#최근-db-역할-정리)을 따릅니다. `models/__init__.py`가 전체 모델을 Alembic 비교 대상에 등록합니다.

`documents`가 대화별 파일과 활성 여부의 기준이며 중복 연결 테이블 `attachments`는 제거했습니다. `document_versions`는 문서당 하나만 허용하고 처리 상태·원본 해시·파서·임베딩·스캐너 이력을, `chunks`는 검색 본문·벡터·근거 위치를 저장합니다. 요청의 MIME 검증은 유지하고 확장자로 복원할 수 있는 `media_type` 저장만 제거했습니다.

미사용 `conversations.settings`와 `messages.prompt_version`을 제거했습니다. 생성의 내부 옵션은 `thinking`과 기존 `max_output_tokens`로 저장하며 API의 `options` 형식은 유지합니다. 생성의 메시지는 같은 대화, 정산 기록은 같은 사용자, 압축 요약의 생성은 같은 대화를 참조하도록 복합 외래 키로 보강했습니다. 이를 위한 복합 unique 3개는 참조 무결성에 필요한 인덱스입니다.

계정·작업 공간·메시지의 세 가지 역할, 발급 당시 예산·사용 누계·요청별 허용 상한, 압축 요약 버전·사용량, 모델·파서·스캐너 이력, 원문·출처·기억/문서 의존성은 각각의 책임을 유지합니다. 테이블별 역할과 데이터 보존·되돌리기 조건은 [ADR 0020](docs/adr/0020-schema-roles.md)을 따릅니다. API·환경 설정은 변경하지 않으며 이 정리에서 새 개발 서버를 시작하지 않습니다.

## 검사와 문서

- `tests/conversation-sidebar.test.mjs`, `chat-message-layout.test.mjs`, `code-block.test.mjs`, `chat-search-navigation.test.mjs`: 행 관리·초점 수명, 장문·입력·답변 버전, 코드 색상·줄바꿈·원문, 검색 위치·앞뒤 페이지 연결을 검사합니다.
- `backend/tests/test_conversation_search_navigation.py`: 검색 발췌·리터럴·권한과 around/before/after 커서·범위·순서를 실제 격리 PostgreSQL에서 검사합니다.

- `tests/chat-store.test.mjs`, `chat-activity.test.mjs`, `chat-interactions.test.mjs`, `file-rag.test.mjs`: 대화·초안·생성 재생·질문·첨부 상태의 공개 동작을 검사합니다.
- `tests/chat-workbench-hooks.test.mjs`, `chat-scroll.test.mjs`, `chat-render.test.mjs`: 초기 SSR, 브라우저 이벤트·타이머·도구 정리, 스크롤과 화면 연결을 검사합니다.
- `backend/tests/test_repositories.py`, `test_conversation_deletion.py`: 권한·목록·메시지·대화와 첨부의 원자적 삭제를 검사합니다.
- `backend/tests/test_generations.py`와 문맥·검색·파일·질문·worker 검사: 실행 계약 분리 후 승인·중단·문맥·실제 사용량 정산을 확인합니다.
- `backend/tests/test_local_calculation_evaluator.py`, `test_local_calculation.py`, `test_quality_calculation.py`: 제한 AST의 산술·Python 의미·자원 및 접근 차단, 준비 단계·취소·사용량·최종 문맥, 독립 평가의 계산 기록을 검사합니다.
- `scripts/test_db.py`: 임시 PostgreSQL에서 migration 왕복·스키마 비교·백엔드 검사를 실행한 뒤 임시 리소스를 정리합니다.

최종 검사 결과는 [개발 현황](docs/development-status.md)에 기록합니다. 실행 명령과 운영 절차는 [run.md](run.md), 과거 기능별 결정은 `docs/adr/`의 당시 기록을 유지합니다.
