# 검색 필요성·검색어·근거 답변 평가

새 개발 순서 **2번**의 평가는 검색을 해야 하는지, 어떤 검색어를 보냈는지, 반환된 근거가 질문에 맞는지, 최종 답변이 그 근거를 정확히 인용하는지를 함께 확인한다. 구현 결정은 [ADR 0024](adr/0024-search-quality-and-intent.md), 공통 채점·기준선 계약은 [답변 품질 평가 안내](answer-quality-evaluation.md)를 따른다.

평가셋은 [search-quality-v1.json](../backend/evaluation/fixtures/search-quality-v1.json)이다. 실제 사용자 계정·대화·기억·첨부를 읽지 않으며 서비스 DB와 사용량 장부를 변경하지 않는다. 새 서버를 실행하지 않고 실제 모델 실행은 이미 가동 중인 loopback 모델만 사용한다.

## 사례와 실행 범위

| 사례 분류                                  | 확인할 내용                                                             |
| ------------------------------------------ | ----------------------------------------------------------------------- |
| 번역·요약·문체·단어 뜻·코드·계산·일반 지식 | 검색처럼 보이는 단어가 있어도 제공된 자료만으로 답할 수 있는지          |
| 개인 기억·검색 금지·OFF·로컬 전용          | 과거 개인 정보를 검색하지 않고 외부 요청 차단을 지키는지                |
| 명시적 검색·최신 버전·직책·구매 추천       | 최신성 단어 유무에 관계없이 외부 사실 확인이 필요한지                   |
| 공개 대상 지시어·생략된 후속 질문          | 최근 공개 대상·주제를 현재 조건과 연결하는지                            |
| 개인 정보가 섞인 문맥·모호한 대상          | 과거 표현 전송 범위를 지키고 불명확한 대상을 추측하지 않는지            |
| 오래된 자료·상충 자료·빈 결과·주입 지시    | 조회 시각과 자료 날짜를 구분하고 불확실성·인용·비신뢰 지시를 처리하는지 |

`expected_search`, `query_required`, `query_forbidden`, 채점 기준은 평가자 전용이며 모델에 주지 않는다. 모델은 제품과 같은 후보 선택·원문 검색어 검증·판단 지침·답변 참고 지침을 사용한다. 판단 입력과 최종 답변 입력을 별도로 기록하므로 어느 단계에서 잘못되었는지 확인할 수 있다.

판단 지침은 직책·가격·구매나 여행 추천에 “최신”이라는 단어가 없어도 검색이 필요하다고 명시한다. 답변 지침은 인물·버전·가격 등 변하는 사실을 제공 자료의 직접 근거 범위로 제한하고 학습 지식으로 답을 대체하거나 보충하지 않도록 한다. 합성·가상 자료는 그 한계를 답변에도 유지해야 하며 실제 최신 사실을 확인한 것처럼 제시하거나 확인 불가 안내 뒤에 현실의 답을 추측해 붙이면 충족으로 보지 않는다.

검색어는 대상·조건의 짧은 원문 구절로 만들고 지시·인사 표현은 제외하도록 한다. 공식 사이트의 검색 결과라는 이유만으로 충분한 최신 근거라고 판단하지 않는다. 과거 다운로드 목록·예전 발표일·지원 권장 버전은 최신판의 직접 근거와 구분한다. 검색 결과를 받았다는 성공과 필요한 최신 사실을 확인했다는 성공도 각각 판독한다.

자료가 있는 답변에는 서비스와 평가의 `build_grounded_search_messages()`가 같은 입력을 만든다. 메시지·자료를 복사하고 기존 system 참고 위치에 자료를 넣으며 마지막 사용자 메시지 복사본에 `[Search evidence policy for this turn]`를 붙인다. 이 정책은 원래 요청의 언어를 유지하고 JSON 자료를 비신뢰 데이터로 취급한다. 원본 메시지는 보존하며 `input_messages`와 실제 입력 토큰에는 추가한 정책도 포함된다. 서비스가 자료 수·출력을 줄여도 한도를 맞추지 못하면 검색 자료 미제공 안내로 전환하고 그 안내마저 포함할 수 없으면 생성하지 않는다.

직접적인 대상·시점 근거가 없을 때는 처음부터 확인 불가를 밝혀야 한다. 목록의 첫 항목·가장 큰 버전·`retrieved_at`에서 최신 상태를 추측하거나 먼저 최신이라고 단정한 뒤 주의 문구로 철회하는 답변은 적절한 불확실성 표현이 아니다. 후속 문장에 학습 지식의 버전·추천·사이트·명령을 추가하여 근거 부재를 메우는 오류도 함께 검사한다.

추천은 사용자가 다르게 말하지 않았다면 예산을 상한으로 해석해야 한다. 예산보다 저렴한 제품을 가격이 정확히 일치하지 않는다는 이유로 제외하거나 자료의 대략적 가격대를 확정 가격으로 바꾸면 오류다. 요청한 조건을 인용된 모델명·가격·사양과 대조하고, 사양에 근거한 적합성은 평가임을 밝혀 설명할 수 있다. 자료에 사용자의 용도가 똑같은 문구로 적혀 있지 않다는 이유만으로 실제 사양 근거까지 없다고 해서는 안 된다. 확인되지 않은 사양·상위 구성·가격은 추정하지 않는다.

구매 추천은 별도 개수·비교 범위·상세도 요청이 없다면 예산과 관련 사양의 인용 근거가 가장 분명한 제품 하나를 골라 모델·확인 가격·관련 사양·추천 이유를 짧게 제시한다. 검색 순서만으로 우선하거나 약한 후보로 개수를 채우는지, 일반적인 구매 권장 조건을 개별 제품의 검증된 사양·성능으로 바꾸는지 검사한다. 요청하지 않은 일반 가이드·반복 결론도 덧붙이지 않는다. 조건에 맞는 제품을 확인하지 못하면 부족한 근거를 한 문장으로 밝히고 끝내며 대안·확인 방법을 추가하지 않아야 한다. 사용자가 복수 추천·비교·상세 설명을 요청했으면 그 요청을 우선하므로 단순히 짧다는 이유만으로 좋은 답변으로 채점하지 않는다. 출력 상한을 높인 실행도 `finish_reason=length`이거나 근거 밖 설명이 있으면 실패를 보존하고, 정상 종료와 근거 해석의 정확성을 구분한다.

새 검색 근거가 없는 경우의 조건부 안내는 서비스와 평가에서 공유한다. `self_contained_or_notice`는 사용자 제공 글·고정 수치·검증된 로컬 계산 결과만으로 완결되는 작업과 일상 대화에 정상 답변하도록 한다. 외부 사실 확인이 필요한 부분에는 이번 응답에 요청 정보를 검증할 검색 근거가 없다는 한 가지 의미만 원래 요청의 답변 언어로 한 문장 전달한다. 특정 언어의 완성 문장을 복사하도록 하지 않으며 주제명 반복·추가 문장·설명·예시·버전·사이트명·링크·명령·추천·확인 방법을 덧붙이지 않는다. 혼합 요청의 독립적인 제공 자료 작업은 계속 답할 수 있다. 대상이 불명확한 `no_query`는 `ask_subject`로 구분해 짧은 대상 확인 질문을 요구하고, 검색이 불필요한 `not_needed`에는 검색 실패 안내를 붙이지 않는다. 자료에 없는 대안을 그럴듯하게 제안하는 답변과 로컬로 해결할 작업까지 차단하는 답변을 각각 평가한다.

검색 불가 안내는 시스템 메시지와 마지막 사용자 메시지의 **모델 입력 복사본**에 담긴 서버 확인 메타데이터로 전달한다. 이 블록은 원래 질문과 분리되며 답변 언어는 원래 질문·명시적 언어 요청에서 정한다. 보고서의 `input_messages`에는 이 실제 입력을 보존하지만 원래 질문·fixture·DB 원본 메시지를 수정하지 않는다. 원문 보존과 모델에게 전달한 실행 입력을 구분하여 판독한다.

서비스는 상태 안내를 포함한 실제 입력 토큰을 다시 계산하고 남은 예산·문맥 한도를 검사한다. 안내와 최소 답변 공간을 확보하지 못하면 생성을 거부하며 안내를 뺀 원래 질문으로 계속 생성하지 않는다. 독립 평가는 최종 입력의 모델 문맥 한도를 확인하지만 계정 잔여 예산·단계 한도·후정산 통합은 별도의 worker 검사에서 확인해야 한다.

생성 승인도 검색 후보·검색 금지 안내의 가능성을 기준으로 입력 여유를 고려한다. OFF·로컬 전용·공급자 미설정이라 외부 검색을 못 하더라도 안내 입력 공간은 필요할 수 있다. 일반 계정의 허용 상한은 문맥 한도와 잔여 예산 중 작은 값이며 이 승인은 선예약·선차감이 아니다. 이 예산 경계는 DB 없는 부속 평가의 성과로 대신하지 않는다.

기본 `run`은 `search_scope=synthetic_search`이며 외부 검색 API 대신 고정 응답을 사용한다. `example.org/search-fixture/` URL은 합성 자료의 식별자이며 접속하지 않는다. 합성 실행은 변하지 않는 자료에서 오래된 근거·충돌·주입 지시를 재현하지만 실제 공급자의 검색 품질을 측정하지 않는다.

`run --live-search --case ...`는 `search_scope=live_search`다. `live_eligible=true`인 공개 질문만 선택할 수 있고 `--case` 생략이나 허용되지 않은 사례 선택은 거부한다. 이때 고정 응답을 대체하여 설정된 Tavily 또는 Brave 공급자를 실제 호출한다. 검색어가 필요한 사례만 공급자를 호출하며 OFF·로컬·검색 불필요 사례에는 호출하지 않는다. 원문 페이지 본문을 방문하지 않으므로 제목·요약·URL이 뒷받침하지 않는 주장까지 검증했다고 해석하지 않는다.

독립 평가는 검색 정책과 입력 구성의 품질을 다룬다. 제품의 DB 권한·설정 revision 변경·단계 장부·사용자 후정산·worker 복구는 별도 통합 검사가 필요하다. 로컬 계산, 장기 압축·기억, 파일 파싱·검색 통합도 이 평가의 범위가 아니다. 이 평가의 모델 호출은 도구 호출을 지원해야 하며 제품의 도구 미지원 단일 검색 호환 경로는 실행하지 않는다.

보고서의 `evaluation_scope`에도 이 경계를 남긴다. 모델의 입력·출력 문맥 상한은 검사하지만 서비스의 계정 잔여 예산에 따른 출처 제외·출력 축소는 재현하지 않는다. 후속 검색이 실패하면 독립 평가는 실행 실패로 기록하는 반면 서비스는 이미 확보한 근거로 답변을 이어갈 수 있다. 독립 평가 결과를 실제 서비스의 전체 실행 결과로 대신하지 않는다.

## 실행 명령

인자 없는 실행과 `validate`는 같으며 모델·DB·외부 검색을 호출하지 않는다. 자료 형식과 입력 구성을 확인해도 품질 상태는 `not_run`이다.

```bash
.venv/bin/python scripts/evaluate_search_quality.py
.venv/bin/python scripts/evaluate_search_quality.py validate \
  --output data/evaluations/search-contract.json
```

실제 로컬 모델과 고정 검색 응답으로 평가하려면 다음 명령을 명시적으로 실행한다. 주소·모델·문맥·출력 상한은 현재 가동 중인 모델 조건에 맞추고, 기존 앱의 답변 생성과 동시에 실행하지 않는다. 모델 주소는 인증 정보·query·fragment 없는 loopback HTTP만 허용한다.

```bash
.venv/bin/python scripts/evaluate_search_quality.py run \
  --base-url http://127.0.0.1:8080/v1 \
  --model mlx-community/Qwen3.8-27B-4bit \
  --context-window 32768 --max-tokens 512 --timeout-seconds 120 \
  --output data/evaluations/search-synthetic.json \
  --review-template data/evaluations/search-synthetic-review.json
```

최종 답변은 기본 일반 모드이며 `--thinking`을 명시해야 생각하기를 켠다. 검색 판단 호출은 제품처럼 생각하기 OFF다. MLX의 도구 호출에만 `temperature=0`, `presence_penalty=0`을 적용하며 일반 답변의 기존 샘플링은 유지한다. `--case`는 반복하여 부분 사례를 선택할 수 있고 `--repeat`로 같은 조건을 반복한다. 부분 실행을 전체 평가셋의 정식 기준선으로 사용하지 않는다.

판단 프롬프트에는 Qwen의 기본 XML 호출 예시와 JSON 배열 형태의 `terms`를 명시한다. 연속된 원문 구절을 그대로 선택하게 하고 떨어진 구절은 별도 항목으로 나눈다. `tool_choice=auto`를 유지해 입력 토큰 계산과 실제 생성 사이에 서버의 강제 도구 지시가 추가되지 않도록 하며, 호출 한 개의 완결성과 확인된 입력·출력 사용량을 검증한다.

실제 검색은 아래처럼 공개 질문을 선택하고 `--live-search`를 함께 지정한다. 이 실행만 로컬 검색 설정을 읽어 공급자·인증을 구성한다. API 키는 기존 로컬 `.env`의 `WEB_SEARCH_API_KEY`에 두며 명령행·보고서에 넣지 않는다. 합성 실행과 기본 검증은 `.env`를 읽어 외부 검색을 활성화하지 않는다.

```bash
.venv/bin/python scripts/evaluate_search_quality.py run \
  --live-search --case latest_python --case current_role \
  --output data/evaluations/search-live-public.json \
  --review-template data/evaluations/search-live-public-review.json
```

명령은 서버를 시작하거나 모델을 내려받지 않는다. 실행 중인 모델이나 검색 인증을 사용할 수 없으면 그 조건은 미실행 또는 실행 실패로 남기고 품질 성공으로 바꾸지 않는다. 기존 출력은 덮어쓰지 않으므로 재실행에는 새 파일명을 사용한다. 보고서는 공통 저장기를 통해 0600 권한으로 저장하며 기본 예시의 `data/evaluations/`는 Git 제외 경로다.

### 제공된 정보로 답하는 부속 평가

[search-self-contained-v1.json](../backend/evaluation/fixtures/search-self-contained-v1.json)은 버전 관리하는 별도 4사례·8개 기준이다. 원본 23사례를 교체하지 않으며 원본의 전체 점수·기준선에 합산하지 않는다. 고정 수치 계산과 번역은 로컬 전용의 조건부 안내, 일상 대화는 검색 OFF의 조건부 안내, 기초 설명은 검색 후보가 아닌 일반 답변 경로를 각각 확인한다. “오늘” 같은 표현이나 검색 근거 부재가 정상적인 로컬 답변을 막는지 판독한다.

```bash
.venv/bin/python scripts/evaluate_search_quality.py validate \
  --dataset backend/evaluation/fixtures/search-self-contained-v1.json \
  --output data/evaluations/search-self-contained-contract.json

.venv/bin/python scripts/evaluate_search_quality.py run \
  --dataset backend/evaluation/fixtures/search-self-contained-v1.json \
  --output data/evaluations/search-self-contained-model.json \
  --review-template data/evaluations/search-self-contained-review.json
```

첫 명령은 무통신 검증이고 둘째 명령은 이미 실행 중인 loopback 모델만 사용한다. 이 부속 자료는 실제 검색에 허용되지 않는다. 산술 결과도 이 평가에서는 검증된 계산 도구 없이 모델이 생성하므로 로컬 계산 검증기·DB·토큰 후정산 통합을 확인했다고 해석하지 않는다. 채점 서식·점수·비교는 아래 공통 명령을 사용하고 부속 평가의 실행 범위를 계속 표시한다.

### 검색 불가 안내의 언어 회귀 평가

[search-fallback-language-v1.json](../backend/evaluation/fixtures/search-fallback-language-v1.json)은 원본과 분리한 7사례·24개 기준이다. 한국어 검색 금지·OFF·로컬·빈 결과 4사례의 기존 질문·검색 설정·품질 기준을 보존하고 필수 `language` 기준을 추가했다. 영어 질문, 한국어 질문의 명시적 영어 요청, 영어 질문의 명시적 스페인어 요청도 포함하며 7사례 모두 응답 언어를 필수로 판독한다. 한국어 문장을 영어 요청에 복사하는 오류와 서버의 영어 지침을 한국어 요청에 복사하는 역방향 오류를 함께 확인한다.

```bash
.venv/bin/python scripts/evaluate_search_quality.py validate \
  --dataset backend/evaluation/fixtures/search-fallback-language-v1.json \
  --output data/evaluations/search-fallback-language-contract.json

.venv/bin/python scripts/evaluate_search_quality.py run \
  --dataset backend/evaluation/fixtures/search-fallback-language-v1.json \
  --output data/evaluations/search-fallback-language-model.json \
  --review-template data/evaluations/search-fallback-language-review.json
```

첫 명령은 무통신 검증이고 둘째 명령은 기존 loopback 모델과 고정 검색 응답을 사용한다. 원본 23사례는 변경하지 않으며 필수 언어 기준을 추가했으므로 과거 품질 점수와 직접 비교하거나 합산하지 않는다. 공통 채점 명령으로 답변 내용과 실제 요청 언어를 함께 판독하고 AI 예비 평가·사람 최종 검토를 구분한다. worker의 정산·SSE 통합 검사 통과만으로 답변 언어까지 성공했다고 판단하지 않는다.

### 로컬에 보존한 실제 검색 자료 재생

실제 Tavily 결과의 오래된 공식 다운로드 목록을 고정한 `data/evaluations/search-recorded-evidence-v1.json`은 별도 1사례 재생 자료다. 공개 검색 요약 원문이 들어 있어 Git 제외 로컬 파일로 보존하며 원본 검색 23개·제공 정보 부속 4개와 분리한다. 파일을 보존한 로컬 환경에서 다음과 같이 사용할 수 있다.

```bash
.venv/bin/python scripts/evaluate_search_quality.py validate \
  --dataset data/evaluations/search-recorded-evidence-v1.json \
  --output data/evaluations/search-recorded-contract.json

.venv/bin/python scripts/evaluate_search_quality.py run \
  --dataset data/evaluations/search-recorded-evidence-v1.json \
  --output data/evaluations/search-recorded-replay.json \
  --review-template data/evaluations/search-recorded-replay-review.json
```

`--live-search`를 사용하지 않으며 외부 공급자를 다시 조회하지 않는다. 실행기는 고정 응답 경로인 `synthetic_search`로 표시하지만 이 자료의 내용은 앞선 실제 조회 응답을 고정한 것이다. 새 조회의 최신성이나 검색 공급자의 결과 개선을 측정하는 실행으로 해석하지 않는다. 같은 오래된 자료에서 최신 단정·단정 후 철회·미검증 버전 보충을 막는지 판독하고 실행 코드·자료 해시·실제 입력·답변을 보존한다. 재생의 통과와 새 실제 검색·실제 worker 통합 검증의 통과는 별도 기록한다.

## 보고서 읽기

| 필드                                                          | 확인할 내용                                                           |
| ------------------------------------------------------------- | --------------------------------------------------------------------- |
| `search_scope`, `configuration.search`                        | 합성/실제 검색 범위, 공급자, 검색 횟수·문자·시간·판단 출력 상한       |
| `configuration.search.planning_thinking`, `planning_sampling` | 최종 답변과 구분한 검색 판단의 생각하기·샘플링 설정                   |
| `evaluation_scope`                                            | 독립 평가에서 확인하는 범위와 서비스 통합 미검증 항목                 |
| `search.policy_gate`, `search.entries`                        | 규칙이 선택한 후보 여부와 로컬 판단에 제공한 제한된 사용자 표현       |
| `search.planner_steps`                                        | 판단별 실제 입력·도구 인자·종료 이유·확정/부분 사용량·지연·실패       |
| `search.searches`                                             | 실제 사용한 검색어와 호출별 공급자·조회 시각·반환된 제목·URL·요약     |
| `search.selected_sources`                                     | 문자 예산에 맞춰 최종 답변에 실제 제공한 근거                         |
| `input_messages`, `answer`, `execution`                       | 최종 모델 입력·답변·종료와 실제 사용량                                |
| `total_confirmed_usage`, `total_elapsed_seconds`              | 검색 판단과 최종 답변의 확인된 모델 사용량 합계 및 준비를 포함한 지연 |
| `diagnostics.search`                                          | 검색 여부, 검색어 단어, 유효 인용 번호의 보조 진단                    |

실행 조건·코드·자료·입력·답변 해시도 공통 보고서에 남긴다. `retrieved_at`·`checked_at`은 검색한 시각이며 자료의 발표일이나 최신성 보증이 아니다. 원본 응답과 최종 채택 자료를 구분하여 문맥에서 제외된 출처를 답변 근거로 세지 않는다.

사용량의 `complete=false`나 실행 실패를 0비용 정상 실행으로 해석하지 않는다. 검색 API 비용은 모델 토큰과 다르며 이 보고서는 서비스 계정 청구를 수행하지 않는다. 모델 메모리와 측정하지 않은 항목은 미측정으로 남긴다. 중단 시 완료된 사례와 보존 가능한 확인 사용량을 남기지만 미완료 답변은 평가 통과가 아니다.

초기 실행에서 관측한 명시적 검색의 잘못된 `no_query`, 원문에 없는 부분문자열 조합, 도구 호출이 없는 응답을 실패 기록으로 보존한다. 이 마지막 경우는 검색 공급자의 빈 검색 결과와 구분한다. 미확인 명령·서비스 제안, 자료의 사실을 학습 지식으로 대체한 답변, 출력 상한으로 잘린 답변도 원래 보고서에 남긴다. 잘림·실행 실패로 전체 채점을 완료하지 못하면 일부 응답의 점수를 전체 완료 점수로 제시하지 않는다. 프롬프트·샘플링 수정 뒤에는 새 보고서로 재검사하며 이전 실패 파일을 덮어쓰거나 성공한 시도만 모아 결과를 제시하지 않는다. 실패한 사례·채점 기준을 수정해야 한다면 이유를 기록하고 변경된 자료로 기준과 후보를 함께 다시 측정한다.

## 채점과 비교

평가 축은 `decision`(필요성·정책), `query`(대상·조건·전송 범위), `grounding`(주장·인용·비신뢰 지시), `freshness`(날짜·최신성·충돌·불확실성)다. 각 사례의 기준별로 0~2점과 관찰 근거를 기록한다. 필수 기준의 0점, 미채점, 실행 실패는 공통 계약대로 구분한다.

검색 여부 일치·단어 포함·인용 번호의 존재만으로 정답 처리하지 않는다. 평가자는 질문과 검색어를 비교하고, 최종 주장별로 실제 제공된 제목·요약·날짜가 그 주장을 뒷받침하는지 읽어야 한다. 예를 들어 오늘 조회한 2020년 자료는 최신 버전의 근거가 아니며, 유효한 `[1]`이 있어도 출처에 없는 가격을 주장하면 인용 정확성이 충족되지 않는다. 자동 진단의 `requires_human_review`는 이 판독이 남았다는 뜻이다.

기존 공통 채점 CLI를 재사용한다. 아래 명령은 저장한 실행 보고서에 결합된 채점 서식을 만들고, 작성한 서식을 검증하여 점수 보고서를 만든다. `run --review-template`을 사용했다면 첫 명령을 다시 실행할 필요가 없다.

```bash
.venv/bin/python scripts/evaluate_search_quality.py review-template \
  --report data/evaluations/search-synthetic.json \
  --output data/evaluations/search-synthetic-review.json

.venv/bin/python scripts/evaluate_search_quality.py score \
  --report data/evaluations/search-synthetic.json \
  --review data/evaluations/search-synthetic-review.json \
  --output data/evaluations/search-synthetic-scored.json

.venv/bin/python scripts/evaluate_search_quality.py compare \
  --candidate data/evaluations/search-candidate-scored.json \
  --baseline data/evaluations/search-baseline-scored.json \
  --output data/evaluations/search-comparison.json
```

`scripts/evaluate_answers.py`의 같은 `review-template`·`score`·`compare` 명령으로도 처리할 수 있다. 사람은 전체 보고서·답변 해시에 결합된 기준별 점수와 근거를 남긴다. AI가 판독하면 평가자와 `review_kind=ai_preliminary`를 명시하여 예비 평가로 보존하고, 사람 최종 검토와 정식 기준선 선정은 별도로 기록한다. AI의 예비 점수를 사람 승인으로 표시하지 않는다.

비교에는 같은 자료·rubric·사례 정의·반복 횟수와 채점 범위를 사용한다. 합성 검색과 실제 검색은 별도 조건이며 서로를 같은 재현 가능한 기준선으로 취급하지 않는다. 실제 검색은 시간에 따라 자료가 바뀌므로 공급자·조회 시각·검색어·반환된 근거의 차이를 먼저 확인한다. 모델·프롬프트·생성 설정의 변경 효과와 검색 자료 변경 효과가 함께 섞이면 하나의 개선 수치로 단정하지 않는다.

초기 목표와 허용 회귀 폭은 공통 평가 정책을 재사용하며 목표 수치는 달성 성능이 아니다. `validate` 성공은 자료 계약 확인, `run` 성공의 `pending_review`는 채점 대기다. 미실행 조건·부분 사례·AI 예비 판독·사람 최종 검토 여부를 실행 결과에 명시하고, 날짜별 측정 기록은 [개발 현황](development-status.md)과 [TODO](../todo.md)에 구분하여 남긴다.

[2026-09-23 개선·검증 결과](evaluations/search-quality-20260923.md)는 초기 전체 실행부터 r14까지 합성 영향 평가·실제 검색·같은 자료 재생·worker·언어 검증의 실패와 개선을 버전별로 보존한다. r11 실제 검색 3통과·1실패와 그 구매 자료의 r14 재생 1통과는 별도 결과다. 최종 소스의 원본 23사례 전체 재실행·사람 최종 검토·정식 운영 기준선은 미완료이며 서로 다른 실행의 점수를 합산하지 않는다.
