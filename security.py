"""Authentification du service memory.

Convention du cluster (comme le Core) : `Authorization: Bearer <NERON_API_KEY>`,
comparaison en temps constant.

Fermé par défaut : sans NERON_API_KEY, toutes les routes protégées répondent
503. L'authentification ne peut être coupée qu'explicitement, pour le
développement, avec NERON_MEMORY_AUTH_DEV_MODE=true. Jusqu'ici le service
n'avait AUCUNE authentification, alors que Caddy l'expose (/memoire) et que, sur
des serveurs distincts, il écoute sur une adresse joignable du réseau.

Restent publiques : /health et /metrics (supervision, homelab du SelfModel).
Le panneau Mémoire du Dashboard passe par Caddy, qui doit injecter la clé.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("memory.security")

PUBLIC_PATHS = frozenset({"/health", "/metrics"})
DEV_MODE_ENV = "NERON_MEMORY_AUTH_DEV_MODE"


def _expected_key() -> str:
    return os.getenv("NERON_API_KEY", "").strip()


def _dev_mode() -> bool:
    return os.getenv(DEV_MODE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _supplied_key(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip()
    return ""


def install_api_key_guard(app: FastAPI) -> None:
    """Protège toutes les routes de `app` sauf PUBLIC_PATHS (et OPTIONS)."""
    if not _expected_key():
        if _dev_mode():
            logger.warning("memory: authentification désactivée (%s=true)", DEV_MODE_ENV)
        else:
            logger.error(
                "memory: NERON_API_KEY absente — les routes protégées répondront 503 "
                "(%s=true pour le développement)", DEV_MODE_ENV,
            )

    @app.middleware("http")
    async def _require_api_key(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        expected = _expected_key()
        if not expected:
            if _dev_mode():
                return await call_next(request)
            return JSONResponse(
                {"detail": "Authentification du service memory non configurée."},
                status_code=503,
            )

        supplied = _supplied_key(request)
        if not supplied or not hmac.compare_digest(supplied, expected):
            return JSONResponse(
                {"detail": "Invalid or missing API key"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
