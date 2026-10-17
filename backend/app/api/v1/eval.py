from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.evaluation import EvalDataset, EvalRun
from app.models.user import User
from app.schemas.common import Page
from app.schemas.eval import (
    EvalCaseBulkCreate,
    EvalCaseCreate,
    EvalCaseOut,
    EvalCompareOut,
    EvalDatasetCreate,
    EvalDatasetOut,
    EvalResultOut,
    EvalRunCreate,
    EvalRunDetail,
    EvalRunOut,
)
from app.services import eval_service, kb_service
from app.services.eval_service import execute_run

router = APIRouter(tags=["eval"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


def _dataset_out(dataset: EvalDataset, case_count: int, run_count: int) -> EvalDatasetOut:
    out = EvalDatasetOut.model_validate(dataset)
    out.case_count = case_count
    out.run_count = run_count
    return out


def _run_out(run: EvalRun, name: str | None) -> EvalRunOut:
    out = EvalRunOut.model_validate(run)
    out.dataset_name = name
    return out


@router.get("/kbs/{kb_id}/eval/datasets", response_model=Page[EvalDatasetOut])
async def list_datasets(
    kb_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 20,
) -> Page[EvalDatasetOut]:
    await kb_service.get_owned_kb(db, kb_id, current_user.id)
    rows, total = await eval_service.list_datasets(db, kb_id, offset, limit)
    return Page[EvalDatasetOut](
        items=[_dataset_out(dataset, cases, runs) for dataset, cases, runs in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/kbs/{kb_id}/eval/datasets",
    response_model=EvalDatasetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    kb_id: UUID,
    payload: EvalDatasetCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> EvalDatasetOut:
    kb = await kb_service.get_owned_kb(db, kb_id, current_user.id)
    dataset = await eval_service.create_dataset(db, kb, payload.name, payload.description)
    await db.commit()
    return _dataset_out(dataset, 0, 0)


@router.delete("/eval/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    dataset = await eval_service.get_owned_dataset(db, dataset_id, current_user.id)
    await eval_service.delete_dataset(db, dataset)
    await db.commit()


@router.get("/eval/datasets/{dataset_id}/cases", response_model=Page[EvalCaseOut])
async def list_cases(
    dataset_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 100,
) -> Page[EvalCaseOut]:
    await eval_service.get_owned_dataset(db, dataset_id, current_user.id)
    items, total = await eval_service.list_cases(db, dataset_id, offset, limit)
    return Page[EvalCaseOut](
        items=[EvalCaseOut.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/eval/datasets/{dataset_id}/cases",
    response_model=EvalCaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_case(
    dataset_id: UUID,
    payload: EvalCaseCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> EvalCaseOut:
    dataset = await eval_service.get_owned_dataset(db, dataset_id, current_user.id)
    case = await eval_service.create_case(db, dataset, payload)
    await db.commit()
    return EvalCaseOut.model_validate(case)


@router.post(
    "/eval/datasets/{dataset_id}/cases/bulk",
    response_model=list[EvalCaseOut],
    status_code=status.HTTP_201_CREATED,
)
async def create_cases(
    dataset_id: UUID,
    payload: EvalCaseBulkCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> list[EvalCaseOut]:
    dataset = await eval_service.get_owned_dataset(db, dataset_id, current_user.id)
    cases = await eval_service.create_cases(db, dataset, payload.cases)
    await db.commit()
    return [EvalCaseOut.model_validate(case) for case in cases]


@router.delete("/eval/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    case = await eval_service.get_owned_case(db, case_id, current_user.id)
    await eval_service.delete_case(db, case)
    await db.commit()


@router.get("/eval/datasets/{dataset_id}/runs", response_model=Page[EvalRunOut])
async def list_runs(
    dataset_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
    offset: Offset = 0,
    limit: Limit = 20,
) -> Page[EvalRunOut]:
    dataset = await eval_service.get_owned_dataset(db, dataset_id, current_user.id)
    items, total = await eval_service.list_runs(db, dataset_id, offset, limit)
    return Page[EvalRunOut](
        items=[_run_out(run, dataset.name) for run in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/eval/datasets/{dataset_id}/runs",
    response_model=EvalRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    dataset_id: UUID,
    payload: EvalRunCreate,
    background: BackgroundTasks,
    db: DbSession,
    current_user: CurrentUser,
) -> EvalRunOut:
    dataset = await eval_service.get_owned_dataset(db, dataset_id, current_user.id)
    run = await eval_service.create_run(db, dataset, payload)
    # Commit before scheduling: the executor opens its own session.
    await db.commit()
    background.add_task(execute_run, run.id)
    return _run_out(run, dataset.name)


@router.get("/eval/runs/{run_id}", response_model=EvalRunDetail)
async def get_run(
    run_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> EvalRunDetail:
    run = await eval_service.get_owned_run(db, run_id, current_user.id)
    results = await eval_service.get_run_results(db, run.id)
    detail = EvalRunDetail.model_validate(run)
    detail.dataset_name = await eval_service.dataset_name(db, run.dataset_id)
    detail.results = [EvalResultOut.model_validate(result) for result in results]
    return detail


@router.post("/eval/runs/{run_id}/cancel", response_model=EvalRunOut)
async def cancel_run(
    run_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> EvalRunOut:
    run = await eval_service.get_owned_run(db, run_id, current_user.id)
    run = await eval_service.cancel_run(db, run)
    await db.commit()
    return _run_out(run, await eval_service.dataset_name(db, run.dataset_id))


@router.get("/eval/compare", response_model=EvalCompareOut)
async def compare_runs(
    base_run_id: UUID,
    candidate_run_id: UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> EvalCompareOut:
    base = await eval_service.get_owned_run(db, base_run_id, current_user.id)
    candidate = await eval_service.get_owned_run(db, candidate_run_id, current_user.id)
    cases, counts = await eval_service.compare_runs(db, base, candidate)
    name = await eval_service.dataset_name(db, base.dataset_id)
    return EvalCompareOut(
        base=_run_out(base, name),
        candidate=_run_out(candidate, name),
        metric_deltas=eval_service.metric_deltas(base.metrics, candidate.metrics),
        cases=cases,
        improved=counts["better"],
        degraded=counts["worse"],
        unchanged=counts["same"],
    )
