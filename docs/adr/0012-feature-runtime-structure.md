# ADR 0012: 기능과 생성 실행 책임에 따른 폴더 구조 분리

- 상태: 결정 채택, 구조 변경·검증 완료
- 날짜: 2026-09-09
- 마이그레이션: 없음. 현재 DB head `0012_network_search` 유지

## 배경과 결정

저장형 채팅·자동 압축·웹검색이 추가되면서 백엔드의 생성 서비스와 프런트의 채팅 화면에 여러 책임이 모였다. 이후 도구 호출, 다단계 사용량 정산, 개인 장기 기억과 RAG를 추가할 때 기존 기능의 소유 위치와 실행 경계를 알 수 있도록 지금 폴더를 분리한다.

이번 변경은 기존 코드의 이동, 책임별 파일 분리, import와 테스트 참조 갱신이다. HTTP API·SSE 이벤트·DB 스키마·저장 데이터·화면 동작과 과금 정책은 유지한다. 디렉터리를 만들었다는 이유로 해당 기능이 구현되었다고 표시하지 않는다. 전체 트리는 [tree.md](../../tree.md), 후속 기능과 진행 상태는 [TODO 최우선 개발 단계](../../todo.md#priority-development)에서 관리한다.

## 백엔드 경계

`api`는 기존 요청·인증 경계를 유지하고, `services`는 영속 대화·생성 같은 업무 흐름을 맡는다. `runtime`은 작업 실행과 공통 중단, `llm`은 모델 호출 규격과 공급자, `context`는 모델 입력과 압축, `tools`는 웹검색 같은 외부 기능을 담당한다. 이번에 분리하는 경로는 다음과 같다. 표의 경로는 `backend/app/` 기준이다.

| 기존 위치                                         | 새 위치와 책임                                                                                                                               |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/generation_worker.py`                   | `runtime/worker.py`: 기존 단일 생성 실행자                                                                                                   |
| `services/compaction_service.py`의 공통 중단 처리 | `runtime/cancellation.py`: 기존 중단 동작을 공유하고 예외 이름을 `GenerationCancelled`로 일반화                                              |
| `providers.py`                                    | `llm/protocol.py`, `llm/registry.py`, `llm/providers/common.py`, `llm/providers/mock.py`, `llm/providers/mlx.py`: 기존 공급자 규격·선택·구현 |
| `services/conversation_context.py`                | `context/builder.py`: 권한 범위의 대화 읽기와 문맥 조립                                                                                      |
| `services/context_compaction.py`                  | `context/compaction.py`: 토큰 계산·최근 원문 선택·요약 입력 구성                                                                             |
| `services/context_policy.py`                      | `context/policy.py`: 중단된 부분 답변의 문맥 표현 정책                                                                                       |
| `services/compaction_service.py`                  | `context/service.py`: 압축 실행·완성된 요약 저장·유지 사용량 기록                                                                            |
| `services/web_search/`                            | `tools/web_search/`: 기존 검색 공급자·자료 정리·정책 적용·출처 저장                                                                          |
| `services/generations.py`                         | `services/generations/{__init__,service,admission,settlement,events}.py`: 기존 생성 서비스의 승인·정산·이벤트 책임 분리                      |

기존 `backend/app/worker.py` CLI 진입점은 유지한다. 생성 서비스의 승인·정산 메서드는 각각 `AdmissionMixin`, `SettlementMixin`으로 이동하되 기존 잠금 순서와 트랜잭션·멱등 정산 의미를 변경하지 않는다. 옛 모듈 경로를 남기는 호환 shim은 만들지 않고 저장소 안의 import와 테스트 대상을 새 위치로 함께 갱신한다.

`runtime`을 만들었다고 다단계 에이전트 실행기가 생기는 것은 아니다. 이번에는 typed tool call 계약, 범용 agent loop, MCP 연결, 개인 장기 기억 구현과 빈 준비용 디렉터리를 추가하지 않는다.

## 프런트엔드 경계

`app/`은 기존 route·page·layout·전역 스타일을 유지한다. 사용자 기능은 `features/`에, 여러 기능이 사용하는 콘텐츠 표시는 `components/content/`에 둔다. 프런트 코드는 프로젝트 루트에 있으며 아래 경로도 프로젝트 루트 기준이다.

| 위치                        | 책임과 포함 파일                                                                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `features/auth/`            | `components/auth-gate.tsx`, `state/auth-session.ts`: 기존 인증 화면과 세션 상태                                                                  |
| `features/chat/components/` | `chat-workbench.tsx`, `chat-message-view.tsx`, `streaming-text.tsx`, `conversation-sidebar.tsx`, `chat-composer.tsx`: 채팅 화면과 목록·입력 표시 |
| `features/chat/state/`      | `chat-store.ts`: 기존 대화·생성 상태                                                                                                             |
| `features/chat/stream/`     | `chat-stream.ts`, `grapheme-typer.ts`: 기존 SSE 처리와 타이핑                                                                                    |
| `features/chat/scroll/`     | `chat-scroll.ts`: 기존 사용자 스크롤 우선 동작                                                                                                   |
| `features/network/`         | `components/network-mode-switch.tsx`, `components/search-sources.tsx`, `state/network-mode-store.ts`, `types.ts`: 모드 설정과 출처 표시          |
| `features/usage/`           | `components/token-usage-panel.tsx`, `types.ts`: 사용량 표시와 타입                                                                               |
| `components/content/`       | `markdown-text.tsx`, `copy-button.tsx`, `markdown-policy.ts`, `clipboard.ts`: 공용 Markdown·안전한 링크·복사 처리                                |

기존 `app/components/`는 제거하고 호환 재내보내기 파일은 남기지 않는다. 사이드바·입력창 등은 기존 JSX와 props를 분리하며 새 상태 저장 방식이나 사용자 기능을 도입하지 않는다. 사용량 조회는 현재 `ChatStore`가 계속 담당하고, 표시와 타입만 `features/usage/`에 둔다. 전역 생성 상태·대화별 초안·앱 내 완료 알림은 별도 후속 구현이다.

## 유지할 정책

제품은 로컬과 사용자가 허용한 같은 Wi-Fi/LAN에서 사용한다. 웹 접속 범위는 유지하며 PostgreSQL·FastAPI·모델 서버는 loopback에 둔다. DB당 단일 생성 실행자와 한 개의 MLX 모델 프로세스를 유지하고, 구조 이동을 이유로 새 서버·외부 배포·원격 미리보기를 추가하지 않는다.

`system`은 서비스 토큰 한도를 면제하되 사용량과 작업 공간 권한을 계속 검사·기록한다. 모델 문맥·메모리·동시성·외부 통신 정책의 면제를 뜻하지 않는다. 일반 사용자는 한국 시간 매월 무료 20,000토큰을 받고 생성 전에 토큰을 예약하거나 차감하지 않는다. 확인된 실제 입력·출력은 답변 완료·중단 후 한 번만 정산한다.

자동 압축은 원문을 보존하고 완성된 요약만 재사용한다. 요약 생성 사용량은 시스템 유지 작업으로 별도 기록하며 사용자 한도에서 차감하지 않는다. 실제 답변에 포함된 요약·검색 자료는 해당 답변의 입력 토큰에 포함한다. 기존 사용자별 로컬 전용 설정과 revision, 검색 실패의 로컬 전환, 중단·권한 철회 처리를 유지한다.

## 후속 기능과 구분

이번에는 아래 기능을 설계 방향과 미완료 TODO로만 남긴다. 각각 API·데이터·정산·권한 계약과 필요한 테스트를 확정한 뒤 개발한다.

- 대화별 초안, 전역 생성 상태와 앱 안의 완료 알림.
- 검증 가능한 도구 호출 자료형, 단계별 실제 사용량 원장, 실행 횟수·시간·토큰·재시도 상한과 각 단계의 권한·로컬 정책 검사.
- 최근 문맥을 반영하되 외부 전송 정보를 최소화하는 검색어 구성과 제한 재검색.
- 압축에서 빠진 정보를 권한 있는 대화 원문에서 제한적으로 회수하는 기능.
- 대화별 압축 요약과 분리된 사용자 소유의 명시적 장기 기억 저장·조회·수정·삭제.
- 컨텍스트 사용량·압축 상태·반영 범위 표시. 사용자 무료 잔여 토큰과 구분한다.
- 위 기반 이후 파일 RAG, 질문 카드, 계획·진행 목록. RAG 원본 저장은 영속 로컬 adapter를 기본으로 하고 S3 호환 저장소는 향후 선택으로 둔다.

구조 참고 소스의 세션 메모나 파일 기반 이력을 그대로 개인 장기 기억으로 가져오지 않는다. 대화 요약은 대화 범위, 개인 기억은 사용자 범위, 검색·업로드 자료는 각각의 접근 권한과 외부 통신 정책에 맞춰 구분해야 한다. 새로운 도구 판단·답변 모델 호출을 기록하더라도 기존 자동 압축의 사용자 비용 면제는 유지한다.

## 검증 결과

2026-09-09 구조 이동과 검증을 완료했다. [TODO](../../todo.md#priority-development)의 이번 구조 작업만 완료로 표시하고, 후속 기능은 미완료로 유지한다.

- 원본을 보존한 후보 트리에서 경로와 공통 중단 예외 이름을 정규화한 함수 본문 848개의 AST 동등성을 확인했다. 새 모듈과 import를 반영한 뒤 검증된 구 모듈을 정리했으며 실행 코드·테스트·스크립트에 옛 경로 참조가 남지 않았다.
- 격리 PostgreSQL에서 migration upgrade·downgrade·upgrade와 schema drift 검사를 통과했다. 인증·권한·정산·중단·worker·압축·검색을 포함한 전체 백엔드 테스트 601개가 191.90초에 통과했다. Python lint와 127개 파일의 format 검사도 통과했다.
- 프런트 상태·SSE·스크롤·타이핑·네트워크 모드·Markdown·SSR 테스트 104개, TypeScript·lint·변경 파일 format 검사와 웹 빌드를 통과했다. 분리한 페이지·입력창·사이드바·사용량 패널의 SSR 회귀를 포함한다.
- 오프라인 문맥 계약 6개 사례와 108턴·3회 압축 검사를 통과했다. 실제 모델의 의미 품질 검증은 이번 작업의 범위에 포함하지 않는다.
- DB schema·API·SSE 계약·사용자 데이터의 변경 없이 `tree.md`·README·실행 문서를 현재 경로와 맞췄다. 기존 API·worker CLI 진입점과 실행 명령은 유지한다.

웹·API·모델 서버나 UI·실제 외부 검색 API는 실행하지 않았다. 자동 테스트는 Mock 공급자와 격리된 임시 PostgreSQL을 사용했고, 테스트 컨테이너와 네트워크는 종료 후 정리했다. 기존 개발 DB와 `.env`는 수정하지 않았다.

## 롤백

DB 변경이 없는 코드 구조 변경이므로 파일 이동·분리와 관련 import·문서·테스트 참조를 같은 변경 단위로 되돌린다. 현재 DB head와 사용자 데이터는 유지한다. 변경 코드를 실행하려면 기존 프로세스의 다음 재시작에 함께 반영하며, 옛 코드와 새 import 경로를 섞어서 실행하지 않는다.
