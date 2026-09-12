"""읽기 경로와 외부 통신을 차단한 macOS 파서 프로세스를 제한 시간 안에 실행한다."""

import asyncio
import json
import sys
from pathlib import Path

from backend.app.files.policy import MAX_TEXT_CHARS


class FileProcessingError(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def sandbox_profile() -> str:
    script = Path(__file__).with_name("parser_worker.py").resolve()
    paths = [
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        Path("/System/Library"),
        Path("/usr/lib"),
        Path("/usr/share/locale"),
    ]
    read_rules = " ".join(f"(subpath {json.dumps(str(path))})" for path in paths)
    return (
        "(version 1)(deny default)(allow process-exec)(allow sysctl-read)"
        "(allow file-read-metadata)"
        f"(allow file-read* {read_rules} (literal {json.dumps(str(script))}) "
        '(literal "/") (literal "/dev/urandom") (literal "/dev/null"))'
    )


async def isolated_parse(data: bytes, extension: str) -> list[dict]:
    if sys.platform != "darwin" or not await asyncio.to_thread(
        Path("/usr/bin/sandbox-exec").exists
    ):
        raise FileProcessingError("parser_sandbox_unavailable")
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/sandbox-exec",
        "-p",
        sandbox_profile(),
        sys.executable,
        "-I",
        str(Path(__file__).with_name("parser_worker.py").resolve()),
        extension,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={"LANG": "en_US.UTF-8"},
        cwd="/private/tmp",
    )

    async def exchange():
        async def write():
            try:
                process.stdin.write(data)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        writer = asyncio.create_task(write())
        try:
            limit = MAX_TEXT_CHARS * 6 + 20_000
            output = await process.stdout.readexactly(limit + 1)
            raise FileProcessingError("parser_output_limit")
        except asyncio.IncompleteReadError as result:
            output = result.partial
        finally:
            if not writer.done():
                writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
        await process.wait()
        try:
            payload = json.loads(output)
            if "error" in payload:
                raise FileProcessingError(payload["error"])
            if process.returncode != 0 or not isinstance(payload["pages"], list):
                raise ValueError
            return payload["pages"]
        except (KeyError, TypeError, ValueError):
            raise FileProcessingError("parser_failed") from None

    async def watch_memory():
        while process.returncode is None:
            probe = await asyncio.create_subprocess_exec(
                "/bin/ps",
                "-o",
                "rss=",
                "-p",
                str(process.pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await probe.communicate()
            if output.strip().isdigit() and int(output) > 768 * 1024:
                process.kill()
                raise FileProcessingError("parser_memory_limit")
            await asyncio.sleep(0.5)

    monitor = asyncio.create_task(watch_memory())
    exchange_task = asyncio.create_task(exchange())
    try:
        done, _ = await asyncio.wait(
            (monitor, exchange_task), timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        if monitor in done:
            monitor.result()
        if exchange_task in done:
            return exchange_task.result()
        raise TimeoutError
    except TimeoutError:
        raise FileProcessingError("parser_timeout") from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        monitor.cancel()
        exchange_task.cancel()
        await asyncio.gather(monitor, exchange_task, return_exceptions=True)
