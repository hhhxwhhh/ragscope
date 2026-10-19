import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.services.eval_service as eval_service
from app.db.session import SessionLocal
from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.models.evaluation import EvalDataset, EvalRun, EvalRunStatus
from app.models.knowledge_base import KnowledgeBase

pytestmark = pytest.mark.integration

KBS = "/api/v1/kbs"
EVAL = "/api/v1/eval"
RUNNING_STATUSES = {"pending", "running"}


@pytest.fixture
async def retrieval_fixture(client: AsyncClient, register: Any) -> dict[str, Any]:
    """A KB with one indexed document, two chunks and an empty dataset."""
    _, headers = await register()

    created = await client.post(KBS, json={"name": "评测库"}, headers=headers)
    assert created.status_code == 201, created.text
    kb_id = UUID(created.json()["id"])

    async with SessionLocal() as db:
        document = Document(
            kb_id=kb_id,
            filename="handbook.md",
            ext="md",
            size_bytes=128,
            sha256=uuid4().hex,
            storage_path="tests/handbook.md",
            status=DocumentStatus.INDEXED,
            chunk_count=2,
            indexed_at=datetime.now(UTC),
        )
        db.add(document)
        await db.flush()
        db.add_all(
            [
                Chunk(
                    document_id=document.id,
                    kb_id=kb_id,
                    chunk_index=0,
                    content="X1 在标准环境下续航十二小时。",
                    embedding_text="续航\nX1 在标准环境下续航十二小时。",
                    heading_path="续航",
                    char_start=0,
                    char_end=14,
                ),
                Chunk(
                    document_id=document.id,
                    kb_id=kb_id,
                    chunk_index=1,
                    content="X2 支持 65W 快充。",
                    embedding_text="充电\nX2 支持 65W 快充。",
                    heading_path="充电",
                    char_start=14,
                    char_end=26,
                ),
            ]
        )
        await db.commit()
        document_id = document.id

    dataset = await client.post(
        f"{KBS}/{kb_id}/eval/datasets",
        json={"name": "基础数据集", "description": "两条用例"},
        headers=headers,
    )
    assert dataset.status_code == 201, dataset.text

    return {
        "kb_id": str(kb_id),
        "document_id": str(document_id),
        "headers": headers,
        "dataset_id": dataset.json()["id"],
    }


async def _add_case(
    client: AsyncClient,
    fixture: dict[str, Any],
    question: str,
    chunk_index: int | None,
    answerable: bool = True,
) -> dict[str, Any]:
    refs = (
        [{"document_id": fixture["document_id"], "chunk_index": chunk_index}]
        if chunk_index is not None
        else []
    )
    response = await client.post(
        f"{EVAL}/datasets/{fixture['dataset_id']}/cases",
        json={"question": question, "relevant_refs": refs, "answerable": answerable},
        headers=fixture["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def _fake_retrieve(document_id: str):
    async def retrieve(db: Any, kb: Any, question: str, *, config: Any) -> SimpleNamespace:
        if "续航" in question:
            candidates = [
                {
                    "chunk_id": str(uuid4()),
                    "document_id": document_id,
                    "chunk_index": 0,
                    "filename": "handbook.md",
                    "heading_path": "续航",
                    "content": "X1 在标准环境下续航十二小时。",
                    "vector_score": 0.9,
                    "bm25_score": 1.2,
                    "vector_rank": 1,
                    "bm25_rank": 1,
                    "fused_score": 0.032,
                    "rerank_score": 0.8,
                }
            ]
            return SimpleNamespace(candidates=candidates)
        return SimpleNamespace(candidates=[])

    return retrieve


async def _wait_for_run(client: AsyncClient, fixture: dict[str, Any], run_id: str) -> dict:
    for _ in range(20):
        response = await client.get(f"{EVAL}/runs/{run_id}", headers=fixture["headers"])
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] not in RUNNING_STATUSES:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError("eval run did not finish")


async def test_dataset_cases_and_run_report_retrieval_metrics(
    client: AsyncClient,
    retrieval_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    await _add_case(client, retrieval_fixture, "X9 的保修政策是什么？", None, answerable=False)
    monkeypatch.setattr(
        eval_service, "retrieve", _fake_retrieve(retrieval_fixture["document_id"])
    )

    started = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
        json={"config": {"use_rerank": False, "final_top_k": 5}},
        headers=retrieval_fixture["headers"],
    )
    assert started.status_code == 202, started.text

    run = await _wait_for_run(client, retrieval_fixture, started.json()["id"])
    metrics = run["metrics"]
    assert run["status"] == "completed"
    assert metrics["cases_total"] == 2
    assert metrics["answerable_total"] == 1
    assert metrics["unanswerable_total"] == 1
    assert metrics["recall@1"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["refusal_accuracy"] == 1.0
    assert metrics["answerable_refusal_rate"] == 0.0
    assert len(run["results"]) == 2

    answerable_result = next(item for item in run["results"] if item["answerable"])
    assert answerable_result["metrics"]["first_relevant_rank"] == 1
    assert answerable_result["retrieved"][0]["chunk_index"] == 0


async def test_compare_two_identical_runs(
    client: AsyncClient,
    retrieval_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    await _add_case(client, retrieval_fixture, "X9 的保修政策是什么？", None, answerable=False)
    monkeypatch.setattr(
        eval_service, "retrieve", _fake_retrieve(retrieval_fixture["document_id"])
    )

    run_ids = []
    for _ in range(2):
        response = await client.post(
            f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
            json={},
            headers=retrieval_fixture["headers"],
        )
        assert response.status_code == 202, response.text
        run = await _wait_for_run(client, retrieval_fixture, response.json()["id"])
        run_ids.append(run["id"])

    compared = await client.get(
        f"{EVAL}/compare",
        params={"base_run_id": run_ids[0], "candidate_run_id": run_ids[1]},
        headers=retrieval_fixture["headers"],
    )
    assert compared.status_code == 200, compared.text
    body = compared.json()
    assert body["improved"] == 0
    assert body["degraded"] == 0
    assert body["unchanged"] == 2
    assert body["metric_deltas"]
    assert all(item["delta"] == 0 for item in body["metric_deltas"])


async def test_case_labels_must_point_at_existing_chunks(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    response = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/cases",
        json={
            "question": "不存在的 chunk",
            "relevant_refs": [{"document_id": str(uuid4()), "chunk_index": 0}],
        },
        headers=retrieval_fixture["headers"],
    )

    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


async def test_unanswerable_case_must_not_carry_labels(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    response = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/cases",
        json={
            "question": "不可答但标了引用",
            "answerable": False,
            "relevant_refs": [
                {"document_id": retrieval_fixture["document_id"], "chunk_index": 0}
            ],
        },
        headers=retrieval_fixture["headers"],
    )

    assert response.status_code == 422


async def test_run_requires_cases(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    response = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
        json={},
        headers=retrieval_fixture["headers"],
    )

    assert response.status_code == 400
    assert "用例" in response.json()["detail"]


async def test_run_refuses_stale_chunk_labels(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)

    # Simulate changing chunking parameters after the dataset was annotated.
    async with SessionLocal() as db:
        kb = await db.get(KnowledgeBase, UUID(retrieval_fixture["kb_id"]))
        assert kb is not None
        kb.chunk_size = 500
        await db.commit()

    response = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
        json={},
        headers=retrieval_fixture["headers"],
    )

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_other_users_cannot_access_eval_resources(
    client: AsyncClient, retrieval_fixture: dict[str, Any], register: Any
) -> None:
    _, stranger_headers = await register(email="stranger@example.com")

    cases = await client.get(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/cases",
        headers=stranger_headers,
    )
    run = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
        json={},
        headers=stranger_headers,
    )

    assert cases.status_code == 404
    assert run.status_code == 404


async def test_case_expected_mode_can_be_set_and_cleared(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    case = await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    assert case["expected_mode"] is None
    url = f"{EVAL}/cases/{case['id']}"

    labelled = await client.patch(
        url, json={"expected_mode": "agent"}, headers=retrieval_fixture["headers"]
    )
    assert labelled.status_code == 200, labelled.text
    assert labelled.json()["expected_mode"] == "agent"

    # PATCH without the field must not clear an existing label.
    untouched = await client.patch(url, json={}, headers=retrieval_fixture["headers"])
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["expected_mode"] == "agent"

    cleared = await client.patch(
        url, json={"expected_mode": None}, headers=retrieval_fixture["headers"]
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["expected_mode"] is None


async def test_create_case_accepts_expected_mode(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    response = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/cases",
        json={
            "question": "这条用例在建的时候就标好路径",
            "relevant_refs": [],
            "answerable": False,
            "expected_mode": "agent",
        },
        headers=retrieval_fixture["headers"],
    )

    assert response.status_code == 201, response.text
    assert response.json()["expected_mode"] == "agent"


async def test_routing_report_scores_labelled_cases(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    rag_case = await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    agent_case = await _add_case(
        client, retrieval_fixture, "X1 和 X2 的屏幕有什么区别？", 1
    )
    for case, mode in ((rag_case, "rag"), (agent_case, "agent")):
        patched = await client.patch(
            f"{EVAL}/cases/{case['id']}",
            json={"expected_mode": mode},
            headers=retrieval_fixture["headers"],
        )
        assert patched.status_code == 200, patched.text

    response = await client.get(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/routing-report",
        headers=retrieval_fixture["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset_id"] == retrieval_fixture["dataset_id"]
    assert body["total"] == 2
    assert body["labeled"] == 2
    assert body["unlabeled"] == 0
    assert body["accuracy"] == 1.0
    assert body["agent_precision"] == 1.0
    assert body["agent_recall"] == 1.0
    assert body["agent_f1"] == 1.0
    assert body["confusion"]["agent->agent"] == 1
    assert body["confusion"]["rag->rag"] == 1
    assert {case["predicted_mode"] for case in body["cases"]} == {"rag", "agent"}
    agent_case_out = next(
        case for case in body["cases"] if case["question"] == "X1 和 X2 的屏幕有什么区别？"
    )
    assert "comparison" in agent_case_out["signals"]
    assert agent_case_out["correct"] is True


async def test_other_users_cannot_patch_cases_or_read_routing_report(
    client: AsyncClient, retrieval_fixture: dict[str, Any], register: Any
) -> None:
    await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    cases = await client.get(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/cases",
        headers=retrieval_fixture["headers"],
    )
    case_id = cases.json()["items"][0]["id"]
    _, stranger_headers = await register(email="routing-stranger@example.com")

    patched = await client.patch(
        f"{EVAL}/cases/{case_id}",
        json={"expected_mode": "agent"},
        headers=stranger_headers,
    )
    report = await client.get(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/routing-report",
        headers=stranger_headers,
    )

    assert patched.status_code == 404
    assert report.status_code == 404


async def test_stale_runs_are_recovered_but_fresh_ones_are_left_alone(
    client: AsyncClient, retrieval_fixture: dict[str, Any]
) -> None:
    async with SessionLocal() as db:
        dataset = await db.scalar(
            select(EvalDataset).where(
                EvalDataset.id == UUID(retrieval_fixture["dataset_id"])
            )
        )
        kb = await db.get(KnowledgeBase, UUID(retrieval_fixture["kb_id"]))
        assert dataset is not None and kb is not None
        stale = EvalRun(
            dataset_id=dataset.id,
            kb_id=kb.id,
            status=EvalRunStatus.RUNNING,
            config={},
            metrics={},
            progress={},
            index_version=kb.index_version,
            created_at=datetime.now(UTC) - timedelta(minutes=30),
        )
        fresh = EvalRun(
            dataset_id=dataset.id,
            kb_id=kb.id,
            status=EvalRunStatus.PENDING,
            config={},
            metrics={},
            progress={},
            index_version=kb.index_version,
            created_at=datetime.now(UTC),
        )
        db.add_all([stale, fresh])
        await db.commit()
        stale_id, fresh_id = stale.id, fresh.id

    async with SessionLocal() as db:
        recovered = await eval_service.recover_stale_runs(db)
    assert recovered == 1

    stale_body = await client.get(
        f"{EVAL}/runs/{stale_id}", headers=retrieval_fixture["headers"]
    )
    fresh_body = await client.get(
        f"{EVAL}/runs/{fresh_id}", headers=retrieval_fixture["headers"]
    )
    assert stale_body.status_code == 200
    assert stale_body.json()["status"] == "failed"
    assert "中断" in stale_body.json()["error"]
    assert fresh_body.json()["status"] == "pending"


async def test_prompt_variants_endpoint_requires_auth_and_lists_choices(
    client: AsyncClient, register: Any
) -> None:
    anonymous = await client.get("/api/v1/prompts")
    assert anonymous.status_code == 401

    _, headers = await register()
    response = await client.get("/api/v1/prompts", headers=headers)
    assert response.status_code == 200, response.text
    variants = response.json()
    ids = {item["id"] for item in variants}
    assert {"grounded", "strict_citation", "structured"} <= ids
    assert all(item["label"] and item["description"] for item in variants)


async def test_generation_run_records_answer_and_metrics(
    client: AsyncClient,
    retrieval_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    await _add_case(client, retrieval_fixture, "X9 的保修政策是什么？", None, answerable=False)
    monkeypatch.setattr(
        eval_service, "retrieve", _fake_retrieve(retrieval_fixture["document_id"])
    )
    # The generation path is gated on a configured key; bypass the real .env.
    monkeypatch.setattr(
        eval_service,
        "get_settings",
        lambda: SimpleNamespace(deepseek_api_key="sk-test-only", max_context_chars=4000),
    )

    seen_variants: list[str | None] = []

    async def fake_complete(
        question: str,
        candidates: list[dict[str, Any]],
        history: list[tuple[str, str]],
        *,
        max_context_chars: int,
        prompt_variant: str | None = None,
    ) -> tuple[str, dict[str, int]]:
        del question, candidates, history, max_context_chars
        seen_variants.append(prompt_variant)
        return "X1 在标准环境下续航十二小时 [1]。", {"total_tokens": 42}

    monkeypatch.setattr(eval_service, "complete_answer", fake_complete)

    started = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
        json={"include_generation": True, "prompt_variant": "strict_citation"},
        headers=retrieval_fixture["headers"],
    )
    assert started.status_code == 202, started.text
    run = await _wait_for_run(client, retrieval_fixture, started.json()["id"])

    assert run["status"] == "completed"
    assert run["include_generation"] is True
    assert run["prompt_variant"] == "strict_citation"
    # Retrieval metrics must survive the optional generation pass.
    assert run["metrics"]["recall@1"] == 1.0
    assert run["metrics"]["generation_cases"] == 2
    assert run["metrics"]["generation_errors"] == 0
    assert run["metrics"]["generation_refusal_accuracy"] == 1.0
    assert run["metrics"]["generation_false_refusal_rate"] == 0.0
    assert run["metrics"]["answer_citation_rate"] == 1.0
    assert run["metrics"]["generation_total_tokens"] == 42
    # Only the answerable case reaches the LLM: empty retrieval short-circuits
    # to the fixed refusal, which keeps unanswerable cases free.
    assert seen_variants == ["strict_citation"]

    answerable_result = next(item for item in run["results"] if item["answerable"])
    assert answerable_result["answer"] == "X1 在标准环境下续航十二小时 [1]。"
    assert answerable_result["metrics"]["answer_grounded"] is True
    assert answerable_result["metrics"]["answer_citation_markers"] == 1
    unanswerable_result = next(item for item in run["results"] if not item["answerable"])
    assert unanswerable_result["answer"] == "根据现有资料无法回答这个问题。"
    assert unanswerable_result["metrics"]["answer_refused"] is True


async def test_generation_run_requires_llm_key(
    client: AsyncClient,
    retrieval_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    monkeypatch.setattr(
        eval_service,
        "get_settings",
        lambda: SimpleNamespace(deepseek_api_key=""),
    )

    response = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
        json={"include_generation": True},
        headers=retrieval_fixture["headers"],
    )

    assert response.status_code == 400
    assert "API key" in response.json()["detail"]


async def test_retrieval_only_run_has_no_prompt_variant(
    client: AsyncClient,
    retrieval_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add_case(client, retrieval_fixture, "X1 的续航多久？", 0)
    monkeypatch.setattr(
        eval_service, "retrieve", _fake_retrieve(retrieval_fixture["document_id"])
    )

    started = await client.post(
        f"{EVAL}/datasets/{retrieval_fixture['dataset_id']}/runs",
        json={"config": {"use_rerank": False}},
        headers=retrieval_fixture["headers"],
    )
    assert started.status_code == 202, started.text
    run = await _wait_for_run(client, retrieval_fixture, started.json()["id"])

    assert run["include_generation"] is False
    assert run["prompt_variant"] is None
    assert "generation_cases" not in run["metrics"]
    assert run["results"][0]["answer"] is None
