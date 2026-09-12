"""사용자 파일명을 경로에 쓰지 않는 로컬 원본 저장소."""

import os
from pathlib import Path
from uuid import UUID


class LocalFiles:
    def __init__(self, root: Path):
        self.root = root.absolute()

    def path(self, workspace_id: UUID, document_id: UUID, version_id: UUID) -> Path:
        parts = [str(UUID(str(value))) for value in (workspace_id, document_id, version_id)]
        path = self.root.joinpath(*parts)
        # 운영자가 심볼릭 링크를 끼워 넣은 경우에도 다른 파일로 이탈하지 않는다.
        for parent in (self.root, *path.relative_to(self.root).parents):
            candidate = parent if parent.is_absolute() else self.root / parent
            if candidate.is_symlink():
                raise OSError("파일 저장 경로에 심볼릭 링크를 사용할 수 없습니다.")
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise OSError("파일 저장 경로가 올바르지 않습니다.")
        return path

    def write(self, workspace_id: UUID, document_id: UUID, version_id: UUID, data: bytes):
        path = self.path(workspace_id, document_id, version_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def read(self, workspace_id: UUID, document_id: UUID, version_id: UUID) -> bytes:
        path = self.path(workspace_id, document_id, version_id)
        from backend.app.files.policy import MAX_BYTES

        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise OSError("원본 크기 상한을 초과했습니다.")
        return data

    def delete(self, workspace_id: UUID, document_id: UUID, version_id: UUID):
        path = self.path(workspace_id, document_id, version_id)
        path.unlink(missing_ok=True)
        # 비어 있는 문서 디렉터리만 정리하고 공유하는 작업 공간은 유지한다.
        try:
            path.parent.rmdir()
        except OSError:
            pass
