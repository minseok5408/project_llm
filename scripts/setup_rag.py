"""로컬 검색용 공개 모델을 고정 커밋으로 한 번만 설치한다. 실행 중에는 다운로드하지 않는다."""

import json
from pathlib import Path

from huggingface_hub import snapshot_download


def main():
    revision = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    destination = Path(__file__).resolve().parents[1] / "models" / "multilingual-e5-small"
    snapshot_download(
        "intfloat/multilingual-e5-small",
        revision=revision,
        local_dir=destination,
        allow_patterns=[
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentencepiece.bpe.model",
            "README.md",
        ],
    )
    (destination / "rag-model.json").write_text(json.dumps({"revision": revision}) + "\n")
    print("로컬 검색 모델 설치 완료:", destination)


if __name__ == "__main__":
    main()
