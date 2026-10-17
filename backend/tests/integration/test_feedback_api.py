from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.db.session import SessionLocal
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.conversation import Conversation
from app.models.document import Document, DocumentStatus
from app.models.message import Message, MessageRole, MessageStatus

pytestmark = pytest.mark.integration


async def _seed_conversation(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    with_citation: bool = True,
) -> dict[str, Any]:
    kb_response = await client.post(
        "/api/v1/kbs",
        json={"name": "反馈测试库", "chunk_size": 400, "chunk_overlap": 60},
        headers=headers,
    )
    assert kb_response.status_code == 201, kb_response.text
    kb_id = UUID(kb_response.json()["id"])
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    now = datetime.now(UTC)

    async with SessionLocal() as db:
        conversation = Conversation(kb_id=kb_id, user_id=UUID(me["id"]), title="X1 续航")
        db.add(conversation)
        await db.flush()

        question = Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="X1 的续航是多久？",
            status=MessageStatus.COMPLETED,
            created_at=now - timedelta(seconds=1),
        )
        answer = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="X1 续航 14 天 [1]。" if with_citation else "根据现有资料无法回答这个问题。",
            status=MessageStatus.COMPLETED,
            created_at=now,
            citation_stats={"markers": 1, "invalid_markers": 0, "citation_rate": 1.0},
        )
        db.add_all([question, answer])
        await db.flush()

        if with_citation:
            document_id = uuid4()
            document = Document(
                id=document_id,
                kb_id=kb_id,
                filename="x1.md",
                ext=".md",
                size_bytes=128,
                sha256=uuid4().hex + uuid4().hex,
                storage_path="test/x1.md",
                status=DocumentStatus.INDEXED,
            )
            chunk = Chunk(
                document_id=document_id,
                kb_id=kb_id,
                chunk_index=0,
                content="X1 在标准使用场景下的续航为 14 天。",
                embedding_text="X1 在标准使用场景下的续航为 14 天。",
                char_start=0,
                char_end=20,
            )
            # Flush the document first: without ORM relationships SQLAlchemy
            # does not infer insert order from the raw foreign key.
            db.add(document)
            await db.flush()
            db.add(chunk)
            await db.flush()
            db.add(
                Citation(
                    message_id=answer.id,
                    chunk_id=chunk.id,
                    document_id=document_id,
                    filename="x1.md",
                    chunk_index=0,
                    rank=1,
                    score=0.91,
                    snippet=chunk.content,
                    cited=True,
                )
            )

        await db.commit()
        return {
            "kb_id": str(kb_id),
            "conversation_id": str(conversation.id),
            "user_message_id": str(question.id),
            "assistant_message_id": str(answer.id),
        }


async def test_feedback_upsert_and_delete(client: AsyncClient, register: Any) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)

    created = await client.put(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback",
        json={"rating": -1, "reason": "incorrect", "comment": "答案过时了"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["rating"] == -1
    assert created.json()["reason"] == "incorrect"

    messages = await client.get(
        f"/api/v1/conversations/{ids['conversation_id']}/messages", headers=headers
    )
    assistant = next(
        item for item in messages.json()["items"] if item["id"] == ids["assistant_message_id"]
    )
    assert assistant["feedback"]["rating"] == -1
    assert assistant["feedback"]["comment"] == "答案过时了"

    updated = await client.put(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback",
        json={"rating": 1},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == created.json()["id"]
    assert updated.json()["rating"] == 1

    deleted = await client.delete(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback", headers=headers
    )
    assert deleted.status_code == 204


async def test_feedback_rejects_invalid_target_and_rating(
    client: AsyncClient, register: Any
) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)

    on_question = await client.put(
        f"/api/v1/messages/{ids['user_message_id']}/feedback",
        json={"rating": 1},
        headers=headers,
    )
    assert on_question.status_code == 400

    invalid_rating = await client.put(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback",
        json={"rating": 0},
        headers=headers,
    )
    assert invalid_rating.status_code == 422


async def test_feedback_of_another_user_is_not_found(
    client: AsyncClient, register: Any
) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)
    _, other_headers = await register()

    response = await client.put(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback",
        json={"rating": 1},
        headers=other_headers,
    )
    assert response.status_code == 404


async def test_negative_feedback_becomes_labelled_eval_case(
    client: AsyncClient, register: Any
) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)
    await client.put(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback",
        json={"rating": -1, "reason": "outdated"},
        headers=headers,
    )

    response = await client.post(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback/eval-case",
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    assert body["dataset_name"] == "用户反馈回归集"

    cases = await client.get(
        f"/api/v1/eval/datasets/{body['dataset_id']}/cases", headers=headers
    )
    assert cases.status_code == 200
    items = cases.json()["items"]
    assert len(items) == 1
    assert items[0]["question"] == "X1 的续航是多久？"
    assert items[0]["answerable"] is True
    assert items[0]["relevant_refs"][0]["chunk_index"] == 0

    again = await client.post(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback/eval-case",
        headers=headers,
    )
    assert again.status_code == 201
    assert again.json()["created"] is False
    assert again.json()["case_id"] == body["case_id"]


async def test_refusal_becomes_unanswerable_case(client: AsyncClient, register: Any) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers, with_citation=False)

    response = await client.post(
        f"/api/v1/messages/{ids['assistant_message_id']}/feedback/eval-case",
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    cases = await client.get(
        f"/api/v1/eval/datasets/{body['dataset_id']}/cases", headers=headers
    )
    case = cases.json()["items"][0]
    assert case["answerable"] is False
    assert case["relevant_refs"] == []
