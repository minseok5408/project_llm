"""개발 서버 안내의 로컬 IPv4와 불필요한 재시작을 막는 감시 범위를 검사한다."""

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from uvicorn import Config

from scripts import dev
from scripts.dev import parse_lan_ipv4_addresses


def test_lan_addresses_exclude_loopback_inactive_and_virtual_interfaces() -> None:
    interfaces = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
    inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    inet 192.168.0.76 netmask 0xffffff00 broadcast 192.168.0.255
    inet6 fe80::1%en0 prefixlen 64
    status: active
en1: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    inet 10.0.0.4 netmask 0xffffff00
    status: inactive
bridge100: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    inet 192.168.64.1 netmask 0xffffff00
    status: active
utun0: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
    inet 10.8.0.2 netmask 0xffffff00
"""
    assert parse_lan_ipv4_addresses(interfaces) == ["192.168.0.76"]


def test_lan_addresses_follow_current_output_and_ignore_non_private_ipv4() -> None:
    interfaces = """en0: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500
    inet 192.168.1.42 netmask 0xffffff00
    inet 192.168.1.42 netmask 0xffffff00
    status: active
en2: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500
    inet 169.254.1.2 netmask 0xffff0000
    inet 203.0.113.8 netmask 0xffffff00
    status: active
en3: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500
    inet 172.16.3.8 netmask 0xffffff00
    status: active
"""
    assert parse_lan_ipv4_addresses(interfaces) == ["172.16.3.8", "192.168.1.42"]
    assert parse_lan_ipv4_addresses("") == []


@pytest.mark.parametrize("mock", [False, True])
def test_dev_reloads_only_application_code(monkeypatch, mock: bool) -> None:
    commands = []

    def capture_process(command, **kwargs):
        assert kwargs["cwd"] == dev.ROOT
        assert kwargs["start_new_session"]
        assert kwargs["env"]["GENERATION_WORKER_ENABLED"] == "false"
        commands.append(command)
        return SimpleNamespace(returncode=0, poll=lambda: 0, wait=lambda **_: 0)

    monkeypatch.setattr(dev, "parse_args", lambda: Namespace(mock=mock))
    monkeypatch.setattr(dev, "get_lan_ipv4_addresses", lambda: [])
    monkeypatch.setattr(dev.signal, "signal", lambda *_: None)
    monkeypatch.setattr(dev.subprocess, "Popen", capture_process)
    monkeypatch.chdir(dev.ROOT)

    assert dev.main() == 0
    command = next(command for command in commands if "uvicorn" in command)
    assert "--reload" in command
    config = Config(
        "backend.app.main:app",
        reload=True,
        reload_dirs=[command[command.index("--reload-dir") + 1]],
    )

    def watched(relative_path: str) -> bool:
        path = dev.ROOT / relative_path
        return any(path.is_relative_to(Path(directory)) for directory in config.reload_dirs)

    assert watched("backend/app/services/generations.py")
    assert not watched("backend/tests/test_generations.py")
    assert not watched("scripts/test_db.py")
    assert not watched("backend/migrations/env.py")
    worker = next(command for command in commands if "backend.app.worker" in command)
    assert worker == [dev.sys.executable, "-m", "backend.app.worker"]
    assert "--reload" not in worker


def test_dev_shutdown_waits_for_worker_before_stopping_model(monkeypatch) -> None:
    stopped = []
    processes = {}
    handlers = {}

    class Process:
        def __init__(self, command, **kwargs):
            self.pid = len(processes) + 100
            self.command, self.returncode = command, None
            processes[self.pid] = self

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            assert self.returncode == 0
            stopped.append(self.command)
            return 0

    def terminate(pid, sent_signal):
        assert sent_signal == dev.signal.SIGTERM
        processes[pid].returncode = 0

    monkeypatch.setattr(dev, "parse_args", lambda: Namespace(mock=False))
    monkeypatch.setattr(dev, "get_lan_ipv4_addresses", lambda: [])
    monkeypatch.setattr(
        dev.signal, "signal", lambda item, handler: handlers.update({item: handler})
    )
    monkeypatch.setattr(dev.subprocess, "Popen", Process)
    monkeypatch.setattr(dev.os, "killpg", terminate)
    monkeypatch.setattr(dev.time, "sleep", lambda _: handlers[dev.signal.SIGINT]())

    assert dev.main() == 0
    assert "npm" in stopped[0]
    assert "backend.app.worker" in stopped[1]
    assert "uvicorn" in stopped[2]
    assert stopped[3][0].endswith("mlx_vlm.server")
