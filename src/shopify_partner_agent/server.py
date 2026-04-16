"""
Shopify Partner Agent -- MCP Server.

25 tools that give Claude full access to Shopify Partner analytics.
Query your Shopify app business in natural language.

Run via Claude Code:
    claude mcp add shopify-partner-agent /path/to/.venv/bin/python /path/to/server.py
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from mcp.server.fastmcp import Context, FastMCP

from .analytics import (
    compute_business_digest,
    compute_churn_analysis,
    compute_churn_risk,
    compute_credits_adjustments,
    compute_customer_ltv,
    compute_growth_velocity,
    compute_install_patterns,
    compute_merchant_health,
    compute_merchant_timeline,
    compute_mrr_movement,
    compute_payout_summary,
    compute_plan_performance,
    compute_referral_revenue,
    compute_retention_cohorts,
    compute_revenue_forecast,
    compute_revenue_summary,
    compute_trial_funnel,
    find_revenue_anomalies,
    parse_period,
    previous_period,
)
from .client import ShopifyPartnerClient, ShopifyPartnerError, create_client

# stdio transport: all logging must go to stderr, never stdout
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)


@dataclass
class AppContext:
    """Lifespan context holding the Shopify Partner client."""

    sp: ShopifyPartnerClient


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize and clean up the Shopify Partner client.

    Args:
        app: The FastMCP application instance.

    Yields:
        AppContext containing the initialized ShopifyPartnerClient.
    """
    sp = create_client()
    try:
        yield AppContext(sp=sp)
    finally:
        await sp.close()


mcp = FastMCP("Shopify Partner", lifespan=lifespan)


def _event_in_period(event: dict, start: date, end: date) -> bool:
    """Check if an event's occurredAt date falls within a period."""
    date_str = event.get("occurredAt", "")
    if not date_str:
        return False
    try:
        event_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        return start <= event_date <= end
    except ValueError:
        return False


def _error(msg: str) -> str:
    """Return a JSON error string for tool responses.

    Args:
        msg: Error message to include.

    Returns:
        JSON string with a single "error" key.
    """
    return json.dumps({"error": msg})


def _get_sp(ctx: Context) -> ShopifyPartnerClient:
    """Extract the Shopify Partner client from the MCP context.

    Args:
        ctx: FastMCP request context with lifespan state.

    Returns:
        The ShopifyPartnerClient instance from the lifespan context.
    """
    return ctx.request_context.lifespan_context.sp


# =============================================================================
# Core Data Tools (5)
# Core tools expose raw API data -- Claude uses these for ad-hoc questions.
# =============================================================================


@mcp.tool()
async def discover_apps(ctx: Context) -> str:
    """Discover all Shopify apps in your partner account.

    Checks configured app IDs from .env AND scans recent transactions
    for additional apps. Returns app GIDs and names -- use these IDs
    for all other tools.

    Returns:
        JSON string with keys: apps (list of id/name dicts), count.
    """
    try:
        sp = _get_sp(ctx)
        apps: dict[str, str] = {}

        # 1. Configured app IDs
        for app_id in sp.app_ids:
            try:
                app = await sp.get_app(app_id)
                apps[app.get("id", app_id)] = app.get("name", "Unknown")
            except ShopifyPartnerError:
                apps[app_id] = "Unknown (API error)"

        # 2. Discover from recent transactions
        try:
            txns = await sp.get_transactions(limit=200)
            for txn in txns:
                app_info = txn.get("app", {})
                if app_info and app_info.get("id"):
                    apps[app_info["id"]] = app_info.get("name", "Unknown")
        except ShopifyPartnerError:
            pass  # Transaction scan is best-effort

        result = [{"id": gid, "name": name} for gid, name in apps.items()]
        if not result:
            return json.dumps(
                {
                    "apps": [],
                    "message": "No apps found. Add GIDs to SHOPIFY_APP_IDS in .env.",
                }
            )
        return json.dumps({"apps": result, "count": len(result)}, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_app_details(app_id: str, ctx: Context) -> str:
    """Get details for a specific Shopify app.

    Args:
        app_id: App GID (e.g., 'gid://partners/App/1234') or numeric ID.

    Returns:
        JSON string with app name, API key, and GID.
    """
    try:
        sp = _get_sp(ctx)
        app = await sp.get_app(app_id)
        if not app:
            return _error(f"App not found: {app_id}")
        return json.dumps(app, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_app_events(
    app_id: str,
    ctx: Context,
    event_types: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
) -> str:
    """Get app events (installs, uninstalls, charges, etc.) for a specific app.

    Args:
        app_id: App GID or numeric ID (required).
        event_types: Comma-separated filter. Available types:
            RELATIONSHIP_INSTALLED, RELATIONSHIP_UNINSTALLED,
            RELATIONSHIP_DEACTIVATED, RELATIONSHIP_REACTIVATED,
            SUBSCRIPTION_CHARGE_ACCEPTED, SUBSCRIPTION_CHARGE_ACTIVATED,
            SUBSCRIPTION_CHARGE_CANCELED, SUBSCRIPTION_CHARGE_DECLINED,
            ONE_TIME_CHARGE_ACCEPTED, ONE_TIME_CHARGE_DECLINED,
            CREDIT_APPLIED, CREDIT_PENDING, CREDIT_FAILED
        date_from: Start date (YYYY-MM-DD format, optional).
        date_to: End date (YYYY-MM-DD format, optional).
        limit: Max events to return (default 100).

    Returns:
        JSON string with keys: events (list with type, shop, date),
        count, app_id.
    """
    try:
        sp = _get_sp(ctx)
        types = (
            [t.strip() for t in event_types.split(",") if t.strip()]
            if event_types
            else None
        )
        occurred_at_min = f"{date_from}T00:00:00Z" if date_from else ""
        occurred_at_max = f"{date_to}T23:59:59Z" if date_to else ""

        events = await sp.get_app_events(
            app_id,
            types=types,
            occurred_at_min=occurred_at_min,
            occurred_at_max=occurred_at_max,
            limit=limit,
        )

        result = []
        for e in events:
            entry: dict = {
                "type": e.get("type", "Unknown"),
                "shop": e.get("shop", {}).get("myshopifyDomain", "N/A"),
                "shop_name": e.get("shop", {}).get("name", "N/A"),
                "occurred_at": e.get("occurredAt", ""),
            }
            # Include uninstall reason if available
            if e.get("reason"):
                entry["reason"] = e["reason"]
            if e.get("description"):
                entry["description"] = e["description"]
            # Include charge amount if available
            charge = e.get("charge", {})
            if charge:
                entry["charge_amount"] = charge.get("amount", {}).get("amount", "")
                entry["charge_currency"] = charge.get("amount", {}).get(
                    "currencyCode", ""
                )
            result.append(entry)

        return json.dumps(
            {"events": result, "count": len(result), "app_id": app_id},
            indent=2,
        )
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_transactions(
    ctx: Context,
    app_id: str = "",
    date_from: str = "",
    date_to: str = "",
    transaction_types: str = "",
    limit: int = 100,
) -> str:
    """Get revenue transactions from your Shopify Partner account.

    Args:
        app_id: Filter by specific app (optional).
        date_from: Start date YYYY-MM-DD (optional).
        date_to: End date YYYY-MM-DD (optional).
        transaction_types: Comma-separated filter. Types:
            APP_SUBSCRIPTION_SALE, APP_USAGE_SALE, APP_ONE_TIME_SALE,
            APP_SALE_ADJUSTMENT, APP_SALE_CREDIT, SERVICE_SALE,
            REFERRAL_TRANSACTION
        limit: Max transactions (default 100).

    Returns:
        JSON string with keys: transactions (list with type, amount,
        app, shop, date), count.
    """
    try:
        sp = _get_sp(ctx)
        types = (
            [t.strip() for t in transaction_types.split(",") if t.strip()]
            if transaction_types
            else None
        )
        created_at_min = f"{date_from}T00:00:00Z" if date_from else ""
        created_at_max = f"{date_to}T23:59:59Z" if date_to else ""

        txns = await sp.get_transactions(
            app_id=app_id,
            created_at_min=created_at_min,
            created_at_max=created_at_max,
            types=types,
            limit=limit,
        )

        result = []
        for t in txns:
            net = t.get("netAmount", {})
            gross = t.get("grossAmount", {})
            fee = t.get("shopifyFee", {})
            entry: dict = {
                "type": t.get("__typename", "Unknown"),
                "created_at": t.get("createdAt", ""),
                "app": t.get("app", {}).get("name", "N/A"),
                "shop": t.get("shop", {}).get("myshopifyDomain", "N/A"),
            }
            if net:
                entry["net_amount"] = net.get("amount", "0")
                entry["currency"] = net.get("currencyCode", "USD")
            if gross:
                entry["gross_amount"] = gross.get("amount", "0")
            if fee:
                entry["shopify_fee"] = fee.get("amount", "0")
            if t.get("billingInterval"):
                entry["billing_interval"] = t["billingInterval"]
            result.append(entry)

        if not result:
            return json.dumps(
                {
                    "transactions": [],
                    "count": 0,
                    "message": "No transactions found for the given filters.",
                }
            )
        return json.dumps({"transactions": result, "count": len(result)}, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_merchants(
    app_id: str,
    ctx: Context,
    status: str = "active",
) -> str:
    """List merchants (stores) using a specific app.

    Args:
        app_id: App GID or numeric ID (required).
        status: 'active' (current installs), 'churned' (uninstalled),
                or 'all' (both). Default 'active'.

    Derived from app events.

    Returns:
        JSON string with keys: merchants (list with shop, status,
        dates, reasons), count, summary.
    """
    try:
        sp = _get_sp(ctx)

        # Fetch install and uninstall events
        install_events = await sp.get_app_events(
            app_id,
            types=["RELATIONSHIP_INSTALLED"],
            limit=500,
        )
        uninstall_events = await sp.get_app_events(
            app_id,
            types=["RELATIONSHIP_UNINSTALLED"],
            limit=500,
        )

        # Build merchant timeline
        merchants: dict[str, dict] = {}
        for e in install_events:
            domain = e.get("shop", {}).get("myshopifyDomain", "")
            if not domain:
                continue
            merchants[domain] = {
                "shop": domain,
                "shop_name": e.get("shop", {}).get("name", "N/A"),
                "installed_at": e.get("occurredAt", ""),
                "status": "active",
            }

        for e in uninstall_events:
            domain = e.get("shop", {}).get("myshopifyDomain", "")
            if not domain:
                continue
            if domain in merchants:
                merchants[domain]["status"] = "churned"
                merchants[domain]["uninstalled_at"] = e.get("occurredAt", "")
                if e.get("reason"):
                    merchants[domain]["uninstall_reason"] = e["reason"]
                if e.get("description"):
                    merchants[domain]["uninstall_description"] = e["description"]
            else:
                merchants[domain] = {
                    "shop": domain,
                    "shop_name": e.get("shop", {}).get("name", "N/A"),
                    "status": "churned",
                    "uninstalled_at": e.get("occurredAt", ""),
                    "uninstall_reason": e.get("reason", ""),
                    "uninstall_description": e.get("description", ""),
                }

        # Filter by status
        result = list(merchants.values())
        if status == "active":
            result = [m for m in result if m["status"] == "active"]
        elif status == "churned":
            result = [m for m in result if m["status"] == "churned"]

        # Sort: active by install date, churned by uninstall date
        result.sort(
            key=lambda m: m.get("uninstalled_at", m.get("installed_at", "")),
            reverse=True,
        )

        summary = {
            "total_active": sum(
                1 for m in merchants.values() if m["status"] == "active"
            ),
            "total_churned": sum(
                1 for m in merchants.values() if m["status"] == "churned"
            ),
        }

        return json.dumps(
            {"merchants": result, "count": len(result), "summary": summary},
            indent=2,
        )
    except ShopifyPartnerError as e:
        return _error(str(e))


# =============================================================================
# Revenue Analytics Tools (4)
# Revenue tools delegate to analytics.py pure functions -- keeps server.py thin.
# =============================================================================


@mcp.tool()
async def get_revenue_summary(
    ctx: Context,
    app_id: str = "",
    period: str = "30d",
) -> str:
    """Get revenue analytics: MRR, ARR, total revenue, growth rate, ARPU.

    Args:
        app_id: Filter by app (optional, defaults to all apps).
        period: '7d', '30d', '90d', '1y', or 'YYYY-MM-DD:YYYY-MM-DD'.

    Returns:
        JSON string with MRR, ARR, total net revenue, Shopify fees,
        growth rate vs. previous period, ARPU, and breakdown by app
        and currency.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)
        prev_start, _ = previous_period(start, end)

        # Event-aware MRR when app_id is provided (events require an app_id)
        events = None
        if app_id:
            events = await sp.get_app_events(app_id, limit=500)
            # All-time transactions needed to find latest subscription amount
            txns = await sp.get_transactions(app_id=app_id, limit=2000)
        else:
            txns = await sp.get_transactions(
                app_id=app_id,
                created_at_min=f"{prev_start}T00:00:00Z",
                created_at_max=f"{end}T23:59:59Z",
                limit=1000,
            )

        result = compute_revenue_summary(txns, start, end, events=events)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_mrr_movement(
    ctx: Context,
    app_id: str = "",
    period: str = "30d",
) -> str:
    """Get MRR movement breakdown: new, expansion, contraction, churn, reactivation.

    Args:
        app_id: Filter by app (optional).
        period: '7d', '30d', '90d', '1y', or 'YYYY-MM-DD:YYYY-MM-DD'.

    Shows how MRR changed between current and previous period.

    Returns:
        JSON string with new, expansion, contraction, churn,
        reactivation MRR values and net new MRR.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)
        prev_start, prev_end = previous_period(start, end)

        events = None
        if app_id:
            events = await sp.get_app_events(app_id, limit=500)
            all_txns = await sp.get_transactions(app_id=app_id, limit=2000)
            result = compute_mrr_movement(
                all_txns,
                all_txns,
                events=events,
                current_period_end=end,
                previous_period_end=prev_end,
            )
        else:
            current_txns = await sp.get_transactions(
                app_id=app_id,
                created_at_min=f"{start}T00:00:00Z",
                created_at_max=f"{end}T23:59:59Z",
                limit=1000,
            )
            prev_txns = await sp.get_transactions(
                app_id=app_id,
                created_at_min=f"{prev_start}T00:00:00Z",
                created_at_max=f"{prev_end}T23:59:59Z",
                limit=1000,
            )
            result = compute_mrr_movement(current_txns, prev_txns)

        result["period"] = f"{start} to {end}"
        result["previous_period"] = f"{prev_start} to {prev_end}"
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_payout_report(
    ctx: Context,
    app_id: str = "",
    period: str = "30d",
) -> str:
    """Get payout summary: gross revenue, Shopify fees, net payout.

    Args:
        app_id: Filter by app (optional).
        period: '7d', '30d', '90d', '1y', or 'YYYY-MM-DD:YYYY-MM-DD'.

    Returns:
        JSON string with gross, fees, net totals and per-type
        breakdown. Note: amounts are for analytics, not accounting.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)

        txns = await sp.get_transactions(
            app_id=app_id,
            created_at_min=f"{start}T00:00:00Z",
            created_at_max=f"{end}T23:59:59Z",
            limit=1000,
        )

        result = compute_payout_summary(txns, start, end)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_plan_performance(
    ctx: Context,
    app_id: str = "",
    period: str = "30d",
) -> str:
    """Get metrics per pricing plan tier.

    Args:
        app_id: Filter by app (optional).
        period: '7d', '30d', '90d', '1y', or 'YYYY-MM-DD:YYYY-MM-DD'.

    Returns:
        JSON string with subscribers, total revenue, avg revenue
        per merchant for each inferred pricing tier.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)

        txns = await sp.get_transactions(
            app_id=app_id,
            created_at_min=f"{start}T00:00:00Z",
            created_at_max=f"{end}T23:59:59Z",
            types=["APP_SUBSCRIPTION_SALE"],
            limit=1000,
        )

        result = compute_plan_performance(txns, start, end)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


# =============================================================================
# Customer Analytics Tools (4)
# Churn tools require both events AND transactions for cross-referencing.
# =============================================================================


@mcp.tool()
async def get_churn_analysis(
    app_id: str,
    ctx: Context,
    period: str = "30d",
) -> str:
    """Get churn metrics: revenue churn %, logo churn %, uninstall reasons.

    Args:
        app_id: App GID or numeric ID (required).
        period: '7d', '30d', '90d', '1y', or 'YYYY-MM-DD:YYYY-MM-DD'.

    Returns:
        JSON string with logo_churn, revenue_churn, and
        uninstall_reasons breakdown.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)
        prev_start, _ = previous_period(start, end)

        # Fetch events (need full history for starting merchant count)
        events = await sp.get_app_events(app_id, limit=500)

        # Fetch transactions for both periods
        txns = await sp.get_transactions(
            app_id=app_id,
            created_at_min=f"{prev_start}T00:00:00Z",
            created_at_max=f"{end}T23:59:59Z",
            limit=1000,
        )

        result = compute_churn_analysis(events, txns, start, end)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_retention_cohorts(
    ctx: Context,
    app_id: str = "",
    months: int = 12,
) -> str:
    """Get monthly revenue retention cohort table.

    Args:
        app_id: Filter by app (optional).
        months: How many months to track per cohort (default 12).

    Returns:
        JSON string with monthly cohort table showing revenue
        retention percentages. Values above 100% indicate expansion.
    """
    try:
        sp = _get_sp(ctx)

        txns = await sp.get_transactions(
            app_id=app_id,
            types=["APP_SUBSCRIPTION_SALE"],
            limit=2000,
        )

        result = compute_retention_cohorts(txns, months)
        if not result.get("cohorts"):
            return json.dumps(
                {
                    "cohorts": {},
                    "message": "No subscription data available for cohort analysis.",
                }
            )
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_customer_ltv(
    ctx: Context,
    app_id: str = "",
) -> str:
    """Get customer lifetime value (LTV), ARPU, and merchant rankings.

    Args:
        app_id: Filter by app (optional).

    Returns:
        JSON string with LTV, ARPU, average lifespan, and top/bottom
        5 merchants by total revenue.
    """
    try:
        sp = _get_sp(ctx)

        txns = await sp.get_transactions(app_id=app_id, limit=2000)
        events = []
        if app_id:
            events = await sp.get_app_events(app_id, limit=500)

        result = compute_customer_ltv(txns, events)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_churned_merchants(
    app_id: str,
    ctx: Context,
    days: int = 30,
) -> str:
    """Get recently churned merchants with uninstall reasons.

    Args:
        app_id: App GID or numeric ID (required).
        days: Look back period in days (default 30).

    Returns:
        JSON string with churned merchant list including shop name,
        domain, uninstall date, and reason. Sorted most recent first.
    """
    try:
        sp = _get_sp(ctx)

        date_from = (date.today() - timedelta(days=days)).isoformat()

        events = await sp.get_app_events(
            app_id,
            types=["RELATIONSHIP_UNINSTALLED"],
            occurred_at_min=f"{date_from}T00:00:00Z",
            limit=200,
        )

        result = []
        for e in events:
            entry = {
                "shop": e.get("shop", {}).get("myshopifyDomain", "N/A"),
                "shop_name": e.get("shop", {}).get("name", "N/A"),
                "uninstalled_at": e.get("occurredAt", ""),
            }
            reason = e.get("reason", "")
            entry["reason"] = reason if reason else "No reason provided"
            if e.get("description"):
                entry["description"] = e["description"]
            result.append(entry)

        result.sort(key=lambda x: x.get("uninstalled_at", ""), reverse=True)

        return json.dumps(
            {"churned_merchants": result, "count": len(result), "lookback_days": days},
            indent=2,
        )
    except ShopifyPartnerError as e:
        return _error(str(e))


# =============================================================================
# Multi-App Tools (2)
# Cross-app comparison and anomaly detection across your entire portfolio.
# =============================================================================


@mcp.tool()
async def get_revenue_anomalies(
    ctx: Context,
    app_id: str = "",
    lookback_days: int = 90,
) -> str:
    """Detect unusual patterns in revenue.

    Args:
        app_id: Filter by app (optional).
        lookback_days: Analysis window in days (default 90).

    Returns:
        JSON string with anomaly list (date, amount, expected,
        deviation, type). Empty list if no unusual patterns found.
    """
    try:
        sp = _get_sp(ctx)

        start = date.today() - timedelta(days=lookback_days)
        txns = await sp.get_transactions(
            app_id=app_id,
            created_at_min=f"{start}T00:00:00Z",
            limit=2000,
        )

        anomalies = find_revenue_anomalies(txns, lookback_days)

        if not anomalies:
            return json.dumps(
                {
                    "anomalies": [],
                    "message": "No unusual revenue patterns detected.",
                    "lookback_days": lookback_days,
                }
            )

        return json.dumps(
            {
                "anomalies": anomalies,
                "count": len(anomalies),
                "lookback_days": lookback_days,
            },
            indent=2,
        )
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_app_comparison(
    ctx: Context,
    period: str = "30d",
) -> str:
    """Compare metrics across all your Shopify apps side by side.

    Args:
        period: '7d', '30d', '90d', '1y', or 'YYYY-MM-DD:YYYY-MM-DD'.

    Returns:
        JSON string with per-app comparison including revenue,
        installs, uninstalls, churn rate, and LTV.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)

        if not sp.app_ids:
            return json.dumps(
                {
                    "error": "No app IDs configured. Add SHOPIFY_APP_IDS to .env.",
                }
            )

        comparison = []
        for app_id in sp.app_ids:
            try:
                app = await sp.get_app(app_id)
                app_name = app.get("name", "Unknown")

                # Get all events and all-time transactions
                all_events = await sp.get_app_events(app_id, limit=500)
                all_txns = await sp.get_transactions(app_id=app_id, limit=2000)

                # Count period installs/uninstalls from events
                installs = [
                    e
                    for e in all_events
                    if e.get("type") == "RELATIONSHIP_INSTALLED"
                    and _event_in_period(e, start, end)
                ]
                uninstalls = [
                    e
                    for e in all_events
                    if e.get("type") == "RELATIONSHIP_UNINSTALLED"
                    and _event_in_period(e, start, end)
                ]

                revenue = compute_revenue_summary(
                    all_txns, start, end, events=all_events
                )

                comparison.append(
                    {
                        "app": app_name,
                        "app_id": app_id,
                        "installs": len(installs),
                        "uninstalls": len(uninstalls),
                        "net_growth": len(installs) - len(uninstalls),
                        "mrr": revenue["mrr"],
                        "total_revenue": revenue["total_net_revenue"],
                        "active_merchants": revenue["active_merchants"],
                        "arpu": revenue["arpu"],
                    }
                )
            except ShopifyPartnerError as e:
                comparison.append(
                    {
                        "app_id": app_id,
                        "error": str(e),
                    }
                )

        return json.dumps(
            {"comparison": comparison, "period": f"{start} to {end}"},
            indent=2,
        )
    except ShopifyPartnerError as e:
        return _error(str(e))


# =============================================================================
# Merchant Intelligence Tools (4)
# Scoring, risk assessment, and digest for individual merchants.
# =============================================================================


@mcp.tool()
async def get_churn_risk(
    app_id: str,
    ctx: Context,
) -> str:
    """Score active merchants by churn risk (low/medium/high).

    Args:
        app_id: App GID or numeric ID (required).

    Heuristic signals: deactivation events, subscription cancellations,
    reinstall patterns, declining charges, long inactivity.

    Returns:
        JSON string with risk summary and merchant lists grouped by
        risk level (high/medium/low) with contributing factors.
    """
    try:
        sp = _get_sp(ctx)

        events = await sp.get_app_events(app_id, limit=500)
        txns = await sp.get_transactions(app_id=app_id, limit=1000)

        results = compute_churn_risk(events, txns)

        high = [r for r in results if r["risk_level"] == "high"]
        medium = [r for r in results if r["risk_level"] == "medium"]
        low = [r for r in results if r["risk_level"] == "low"]

        return json.dumps(
            {
                "summary": {
                    "total_active": len(results),
                    "high_risk": len(high),
                    "medium_risk": len(medium),
                    "low_risk": len(low),
                },
                "high_risk_merchants": high,
                "medium_risk_merchants": medium,
                "low_risk_merchants": low[:5],
            },
            indent=2,
        )
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_merchant_health(
    app_id: str,
    ctx: Context,
) -> str:
    """Score merchants with a composite health grade (A-F).

    Args:
        app_id: App GID or numeric ID (required).

    Grades based on 4 dimensions (0-25 each, total 0-100):
    - Tenure: how long installed
    - Revenue: total and recent charges
    - Stability: no deactivations/reinstalls
    - Engagement: recent activity

    A (85+), B (70-84), C (55-69), D (40-54), F (<40).

    Returns:
        JSON string with grade distribution summary and per-merchant
        health scores with dimensional breakdown.
    """
    try:
        sp = _get_sp(ctx)

        events = await sp.get_app_events(app_id, limit=500)
        txns = await sp.get_transactions(app_id=app_id, limit=1000)

        results = compute_merchant_health(events, txns)

        grade_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for m in results:
            grade_counts[m["grade"]] = grade_counts.get(m["grade"], 0) + 1

        avg_score = (
            round(sum(m["score"] for m in results) / len(results), 1)
            if results
            else 0.0
        )

        return json.dumps(
            {
                "summary": {
                    "total_merchants": len(results),
                    "average_score": avg_score,
                    "grade_distribution": grade_counts,
                },
                "merchants": results,
            },
            indent=2,
        )
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_business_digest(
    app_id: str,
    ctx: Context,
    period: str = "7d",
) -> str:
    """Get a business digest with key metrics and highlights.

    Args:
        app_id: App GID or numeric ID (required).
        period: '7d' (weekly), '30d' (monthly), or custom range.

    Returns:
        JSON string with period summary, comparison to previous
        period, highlights, new installs, and recent churns.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)

        events = await sp.get_app_events(app_id, limit=500)
        # All-time transactions: event-aware MRR needs latest subscription
        # amount per merchant regardless of period
        txns = await sp.get_transactions(app_id=app_id, limit=2000)

        result = compute_business_digest(events, txns, start, end)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_merchant_lookup(
    app_id: str,
    shop_domain: str,
    ctx: Context,
) -> str:
    """Get full timeline for a specific merchant.

    Args:
        app_id: App GID or numeric ID (required).
        shop_domain: The merchant's myshopify.com domain (required).
            Example: 'my-store.myshopify.com'

    Returns:
        JSON string with merchant status, revenue total, and full
        chronological timeline of events and transactions.
    """
    try:
        sp = _get_sp(ctx)

        events = await sp.get_app_events(app_id, limit=500)
        txns = await sp.get_transactions(app_id=app_id, limit=2000)

        result = compute_merchant_timeline(events, txns, shop_domain)
        if not result.get("timeline"):
            return json.dumps(
                {
                    "error": f"No data found for merchant: {shop_domain}",
                    "tip": "Use get_merchants to see all merchant domains.",
                }
            )
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


# =============================================================================
# Growth & Forecasting Tools (6)
# Trends, patterns, projections, referrals, and credits.
# =============================================================================


@mcp.tool()
async def get_trial_funnel(
    app_id: str,
    ctx: Context,
    period: str = "90d",
) -> str:
    """Get trial-to-paid conversion funnel.

    Args:
        app_id: App GID or numeric ID (required).
        period: '7d', '30d', '90d', '1y', or 'YYYY-MM-DD:YYYY-MM-DD'.

    Returns:
        JSON string with conversion funnel metrics, timing analysis,
        and lists of converted/unconverted merchants.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)

        events = await sp.get_app_events(app_id, limit=500)
        result = compute_trial_funnel(events, start, end)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_revenue_forecast(
    ctx: Context,
    app_id: str = "",
    forecast_months: int = 6,
) -> str:
    """Project future MRR for the next 3-6 months.

    Args:
        app_id: Filter by app (optional).
        forecast_months: How many months to project (default 6).

    Returns:
        JSON string with current MRR, growth rate, historical data,
        monthly projections, and projected ARR.
    """
    try:
        sp = _get_sp(ctx)

        events = None
        if app_id:
            events = await sp.get_app_events(app_id, limit=500)
            txns = await sp.get_transactions(app_id=app_id, limit=2000)
        else:
            start = date.today() - timedelta(days=210)
            txns = await sp.get_transactions(
                app_id=app_id,
                created_at_min=f"{start}T00:00:00Z",
                limit=2000,
            )

        result = compute_revenue_forecast(txns, forecast_months, events=events)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_growth_velocity(
    app_id: str,
    ctx: Context,
    weeks: int = 12,
) -> str:
    """Get week-over-week growth trends with acceleration signals.

    Args:
        app_id: App GID or numeric ID (required).
        weeks: Number of weeks to analyze (default 12).

    Returns:
        JSON string with weekly install/uninstall/revenue data and
        acceleration/deceleration signals.
    """
    try:
        sp = _get_sp(ctx)

        start = date.today() - timedelta(days=7 * weeks + 7)

        events = await sp.get_app_events(
            app_id,
            occurred_at_min=f"{start}T00:00:00Z",
            limit=500,
        )
        txns = await sp.get_transactions(
            app_id=app_id,
            created_at_min=f"{start}T00:00:00Z",
            limit=1000,
        )

        result = compute_growth_velocity(events, txns, weeks)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_install_patterns(
    app_id: str,
    ctx: Context,
) -> str:
    """Analyze install patterns by day of week and time of month.

    Args:
        app_id: App GID or numeric ID (required).

    Returns:
        JSON string with install counts by day of week, month period,
        monthly trend, and best day for installs.
    """
    try:
        sp = _get_sp(ctx)

        events = await sp.get_app_events(
            app_id,
            types=["RELATIONSHIP_INSTALLED"],
            limit=500,
        )

        result = compute_install_patterns(events)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_referral_revenue(
    ctx: Context,
    period: str = "90d",
) -> str:
    """Track referral revenue from merchant referrals to Shopify.

    Args:
        period: '7d', '30d', '90d', '1y', or custom range.

    Returns:
        JSON string with total referral revenue, count, previous
        period comparison, and individual referral details.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)
        prev_start, _ = previous_period(start, end)

        txns = await sp.get_transactions(
            created_at_min=f"{prev_start}T00:00:00Z",
            created_at_max=f"{end}T23:59:59Z",
            limit=1000,
        )

        result = compute_referral_revenue(txns, start, end)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


@mcp.tool()
async def get_credits_adjustments(
    ctx: Context,
    app_id: str = "",
    period: str = "90d",
) -> str:
    """Track credits, adjustments, and refunds.

    Args:
        app_id: Filter by app (optional).
        period: '7d', '30d', '90d', '1y', or custom range.

    Returns:
        JSON string with credit/adjustment totals, giveback
        percentage, and individual transaction details.
    """
    try:
        sp = _get_sp(ctx)
        start, end = parse_period(period)

        txns = await sp.get_transactions(
            app_id=app_id,
            created_at_min=f"{start}T00:00:00Z",
            created_at_max=f"{end}T23:59:59Z",
            limit=1000,
        )

        result = compute_credits_adjustments(txns, start, end)
        return json.dumps(result, indent=2)
    except ShopifyPartnerError as e:
        return _error(str(e))


def main() -> None:
    """Entry point for the `shopify-partner-agent` console script."""
    mcp.run(transport="stdio")


# stdio transport -- MCP clients (Claude Code, Cursor, etc.) connect via stdin/stdout
if __name__ == "__main__":
    main()
