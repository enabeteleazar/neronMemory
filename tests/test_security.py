"""Authentification du service memory (memory/security.py).

Le service n'avait aucune authentification : n'importe quel processus de la
machine, ou du réseau sur des serveurs distincts, pouvait lire la mémoire de
l'utilisateur ou l'effacer (POST /memory/forget), et Caddy l'exposait (/memoire).

REGLE ABSOLUE (conftest.py) : aucun test ne touche la base réelle. Le client
n'est donc PAS ouvert en contexte : le lifespan (qui ouvre la base) ne tourne
pas. Le garde répond avant toute route ; une requête authentifiée atteint la
route, qui échoue faute d'état — ce qui suffit à prouver qu'elle est passée.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import memory.app as app_module

KEY = "cle-memory"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("NERON_MEMORY_AUTH_DEV_MODE", raising=False)
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/memory/forget"),
        ("post", "/memory/remember"),
        ("post", "/memory/recall"),
        ("post", "/memory/observe"),
        ("get", "/memory/facts"),
        ("get", "/memory/search?q=x"),
        ("get", "/knowledge/query?q=x"),
        ("get", "/status"),
    ],
)
def test_protected_routes_refuse_missing_or_wrong_key(client, monkeypatch, method, path):
    monkeypatch.setenv("NERON_API_KEY", KEY)
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"Authorization": "Bearer mauvaise"}).status_code == 401


def test_valid_key_passes_the_guard(client, monkeypatch):
    monkeypatch.setenv("NERON_API_KEY", KEY)
    response = client.get("/memory/facts", headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code not in {401, 503}


def test_supervision_stays_public(client, monkeypatch):
    monkeypatch.setenv("NERON_API_KEY", KEY)
    assert client.get("/metrics").status_code == 200


def test_missing_key_configuration_fails_closed(client, monkeypatch):
    monkeypatch.delenv("NERON_API_KEY", raising=False)
    assert client.post("/memory/forget").status_code == 503


def test_dev_mode_is_an_explicit_opt_out(client, monkeypatch):
    monkeypatch.delenv("NERON_API_KEY", raising=False)
    monkeypatch.setenv("NERON_MEMORY_AUTH_DEV_MODE", "true")
    assert client.post("/memory/forget").status_code not in {401, 503}
