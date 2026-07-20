from fastapi import APIRouter

router = APIRouter()


@router.get(
    "/health",
    operation_id="health",
    summary="Check service health",
    description="Returns a simple status payload for service health checks.",
    tags=["system"],
)
def health():
    return {"status": "ok"}
