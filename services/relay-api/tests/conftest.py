from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from google.cloud.firestore_v1 import AsyncClient


def _emulator_is_available(host: str) -> bool:
    hostname, separator, port = host.rpartition(":")
    if not separator or not hostname or not port.isdecimal():
        return False
    try:
        with socket.create_connection((hostname, int(port)), timeout=0.25):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture
async def firestore_client() -> AsyncIterator[AsyncClient]:
    host = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if not host or not _emulator_is_available(host):
        pytest.skip("Firestore emulator is required")

    client = AsyncClient(project=f"relay-test-{uuid4().hex}")
    try:
        yield client
    finally:
        client.close()


@pytest_asyncio.fixture
async def actions(firestore_client: AsyncClient):
    from app.repositories.actions import FirestoreActionRepository

    return FirestoreActionRepository(firestore_client)


@pytest_asyncio.fixture
async def events(firestore_client: AsyncClient):
    from app.repositories.events import FirestoreEventRepository

    return FirestoreEventRepository(firestore_client)
