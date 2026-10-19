"""Read-only prompt registry.

Prompt text lives in code so every variant is reviewable and versioned. The
frontend fetches this list instead of duplicating labels and descriptions.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.models.user import User
from app.rag.prompt import PROMPT_VARIANTS
from app.schemas.chat import PromptVariantOut

router = APIRouter(tags=["prompts"])
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/prompts", response_model=list[PromptVariantOut])
async def list_prompts(current_user: CurrentUser) -> list[PromptVariantOut]:
    del current_user  # authentication only; prompt text is not user-owned
    return [
        PromptVariantOut(
            id=variant.id,
            label=variant.label,
            description=variant.description,
        )
        for variant in PROMPT_VARIANTS.values()
    ]
