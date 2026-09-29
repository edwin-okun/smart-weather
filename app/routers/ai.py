from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_scopes
from app.exceptions import AIServiceError, AITimeoutError
from app.permissions import AI_ASK
from app.schemas.ai import AskRequest, AskResponse
from app.schemas.auth import AuthenticatedClient
from app.services.ai_service import ask_weather_assistant

router = APIRouter(tags=["ai"])


@router.post(
    "/ai/ask",
    response_model=AskResponse,
    operation_id="ask_weather_assistant",
    summary="Ask the weather assistant a question",
    description=(
        "Runs an LLM agent that answers weather questions, calling weather tools "
        "as needed. Stateless: each request is independent."
    ),
)
async def ask(
    body: AskRequest,
    client: Annotated[AuthenticatedClient, Depends(require_scopes(AI_ASK))],
):
    try:
        return await ask_weather_assistant(body.question, client)
    except AITimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
