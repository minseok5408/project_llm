#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAN_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


def parse_lan_ipv4_addresses(interface_output: str) -> list[str]:
    """활성 유선·무선 인터페이스의 사설 IPv4만 추려 가상 네트워크 주소를 제외한다."""
    addresses: set[IPv4Address] = set()
    for block in re.split(r"\n(?=\S)", interface_output.strip()):
        header = block.partition("\n")[0]
        interface = re.match(r"([A-Za-z0-9_.-]+)(?::|\s)", header)
        if interface is None or not interface[1].startswith(("en", "eth", "wl")):
            continue
        if not re.search(r"\bUP\b", header) or not re.search(r"\bRUNNING\b", header):
            continue
        if re.search(r"\bstatus:\s*inactive\b", block):
            continue
        for candidate in re.findall(r"\binet(?:\s+| addr:)([0-9.]+)", block):
            try:
                address = IPv4Address(candidate)
            except ValueError:
                continue
            if any(address in network for network in LAN_NETWORKS):
                addresses.add(address)
    return [str(address) for address in sorted(addresses)]


def get_lan_ipv4_addresses() -> list[str]:
    """외부 접속이나 DNS 조회 없이 시작 시점의 로컬 인터페이스를 읽는다."""
    command = shutil.which("ifconfig")
    if command is None and Path("/sbin/ifconfig").is_file():
        command = "/sbin/ifconfig"
    if command is None:
        return []
    try:
        result = subprocess.run([command], capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_lan_ipv4_addresses(result.stdout)


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
    environment["GENERATION_WORKER_ENABLED"] = "false"
    processes: list[subprocess.Popen[bytes]] = []
    stop_requested = False

    commands: list[list[str]] = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--reload",
            "--reload-dir",
            "backend/app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        [sys.executable, "-m", "backend.app.worker"],
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
                try:
                    # uvicorn/npm의 자식까지 종료하되 worker 정산 전에는 모델을 닫지 않는다.
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=15)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)

    def request_stop(*_: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    mode = "mock" if args.mock else "MLX 4-bit"
    print(f"Starting Qwen Workbench ({mode})", flush=True)
    print("이 Mac: http://localhost:3000 · API(로컬 전용): http://127.0.0.1:8000", flush=True)
    print("답변 생성: 독립 worker · API 코드 재시작 중에도 생성 유지", flush=True)
    lan_addresses = get_lan_ipv4_addresses()
    if lan_addresses:
        for address in lan_addresses:
            print(f"같은 Wi-Fi: http://{address}:3000", flush=True)
    else:
        print(
            "LAN 주소를 찾지 못했습니다. 시스템 설정의 Wi-Fi IP로 http://<IP>:3000에 접속하세요.",
            flush=True,
        )

    try:
        for command in commands:
            if stop_requested:
                break
            processes.append(
                subprocess.Popen(command, cwd=ROOT, env=environment, start_new_session=True)
            )
        while not stop_requested and all(process.poll() is None for process in processes):
            time.sleep(0.5)
    finally:
        stop_all()

    if stop_requested:
        return 0
    return next((process.returncode or 0 for process in processes if process.returncode), 0)


if __name__ == "__main__":
    raise SystemExit(main())
