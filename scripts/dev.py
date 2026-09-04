#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Qwen Workbench locally")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="run without starting MLX or downloading the model",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    environment = os.environ.copy()
    environment["LLM_BACKEND"] = "mock" if args.mock else "mlx"
    processes: list[subprocess.Popen[bytes]] = []
    stop_requested = False

    commands: list[list[str]] = [
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--reload", "--port", "8000"],
        ["npm", "run", "dev"],
    ]

    if not args.mock:
        model_server = Path(sys.executable).parent / "mlx_vlm.server"
        commands.insert(
            0,
            [
                str(model_server),
                "--model",
                "mlx-community/Qwen3.8-27B-4bit",
                "--host",
                "127.0.0.1",
                "--port",
                "8080",
                "--max-kv-size",
                "32768",
                "--prefill-step-size",
                "512",
                "--max-num-seqs",
                "1",
            ],
        )

    def stop_all() -> None:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()

    def request_stop(*_: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        stop_all()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    mode = "mock" if args.mock else "MLX 4-bit"
    print(f"Starting Qwen Workbench ({mode})", flush=True)
    print("Web: http://localhost:3000 · API: http://127.0.0.1:8000", flush=True)

    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT, env=environment))
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
    finally:
        stop_all()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()

    if stop_requested:
        return 0
    return next((process.returncode or 0 for process in processes if process.returncode), 0)


if __name__ == "__main__":
    raise SystemExit(main())
