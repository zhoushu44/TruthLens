"""
Chat API endpoint.

POST /api/v1/chat — Proxy to LLM APIs with streaming support.
"""

import uuid
import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.apikey_auth import require_api_key_if_configured

from app.models.chat import ChatRequest, ChatResponse
from app.utils.llm_clients import get_llm_client
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat")
async def chat_with_model(
    request: ChatRequest,
    _key_info: Optional[dict] = Depends(require_api_key_if_configured),
):
    """
    Send a message to the specified LLM model.
    
    Supports both streaming and non-streaming responses.
    The conversation history is forwarded to the model for context.
    """
    settings = get_settings()
    llm_client = get_llm_client()

    # Validate model
    if request.model_id not in settings.supported_models:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model: {request.model_id}. "
                   f"Supported: {list(settings.supported_models.keys())}",
        )

    # Check API key availability
    model_info = settings.supported_models[request.model_id]
    api_key_field = model_info.get("api_key_field")
    if api_key_field and not getattr(settings, api_key_field, None):
        raise HTTPException(
            status_code=400,
            detail=f"API key not configured for {model_info['provider']}",
        )

    conversation_id = request.conversation_id or str(uuid.uuid4())
    history = [msg.model_dump() for msg in request.conversation_history]

    try:
        if request.stream:
            # Streaming response
            async def generate():
                try:
                    async for chunk in llm_client.chat_stream(
                        model_id=request.model_id,
                        message=request.message,
                        conversation_history=history,
                    ):
                        yield f"data: {chunk}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    logger.error(f"Chat streaming error: {e}")
                    yield f"data: [ERROR] {str(e)}\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Conversation-ID": conversation_id,
                },
            )
        else:
            # Non-streaming response
            response_text = await llm_client.chat(
                model_id=request.model_id,
                message=request.message,
                conversation_history=history,
            )

            return ChatResponse(
                conversation_id=conversation_id,
                model_id=request.model_id,
                response=response_text,
            )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Chat failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get response from {request.model_id}: {str(e)}",
        )
