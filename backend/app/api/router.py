from fastapi import APIRouter

from app.api.v1 import (
    api_keys,
    auth,
    chat,
    conversations,
    dashboard,
    documents,
    eval,
    feedback,
    health,
    kbs,
    ops,
    prompts,
    public,
    retrieval,
    reviews,
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
