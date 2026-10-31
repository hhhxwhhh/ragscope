"""GraphRAG: entity/relation extraction and one-hop retrieval expansion.

Two distinct operations live here, and keeping them separate is the main design
decision:

**Build (offline, expensive)** — an LLM reads each chunk and returns a small JSON
object of entities and relations. This costs tokens, so it runs on demand from the
UI/script, never inside the ingestion path. Extraction is idempotent: entities are
upserted by ``(kb_id, normalized_name)``, edges by ``(source, target, relation)``,
so re-running it repairs a partial build instead of duplicating the graph.

**Expand (online, cheap)** — at query time we only string-match the question
against entity names already in the graph, walk one hop, and pull the chunks that
mention the neighbours. There is no LLM call on this path; worst case it adds one
indexed query and three candidate chunks. That keeps the latency budget honest and
makes the graph an *optional* retrieval stage rather than a new bottleneck.

Every expanded candidate carries ``graph_score`` and ``graph_via`` so the trace can
show exactly which edge produced it — a graph hit that cannot be explained is a
bug, not a feature.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jieba
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.graph import ChunkEntity, GraphEntity, GraphRelation
from app.models.knowledge_base import KnowledgeBase
from app.rag.agent.graph import parse_json_object
from app.rag.llm import get_agent_model, usage_to_dict

logger = logging.getLogger(__name__)

ENTITY_TYPES = ("product", "feature", "policy", "organization", "concept", "person", "other")

EXTRACT_PROMPT = """你是知识图谱抽取器。从给定文本中抽取实体和实体之间的明确关系。

规则：
1. 只抽取文本中明确出现的内容，不要引入外部知识。
2. 实体类型只能是：product（产品/型号）、feature（参数/功能）、policy（政策/条款）、
   organization（组织/部门）、concept（概念/术语）、person（人物）、other。
3. 实体名称保持原文写法，2-20 字，不要抽「用户」「文档」这类泛化词。
4. 关系必须有原文依据；拿不准就不输出。source 和 target 必须是本次输出的实体名称。
5. 关系名称用 2-6 个字的动宾短语，例如「支持」「属于」「包含」「适用于」「保修期」。

严格输出 JSON：
{"entities": [{"name": "...", "type": "product", "description": "一句话说明"}],
 "relations": [{"source": "...", "target": "...", "relation": "...", "description": "..."}]}"""

_MAX_ENTITIES_PER_CHUNK = 10
_MAX_RELATIONS_PER_CHUNK = 12
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[，。！？；：、（）()【】\[\]「」《》\"'’“”]+")


def normalize_name(name: str) -> str:
    """Canonical form used for dedupe: casefold, collapse spaces, strip punctuation."""
    text = _PUNCT.sub(" ", name or "").casefold().strip()
    return _SPACE.sub(" ", text)


@dataclass
class BuildStats:
    documents: int = 0
    chunks: int = 0
    entities: int = 0
    relations: int = 0
    failed_chunks: int = 0
    skipped: bool = False


def candidate_terms(query: str, limit: int = 160) -> list[str]:
    """Terms used to find entity mentions in a question.

    Three sources, because each one covers a different shape of entity name:

    * jieba tokens — real Chinese words ("保修期", "进水");
    * short character n-grams — words the dictionary splits differently;
    * alphanumeric runs — model codes and specs ("X1", "IP68", "X1 Pro").

    All of them are matched in a single ``IN`` query. Truncating by length would
    silently drop "x1" in favour of meaningless long grams, so the budget is spent
    on jieba tokens and runs first and only then on n-grams.
    """
    terms: list[str] = []

    def add(value: str) -> None:
        cleaned = normalize_name(value)
        if 2 <= len(cleaned) <= 20 and cleaned not in terms:
            terms.append(cleaned)

    for token in jieba.lcut(query or ""):
        add(token)

    lowered = _SPACE.sub(" ", (query or "").casefold())
    for run in re.findall(r"[a-z0-9][a-z0-9._\- ]{0,14}[a-z0-9]", lowered):
        add(run)
    for run in re.findall(r"[\u4e00-\u9fff]{2,12}", lowered):
        add(run)

    # Short n-grams catch names the tokenizer splits ("星盾2.0" vs "星盾 2.0").
    for size in (2, 3, 4):
        for index in range(max(0, len(lowered) - size + 1)):
            gram = lowered[index : index + size].strip()
            if len(gram) == size and not gram.isdigit():
                add(gram)

    return terms[:limit]


async def _extract_chunk(text: str, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    model = get_agent_model()
    try:
        message = await asyncio.wait_for(
            model.ainvoke(
                [
                    SystemMessage(content=EXTRACT_PROMPT),
                    HumanMessage(content=f"文本：\n{text[:3000]}"),
                ]
            ),
            timeout=timeout,
        )
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"

    content = message.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    payload = parse_json_object(str(content))
    metrics.observe_llm_usage(usage_to_dict(message.usage_metadata))
    return payload, None


async def _upsert_entity(
    db: AsyncSession,
    kb_id: UUID,
    name: str,
    entity_type: str,
    description: str | None,
) -> UUID:
    normalized = normalize_name(name)
    stmt = (
        pg_insert(GraphEntity)
        .values(
            kb_id=kb_id,
            name=name[:120],
            normalized_name=normalized[:120],
            entity_type=entity_type if entity_type in ENTITY_TYPES else "other",
            description=(description or None),
            mention_count=1,
        )
        .on_conflict_do_update(
            constraint="uq_graph_entities_kb_name",
            set_={
                "mention_count": GraphEntity.mention_count + 1,
                "description": func.coalesce(GraphEntity.description, description),
            },
        )
        .returning(GraphEntity.id)
    )
    return (await db.execute(stmt)).scalar_one()


async def _upsert_relation(
    db: AsyncSession,
    kb_id: UUID,
    source_id: UUID,
    target_id: UUID,
    relation: str,
    description: str | None,
    chunk_id: UUID,
) -> None:
    if source_id == target_id:
        return
    stmt = (
        pg_insert(GraphRelation)
        .values(
            kb_id=kb_id,
            source_id=source_id,
            target_id=target_id,
            relation=relation[:40],
            description=description,
            weight=1.0,
            source_chunk_id=chunk_id,
        )
        .on_conflict_do_update(
            constraint="uq_graph_relations_edge",
            set_={"weight": GraphRelation.weight + 1.0},
        )
    )
    await db.execute(stmt)


async def _link_mentions(db: AsyncSession, chunk_id: UUID, entity_ids: list[UUID]) -> None:
    if not entity_ids:
        return
    rows = [
        {"chunk_id": chunk_id, "entity_id": entity_id, "weight": 1.0}
        for entity_id in entity_ids
    ]
    stmt = (
        pg_insert(ChunkEntity)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_chunk_entities_pair")
    )
    await db.execute(stmt)


async def build_for_document(
    db: AsyncSession,
    kb: KnowledgeBase,
    document_id: UUID,
    *,
    max_chunks: int | None = None,
) -> BuildStats:
    settings = get_settings()
    stats = BuildStats(documents=1)
    chunks = (
        await db.scalars(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
            .limit(max_chunks or settings.graph_max_chunks_per_build)
        )
    ).all()

    for chunk in chunks:
        stats.chunks += 1
        payload, error = await _extract_chunk(chunk.content, settings.graph_extract_timeout_seconds)
        if payload is None:
            stats.failed_chunks += 1
            logger.warning("graph extraction failed chunk=%s error=%s", chunk.id, error)
            continue

        raw_entities = [
            item
            for item in (payload.get("entities") or [])
            if isinstance(item, dict) and normalize_name(str(item.get("name") or ""))
        ][: _MAX_ENTITIES_PER_CHUNK]
        if not raw_entities:
            continue

        entity_ids: dict[str, UUID] = {}
        for item in raw_entities:
            name = str(item.get("name")).strip()
            entity_id = await _upsert_entity(
                db,
                kb.id,
                name,
                str(item.get("type") or "other"),
                item.get("description"),
            )
            entity_ids[normalize_name(name)] = entity_id
            stats.entities += 1

        await _link_mentions(db, chunk.id, list(set(entity_ids.values())))

        for relation in (payload.get("relations") or [])[: _MAX_RELATIONS_PER_CHUNK]:
            if not isinstance(relation, dict):
                continue
            source = entity_ids.get(normalize_name(str(relation.get("source") or "")))
            target = entity_ids.get(normalize_name(str(relation.get("target") or "")))
            if source is None or target is None or source == target:
                continue
            label = str(relation.get("relation") or "").strip()
            if not label:
                continue
            await _upsert_relation(
                db,
                kb.id,
                source,
                target,
                label,
                relation.get("description"),
                chunk.id,
            )
            stats.relations += 1

        await db.commit()

    metrics.observe_graph("build", "ok" if not stats.failed_chunks else "partial")
    return stats


async def build_for_kb(db: AsyncSession, kb: KnowledgeBase) -> BuildStats:
    """Extract the whole knowledge base, document by document."""
    stats = BuildStats()
    document_ids = (
        await db.scalars(select(Document.id).where(Document.kb_id == kb.id))
    ).all()
    for document_id in document_ids:
        document_stats = await build_for_document(db, kb, document_id)
        stats.documents += 1
        stats.chunks += document_stats.chunks
        stats.entities += document_stats.entities
        stats.relations += document_stats.relations
        stats.failed_chunks += document_stats.failed_chunks
    return stats


async def clear_graph(db: AsyncSession, kb_id: UUID) -> None:
    entity_ids = select(GraphEntity.id).where(GraphEntity.kb_id == kb_id)
    await db.execute(delete(GraphRelation).where(GraphRelation.kb_id == kb_id))
    await db.execute(delete(ChunkEntity).where(ChunkEntity.entity_id.in_(entity_ids)))
    await db.execute(delete(GraphEntity).where(GraphEntity.kb_id == kb_id))
    await db.commit()


async def match_entities(
    db: AsyncSession, kb_id: UUID, query: str, limit: int = 6
) -> list[GraphEntity]:
    terms = candidate_terms(query)
    if not terms:
        return []
    exact = or_(
        GraphEntity.normalized_name.in_(terms),
        *[GraphEntity.normalized_name.contains(term) for term in terms[:4]],
    )
    rows = await db.scalars(
        select(GraphEntity)
        .where(GraphEntity.kb_id == kb_id, exact)
        .order_by(GraphEntity.mention_count.desc())
        .limit(limit)
    )
    return list(rows)


async def expand(
    db: AsyncSession,
    kb: KnowledgeBase,
    query: str,
    existing_chunk_ids: list[str],
    *,
    limit: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One-hop expansion. Returns new candidates plus a trace payload."""
    started = time.perf_counter()
    seeds = await match_entities(db, kb.id, query)
    if not seeds:
        return [], {
            "matched_entities": [],
            "added": 0,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    seed_ids = [entity.id for entity in seeds]
    edges = (
        await db.scalars(
            select(GraphRelation)
            .where(
                GraphRelation.kb_id == kb.id,
                or_(
                    GraphRelation.source_id.in_(seed_ids),
                    GraphRelation.target_id.in_(seed_ids),
                ),
            )
            .order_by(GraphRelation.weight.desc())
            .limit(40)
        )
    ).all()

    weights: dict[UUID, float] = {}
    via: dict[UUID, str] = {}
    names = {entity.id: entity.name for entity in seeds}
    for edge in edges:
        neighbour = edge.target_id if edge.source_id in seed_ids else edge.source_id
        weights[neighbour] = max(weights.get(neighbour, 0.0), float(edge.weight))
        via.setdefault(neighbour, names.get(edge.source_id) or names.get(edge.target_id) or "")

    neighbour_ids = list(weights) or seed_ids
    excluded = [UUID(item) for item in existing_chunk_ids if _is_uuid(item)]

    condition = [ChunkEntity.entity_id.in_(neighbour_ids), Chunk.kb_id == kb.id]
    if excluded:
        condition.append(Chunk.id.not_in(excluded))

    rows = (
        await db.execute(
            select(Chunk, Document.filename, ChunkEntity.entity_id)
            .join(ChunkEntity, ChunkEntity.chunk_id == Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .where(*condition)
            .limit(limit * 3)
        )
    ).all()

    entity_names = {entity.id: entity.name for entity in seeds}
    entity_names.update(
        {
            entity_id: name
            for entity_id, name in (
                await db.execute(
                    select(GraphEntity.id, GraphEntity.name).where(
                        GraphEntity.id.in_(neighbour_ids)
                    )
                )
            ).all()
        }
    )

    candidates: list[dict[str, Any]] = []
    seen: set[UUID] = set()

    for chunk, filename, entity_id in rows:
        if chunk.id in seen:
            continue
        seen.add(chunk.id)
        candidates.append(
            {
                "chunk_id": str(chunk.id),
                "document_id": str(chunk.document_id),
                "filename": filename,
                "chunk_index": chunk.chunk_index,
                "heading_path": chunk.heading_path,
                "page_number": chunk.page_number,
                "content": chunk.content,
                "graph_score": round(weights.get(entity_id, 0.5), 4),
                "graph_via": entity_names.get(entity_id) or via.get(entity_id) or "",
            }
        )

    candidates.sort(key=lambda item: item["graph_score"], reverse=True)
    candidates = candidates[:limit]
    metrics.observe_graph("expand", "hit" if candidates else "miss")
    return candidates, {
        "matched_entities": [{"id": str(entity.id), "name": entity.name} for entity in seeds],
        "added": len(candidates),
        "edges": len(edges),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


async def get_graph(
    db: AsyncSession, kb: KnowledgeBase, *, limit: int = 200
) -> dict[str, Any]:
    entities = (
        await db.scalars(
            select(GraphEntity)
            .where(GraphEntity.kb_id == kb.id)
            .order_by(GraphEntity.mention_count.desc())
            .limit(limit)
        )
    ).all()
    node_ids = {entity.id for entity in entities}
    edges = (
        await db.scalars(
            select(GraphRelation)
            .where(GraphRelation.kb_id == kb.id)
            .order_by(GraphRelation.weight.desc())
            .limit(limit * 2)
        )
    ).all()

    nodes = [
        {
            "id": str(entity.id),
            "name": entity.name,
            "type": entity.entity_type,
            "mention_count": entity.mention_count,
            "description": entity.description,
        }
        for entity in entities
    ]
    links = [
        {
            "source": str(edge.source_id),
            "target": str(edge.target_id),
            "relation": edge.relation,
            "weight": edge.weight,
        }
        for edge in edges
        if edge.source_id in node_ids and edge.target_id in node_ids
    ]
    return {"nodes": nodes, "edges": links}


async def stats(db: AsyncSession, kb_id: UUID) -> dict[str, Any]:
    entities = await db.scalar(
        select(func.count()).select_from(GraphEntity).where(GraphEntity.kb_id == kb_id)
    )
    relations = await db.scalar(
        select(func.count()).select_from(GraphRelation).where(GraphRelation.kb_id == kb_id)
    )
    mentions = await db.scalar(
        select(func.count())
        .select_from(ChunkEntity)
        .join(GraphEntity, GraphEntity.id == ChunkEntity.entity_id)
        .where(GraphEntity.kb_id == kb_id)
    )
    return {
        "entities": int(entities or 0),
        "relations": int(relations or 0),
        "mentions": int(mentions or 0),
    }
