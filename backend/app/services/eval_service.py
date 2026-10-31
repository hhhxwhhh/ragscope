"""Evaluation datasets, runs and retrieval metrics.

The default run is retrieval-only and deterministic: no LLM judge, no API cost.
A case is labelled at chunk level; metrics are computed against the final
retrieval order that the chat pipeline would send to the LLM. Runs can opt into
an answer-generation pass, which adds deterministic refusal and citation checks
plus prompt-variant attribution, but no LLM-as-judge score.
"""

import logging
import math
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.session import SessionLocal
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.evaluation import (
    EvalCase,
    EvalDataset,
    EvalResult,
    EvalRun,
    EvalRunStatus,
)
from app.models.knowledge_base import KnowledgeBase
from app.rag.answer import complete_answer
from app.rag.pipeline import RetrievalConfig, retrieve
from app.rag.prompt import (
    DEFAULT_PROMPT_VARIANT,
    REFUSAL_MESSAGE,
    citation_stats,
    extract_citation_ranks,
)
from app.rag.router import choose_mode
from app.schemas.eval import EvalCaseCreate, EvalCaseUpdate, EvalRunCreate, RelevantRef
from app.services import webhook_service

logger = logging.getLogger(__name__)

RECALL_KS = (1, 3, 5, 10)
METRIC_LABELS = {
    "recall@1": "Recall@1",
    "recall@3": "Recall@3",
    "recall@5": "Recall@5",
    "recall@10": "Recall@10",
    "hit@5": "Hit@5",
    "mrr": "MRR",
    "refusal_accuracy": "拒答准确率",
    "answerable_refusal_rate": "误拒答率",
    "avg_latency_ms": "平均检索耗时",
    "p50_latency_ms": "P50 耗时",
    "p95_latency_ms": "P95 耗时",
    "generation_refusal_accuracy": "生成拒答准确率",
    "generation_false_refusal_rate": "生成误拒答率",
    "answer_citation_rate": "回答引用合规率",
    "avg_generation_latency_ms": "平均生成耗时",
    "generation_total_tokens": "生成 token 总量",
}
TERMINAL_STATUSES = {
    EvalRunStatus.COMPLETED,
    EvalRunStatus.FAILED,
    EvalRunStatus.CANCELLED,
}
STALE_RUN_MINUTES = 15


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def get_owned_dataset(
    db: AsyncSession,
    dataset_id: UUID,
    user_id: UUID,
) -> EvalDataset:
    dataset = await db.scalar(
        select(EvalDataset)
        .join(KnowledgeBase, EvalDataset.kb_id == KnowledgeBase.id)
        .where(EvalDataset.id == dataset_id, KnowledgeBase.owner_id == user_id)
    )
    if dataset is None:
        raise NotFoundError("评测数据集不存在")
    return dataset


async def get_owned_case(db: AsyncSession, case_id: UUID, user_id: UUID) -> EvalCase:
    case = await db.scalar(
        select(EvalCase)
        .join(EvalDataset, EvalCase.dataset_id == EvalDataset.id)
        .join(KnowledgeBase, EvalDataset.kb_id == KnowledgeBase.id)
        .where(EvalCase.id == case_id, KnowledgeBase.owner_id == user_id)
    )
    if case is None:
        raise NotFoundError("评测用例不存在")
    return case


async def get_owned_run(db: AsyncSession, run_id: UUID, user_id: UUID) -> EvalRun:
    run = await db.scalar(
        select(EvalRun)
        .join(KnowledgeBase, EvalRun.kb_id == KnowledgeBase.id)
        .where(EvalRun.id == run_id, KnowledgeBase.owner_id == user_id)
    )
    if run is None:
        raise NotFoundError("评测运行不存在")
    return run


# ---------------------------------------------------------------------------
# Datasets and cases
# ---------------------------------------------------------------------------


async def list_datasets(
    db: AsyncSession,
    kb_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[tuple[EvalDataset, int, int]], int]:
    case_count = (
        select(func.count(EvalCase.id))
        .where(EvalCase.dataset_id == EvalDataset.id)
        .correlate(EvalDataset)
        .scalar_subquery()
    )
    run_count = (
        select(func.count(EvalRun.id))
        .where(EvalRun.dataset_id == EvalDataset.id)
        .correlate(EvalDataset)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(EvalDataset, case_count, run_count)
            .where(EvalDataset.kb_id == kb_id)
            .order_by(EvalDataset.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total = await db.scalar(
        select(func.count()).select_from(EvalDataset).where(EvalDataset.kb_id == kb_id)
    )
    return [(dataset, int(cases), int(runs)) for dataset, cases, runs in rows], int(total or 0)


async def create_dataset(
    db: AsyncSession,
    kb: KnowledgeBase,
    name: str,
    description: str | None,
) -> EvalDataset:
    exists = await db.scalar(
        select(EvalDataset.id).where(EvalDataset.kb_id == kb.id, EvalDataset.name == name)
    )
    if exists is not None:
        raise ConflictError("同名评测数据集已存在")
    dataset = EvalDataset(
        kb_id=kb.id,
        name=name,
        description=description,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
    )
    db.add(dataset)
    await db.flush()
    return dataset


async def delete_dataset(db: AsyncSession, dataset: EvalDataset) -> None:
    await db.delete(dataset)


async def dataset_stats(db: AsyncSession, dataset_id: UUID) -> tuple[int, int]:
    cases = await db.scalar(
        select(func.count()).select_from(EvalCase).where(EvalCase.dataset_id == dataset_id)
    )
    runs = await db.scalar(
        select(func.count()).select_from(EvalRun).where(EvalRun.dataset_id == dataset_id)
    )
    return int(cases or 0), int(runs or 0)


async def list_cases(
    db: AsyncSession,
    dataset_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[EvalCase], int]:
    items = (
        await db.scalars(
            select(EvalCase)
            .where(EvalCase.dataset_id == dataset_id)
            .order_by(EvalCase.created_at, EvalCase.question)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total = await db.scalar(
        select(func.count()).select_from(EvalCase).where(EvalCase.dataset_id == dataset_id)
    )
    return list(items), int(total or 0)


async def _validate_refs(
    db: AsyncSession,
    kb_id: UUID,
    refs: list[RelevantRef],
) -> list[dict[str, Any]]:
    """Ensure every label points at an existing chunk of this KB.

    Filenames are read from the database rather than trusted from the client.
    """
    if not refs:
        return []
    document_ids = {ref.document_id for ref in refs}
    rows = (
        await db.execute(
            select(Chunk.document_id, Chunk.chunk_index, Document.filename)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.kb_id == kb_id, Chunk.document_id.in_(document_ids))
        )
    ).all()
    known = {
        (str(document_id), int(chunk_index)): filename
        for document_id, chunk_index, filename in rows
    }
    normalized: list[dict[str, Any]] = []
    for ref in refs:
        key = (str(ref.document_id), ref.chunk_index)
        filename = known.get(key)
        if filename is None:
            raise BadRequestError(
                f"标注的 chunk 不存在或不属于该知识库：{ref.document_id}#{ref.chunk_index}"
            )
        normalized.append(
            {
                "document_id": str(ref.document_id),
                "chunk_index": ref.chunk_index,
                "filename": filename,
            }
        )
    return normalized


async def create_case(
    db: AsyncSession,
    dataset: EvalDataset,
    payload: EvalCaseCreate,
) -> EvalCase:
    refs = await _validate_refs(db, dataset.kb_id, payload.relevant_refs)
    case = EvalCase(
        dataset_id=dataset.id,
        question=payload.question,
        relevant_refs=refs,
        answerable=payload.answerable,
        expected_mode=payload.expected_mode,
        notes=payload.notes,
    )
    db.add(case)
    await db.flush()
    return case


async def update_case(
    db: AsyncSession,
    case: EvalCase,
    payload: EvalCaseUpdate,
) -> EvalCase:
    # PATCH semantics: only touch fields the client actually sent, so an
    # omitted expected_mode keeps the existing label while an explicit null
    # clears it.
    if "expected_mode" in payload.model_fields_set:
        case.expected_mode = payload.expected_mode
    await db.flush()
    return case


async def create_cases(
    db: AsyncSession,
    dataset: EvalDataset,
    payloads: list[EvalCaseCreate],
) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for payload in payloads:
        cases.append(await create_case(db, dataset, payload))
    return cases


async def delete_case(db: AsyncSession, case: EvalCase) -> None:
    await db.delete(case)


# ---------------------------------------------------------------------------
# Routing evaluation
# ---------------------------------------------------------------------------


def build_routing_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the auto router against human route labels.

    This is deterministic and free: no retrieval, no model call, no background
    job. ``rows`` items carry case_id, question, answerable and expected_mode.
    """
    confusion = {"rag->rag": 0, "rag->agent": 0, "agent->rag": 0, "agent->agent": 0}
    cases: list[dict[str, Any]] = []
    total = len(rows)
    labeled = 0
    correct = 0
    predicted_agent = 0
    expected_agent = 0
    true_positive = 0
    false_positive = 0
    false_negative = 0

    for row in rows:
        decision = choose_mode(str(row["question"]))
        expected = row.get("expected_mode")
        if expected not in ("rag", "agent"):
            expected = None
        predicted = decision.mode
        if predicted == "agent":
            predicted_agent += 1
        is_correct: bool | None = None
        if expected is not None:
            labeled += 1
            is_correct = predicted == expected
            if is_correct:
                correct += 1
            if expected == "agent":
                expected_agent += 1
            confusion[f"{expected}->{predicted}"] += 1
            if expected == "agent" and predicted == "agent":
                true_positive += 1
            elif expected == "rag" and predicted == "agent":
                false_positive += 1
            elif expected == "agent" and predicted == "rag":
                false_negative += 1

        cases.append(
            {
                "case_id": row["case_id"],
                "question": row["question"],
                "answerable": bool(row["answerable"]),
                "expected_mode": expected,
                "predicted_mode": predicted,
                "correct": is_correct,
                "signals": list(decision.signals),
                "reason": decision.reason,
            }
        )

    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else None
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else None
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "total": total,
        "labeled": labeled,
        "unlabeled": total - labeled,
        "accuracy": round(correct / labeled, 4) if labeled else None,
        "agent_precision": round(precision, 4) if precision is not None else None,
        "agent_recall": round(recall, 4) if recall is not None else None,
        "agent_f1": round(f1, 4) if f1 is not None else None,
        "predicted_agent_rate": round(predicted_agent / total, 4) if total else 0.0,
        "expected_agent_rate": round(expected_agent / labeled, 4) if labeled else None,
        "confusion": confusion,
        "cases": cases,
    }


async def routing_report(db: AsyncSession, dataset: EvalDataset) -> dict[str, Any]:
    cases = (
        await db.scalars(
            select(EvalCase)
            .where(EvalCase.dataset_id == dataset.id)
            .order_by(EvalCase.created_at, EvalCase.question)
        )
    ).all()
    report = build_routing_report(
        [
            {
                "case_id": case.id,
                "question": case.question,
                "answerable": case.answerable,
                "expected_mode": case.expected_mode,
            }
            for case in cases
        ]
    )
    report["dataset_id"] = dataset.id
    return report


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _relevant_set(refs: list[dict[str, Any]]) -> set[tuple[str, int]]:
    return {(str(ref["document_id"]), int(ref["chunk_index"])) for ref in refs}


def score_case(
    retrieved: list[dict[str, Any]],
    relevant_refs: list[dict[str, Any]],
    answerable: bool,
) -> dict[str, Any]:
    relevant = _relevant_set(relevant_refs)
    ranks = [
        rank
        for rank, candidate in enumerate(retrieved, start=1)
        if (str(candidate.get("document_id")), int(candidate.get("chunk_index", -1))) in relevant
    ]
    first = ranks[0] if ranks else None
    recalled = {
        f"recall@{k}": (len([rank for rank in ranks if rank <= k]) / len(relevant))
        if answerable and relevant
        else 0.0
        for k in RECALL_KS
    }
    metrics: dict[str, Any] = {
        **recalled,
        "hit@5": 1.0 if first is not None and first <= 5 else 0.0,
        "mrr": 1.0 / first if first is not None else 0.0,
        "first_relevant_rank": first,
        "retrieved_count": len(retrieved),
        "refused": len(retrieved) == 0,
    }
    if not answerable:
        # Unanswerable cases are scored by refusal, not recall.
        metrics["hit@5"] = 0.0
        metrics["mrr"] = 0.0
        for k in RECALL_KS:
            metrics[f"recall@{k}"] = 0.0
    return metrics


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(max(math.ceil(ratio * len(ordered)) - 1, 0), len(ordered) - 1)
    return round(ordered[index], 1)


_REFUSAL_MARKERS = (
    "根据现有资料无法回答",
    "无法根据现有资料",
    "资料不足",
    "没有足够资料",
    "无法确认",
)


def looks_refused(answer: str) -> bool:
    """Cheap deterministic refusal detector for generation evaluation.

    The strict variants ask the model to emit one fixed sentence, but the
    default variant is freer, so a few common phrasings are accepted. This is a
    heuristic, not an LLM judge; a false positive only affects the generation
    metrics, never the retrieval ones.
    """
    head = answer.strip()[:200]
    return any(marker in head for marker in _REFUSAL_MARKERS)


def aggregate_metrics(
    results: list[dict[str, Any]],
    *,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    """Aggregate ordered per-case metric dicts.

    `results` items: {"answerable", "metrics", "refused", "latency_ms", "error"}.
    Errored cases are excluded from quality metrics but counted.
    """
    ok = [item for item in results if not item.get("error")]
    answerable = [item for item in ok if item["answerable"]]
    unanswerable = [item for item in ok if not item["answerable"]]

    def avg(items: list[dict[str, Any]], key: str) -> float:
        return (
            round(mean([float(item["metrics"].get(key, 0.0)) for item in items]), 4)
            if items
            else 0.0
        )

    latencies = [float(item["latency_ms"]) for item in ok]
    metrics: dict[str, Any] = {
        "cases_total": len(results),
        "cases_scored": len(ok),
        "errors": len(results) - len(ok),
        "answerable_total": len(answerable),
        "unanswerable_total": len(unanswerable),
    }
    for k in RECALL_KS:
        metrics[f"recall@{k}"] = avg(answerable, f"recall@{k}")
    metrics["hit@5"] = avg(answerable, "hit@5")
    metrics["mrr"] = avg(answerable, "mrr")
    metrics["refusal_accuracy"] = (
        round(
            mean([1.0 if item["refused"] else 0.0 for item in unanswerable]),
            4,
        )
        if unanswerable
        else 0.0
    )
    metrics["answerable_refusal_rate"] = (
        round(
            mean([1.0 if item["refused"] else 0.0 for item in answerable]),
            4,
        )
        if answerable
        else 0.0
    )
    metrics["avg_latency_ms"] = round(mean(latencies), 1) if latencies else 0.0
    metrics["p50_latency_ms"] = _percentile(latencies, 0.50)
    metrics["p95_latency_ms"] = _percentile(latencies, 0.95)

    # Generation metrics only exist when the run enabled the optional answer
    # pass; retrieval-only runs keep the exact same shape as before.
    generation = [item for item in ok if "generation_latency_ms" in item["metrics"]]
    if generation:
        gen_answerable = [item for item in generation if item["answerable"]]
        gen_unanswerable = [item for item in generation if not item["answerable"]]
        answered = [
            item
            for item in gen_answerable
            if not item["metrics"].get("answer_refused")
        ]
        metrics["generation_cases"] = len(generation)
        metrics["generation_errors"] = sum(
            1 for item in generation if item["metrics"].get("generation_error")
        )
        metrics["generation_refusal_accuracy"] = (
            round(
                mean(
                    [
                        1.0 if item["metrics"].get("answer_refused") else 0.0
                        for item in gen_unanswerable
                    ]
                ),
                4,
            )
            if gen_unanswerable
            else 0.0
        )
        metrics["generation_false_refusal_rate"] = (
            round(
                mean(
                    [
                        1.0 if item["metrics"].get("answer_refused") else 0.0
                        for item in gen_answerable
                    ]
                ),
                4,
            )
            if gen_answerable
            else 0.0
        )
        metrics["answer_citation_rate"] = (
            round(
                mean(
                    [
                        1.0 if item["metrics"].get("answer_grounded") else 0.0
                        for item in answered
                    ]
                ),
                4,
            )
            if answered
            else 0.0
        )
        gen_latencies = [
            float(item["metrics"].get("generation_latency_ms", 0.0))
            for item in generation
            if not item["metrics"].get("generation_error")
        ]
        metrics["avg_generation_latency_ms"] = (
            round(mean(gen_latencies), 1) if gen_latencies else 0.0
        )
        metrics["generation_total_tokens"] = int(
            sum(int(item["metrics"].get("generation_tokens", 0)) for item in generation)
        )

    metrics["elapsed_seconds"] = round(elapsed_seconds, 2)
    return metrics


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _effective_config(kb: KnowledgeBase, payload: EvalRunCreate) -> RetrievalConfig:
    config = RetrievalConfig.from_kb(kb)
    if payload.config is not None:
        config = replace(config, **payload.config.model_dump(exclude_none=True))
    # Evals measure real retrieval latency and must not share cached candidates
    # across cases (or with chat); the snapshot records this explicitly.
    return replace(config, use_cache=False)


async def create_run(
    db: AsyncSession,
    dataset: EvalDataset,
    payload: EvalRunCreate,
) -> EvalRun:
    kb = await db.get(KnowledgeBase, dataset.kb_id)
    if kb is None:
        raise NotFoundError("知识库不存在")
    if (kb.chunk_size, kb.chunk_overlap) != (dataset.chunk_size, dataset.chunk_overlap):
        raise ConflictError(
            "知识库切块参数已变更，chunk 级别标注已失效；请新建评测数据集并重新标注"
        )
    case_count = await db.scalar(
        select(func.count()).select_from(EvalCase).where(EvalCase.dataset_id == dataset.id)
    )
    if not case_count:
        raise BadRequestError("数据集还没有用例，先添加用例再运行")

    if payload.base_run_id is not None:
        base = await db.get(EvalRun, payload.base_run_id)
        if base is None or base.dataset_id != dataset.id:
            raise BadRequestError("基线运行不属于该数据集")

    if payload.include_generation and not get_settings().deepseek_api_key:
        raise BadRequestError("未配置 LLM API key，无法运行生成评测")

    config = _effective_config(kb, payload)
    run = EvalRun(
        dataset_id=dataset.id,
        kb_id=dataset.kb_id,
        base_run_id=payload.base_run_id,
        status=EvalRunStatus.PENDING,
        config=asdict(config),
        metrics={},
        progress={"done": 0, "total": int(case_count or 0)},
        index_version=kb.index_version,
        include_generation=payload.include_generation,
        prompt_variant=(
            (payload.prompt_variant or DEFAULT_PROMPT_VARIANT)
            if payload.include_generation
            else None
        ),
    )
    db.add(run)
    await db.flush()
    return run


def _retrieved_summary(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "chunk_id",
        "document_id",
        "filename",
        "chunk_index",
        "heading_path",
        "vector_score",
        "bm25_score",
        "vector_rank",
        "bm25_rank",
        "fused_score",
        "rerank_score",
    )
    return [
        {
            "rank": rank,
            **{key: candidate.get(key) for key in keys},
            "content": (candidate.get("content") or "")[:300],
        }
        for rank, candidate in enumerate(candidates, start=1)
    ]


async def _generate_case(
    question: str,
    candidates: list[dict[str, Any]],
    prompt_variant: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """Answer one case and compute deterministic generation checks.

    A generation failure is data, not a run failure: the retrieval metrics stay
    valid and the error is attached to this case.
    """
    started = time.perf_counter()
    if not candidates:
        answer = REFUSAL_MESSAGE
        stats = citation_stats(answer, 0, set())
        return answer, {
            "answer_refused": True,
            "answer_grounded": False,
            "answer_citation_markers": stats["markers"],
            "answer_invalid_citations": stats["invalid_markers"],
            "answer_citation_rate": 0.0,
            "generation_latency_ms": int((time.perf_counter() - started) * 1000),
            "generation_tokens": 0,
        }

    try:
        answer, usage = await complete_answer(
            question,
            candidates,
            [],
            max_context_chars=get_settings().max_context_chars,
            prompt_variant=prompt_variant,
        )
        answer = (answer or "").strip() or REFUSAL_MESSAGE
    except Exception as exc:  # noqa: BLE001 - keep retrieval metrics intact
        logger.exception("generation eval failed for question: %s", question)
        return None, {
            "generation_error": str(exc)[:300],
            "generation_latency_ms": int((time.perf_counter() - started) * 1000),
            "generation_tokens": 0,
        }

    cited = extract_citation_ranks(answer, len(candidates))
    stats = citation_stats(answer, len(candidates), cited)
    refused = looks_refused(answer)
    return answer, {
        "answer_refused": refused,
        "answer_grounded": bool(
            not refused and stats["markers"] > 0 and stats["invalid_markers"] == 0
        ),
        "answer_citation_markers": stats["markers"],
        "answer_invalid_citations": stats["invalid_markers"],
        "answer_citation_rate": stats["citation_rate"],
        "generation_latency_ms": int((time.perf_counter() - started) * 1000),
        "generation_tokens": int((usage or {}).get("total_tokens", 0)),
    }


async def execute_run(run_id: UUID) -> None:
    """Background executor. Owns a fresh session and commits case by case."""
    async with SessionLocal() as db:
        run = await db.get(EvalRun, run_id)
        if run is None or run.status != EvalRunStatus.PENDING:
            return
        config = RetrievalConfig(**run.config)
        dataset = await db.get(EvalDataset, run.dataset_id)
        kb = await db.get(KnowledgeBase, run.kb_id)
        if dataset is None or kb is None:
            run.status = EvalRunStatus.FAILED
            run.error = "数据集或知识库已被删除"
            run.finished_at = datetime.now(UTC)
            await db.commit()
            return

        cases = (
            await db.scalars(
                select(EvalCase)
                .where(EvalCase.dataset_id == dataset.id)
                .order_by(EvalCase.created_at, EvalCase.question)
            )
        ).all()
        run.status = EvalRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        run.progress = {"done": 0, "total": len(cases), "current_case": None}
        await db.commit()

        started = time.perf_counter()
        result_metrics: list[dict[str, Any]] = []
        cancelled = False
        try:
            for index, case in enumerate(cases):
                await db.refresh(run)
                if run.status == EvalRunStatus.CANCELLED:
                    cancelled = True
                    break

                case_started = time.perf_counter()
                answer: str | None = None
                try:
                    outcome = await retrieve(db, kb, case.question, config=config)
                    # Retrieval latency stays separate from optional generation:
                    # a slow LLM must not pollute recall/latency comparisons.
                    latency_ms = int((time.perf_counter() - case_started) * 1000)
                    retrieved = _retrieved_summary(outcome.candidates)
                    metrics = score_case(retrieved, case.relevant_refs, case.answerable)
                    error = None
                    if run.include_generation:
                        answer, gen_metrics = await _generate_case(
                            case.question,
                            outcome.candidates,
                            run.prompt_variant,
                        )
                        metrics.update(gen_metrics)
                except Exception as exc:  # noqa: BLE001 - one bad case must not kill the run
                    logger.exception("eval case failed: %s", case.id)
                    latency_ms = int((time.perf_counter() - case_started) * 1000)
                    retrieved = []
                    metrics = score_case([], case.relevant_refs, case.answerable)
                    error = str(exc)[:1000]

                db.add(
                    EvalResult(
                        run_id=run.id,
                        case_id=case.id,
                        question=case.question,
                        answerable=case.answerable,
                        relevant_refs=case.relevant_refs,
                        retrieved=retrieved,
                        metrics=metrics,
                        refused=bool(metrics["refused"]),
                        latency_ms=latency_ms,
                        answer=answer,
                        error=error,
                    )
                )
                result_metrics.append(
                    {
                        "answerable": case.answerable,
                        "metrics": metrics,
                        "refused": bool(metrics["refused"]),
                        "latency_ms": latency_ms,
                        "error": error,
                    }
                )
                run.progress = {
                    "done": index + 1,
                    "total": len(cases),
                    "current_case": None,
                }
                await db.commit()

            run.status = EvalRunStatus.CANCELLED if cancelled else EvalRunStatus.COMPLETED
            run.metrics = aggregate_metrics(
                result_metrics,
                elapsed_seconds=time.perf_counter() - started,
            )
            run.progress = {
                "done": len(result_metrics),
                "total": len(cases),
                "current_case": None,
            }
            run.finished_at = datetime.now(UTC)
            await db.commit()
            await webhook_service.notify(
                db,
                owner_id=kb.owner_id,
                event="eval.completed",
                payload={
                    "run_id": str(run.id),
                    "kb_id": str(kb.id),
                    "status": str(run.status),
                    "metrics": run.metrics,
                },
            )
            logger.info(
                "eval run %s finished status=%s cases=%d mrr=%s",
                run.id,
                run.status,
                len(result_metrics),
                run.metrics.get("mrr"),
            )
        except Exception as exc:  # noqa: BLE001 - persist the failure for the UI
            logger.exception("eval run failed: %s", run_id)
            await db.rollback()
            run = await db.get(EvalRun, run_id)
            if run is not None:
                run.status = EvalRunStatus.FAILED
                run.error = str(exc)[:1000]
                run.finished_at = datetime.now(UTC)
                await db.commit()


async def cancel_run(db: AsyncSession, run: EvalRun) -> EvalRun:
    if run.status in TERMINAL_STATUSES:
        return run
    run.status = EvalRunStatus.CANCELLED
    return run


async def recover_stale_runs(db: AsyncSession) -> int:
    """Fail runs orphaned by a process restart.

    BackgroundTasks live in the API process, so after a restart nothing will
    ever finish a row left in pending/running. Runs younger than the cutoff are
    left alone in case another worker is still executing them.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_RUN_MINUTES)
    result = await db.execute(
        update(EvalRun)
        .where(
            EvalRun.status.in_([EvalRunStatus.PENDING, EvalRunStatus.RUNNING]),
            EvalRun.created_at < cutoff,
        )
        .values(
            status=EvalRunStatus.FAILED,
            error="评测进程中断，运行未完成；请重新发起",
            finished_at=datetime.now(UTC),
        )
    )
    await db.commit()
    return int(result.rowcount or 0)


async def list_runs(
    db: AsyncSession,
    dataset_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[EvalRun], int]:
    items = (
        await db.scalars(
            select(EvalRun)
            .where(EvalRun.dataset_id == dataset_id)
            .order_by(EvalRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total = await db.scalar(
        select(func.count()).select_from(EvalRun).where(EvalRun.dataset_id == dataset_id)
    )
    return list(items), int(total or 0)


async def get_run_results(db: AsyncSession, run_id: UUID) -> list[EvalResult]:
    items = (
        await db.scalars(
            select(EvalResult)
            .where(EvalResult.run_id == run_id)
            .order_by(EvalResult.created_at, EvalResult.question)
        )
    ).all()
    return list(items)


async def dataset_name(db: AsyncSession, dataset_id: UUID) -> str | None:
    return await db.scalar(select(EvalDataset.name).where(EvalDataset.id == dataset_id))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _rank_for(result: EvalResult | None) -> int | None:
    if result is None:
        return None
    value = result.metrics.get("first_relevant_rank")
    return int(value) if value is not None else None


def _case_outcome(
    base: EvalResult,
    candidate: EvalResult,
) -> str:
    if base.answerable:
        base_rank = _rank_for(base)
        candidate_rank = _rank_for(candidate)
        if base_rank == candidate_rank:
            return "same"
        if candidate_rank is None:
            return "worse"
        if base_rank is None or candidate_rank < base_rank:
            return "better"
        return "worse"
    if base.refused == candidate.refused:
        return "same"
    return "better" if candidate.refused else "worse"


async def compare_runs(
    db: AsyncSession,
    base_run: EvalRun,
    candidate_run: EvalRun,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if base_run.dataset_id != candidate_run.dataset_id:
        raise ConflictError("只能对比同一数据集下的运行")
    if base_run.index_version != candidate_run.index_version:
        raise ConflictError("两次运行的索引版本不同，chunk 标注不可比")
    if (
        base_run.status != EvalRunStatus.COMPLETED
        or candidate_run.status != EvalRunStatus.COMPLETED
    ):
        raise BadRequestError("只能对比已完成的运行")

    base_results = {result.case_id: result for result in await get_run_results(db, base_run.id)}
    candidate_results = {
        result.case_id: result for result in await get_run_results(db, candidate_run.id)
    }
    case_ids = list(dict.fromkeys([*base_results.keys(), *candidate_results.keys()]))

    cases: list[dict[str, Any]] = []
    counts = {"better": 0, "worse": 0, "same": 0}
    for case_id in case_ids:
        base = base_results.get(case_id)
        candidate = candidate_results.get(case_id)
        if base is None or candidate is None:
            continue
        outcome = _case_outcome(base, candidate)
        counts[outcome] += 1
        cases.append(
            {
                "case_id": case_id,
                "question": candidate.question,
                "answerable": candidate.answerable,
                "base_rank": _rank_for(base),
                "candidate_rank": _rank_for(candidate),
                "base_refused": base.refused,
                "candidate_refused": candidate.refused,
                "outcome": outcome,
                "base_metrics": base.metrics,
                "candidate_metrics": candidate.metrics,
            }
        )
    return cases, counts


def metric_deltas(
    base_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    deltas: list[dict[str, Any]] = []
    for key, label in METRIC_LABELS.items():
        base = base_metrics.get(key)
        candidate = candidate_metrics.get(key)
        if not isinstance(base, (int, float)) or not isinstance(candidate, (int, float)):
            continue
        deltas.append(
            {
                "key": key,
                "label": label,
                "base": round(float(base), 4),
                "candidate": round(float(candidate), 4),
                "delta": round(float(candidate) - float(base), 4),
            }
        )
    return deltas
