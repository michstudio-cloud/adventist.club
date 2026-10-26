"""«Ver como…» (031): the coordination opens the judge's or a director's screens in a
read-only sandbox. The web app sends `X-Event-Preview: <who>` on EVERY call while in it, and
this middleware answers 403 to any write that carries the header — whatever the route — so a
preview can never save, even if a screen forgot to disable its button.
"""
import json

PREVIEW_HEADER = b"x-event-preview"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PREVIEW_DETAIL = "Vista previa: no se guarda nada. Sal de la vista previa para hacer cambios."


class PreviewReadOnlyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method", "GET") not in SAFE_METHODS:
            if any(name.lower() == PREVIEW_HEADER and value.strip() for name, value in scope.get("headers") or []):
                body = json.dumps({"detail": PREVIEW_DETAIL, "code": "event_preview"}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
