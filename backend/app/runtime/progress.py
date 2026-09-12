"""모델의 추측 대신 실행자가 확정한 단계만 사용자 진행 목록에 기록한다."""

from backend.app.models import GenerationRun


def initial_progress() -> list[dict]:
    return [
        {"id": "context", "name": "context", "status": "pending"},
        {"id": "answer", "name": "answer", "status": "pending"},
    ]


def advance_progress(run: GenerationRun, key: str, status: str, *, name: str | None = None) -> None:
    items = [dict(item) for item in (run.progress or initial_progress())]
    for item in items:
        if item["id"] == key:
            item["status"] = status
            break
    else:
        items.insert(max(0, len(items) - 1), {"id": key, "name": name, "status": status})
    run.progress = items


def finish_progress(run: GenerationRun) -> None:
    items = [dict(item) for item in (run.progress or [])]
    for item in items:
        if item["status"] == "pending":
            item["status"] = "skipped"
        elif item["status"] == "running":
            item["status"] = run.status if run.status in ("completed", "cancelled") else "failed"
    # 사용량은 확정됐어도 저장·검증 실패로 답변이 끝나지 않았다면 완료로 표시하지 않는다.
    if run.status != "completed":
        for item in items:
            if item["id"] == "answer" and item["status"] == "completed":
                item["status"] = "cancelled" if run.status == "cancelled" else "failed"
    run.progress = items
