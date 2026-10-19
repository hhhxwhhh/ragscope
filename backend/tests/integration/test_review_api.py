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
) -> dict[str, Any]:
    kb_response = await client.post(
        "/api/v1/kbs",
        json={"name": "抽检测试库", "chunk_size": 400, "chunk_overlap": 60},
        headers=headers,
    )
    assert kb_response.status_code == 201, kb_response.text
    kb_id = UUID(kb_response.json()["id"])
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    now = datetime.now(UTC)

    async with SessionLocal() as db:
        conversation = Conversation(kb_id=kb_id, user_id=UUID(me["id"]), title="续航对比")
        db.add(conversation)
        await db.flush()

        question_one = Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="X1 的续航是多久？",
            status=MessageStatus.COMPLETED,
            created_at=now - timedelta(seconds=4),
        )
        answer_one = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="X1 续航 14 天 [1]。",
            status=MessageStatus.COMPLETED,
            citation_stats={"markers": 1, "invalid_markers": 0, "citation_rate": 1.0},
            latency_ms=800,
            prompt_variant="grounded",
            created_at=now - timedelta(seconds=3),
        )
        question_two = Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="X2 支持哪些表带？",
            status=MessageStatus.COMPLETED,
            created_at=now - timedelta(seconds=2),
        )
        answer_two = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="根据现有资料无法回答这个问题。",
            status=MessageStatus.COMPLETED,
            created_at=now - timedelta(seconds=1),
        )
        db.add_all([question_one, answer_one, question_two, answer_two])
        await db.flush()

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
        db.add(document)
        await db.flush()
        db.add(chunk)
        await db.flush()
        db.add(
            Citation(
                message_id=answer_one.id,
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
            "question_one_id": str(question_one.id),
            "answer_one_id": str(answer_one.id),
            "answer_two_id": str(answer_two.id),
        }


async def test_review_queue_summary_and_filters(client: AsyncClient, register: Any) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)

    queue = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/queue", headers=headers
    )
    assert queue.status_code == 200, queue.text
    body = queue.json()
    assert body["total"] == 2
    # Newest answer first, paired with the user question before it.
    assert body["items"][0]["message_id"] == ids["answer_two_id"]
    assert body["items"][0]["question"] == "X2 支持哪些表带？"
    assert body["items"][1]["question"] == "X1 的续航是多久？"
    assert len(body["items"][1]["citations"]) == 1
    assert body["items"][0]["review"] is None

    summary = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/summary", headers=headers
    )
    assert summary.status_code == 200
    assert summary.json()["total_answers"] == 2
    assert summary.json()["unreviewed"] == 2
    assert summary.json()["coverage"] == 0.0

    reviewed = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/queue",
        params={"status": "all", "q": "续航"},
        headers=headers,
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["total"] == 1
    assert reviewed.json()["items"][0]["message_id"] == ids["answer_one_id"]


async def test_review_upsert_validation_and_coverage(
    client: AsyncClient, register: Any
) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)

    created = await client.put(
        f"/api/v1/messages/{ids['answer_one_id']}/review",
        json={"verdict": "pass"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["verdict"] == "pass"
    assert created.json()["issue_tags"] == []

    updated = await client.put(
        f"/api/v1/messages/{ids['answer_one_id']}/review",
        json={
            "verdict": "fail",
            "issue_tags": ["incomplete", "incomplete", "unsupported_claim"],
            "note": "只回答了续航，没有覆盖充电时间",
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == created.json()["id"]
    assert updated.json()["issue_tags"] == ["incomplete", "unsupported_claim"]

    summary = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/summary", headers=headers
    )
    assert summary.status_code == 200
    assert summary.json()["reviewed"] == 1
    assert summary.json()["unreviewed"] == 1
    assert summary.json()["fail_count"] == 1
    assert summary.json()["coverage"] == 0.5
    assert summary.json()["issue_counts"]["incomplete"] == 1

    unreviewed = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/queue", headers=headers
    )
    assert unreviewed.json()["total"] == 1
    assert unreviewed.json()["items"][0]["message_id"] == ids["answer_two_id"]

    reviewed = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/queue",
        params={"status": "reviewed", "verdict": "fail"},
        headers=headers,
    )
    assert reviewed.json()["total"] == 1
    assert reviewed.json()["items"][0]["review"]["verdict"] == "fail"


async def test_review_rejects_inconsistent_payloads(
    client: AsyncClient, register: Any
) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)

    pass_with_tag = await client.put(
        f"/api/v1/messages/{ids['answer_one_id']}/review",
        json={"verdict": "pass", "issue_tags": ["incomplete"]},
        headers=headers,
    )
    assert pass_with_tag.status_code == 422

    fail_without_tag = await client.put(
        f"/api/v1/messages/{ids['answer_one_id']}/review",
        json={"verdict": "fail"},
        headers=headers,
    )
    assert fail_without_tag.status_code == 422

    user_message = await client.put(
        f"/api/v1/messages/{ids['question_one_id']}/review",
        json={"verdict": "pass"},
        headers=headers,
    )
    assert user_message.status_code == 400


async def test_review_is_scoped_to_owner(client: AsyncClient, register: Any) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)
    _, other_headers = await register()

    on_other_message = await client.put(
        f"/api/v1/messages/{ids['answer_one_id']}/review",
        json={"verdict": "pass"},
        headers=other_headers,
    )
    assert on_other_message.status_code == 404

    other_kb_queue = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/queue", headers=other_headers
    )
    assert other_kb_queue.status_code == 404


async def test_failed_review_becomes_regression_case(
    client: AsyncClient, register: Any
) -> None:
    _, headers = await register()
    ids = await _seed_conversation(client, headers)

    review = await client.put(
        f"/api/v1/messages/{ids['answer_one_id']}/review",
        json={"verdict": "needs_fix", "issue_tags": ["wrong_citation"]},
        headers=headers,
    )
    assert review.status_code == 200, review.text
    review_id = review.json()["id"]

    converted = await client.post(
        f"/api/v1/reviews/{review_id}/eval-case", headers=headers
    )
    assert converted.status_code == 201, converted.text
    body = converted.json()
    assert body["created"] is True
    assert body["dataset_name"] == "人工抽检回归集"

    repeated = await client.post(
        f"/api/v1/reviews/{review_id}/eval-case", headers=headers
    )
    assert repeated.status_code == 201
    assert repeated.json()["case_id"] == body["case_id"]
    assert repeated.json()["created"] is False

    deleted = await client.delete(
        f"/api/v1/messages/{ids['answer_one_id']}/review", headers=headers
    )
    assert deleted.status_code == 204
    summary = await client.get(
        f"/api/v1/kbs/{ids['kb_id']}/reviews/summary", headers=headers
    )
    assert summary.json()["reviewed"] == 0
    assert summary.json()["unreviewed"] == 2
