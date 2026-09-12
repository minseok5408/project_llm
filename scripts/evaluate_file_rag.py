"""실제 저장소 문서에서 한국어 질문의 근거 회수율과 조각·검색 설정을 비교한다."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.files.embeddings import LocalEmbeddings  # noqa: E402
from backend.app.files.indexing import chunk_pages  # noqa: E402
from backend.app.files.policy import EMBEDDING_VERSION  # noqa: E402
from backend.app.files.retrieval import rank  # noqa: E402


async def main():
    fixture = json.loads((ROOT / "backend/tests/fixtures/file_rag_queries.json").read_text())
    documents = [(path, (ROOT / path).read_text()) for path in fixture["sources"]]
    for query in fixture["queries"]:
        assert any(query["evidence"] in content for _, content in documents), query
    model = LocalEmbeddings(ROOT / "models/multilingual-e5-small")
    questions = await model.encode([item["question"] for item in fixture["queries"]], query=True)
    results = []
    for size, overlap in ((600, 80), (900, 120), (1200, 160)):
        start = monotonic()
        rows = []
        for path, content in documents:
            chunks = await model.split(
                chunk_pages([{"page": 1, "text": content}], size=size, overlap=overlap)
            )
            vectors = await model.encode([chunk["content"] for chunk in chunks])
            for chunk, vector in zip(chunks, vectors, strict=True):
                rows.append(
                    (
                        SimpleNamespace(filename=Path(path).name),
                        None,
                        SimpleNamespace(**chunk, embedding=vector),
                    )
                )
        for method, rerank in (
            ("keyword", False),
            ("vector", False),
            ("hybrid", False),
            ("hybrid", True),
        ):
            hits, reciprocal, misses = 0, 0.0, []
            for item, vector in zip(fixture["queries"], questions, strict=True):
                order = rank(item["question"], rows, vector, method=method, rerank=rerank)
                place = next(
                    (
                        i
                        for i, index in enumerate(order[:4], 1)
                        if item["evidence"] in rows[index][2].content
                    ),
                    None,
                )
                hits += bool(place)
                reciprocal += 1 / place if place else 0
                if not place:
                    misses.append(item["question"])
            result = {
                "size": size,
                "overlap": overlap,
                "chunks": len(rows),
                "method": method,
                "lexical_rerank": rerank,
                "hit_at_4": hits / len(questions),
                "mrr_at_4": reciprocal / len(questions),
                "misses": misses,
            }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        print(f"설정 {size}/{overlap} 처리: {monotonic() - start:.2f}초", flush=True)
    output = {
        "embedding_version": EMBEDDING_VERSION,
        "questions": len(questions),
        "sources": {
            path: hashlib.sha256(content.encode()).hexdigest() for path, content in documents
        },
        "results": results,
    }
    destination = ROOT / "docs/evaluations/file-rag-retrieval.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
