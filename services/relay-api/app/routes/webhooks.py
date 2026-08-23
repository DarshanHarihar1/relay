from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.providers.webhooks import (
    VoiceWebhookHandler,
    WebhookPayloadTooLarge,
    WebhookRateLimited,
    WebhookVerificationError,
)


router = APIRouter(tags=["webhooks"])


async def get_webhook_handler(request: Request) -> VoiceWebhookHandler:
    handler = getattr(request.app.state, "voice_webhook_handler", None)
    if handler is None:
        raise HTTPException(status_code=503)
    return handler


@router.post("/v1/webhooks/vapi", status_code=204, response_class=Response)
async def receive_vapi_webhook(
    request: Request,
    handler: VoiceWebhookHandler = Depends(get_webhook_handler),
) -> Response:
    raw_body = await request.body()
    try:
        await handler.handle_vapi(raw_body, request.headers, str(request.url))
    except WebhookPayloadTooLarge as error:
        raise HTTPException(status_code=413) from error
    except WebhookRateLimited as error:
        raise HTTPException(status_code=429) from error
    except WebhookVerificationError as error:
        raise HTTPException(status_code=401) from error
    except LookupError as error:
        raise HTTPException(status_code=404) from error
    except ValueError as error:
        raise HTTPException(status_code=400) from error
    return Response(status_code=204)


@router.post("/v1/webhooks/twilio", status_code=204, response_class=Response)
async def receive_twilio_webhook(
    request: Request,
    handler: VoiceWebhookHandler = Depends(get_webhook_handler),
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    raw_body = await request.body()
    headers = {"x-twilio-signature": x_twilio_signature} if x_twilio_signature else {}
    try:
        await handler.handle_twilio(raw_body, headers, str(request.url))
    except WebhookPayloadTooLarge as error:
        raise HTTPException(status_code=413) from error
    except WebhookRateLimited as error:
        raise HTTPException(status_code=429) from error
    except WebhookVerificationError as error:
        raise HTTPException(status_code=401) from error
    except LookupError as error:
        raise HTTPException(status_code=404) from error
    except ValueError as error:
        raise HTTPException(status_code=400) from error
    return Response(status_code=204)


__all__ = ["get_webhook_handler", "router", "receive_twilio_webhook", "receive_vapi_webhook"]
