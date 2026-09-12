"""명시적으로 설치한 고정 버전 모델만 읽는 오프라인 다국어 임베딩."""

import asyncio
import json
import math
from pathlib import Path
from threading import Lock

from backend.app.files.parser import FileProcessingError
from backend.app.files.policy import EMBEDDING_REVISION


class LocalEmbeddings:
    def __init__(self, path: Path):
        self.path, self.model, self.tokenizer = path.resolve(), None, None
        self.lock = Lock()

    def _encode(self, texts: list[str], query: bool) -> list[list[float]]:
        with self.lock:
            if self.model is None:
                try:
                    metadata = json.loads((self.path / "rag-model.json").read_text())
                    config = json.loads((self.path / "config.json").read_text())
                    if metadata["revision"] != EMBEDDING_REVISION or (
                        config["model_type"] != "bert" or config["hidden_size"] != 384
                    ):
                        raise ValueError
                    import mlx.core as mx
                    from mlx_embeddings.utils import load

                    self.model, self.tokenizer = load(
                        str(self.path),
                        tokenizer_config={"local_files_only": True, "trust_remote_code": False},
                    )
                    mx.eval(self.model.parameters())
                except Exception:
                    raise FileProcessingError(
                        "embedding_model_unavailable", retryable=True
                    ) from None
            import mlx.core as mx

            results = []
            # 한 번에 최대 8개 조각만 GPU에 올려 답변 모델과 메모리 여유를 공유한다.
            for start in range(0, len(texts), 8):
                batch = self.tokenizer(
                    [
                        ("query: " if query else "passage: ") + value
                        for value in texts[start : start + 8]
                    ],
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="np",
                )
                output = self.model(
                    input_ids=mx.array(batch["input_ids"]),
                    attention_mask=mx.array(batch["attention_mask"]),
                ).text_embeds
                mx.eval(output)
                results.extend(output.tolist())
            if any(len(row) != 384 or not all(math.isfinite(x) for x in row) for row in results):
                raise FileProcessingError("embedding_invalid")
            return results

    def _split(self, chunks: list[dict]) -> list[dict]:
        self._encode([], False)
        result = []
        with self.lock:
            for chunk in chunks:
                value, offset = chunk["content"], 0
                while offset < len(value):
                    end = len(value)
                    if len(self.tokenizer.encode("passage: " + value[offset:end])) > 500:
                        low, high = offset + 1, end
                        while low < high:
                            middle = (low + high + 1) // 2
                            if (
                                len(self.tokenizer.encode("passage: " + value[offset:middle]))
                                <= 500
                            ):
                                low = middle
                            else:
                                high = middle - 1
                        end = low
                    result.append(
                        {
                            **chunk,
                            "ordinal": len(result) + 1,
                            "start_char": chunk["start_char"] + offset,
                            "end_char": chunk["start_char"] + end,
                            "content": value[offset:end],
                        }
                    )
                    if end == len(value):
                        break
                    offset = max(offset + 1, end - 60)
        if len(result) > 256:
            raise FileProcessingError("chunk_limit")
        return result

    async def split(self, chunks: list[dict]) -> list[dict]:
        task = asyncio.create_task(asyncio.to_thread(self._split, chunks))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        # 취소한 스레드의 GPU 연산이 다음 생성과 겹치지 않게 종료까지 회수한다.
        task = asyncio.create_task(asyncio.to_thread(self._encode, texts, query))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise
