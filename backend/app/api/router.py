from fastapi import APIRouter

from app.api.v1 import auth, chat, conversations, documents, health, kbs, retrieval

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(kbs.router)
api_router.include_router(documents.router)
api_router.include_router(conversations.router)
api_router.include_router(chat.router)
api_router.include_router(retrieval.router)
