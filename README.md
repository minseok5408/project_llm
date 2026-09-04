# Qwen Workbench

Apple Silicon에서 `Qwen3.8-27B-4bit`를 실행하는 로컬 채팅 앱입니다. 화면, Python API, MLX 추론 서버를 서로 분리해 이후 다른 모델이나 원격 OpenAI 호환 서버로 교체할 수 있습니다.

## 구성

- Web: Vinext + React, 스트리밍 채팅 UI
- Gateway: Python 3.12 + FastAPI, 입력 검증과 SSE 정규화
- Inference: `mlx-vlm.server`, OpenAI 호환 API
- Default model: `mlx-community/Qwen3.8-27B-4bit`

```text
Browser :3000  →  FastAPI gateway :8000  →  OpenAI-compatible provider :8080
                    validation · SSE          MLX today, replaceable later
```

## 처음 실행

Python 환경과 패키지는 프로젝트의 `.venv`에 설치됩니다.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm install
```

모델 없이 UI와 연결을 빠르게 확인합니다.

```bash
.venv/bin/python scripts/dev.py --mock
```

실제 Qwen 모델로 실행합니다. 첫 실행에는 약 16.1 GB 모델이 Hugging Face 캐시에 다운로드됩니다.

```bash
.venv/bin/python scripts/dev.py
```

브라우저에서 <http://localhost:3000>을 엽니다. 종료는 실행한 터미널에서 `Ctrl+C`를 누릅니다.

## 개별 실행

각 계층을 독립적으로 디버깅하려면 터미널을 세 개 사용합니다.

```bash
.venv/bin/mlx_vlm.server --model mlx-community/Qwen3.8-27B-4bit --host 127.0.0.1 --port 8080 --max-kv-size 32768 --prefill-step-size 512 --max-num-seqs 1
```

```bash
.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8000
```

```bash
npm run dev
```

환경값은 `.env.example`을 `.env`로 복사해 변경할 수 있습니다. 기본 컨텍스트 상한은 이 Mac의 36 GB 통합 메모리에 맞춘 32K이며, 동시 생성은 한 건으로 제한합니다.

## 검증

테스트는 모델 다운로드 없이 mock provider로 실행됩니다.

```bash
.venv/bin/python -m pytest
npm run build
```
