"""A dedicated tunnel target exposing only the authenticated ADO webhook."""

import argparse
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MAX_BODY = 262144


def create_ingress(control_url: str = "http://127.0.0.1:8768", transport=None) -> FastAPI:
    destination = urlsplit(control_url)
    if (
        destination.scheme != "http"
        or destination.hostname not in {"127.0.0.1", "localhost"}
        or destination.username
        or destination.password
        or destination.path not in {"", "/"}
        or destination.query
        or destination.fragment
    ):
        raise ValueError("The webhook ingress must forward to a loopback control URL")
    client = httpx.AsyncClient(
        base_url=control_url.rstrip("/"),
        timeout=15,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok", "service": "agent-control-webhook-ingress"}

    @app.post("/webhooks/ado")
    async def receive(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith(("Basic ", "Bearer ")):
            return JSONResponse({"detail": "Webhook authentication required"}, status_code=401)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > MAX_BODY:
                return JSONResponse({"detail": "Webhook too large"}, status_code=413)
        try:
            response = await client.post(
                "/webhooks/ado",
                content=bytes(body),
                headers={"Authorization": authorization, "Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            return JSONResponse({"detail": "Local Agent Desk is unavailable"}, status_code=503)
        if response.status_code != 202:
            status = (
                response.status_code if response.status_code in {400, 401, 413, 429, 503} else 502
            )
            return JSONResponse(
                {"detail": "Local webhook receiver refused the request"}, status_code=status
            )
        try:
            result = response.json()
            if result.get("accepted") is not True or not isinstance(result.get("duplicate"), bool):
                raise ValueError
        except (ValueError, AttributeError):
            return JSONResponse(
                {"detail": "Invalid local webhook acknowledgement"}, status_code=502
            )
        return JSONResponse({"accepted": True, "duplicate": result["duplicate"]}, status_code=202)

    return app


def main():
    parser = argparse.ArgumentParser(description="Dedicated ADO webhook ingress for a local tunnel")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--control-url", default="http://127.0.0.1:8768")
    args = parser.parse_args()
    uvicorn.run(
        create_ingress(args.control_url), host="127.0.0.1", port=args.port, access_log=False
    )


if __name__ == "__main__":
    main()
