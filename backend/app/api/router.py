from fastapi import APIRouter

from app.api.v1 import (
    api_keys,
    auth,
    chat,
    conversations,
    corpus,
    dashboard,
    documents,
    eval,
    feedback,
    graph,
    health,
    kbs,
    mcp,
    ops,
    prompts,
    public,
    retrieval,
    reviews,
    sources,
    traces,
    usage,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(kbs.router)
api_router.include_router(documents.router)
api_router.include_router(conversations.router)
api_router.include_router(chat.router)
api_router.include_router(feedback.router)
api_router.include_router(prompts.router)
api_router.include_router(retrieval.router)
api_router.include_router(eval.router)
api_router.include_router(reviews.router)
api_router.include_router(dashboard.router)
api_router.include_router(ops.router)
api_router.include_router(api_keys.router)
api_router.include_router(public.router)
api_router.include_router(mcp.router)
api_router.include_router(graph.router)
api_router.include_router(sources.router)
api_router.include_router(usage.router)
api_router.include_router(webhooks.router)
api_router.include_router(corpus.router)
api_router.include_router(traces.router)
