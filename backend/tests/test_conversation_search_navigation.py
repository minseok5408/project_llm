"""실제 PostgreSQL API의 검색 발췌·메시지 이동과 접근 범위를 검증한다."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from backend.app.models import Workspace, WorkspaceMember
from backend.app.repositories import Repository
from backend.tests.test_conversation_api import (
    conversation_client as conversation_client,
)
from backend.tests.test_conversation_api import (
    create_account,
    create_conversation,
    sign_in,
)

pytestmark = pytest.mark.postgres


async def message_conversation(client, database, account, contents, *, title="검색 이동 테스트"):
    conversation = await create_conversation(client, account, title=title)
    records = []
    async with database.session() as session:
        repository = Repository(session, account.user_id)
        for index, content in enumerate(contents):
            message = await repository.append_message(
                account.workspace_id,
                UUID(conversation["id"]),
                role="user" if index % 2 == 0 else "assistant",
                content=content,
            )
            records.append(
                {
                    "id": str(message.id),
                    "sequence": message.sequence,
                    "role": message.role,
                    "content": content,
                }
            )
        await session.commit()
    return conversation, records


async def search(client, account, query=None, **values):
    parameters = {"workspace_id": str(account.workspace_id), **values}
    if query is not None:
        parameters["q"] = query
    return await client.get("/api/v1/conversations", params=parameters)


async def message_page(client, conversation, **parameters):
    response = await client.get(
        f"/api/v1/conversations/{conversation['id']}/messages", params=parameters
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    sequences = [item["sequence"] for item in payload["items"]]
    assert sequences == sorted(set(sequences))
    assert len(sequences) <= parameters.get("limit", 50)
    assert all(item["conversation_id"] == conversation["id"] for item in payload["items"])
    return payload


async def test_search_uses_latest_matching_message_and_title_only_has_no_match(
    conversation_client, schema_database
):
    client, app, account = conversation_client
    matched, records = await message_conversation(
        client,
        schema_database,
        account,
        ["탐색어 첫 일치", "다른 내용", "탐색어 마지막 일치", "가장 최신이지만 무관한 내용"],
        title="본문으로 찾는 대화",
    )
    title_only, _ = await message_conversation(
        client, schema_database, account, ["제목에만 포함된 단어"], title="탐색어 제목"
    )
    await create_conversation(client, account, title="무관한 대화")

    response = await search(client, account, "  탐색어  ")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = {item["id"]: item for item in response.json()["items"]}
    assert set(items) == {matched["id"], title_only["id"]}
    assert items[title_only["id"]]["search_match"] is None
    assert items[matched["id"]]["search_match"] == {
        "message_id": records[2]["id"],
        "sequence": 3,
        "role": "user",
        "snippet": records[2]["content"],
    }
    baseline = (await search(client, account)).json()
    assert all("search_match" not in item for item in baseline["items"])
    for query in ("", " \n\t "):
        assert (await search(client, account, query)).json() == baseline
    assert app.state.database.engine.pool.checkedout() == 0


async def test_search_percent_underscore_and_html_are_literal_message_text(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    text = '<script>alert("검색")</script> 할인율 100%_값 <img src=x onerror="alert(1)">'
    matched, records = await message_conversation(client, schema_database, account, [text])
    await message_conversation(
        client, schema_database, account, ["할인율 100ab값 다른 일반 내용"], title="일반 대화"
    )
    for query in ("%", "_", "%_", "<script>"):
        response = await search(client, account, query)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["cache-control"] == "no-store"
        assert [item["id"] for item in response.json()["items"]] == [matched["id"]]
        match = response.json()["items"][0]["search_match"]
        assert match["message_id"] == records[0]["id"]
        assert match["snippet"] == text
        assert "&lt;script&gt;" not in match["snippet"]


async def test_search_200_character_query_stays_inside_bounded_match_excerpt(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    query = "자료" * 100
    text = "앞" * 500 + query + "뒤" * 600
    conversation, records = await message_conversation(
        client, schema_database, account, ["먼저 쓴 무관한 내용", text]
    )
    response = await search(client, account, f"  {query}  ")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [conversation["id"]]
    match = response.json()["items"][0]["search_match"]
    assert match["message_id"] == records[1]["id"]
    assert match["role"] == "assistant"
    assert match["sequence"] == 2
    assert len(match["snippet"]) <= 282
    assert query in match["snippet"]
    assert match["snippet"].startswith("…") and match["snippet"].endswith("…")
    assert text not in response.text
    invalid = await search(client, account, query + "초")
    assert invalid.status_code == 422
    assert query not in invalid.text


async def test_search_never_discloses_deleted_other_workspace_or_private_messages(
    conversation_client, schema_database
):
    client, app, owner = conversation_client
    visible, _ = await message_conversation(
        client, schema_database, owner, ["공통검색 공개된 내 대화"]
    )
    deleted, _ = await message_conversation(
        client, schema_database, owner, ["공통검색 삭제한원문비밀"], title="삭제제목비밀"
    )
    assert (
        await client.request("DELETE", f"/api/v1/conversations/{deleted['id']}", json={})
    ).status_code == 204
    async with schema_database.session() as session:
        workspace = Workspace(name="같은 계정의 다른 공간", created_by=owner.user_id)
        session.add(workspace)
        await session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.user_id, role="owner"))
        other_workspace = replace(owner, workspace_id=workspace.id)
        await session.commit()
    other, other_records = await message_conversation(
        client,
        schema_database,
        other_workspace,
        ["공통검색 다른공간원문비밀"],
        title="다른공간제목비밀",
    )
    outsider = await create_account(schema_database, app.state.settings)
    sign_in(client, outsider)
    private, private_records = await message_conversation(
        client, schema_database, outsider, ["공통검색 타인원문비밀"], title="타인제목비밀"
    )
    forbidden = await search(client, owner, "공통검색", status="all")
    assert forbidden.status_code == 404
    assert "공개된 내 대화" not in forbidden.text
    sign_in(client, owner)
    result = await search(client, owner, "공통검색", status="all")
    assert result.status_code == 200
    assert [item["id"] for item in result.json()["items"]] == [visible["id"]]
    for secret in (
        deleted["id"],
        "삭제한원문비밀",
        "삭제제목비밀",
        other["id"],
        other_records[0]["id"],
        "다른공간원문비밀",
        "다른공간제목비밀",
        private["id"],
        private_records[0]["id"],
        "타인원문비밀",
        "타인제목비밀",
    ):
        assert secret not in result.text
    assert (await search(client, outsider, "공통검색")).status_code == 404
    assert app.state.database.engine.pool.checkedout() == 0


async def test_around_centers_target_and_reports_both_edges_even_with_limit_one(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    conversation, records = await message_conversation(
        client, schema_database, account, [f"원문 {index}" for index in range(1, 116)]
    )
    for sequence, limit in ((60, 50), (1, 50), (115, 50), (1, 1), (60, 1), (115, 1)):
        target = records[sequence - 1]
        page = await message_page(client, conversation, around=target["id"], limit=limit)
        items = page["items"]
        sequences = [item["sequence"] for item in items]
        assert sequences.count(sequence) == 1
        assert target["id"] == next(item["id"] for item in items if item["sequence"] == sequence)
        assert sequences == list(range(sequences[0], sequences[-1] + 1))
        assert page["next_cursor"] == (sequences[0] if sequences[0] > 1 else None)
        assert page["newer_cursor"] == (sequences[-1] if sequences[-1] < 115 else None)
        if limit == 1:
            assert sequences == [sequence]
        elif sequence == 60:
            assert len(items) == limit
            assert abs((sequence - sequences[0]) - (sequences[-1] - sequence)) <= 1


async def test_before_and_after_traverse_chronologically_without_gaps_or_duplicates(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    conversation, records = await message_conversation(
        client, schema_database, account, [f"페이지 원문 {index}" for index in range(1, 24)]
    )
    center = await message_page(client, conversation, around=records[11]["id"], limit=5)
    merged = center["items"][:]
    cursor = center["next_cursor"]
    while cursor is not None:
        page = await message_page(client, conversation, before=cursor, limit=5)
        assert page["items"] and all(item["sequence"] < cursor for item in page["items"])
        assert page["next_cursor"] == (
            page["items"][0]["sequence"] if page["items"][0]["sequence"] > 1 else None
        )
        assert page["newer_cursor"] == (
            page["items"][-1]["sequence"] if page["items"][-1]["sequence"] < 23 else None
        )
        merged = page["items"] + merged
        cursor = page["next_cursor"]
    cursor = center["newer_cursor"]
    while cursor is not None:
        page = await message_page(client, conversation, after=cursor, limit=5)
        assert page["items"] and all(item["sequence"] > cursor for item in page["items"])
        assert page["next_cursor"] == page["items"][0]["sequence"]
        assert page["newer_cursor"] == (
            page["items"][-1]["sequence"] if page["items"][-1]["sequence"] < 23 else None
        )
        merged += page["items"]
        cursor = page["newer_cursor"]
    assert [item["id"] for item in merged] == [item["id"] for item in records]
    assert [item["sequence"] for item in merged] == list(range(1, 24))
    assert [item["content"] for item in merged] == [item["content"] for item in records]
    newest = await message_page(client, conversation, limit=5)
    assert [item["sequence"] for item in newest["items"]] == [19, 20, 21, 22, 23]
    assert newest["next_cursor"] == 19 and newest["newer_cursor"] is None
    for direction, cursor in (("before", 1), ("after", 23)):
        empty = await message_page(client, conversation, **{direction: cursor}, limit=1)
        assert empty["items"] == []
        assert empty["next_cursor"] is None and empty["newer_cursor"] is None


async def test_message_directions_are_exclusive_and_invalid_identifiers_are_private(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    conversation, records = await message_conversation(
        client, schema_database, account, ["오류 응답에 포함하지 않을 원문"]
    )
    path = f"/api/v1/conversations/{conversation['id']}/messages"
    target = records[0]["id"]
    for parameters in (
        {"before": 2, "after": 1},
        {"before": 2, "around": target},
        {"after": 1, "around": target},
        {"before": 2, "after": 1, "around": target},
        {"around": "식별자가 아닌 비밀값"},
        {"after": 0},
        {"after": 2**63},
        {"around": target, "limit": 0},
        {"around": target, "limit": 101},
    ):
        response = await client.get(path, params=parameters)
        assert response.status_code == 422
        assert "오류 응답에 포함하지 않을 원문" not in response.text
        assert "식별자가 아닌 비밀값" not in response.text
        assert "input" not in response.text
    duplicate = await client.get(path, params=[("around", target), ("around", target)])
    assert duplicate.status_code == 422


async def test_around_rejects_other_conversation_deleted_missing_and_other_account_targets(
    conversation_client, schema_database
):
    client, app, owner = conversation_client
    own, records = await message_conversation(
        client, schema_database, owner, ["타인에게 숨겨야 할 원문"], title="숨겨야 할 제목"
    )
    other, other_records = await message_conversation(client, schema_database, owner, ["다른 대화"])
    path = f"/api/v1/conversations/{own['id']}/messages"
    for target in (other_records[0]["id"], str(uuid4())):
        response = await client.get(path, params={"around": target})
        assert response.status_code == 404
        assert "다른 대화" not in response.text
    for system in (False, True):
        outsider = await create_account(schema_database, app.state.settings, system=system)
        sign_in(client, outsider)
        response = await client.get(path, params={"around": records[0]["id"]})
        assert response.status_code == 404
        assert "숨겨야 할" not in response.text
        outsider_conversation, _ = await message_conversation(
            client, schema_database, outsider, ["현재 계정의 대화"]
        )
        response = await client.get(
            f"/api/v1/conversations/{outsider_conversation['id']}/messages",
            params={"around": records[0]["id"]},
        )
        assert response.status_code == 404
        assert "숨겨야 할" not in response.text
    sign_in(client, owner)
    assert (
        await client.request("DELETE", f"/api/v1/conversations/{own['id']}", json={})
    ).status_code == 204
    assert (await client.get(path, params={"around": records[0]["id"]})).status_code == 404
    other_path = f"/api/v1/conversations/{other['id']}/messages"
    assert (await client.get(other_path, params={"around": records[0]["id"]})).status_code == 404
    assert app.state.database.engine.pool.checkedout() == 0
