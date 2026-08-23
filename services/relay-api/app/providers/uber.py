from __future__ import annotations

from urllib.parse import urlencode

from app.contracts import ActionRecord


class UberDeepLinkBuilder:
    def __init__(self, *, client_id: str, base_url: str = "https://m.uber.com/ul/") -> None:
        if not client_id.strip():
            raise ValueError("Uber client ID must not be blank")
        self._client_id = client_id
        self._base_url = base_url

    def build(self, action: ActionRecord) -> str:
        if action.type != "uber_deep_link":
            raise ValueError("UberDeepLinkBuilder only accepts uber_deep_link actions")
        snapshot = action.authorization_snapshot
        if snapshot.handoff_label != "Open Uber":
            raise ValueError("Uber handoff label is invalid")
        query = urlencode(
            {
                "action": "setPickup",
                "pickup[formatted_address]": snapshot.pickup,
                "dropoff[formatted_address]": snapshot.destination,
                "client_id": self._client_id,
            }
        )
        return f"{self._base_url}?{query}"


__all__ = ["UberDeepLinkBuilder"]
