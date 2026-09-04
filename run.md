# 실행 명령어

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

```bash
cp .env.example .env
```

## 실제 Qwen 모델로 전체 실행

```bash
.venv/bin/python scripts/dev.py
```

## 모델 없이 Mock으로 전체 실행

```bash
.venv/bin/python scripts/dev.py --mock
```

## 계층별 개별 실행

### 터미널 1: MLX 모델 서버

```bash
.venv/bin/mlx_vlm.server --model mlx-community/Qwen3.8-27B-4bit --host 127.0.0.1 --port 8080 --max-kv-size 32768 --prefill-step-size 512 --max-num-seqs 1
```

### 터미널 2: FastAPI 게이트웨이

```bash
.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8000
```

### 터미널 3: 채팅 화면

```bash
npm run dev
```

## 검사

```bash
npm run test:backend
npm run lint:python
npm run lint
npm run build
```

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
