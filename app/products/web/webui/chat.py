"""WebUI chat API routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.control.model import registry as model_registry
from app.platform.auth.middleware import verify_webui_key
from app.products.openai.router import chat_completions_endpoint
from app.products.openai.images import generate as generate_image
from app.products.openai.schemas import ChatCompletionRequest, ImageGenerationRequest

router = APIRouter(prefix="/webui/api", dependencies=[Depends(verify_webui_key)], tags=["WebUI - Chat"])


def _model_type(spec) -> str:
    if spec.is_image_edit():
        return "image_edit"
    if spec.is_image():
        return "image"
    if spec.is_video():
        return "video"
    if spec.is_voice():
        return "voice"
    return "chat"


def _model_entry(model_id: str, *, model_type: str, source: str, name: str | None = None) -> dict:
    return {
        "id": model_id,
        "object": "model",
        "name": name or model_id,
        "type": model_type,
        "source": source,
    }


@router.get("/models")
async def list_webui_models(request: Request):
    directory = getattr(request.app.state, "upstream_directory", None)
    if directory is not None:
        await directory.sync_if_changed()

    models_by_id: dict[str, dict] = {}
    for spec in model_registry.list_enabled():
        models_by_id[spec.model_name] = _model_entry(
            spec.model_name,
            model_type=_model_type(spec),
            source="local",
            name=spec.public_name,
        )

    upstream_model_ids = directory.models() if directory is not None else []
    for model_id in upstream_model_ids:
        models_by_id.setdefault(
            model_id,
            _model_entry(model_id, model_type="chat", source="upstream"),
        )

    models = list(models_by_id.values())
    return JSONResponse({"object": "list", "models": models, "data": models})


@router.post("/chat/completions")
async def webui_chat_completions(req: ChatCompletionRequest, request: Request):
    return await chat_completions_endpoint(req, request)


@router.post("/images/generations")
async def webui_image_generations(req: ImageGenerationRequest):
    result = await generate_image(
        model=req.model,
        prompt=req.prompt,
        n=req.n or 1,
        size=req.size or "1024x1024",
        aspect_ratio=req.aspect_ratio,
        response_format=req.response_format or "url",
        stream=False,
        chat_format=False,
    )
    return JSONResponse(result)


__all__ = ["router"]
