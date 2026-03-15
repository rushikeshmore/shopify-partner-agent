"""
Shopify Partner API client.

Async GraphQL client with automatic pagination and rate limiting.
All methods are async. Returns parsed JSON (dicts / lists).
Auth is a static token — no OAuth, no refresh needed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from queries import QUERY_APP_DETAILS, QUERY_APP_EVENTS, QUERY_TRANSACTIONS

API_VERSION = "2026-01"


class ShopifyPartnerError(Exception):
    """Raised when a Shopify Partner API call fails."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Shopify Partner API {status_code}: {message}")


class ShopifyPartnerClient:
    """Async client for the Shopify Partner GraphQL API."""

    def __init__(
        self,
        org_id: str,
        access_token: str,
        app_ids: list[str],
        api_version: str = API_VERSION,
    ) -> None:
        self.org_id = org_id
        self.access_token = access_token
        self.app_ids = app_ids
        self.api_version = api_version
        self.endpoint = (
            f"https://partners.shopify.com/{org_id}/api/{api_version}/graphql.json"
        )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": self.access_token,
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a single GraphQL request with rate limiting.

        Handles both HTTP errors and GraphQL errors (which come back as 200).
        Retries once on 429 (rate limit) with 2s backoff.
        """
        client = await self._get_client()
        body: dict = {"query": query}
        if variables:
            body["variables"] = variables

        try:
            resp = await client.post(self.endpoint, json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                await asyncio.sleep(2)
                resp = await client.post(self.endpoint, json=body)
                resp.raise_for_status()
            else:
                text = e.response.text[:300] if e.response else "No response body"
                raise ShopifyPartnerError(e.response.status_code, text) from e
        except httpx.RequestError as e:
            raise ShopifyPartnerError(0, f"Connection error: {e}") from e

        data = resp.json()

        # GraphQL can return 200 with errors in the body
        if "errors" in data:
            messages = [err.get("message", str(err)) for err in data["errors"]]
            raise ShopifyPartnerError(200, "; ".join(messages))

        # Rate limit: 4 req/sec — sleep 0.3s between requests
        await asyncio.sleep(0.3)

        return data.get("data", {})

    async def _graphql_paginated(
        self,
        query: str,
        variables: dict,
        path: list[str],
        limit: int = 0,
    ) -> list:
        """Execute a paginated GraphQL query following Relay cursors.

        Args:
            query: The GraphQL query string with $first and $after variables.
            variables: Initial variables (must include 'first').
            path: JSON path to the connection object (e.g., ['transactions']
                  or ['app', 'events']).
            limit: Max total items to return. 0 = unlimited.

        Returns:
            List of all node objects across all pages.
        """
        all_nodes: list = []
        has_next = True

        while has_next:
            data = await self._graphql(query, variables)

            # Navigate to the connection object
            connection = data
            for key in path:
                connection = connection.get(key, {})

            edges = connection.get("edges", [])
            for edge in edges:
                all_nodes.append(edge.get("node", {}))
                if limit and len(all_nodes) >= limit:
                    return all_nodes[:limit]

            page_info = connection.get("pageInfo", {})
            has_next = page_info.get("hasNextPage", False)
            if has_next and edges:
                # Partner API: cursor is on each edge, not endCursor on pageInfo
                variables["after"] = edges[-1].get("cursor")

        return all_nodes

    # --- App Methods ---

    async def get_app(self, app_id: str) -> dict:
        """Get details for a specific app by GID."""
        app_id = _normalize_app_id(app_id)
        data = await self._graphql(QUERY_APP_DETAILS, {"appId": app_id})
        return data.get("app", {})

    # --- Transaction Methods ---

    async def get_transactions(
        self,
        *,
        app_id: str = "",
        created_at_min: str = "",
        created_at_max: str = "",
        types: list[str] | None = None,
        limit: int = 100,
    ) -> list:
        """Get revenue transactions with optional filtering.

        Args:
            app_id: Filter by app GID (optional).
            created_at_min: ISO datetime string (optional).
            created_at_max: ISO datetime string (optional).
            types: List of TransactionType strings (optional).
            limit: Max transactions to return (default 100).
        """
        variables: dict = {"first": min(limit, 100)}
        if app_id:
            variables["appId"] = _normalize_app_id(app_id)
        if created_at_min:
            variables["createdAtMin"] = created_at_min
        if created_at_max:
            variables["createdAtMax"] = created_at_max
        if types:
            variables["types"] = types

        return await self._graphql_paginated(
            QUERY_TRANSACTIONS,
            variables,
            path=["transactions"],
            limit=limit,
        )

    # --- App Event Methods ---

    async def get_app_events(
        self,
        app_id: str,
        *,
        types: list[str] | None = None,
        occurred_at_min: str = "",
        occurred_at_max: str = "",
        limit: int = 100,
    ) -> list:
        """Get app events (installs, uninstalls, charges, etc.).

        Args:
            app_id: App GID (required).
            types: List of AppEventType strings (optional).
            occurred_at_min: ISO datetime string (optional).
            occurred_at_max: ISO datetime string (optional).
            limit: Max events to return (default 100).
        """
        app_id = _normalize_app_id(app_id)
        variables: dict = {"appId": app_id, "first": min(limit, 100)}
        if types:
            variables["types"] = types
        if occurred_at_min:
            variables["occurredAtMin"] = occurred_at_min
        if occurred_at_max:
            variables["occurredAtMax"] = occurred_at_max

        return await self._graphql_paginated(
            QUERY_APP_EVENTS,
            variables,
            path=["app", "events"],
            limit=limit,
        )


def _normalize_app_id(app_id: str) -> str:
    """Normalize app ID to GID format.

    Accepts '1234' or 'gid://partners/App/1234'.
    Always returns 'gid://partners/App/1234'.
    """
    if app_id.startswith("gid://"):
        return app_id
    return f"gid://partners/App/{app_id}"


def create_client() -> ShopifyPartnerClient:
    """Create a ShopifyPartnerClient from environment variables."""
    load_dotenv(Path(__file__).parent / ".env")

    org_id = os.environ.get("SHOPIFY_ORG_ID", "")
    access_token = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
    api_version = os.environ.get("SHOPIFY_API_VERSION", API_VERSION)
    app_ids_raw = os.environ.get("SHOPIFY_APP_IDS", "")

    if not org_id:
        raise ShopifyPartnerError(0, "SHOPIFY_ORG_ID not set in .env")
    if not access_token:
        raise ShopifyPartnerError(0, "SHOPIFY_ACCESS_TOKEN not set in .env")

    app_ids = [aid.strip() for aid in app_ids_raw.split(",") if aid.strip()]

    return ShopifyPartnerClient(
        org_id=org_id,
        access_token=access_token,
        app_ids=app_ids,
        api_version=api_version,
    )
