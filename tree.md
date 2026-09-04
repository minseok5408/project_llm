# 프로젝트 구조

```text
project_llm/
├── app/                       # React/Vinext 채팅 페이지, 레이아웃, 전역 스타일
├── backend/
│   ├── app/                   # FastAPI API, 설정, 스키마, LLM provider
│   └── tests/                 # Mock provider 기반 API·SSE 테스트
├── components/
│   └── ui/                    # shadcn 기반 재사용 UI 컴포넌트
├── hooks/                     # 프런트엔드 공용 React 훅
├── lib/                       # 프런트엔드 공용 유틸리티
├── public/                    # favicon 등 정적 파일
├── scripts/                   # 로컬 개발 환경 통합 실행 스크립트
├── .openai/                   # Sites와 Cloudflare 빌드 설정
├── .idea/                     # PyCharm 프로젝트 설정
├── .venv/                     # [생성] Python 3.12 가상환경
├── node_modules/              # [생성] npm 패키지
├── dist/                      # [생성] 프로덕션 웹 빌드 결과
├── .next/                     # [생성] Next 호환 타입·빌드 데이터
├── .vinext/                   # [생성] Vinext 개발 데이터
├── .wrangler/                 # [생성] Wrangler 로컬 실행 데이터
├── .pytest_cache/             # [생성] pytest 캐시
├── .ruff_cache/               # [생성] Ruff 캐시
├── qwen_workbench.egg-info/   # [생성] Python editable 설치 메타데이터
├── README.md                  # 프로젝트 개요와 설치 안내
├── run.md                     # 실행·검사 명령 모음
├── todo.md                    # 실제 서비스화를 위한 단계별 개발 로드맵
├── tree.md                    # 프로젝트 폴더와 주요 파일 설명
├── pyproject.toml             # Python 패키지·도구 설정
├── package.json               # 프런트엔드 패키지와 npm 명령
├── package-lock.json          # npm 의존성 잠금 파일
├── .env.example               # 로컬 환경 변수 예시
├── vite.config.ts             # Vinext/Vite/Cloudflare 구성
└── tsconfig.json              # TypeScript 설정
```

## 실행 흐름

```text
브라우저 :3000
    ↓
FastAPI 게이트웨이 :8000
    ↓
OpenAI 호환 MLX-VLM 서버 :8080
    ↓
mlx-community/Qwen3.8-27B-4bit
```

## 소스 폴더

- `app/`: 사용자가 보는 채팅 화면입니다. `page.tsx`가 메시지 상태와 SSE 스트림을 처리하고, `layout.tsx`와 `globals.css`가 공통 레이아웃과 테마를 담당합니다.
- `backend/app/`: 프런트엔드와 모델 사이의 Python 게이트웨이입니다. 요청을 검증하고 MLX 응답을 브라우저용 SSE로 전달합니다.
- `backend/tests/`: 실제 모델을 로드하지 않고 API 상태, 한글 스트리밍, 입력 검증, 오류 처리를 검사합니다.
- `components/ui/`: 버튼, 입력창, 스위치 등 채팅 화면에서 사용하는 UI 기본 컴포넌트입니다.
- `hooks/`: 여러 화면에서 재사용할 수 있는 React 훅을 둡니다.
- `lib/`: 클래스 이름 결합 등 프런트엔드 공용 함수를 둡니다.
- `public/`: 브라우저가 그대로 제공하는 favicon과 정적 자산을 둡니다.
- `scripts/`: 모델 서버, FastAPI, 웹 화면을 함께 시작하고 종료하는 `dev.py`가 있습니다.
- `.openai/`: Sites/Vinext의 로컬 및 배포 빌드 정보를 보관합니다.
- `.idea/`: 프로젝트 인터프리터와 TypeScript 경로 등 PyCharm 설정을 보관합니다.

## 백엔드 주요 파일

- `backend/app/main.py`: `/health`, `/api/status`, `/api/chat` 엔드포인트와 SSE 스트리밍을 정의합니다.
- `backend/app/providers.py`: 실제 MLX 서버와 Mock 서버를 동일한 인터페이스로 연결합니다.
- `backend/app/config.py`: `.env`에서 모델 주소, 컨텍스트 크기, 동시 실행 수 등을 읽습니다.
- `backend/app/schemas.py`: 채팅 메시지와 생성 옵션의 데이터 구조 및 검증 규칙입니다.
- `backend/tests/test_api.py`: FastAPI 게이트웨이의 자동 테스트입니다.

## 자동 생성 폴더

`[생성]`으로 표시한 폴더는 설치, 테스트, 개발 서버 또는 빌드 과정에서 다시 만들어집니다. 애플리케이션 기능을 변경할 때는 이 폴더보다 `app/`, `backend/`, `components/`, `hooks/`, `lib/`, `public/`, `scripts/`를 수정합니다.
