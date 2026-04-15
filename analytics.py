"""
Analytics computation functions for Shopify Partner Agent.

Pure functions that take raw transaction/event lists and return computed
metrics. No API calls -- just math. Independently testable.

All financial math uses Decimal for precision.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import mean, stdev

__all__ = [
    "_active_merchants_from_events",
    "_all_time_subscription_map",
    "_usage_mrr_trailing",
    "compute_business_digest",
    "compute_churn_analysis",
    "compute_churn_risk",
    "compute_credits_adjustments",
    "compute_customer_ltv",
    "compute_growth_velocity",
    "compute_install_patterns",
    "compute_merchant_health",
    "compute_merchant_timeline",
    "compute_mrr_movement",
    "compute_payout_summary",
    "compute_plan_performance",
    "compute_referral_revenue",
    "compute_retention_cohorts",
    "compute_revenue_forecast",
    "compute_revenue_summary",
    "compute_trial_funnel",
    "find_revenue_anomalies",
    "parse_period",
    "previous_period",
]


# =============================================================================
# Period Parsing
# =============================================================================


def parse_period(period: str) -> tuple[date, date]:
    """Parse a period string into (start_date, end_date).

    Args:
        period: Period string. Supported formats:
            '7d', '30d', '90d', '365d' -- relative days from today.
            '1y' -- 365 days.
            'YYYY-MM-DD:YYYY-MM-DD' -- custom range.

    Returns:
        Tuple of (start_date, end_date) as date objects. Falls back
        to 30 days if the format is not recognized.
    """
    today = date.today()

    if not period:
        period = "30d"

    if period == "1y":
        return today - timedelta(days=365), today

    if "d" in period:
        try:
            days = int(period.replace("d", ""))
            return today - timedelta(days=days), today
        except ValueError:
            pass

    if ":" in period:
        parts = period.split(":")
        if len(parts) == 2:
            try:
                start = date.fromisoformat(parts[0].strip())
                end = date.fromisoformat(parts[1].strip())
                return start, end
            except ValueError:
                pass

    # Default fallback
    return today - timedelta(days=30), today


def previous_period(start: date, end: date) -> tuple[date, date]:
    """Calculate the equivalent previous period for growth comparison.

    Args:
        start: Start date of the current period.
        end: End date of the current period.

    Returns:
        Tuple of (prev_start, prev_end) representing the period
        immediately before the given range, with equal duration.
    """
    duration = (end - start).days
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=duration)
    return prev_start, prev_end


# =============================================================================
# Financial Helpers
# =============================================================================


def _to_decimal(amount_str: str) -> Decimal:
    """Safely convert a string amount to Decimal.

    Args:
        amount_str: String representation of a decimal number.

    Returns:
        Decimal value, or Decimal("0") if conversion fails.
    """
    try:
        return Decimal(str(amount_str))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _filter_by_date(
    items: list[dict],
    start: date,
    end: date,
    date_field: str = "createdAt",
) -> list[dict]:
    """Filter items by date range.

    Args:
        items: List of dicts containing a date field.
        start: Inclusive start date.
        end: Inclusive end date.
        date_field: Key name for the ISO datetime string.
            Defaults to "createdAt".

    Returns:
        Filtered list of items within the date range.
    """
    result = []
    for item in items:
        date_str = item.get(date_field, "")
        if not date_str:
            continue
        try:
            item_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            if start <= item_date <= end:
                result.append(item)
        except ValueError:
            continue
    return result


# =============================================================================
# Merchant Status Helpers
# =============================================================================

# Event types that affect merchant active status (Shopify Partner API has 20 total)

_RELATIONSHIP_ACTIVE = frozenset(
    {
        "RELATIONSHIP_INSTALLED",
        "RELATIONSHIP_REACTIVATED",
    }
)
_RELATIONSHIP_INACTIVE = frozenset(
    {
        "RELATIONSHIP_UNINSTALLED",
        "RELATIONSHIP_DEACTIVATED",
    }
)
_SUBSCRIPTION_INACTIVE = frozenset(
    {
        "SUBSCRIPTION_CHARGE_CANCELED",
        "SUBSCRIPTION_CHARGE_EXPIRED",
        "SUBSCRIPTION_CHARGE_FROZEN",
    }
)
_SUBSCRIPTION_ACTIVE = frozenset(
    {
        "SUBSCRIPTION_CHARGE_ACCEPTED",
        "SUBSCRIPTION_CHARGE_ACTIVATED",
        "SUBSCRIPTION_CHARGE_UNFROZEN",
    }
)


def _active_merchants_from_events(
    events: list[dict],
    *,
    as_of: date | None = None,
) -> set[str]:
    """Determine currently active merchants from event history.

    Two-track status model:
        1. Relationship: is the app installed on the store?
        2. Subscription: is the subscription generating revenue?

    A merchant is active for MRR if the app is installed AND the
    subscription has not been explicitly canceled, expired, or frozen.

    Events are replayed chronologically -- last event wins per track.
    Handles plan upgrades (CANCELED old -> ACCEPTED new = still active),
    frozen stores (FROZEN without uninstall = excluded), and reinstalls.

    Args:
        events: App event list from Shopify Partner API.
        as_of: Only consider events up to this date (inclusive).
            None means consider all events (current state).

    Returns:
        Set of myshopifyDomain strings for currently active merchants.
    """
    parsed: list[tuple[datetime, str, str]] = []
    for e in events:
        shop = e.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        date_str = e.get("occurredAt", "")
        if not date_str:
            continue
        try:
            event_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if as_of is not None and event_dt.date() > as_of:
            continue
        parsed.append((event_dt, e.get("type", ""), shop))

    # Sort by full datetime -- .date() loses intra-day ordering,
    # and the API returns events reverse-chronologically
    parsed.sort(key=lambda x: x[0])

    relationship_active: set[str] = set()
    subscription_killed: set[str] = set()

    for _, event_type, shop in parsed:
        if event_type in _RELATIONSHIP_ACTIVE:
            relationship_active.add(shop)
        elif event_type in _RELATIONSHIP_INACTIVE:
            relationship_active.discard(shop)

        if event_type in _SUBSCRIPTION_INACTIVE:
            subscription_killed.add(shop)
        elif event_type in _SUBSCRIPTION_ACTIVE:
            subscription_killed.discard(shop)

    return relationship_active - subscription_killed


def _all_time_subscription_map(
    transactions: list[dict],
    *,
    as_of: date | None = None,
) -> dict[str, Decimal]:
    """Map each merchant to their latest subscription charge amount (monthly).

    Scans all transactions and takes the most recent AppSubscriptionSale
    per merchant. Annual billing is divided by 12.

    Args:
        transactions: Full transaction history from API.
        as_of: Only consider transactions up to this date (inclusive).
            None means consider all transactions.

    Returns:
        Dict mapping myshopifyDomain to monthly Decimal amount.
    """
    latest: dict[str, tuple[datetime, Decimal]] = {}
    for txn in transactions:
        if txn.get("__typename") != "AppSubscriptionSale":
            continue
        shop = txn.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        date_str = txn.get("createdAt", "")
        if not date_str:
            continue
        try:
            txn_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if as_of is not None and txn_dt.date() > as_of:
            continue
        amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        billing = txn.get("billingInterval", "EVERY_30_DAYS")
        monthly = amount / 12 if billing == "ANNUAL" else amount

        if shop not in latest or txn_dt > latest[shop][0]:
            latest[shop] = (txn_dt, monthly)

    return {shop: amount for shop, (_, amount) in latest.items()}


def _usage_mrr_trailing(
    transactions: list[dict],
    *,
    as_of: date | None = None,
    window_days: int = 30,
) -> dict[str, Decimal]:
    """Sum AppUsageSale charges per merchant in a trailing window.

    Usage charges are irregular, so the trailing 30-day total serves
    as the monthly run rate.

    Args:
        transactions: Full transaction history from API.
        as_of: End date of the trailing window (inclusive).
            None means today.
        window_days: Size of the trailing window in days.

    Returns:
        Dict mapping myshopifyDomain to trailing usage Decimal.
    """
    cutoff_end = as_of or date.today()
    cutoff_start = cutoff_end - timedelta(days=window_days)

    result: dict[str, Decimal] = defaultdict(Decimal)
    for txn in transactions:
        if txn.get("__typename") != "AppUsageSale":
            continue
        shop = txn.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        date_str = txn.get("createdAt", "")
        if not date_str:
            continue
        try:
            txn_date = datetime.fromisoformat(
                date_str.replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue
        if cutoff_start <= txn_date <= cutoff_end:
            amount = _to_decimal(
                txn.get("netAmount", {}).get("amount", "0")
            )
            result[shop] += amount

    return dict(result)


# =============================================================================
# Revenue Analytics
# =============================================================================

# MRR calculation: event-aware when events provided, charge-based fallback


def compute_revenue_summary(
    transactions: list[dict],
    period_start: date,
    period_end: date,
    *,
    events: list[dict] | None = None,
) -> dict:
    """Compute MRR, ARR, total revenue, growth rate, ARPU, and fees.

    When events are provided, MRR is event-aware: it uses all-time
    transaction history to find each merchant's latest subscription
    price, then filters to merchants who are currently active (based
    on install/uninstall/freeze/cancel events). This correctly handles
    annual merchants between charges and excludes churned/frozen stores.

    When events are None, falls back to charge-based MRR (sum of
    subscription charges in the period window). This is less accurate
    but requires no event data.

    Args:
        transactions: Raw transaction list from API. For event-aware MRR,
            should include full history (not just the period).
        period_start: Start of the analysis period.
        period_end: End of the analysis period.
        events: App event list for active merchant detection (optional).

    Returns:
        Dict with keys: mrr, arr, total_net_revenue,
        total_gross_revenue, total_shopify_fees, growth_rate_pct,
        arpu, active_merchants, mrr_subscribers, mrr_method,
        transaction_count, by_currency, by_app.
    """
    current = _filter_by_date(transactions, period_start, period_end)
    prev_start, prev_end = previous_period(period_start, period_end)
    prev = _filter_by_date(transactions, prev_start, prev_end)

    # Group by currency
    by_currency: dict[str, dict] = defaultdict(
        lambda: {
            "net": Decimal("0"),
            "gross": Decimal("0"),
            "fees": Decimal("0"),
            "count": 0,
        }
    )

    # Group by app
    by_app: dict[str, dict] = defaultdict(lambda: {"net": Decimal("0"), "count": 0})

    # Track merchants seen in period (for total revenue metrics)
    active_merchants: set[str] = set()

    for txn in current:
        net = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        gross = _to_decimal(txn.get("grossAmount", {}).get("amount", "0"))
        fee = _to_decimal(txn.get("shopifyFee", {}).get("amount", "0"))
        currency = txn.get("netAmount", {}).get(
            "currencyCode",
            txn.get("grossAmount", {}).get("currencyCode", "USD"),
        )
        app_name = txn.get("app", {}).get("name", "Unknown")
        shop = txn.get("shop", {}).get("myshopifyDomain", "")

        by_currency[currency]["net"] += net
        by_currency[currency]["gross"] += gross
        by_currency[currency]["fees"] += fee
        by_currency[currency]["count"] += 1
        by_app[app_name]["net"] += net
        by_app[app_name]["count"] += 1

        if shop:
            active_merchants.add(shop)

    # MRR calculation: event-aware or charge-based
    if events is not None:
        # Event-aware: state as of period_end so period comparison works
        all_subs = _all_time_subscription_map(transactions, as_of=period_end)
        currently_active = _active_merchants_from_events(events, as_of=period_end)
        subscription_merchants = {
            shop: amount
            for shop, amount in all_subs.items()
            if shop in currently_active
        }
        mrr_method = "event_aware"
    else:
        # Charge-based fallback: subscription charges in the period window
        subscription_merchants: dict[str, Decimal] = {}
        for txn in current:
            if txn.get("__typename") == "AppSubscriptionSale":
                shop = txn.get("shop", {}).get("myshopifyDomain", "")
                if shop:
                    net_amt = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
                    billing = txn.get("billingInterval", "EVERY_30_DAYS")
                    monthly = net_amt / 12 if billing == "ANNUAL" else net_amt
                    subscription_merchants[shop] = monthly
        mrr_method = "charge_based"

    # Usage MRR: trailing AppUsageSale per active merchant
    usage_merchants: dict[str, Decimal] = {}
    if events is not None:
        usage_all = _usage_mrr_trailing(
            transactions, as_of=period_end
        )
        usage_merchants = {
            s: a
            for s, a in usage_all.items()
            if s in currently_active
        }

    # Calculate MRR and derived metrics
    subscription_mrr = sum(
        subscription_merchants.values(), Decimal("0")
    )
    usage_mrr = sum(usage_merchants.values(), Decimal("0"))
    mrr = subscription_mrr + usage_mrr
    arr = mrr * 12
    total_net = sum(
        (c["net"] for c in by_currency.values()), Decimal("0")
    )
    total_gross = sum(
        (c["gross"] for c in by_currency.values()), Decimal("0")
    )
    total_fees = sum(
        (c["fees"] for c in by_currency.values()), Decimal("0")
    )
    merchant_count = len(active_merchants)
    arpu = (
        total_net / merchant_count
        if merchant_count > 0
        else Decimal("0")
    )

    # Growth rate
    prev_total = sum(
        _to_decimal(t.get("netAmount", {}).get("amount", "0"))
        for t in prev
    )
    growth_pct = (
        float((total_net - prev_total) / prev_total * 100)
        if prev_total > 0
        else None
    )

    return {
        "mrr": str(mrr.quantize(Decimal("0.01"))),
        "subscription_mrr": str(
            subscription_mrr.quantize(Decimal("0.01"))
        ),
        "usage_mrr": str(usage_mrr.quantize(Decimal("0.01"))),
        "arr": str(arr.quantize(Decimal("0.01"))),
        "total_net_revenue": str(total_net.quantize(Decimal("0.01"))),
        "total_gross_revenue": str(
            total_gross.quantize(Decimal("0.01"))
        ),
        "total_shopify_fees": str(
            total_fees.quantize(Decimal("0.01"))
        ),
        "active_merchants": merchant_count,
        "mrr_subscribers": len(subscription_merchants),
        "mrr_method": mrr_method,
        "arpu": str(arpu.quantize(Decimal("0.01"))),
        "transaction_count": len(current),
        "growth_rate_pct": (
            round(growth_pct, 1) if growth_pct is not None else None
        ),
        "period": f"{period_start} to {period_end}",
        "by_currency": {
            k: {
                "net": str(v["net"].quantize(Decimal("0.01"))),
                "gross": str(v["gross"].quantize(Decimal("0.01"))),
                "fees": str(v["fees"].quantize(Decimal("0.01"))),
                "count": v["count"],
            }
            for k, v in by_currency.items()
        },
        "by_app": {
            k: {
                "net": str(v["net"].quantize(Decimal("0.01"))),
                "count": v["count"],
            }
            for k, v in by_app.items()
        },
    }


def compute_mrr_movement(
    current_transactions: list[dict],
    previous_transactions: list[dict],
) -> dict:
    """Compute MRR movement: new, expansion, contraction, churn, reactivation.

    Compares subscription charges between two periods to classify changes.

    Args:
        current_transactions: Transactions from the current period.
        previous_transactions: Transactions from the previous period.

    Returns:
        Dict with keys: new_mrr, expansion_mrr, contraction_mrr,
        churn_mrr, reactivation_mrr, net_new_mrr,
        current_subscribers, previous_subscribers.
    """

    def _subscription_map(txns: list[dict]) -> dict[str, Decimal]:
        """Map shop domain to monthly subscription amount.

        Args:
            txns: Transaction list to scan for AppSubscriptionSale.

        Returns:
            Dict mapping myshopifyDomain to monthly Decimal amount.
        """
        result: dict[str, Decimal] = {}
        for txn in txns:
            if txn.get("__typename") != "AppSubscriptionSale":
                continue
            shop = txn.get("shop", {}).get("myshopifyDomain", "")
            if not shop:
                continue
            amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
            billing = txn.get("billingInterval", "EVERY_30_DAYS")
            monthly = amount / 12 if billing == "ANNUAL" else amount
            result[shop] = monthly
        return result

    curr_map = _subscription_map(current_transactions)
    prev_map = _subscription_map(previous_transactions)

    new_mrr = Decimal("0")
    expansion_mrr = Decimal("0")
    contraction_mrr = Decimal("0")
    churn_mrr = Decimal("0")
    reactivation_mrr = Decimal("0")

    all_shops = set(curr_map.keys()) | set(prev_map.keys())

    for shop in all_shops:
        curr_amt = curr_map.get(shop, Decimal("0"))
        prev_amt = prev_map.get(shop, Decimal("0"))

        if prev_amt == 0 and curr_amt > 0:
            # Could be new or reactivation -- we'd need older history
            # to distinguish. For now, treat as new.
            new_mrr += curr_amt
        elif prev_amt > 0 and curr_amt == 0:
            churn_mrr += prev_amt
        elif curr_amt > prev_amt:
            expansion_mrr += curr_amt - prev_amt
        elif curr_amt < prev_amt:
            contraction_mrr += prev_amt - curr_amt

    net_new = new_mrr + reactivation_mrr + expansion_mrr - contraction_mrr - churn_mrr

    return {
        "new_mrr": str(new_mrr.quantize(Decimal("0.01"))),
        "expansion_mrr": str(expansion_mrr.quantize(Decimal("0.01"))),
        "contraction_mrr": str(contraction_mrr.quantize(Decimal("0.01"))),
        "churn_mrr": str(churn_mrr.quantize(Decimal("0.01"))),
        "reactivation_mrr": str(reactivation_mrr.quantize(Decimal("0.01"))),
        "net_new_mrr": str(net_new.quantize(Decimal("0.01"))),
        "current_subscribers": len(curr_map),
        "previous_subscribers": len(prev_map),
    }


def compute_payout_summary(
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Compute payout summary: gross, fees, net, by type.

    Args:
        transactions: Raw transaction list from API.
        period_start: Start of the analysis period.
        period_end: End of the analysis period.

    Returns:
        Dict with keys: total_gross, total_shopify_fees,
        total_net_payout, fee_percentage, transaction_count,
        period, by_type.
    """
    current = _filter_by_date(transactions, period_start, period_end)

    by_type: dict[str, dict] = defaultdict(
        lambda: {
            "gross": Decimal("0"),
            "fees": Decimal("0"),
            "net": Decimal("0"),
            "count": 0,
        }
    )

    for txn in current:
        typename = txn.get("__typename", "Unknown")
        net = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        gross = _to_decimal(txn.get("grossAmount", {}).get("amount", "0"))
        fee = _to_decimal(txn.get("shopifyFee", {}).get("amount", "0"))

        by_type[typename]["net"] += net
        by_type[typename]["gross"] += gross
        by_type[typename]["fees"] += fee
        by_type[typename]["count"] += 1

    total_gross = sum((v["gross"] for v in by_type.values()), Decimal("0"))
    total_fees = sum((v["fees"] for v in by_type.values()), Decimal("0"))
    total_net = sum((v["net"] for v in by_type.values()), Decimal("0"))

    return {
        "total_gross": str(total_gross.quantize(Decimal("0.01"))),
        "total_shopify_fees": str(total_fees.quantize(Decimal("0.01"))),
        "total_net_payout": str(total_net.quantize(Decimal("0.01"))),
        "fee_percentage": str(
            (total_fees / total_gross * 100).quantize(Decimal("0.1"))
            if total_gross > 0
            else "0.0"
        ),
        "transaction_count": len(current),
        "period": f"{period_start} to {period_end}",
        "by_type": {
            k: {
                "gross": str(v["gross"].quantize(Decimal("0.01"))),
                "fees": str(v["fees"].quantize(Decimal("0.01"))),
                "net": str(v["net"].quantize(Decimal("0.01"))),
                "count": v["count"],
            }
            for k, v in by_type.items()
        },
    }


def compute_plan_performance(
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Compute metrics per plan tier, inferred from charge amounts.

    Groups subscription charges by amount to identify plan tiers.

    Args:
        transactions: Raw transaction list from API.
        period_start: Start of the analysis period.
        period_end: End of the analysis period.

    Returns:
        Dict with keys: plans (keyed by price tier), total_plans,
        period. Each plan has subscribers, total_revenue,
        transactions, avg_revenue_per_merchant, billing_intervals.
    """
    current = _filter_by_date(transactions, period_start, period_end)
    sub_txns = [t for t in current if t.get("__typename") == "AppSubscriptionSale"]

    # Group by charge amount (proxy for plan tier)
    plans: dict[str, dict] = defaultdict(
        lambda: {
            "merchants": set(),
            "total_revenue": Decimal("0"),
            "count": 0,
            "billing_intervals": set(),
        }
    )

    for txn in sub_txns:
        amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        billing = txn.get("billingInterval", "EVERY_30_DAYS")
        monthly = amount / 12 if billing == "ANNUAL" else amount
        # Round to nearest dollar for plan grouping
        plan_key = f"${monthly.quantize(Decimal('1'))}/mo"
        shop = txn.get("shop", {}).get("myshopifyDomain", "")

        plans[plan_key]["merchants"].add(shop)
        plans[plan_key]["total_revenue"] += amount
        plans[plan_key]["count"] += 1
        plans[plan_key]["billing_intervals"].add(billing)

    result = {}
    for plan_name, data in sorted(plans.items()):
        merchant_count = len(data["merchants"])
        result[plan_name] = {
            "subscribers": merchant_count,
            "total_revenue": str(data["total_revenue"].quantize(Decimal("0.01"))),
            "transactions": data["count"],
            "avg_revenue_per_merchant": str(
                (data["total_revenue"] / merchant_count).quantize(Decimal("0.01"))
                if merchant_count > 0
                else "0.00"
            ),
            "billing_intervals": list(data["billing_intervals"]),
        }

    return {
        "plans": result,
        "total_plans": len(result),
        "period": f"{period_start} to {period_end}",
    }


# =============================================================================
# Customer / Churn Analytics
# Churn requires comparing two periods -- we derive starting state from all
# historical events.
# =============================================================================


def compute_churn_analysis(
    events: list[dict],
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Compute revenue churn %, subscription churn %, and logo churn %.

    Formulas (industry standard SaaS metrics):
        Revenue Churn % = (Churned MRR + Contraction) / Starting MRR x 100
        Logo Churn %    = Uninstalled merchants / Starting merchants x 100

    Args:
        events: App event list (installs, uninstalls).
        transactions: Revenue transaction list.
        period_start: Start of the analysis period.
        period_end: End of the analysis period.

    Returns:
        Dict with keys: logo_churn (starting_merchants,
        churned_merchants, churn_pct), revenue_churn (churned_mrr,
        contraction_mrr, revenue_churn_pct), uninstall_reasons,
        period.
    """
    prev_start, prev_end = previous_period(period_start, period_end)

    # Logo churn from events
    installs_before = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_INSTALLED"],
        date(2000, 1, 1),
        period_start - timedelta(days=1),
        date_field="occurredAt",
    )
    uninstalls_before = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_UNINSTALLED"],
        date(2000, 1, 1),
        period_start - timedelta(days=1),
        date_field="occurredAt",
    )
    uninstalls_during = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_UNINSTALLED"],
        period_start,
        period_end,
        date_field="occurredAt",
    )

    installed_set = {e.get("shop", {}).get("myshopifyDomain") for e in installs_before}
    uninstalled_set = {
        e.get("shop", {}).get("myshopifyDomain") for e in uninstalls_before
    }
    starting_merchants = len(installed_set - uninstalled_set)
    churned_merchants = len(
        {e.get("shop", {}).get("myshopifyDomain") for e in uninstalls_during}
    )

    logo_churn_pct = (
        round(churned_merchants / starting_merchants * 100, 1)
        if starting_merchants > 0
        else 0.0
    )

    # Uninstall reason breakdown
    reason_counts: dict[str, int] = defaultdict(int)
    for e in uninstalls_during:
        reasons = e.get("reason", "")
        if reasons:
            for reason in reasons.split(","):
                reason_counts[reason.strip()] += 1
        else:
            reason_counts["No reason provided"] += 1

    # Revenue churn from transactions
    mrr_movement = compute_mrr_movement(
        _filter_by_date(transactions, period_start, period_end),
        _filter_by_date(transactions, prev_start, prev_end),
    )
    starting_mrr = _to_decimal(mrr_movement["churn_mrr"]) + _to_decimal(
        mrr_movement["contraction_mrr"]
    )
    # Starting MRR approximation from previous period subscribers
    prev_sub_txns = [
        t
        for t in _filter_by_date(transactions, prev_start, prev_end)
        if t.get("__typename") == "AppSubscriptionSale"
    ]
    prev_mrr = sum(
        _to_decimal(t.get("netAmount", {}).get("amount", "0")) for t in prev_sub_txns
    )
    revenue_churn_pct = (
        round(float(starting_mrr / prev_mrr * 100), 1) if prev_mrr > 0 else 0.0
    )

    return {
        "logo_churn": {
            "starting_merchants": starting_merchants,
            "churned_merchants": churned_merchants,
            "churn_pct": logo_churn_pct,
        },
        "revenue_churn": {
            "churned_mrr": mrr_movement["churn_mrr"],
            "contraction_mrr": mrr_movement["contraction_mrr"],
            "revenue_churn_pct": revenue_churn_pct,
        },
        "uninstall_reasons": dict(
            sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)
        ),
        "period": f"{period_start} to {period_end}",
    }


def compute_retention_cohorts(
    transactions: list[dict],
    months: int = 12,
) -> dict:
    """Compute monthly revenue retention cohort table.

    Cohorts are based on first payment month.
    Retention = Revenue in month N / Revenue in month 0.

    Args:
        transactions: Full subscription transaction history.
        months: Number of months to track per cohort. Defaults to 12.

    Returns:
        Dict with keys: cohorts (keyed by YYYY-MM, each with
        merchants, initial_revenue, retention percentages),
        months_tracked.
    """
    # Group transactions by merchant's first payment month
    merchant_first_payment: dict[str, date] = {}
    merchant_monthly_revenue: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )

    for txn in transactions:
        if txn.get("__typename") != "AppSubscriptionSale":
            continue
        shop = txn.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        date_str = txn.get("createdAt", "")
        if not date_str:
            continue
        try:
            txn_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        except ValueError:
            continue

        amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        month_key = txn_date.strftime("%Y-%m")

        if (
            shop not in merchant_first_payment
            or txn_date < merchant_first_payment[shop]
        ):
            merchant_first_payment[shop] = txn_date

        merchant_monthly_revenue[shop][month_key] += amount

    # Build cohort table
    cohorts: dict[str, dict] = {}
    for shop, first_date in merchant_first_payment.items():
        cohort_key = first_date.strftime("%Y-%m")
        if cohort_key not in cohorts:
            cohorts[cohort_key] = {
                "merchants": 0,
                "initial_revenue": Decimal("0"),
                "months": {},
            }
        cohorts[cohort_key]["merchants"] += 1

        # Calculate revenue for each month relative to first payment
        for month_offset in range(months + 1):
            # timedelta(days=30 * offset) is approximate -- acceptable
            # for monthly cohort bucketing
            target_date = first_date.replace(day=1) + timedelta(days=30 * month_offset)
            target_key = target_date.strftime("%Y-%m")
            revenue = merchant_monthly_revenue[shop].get(target_key, Decimal("0"))

            if month_offset == 0:
                cohorts[cohort_key]["initial_revenue"] += revenue
            month_label = f"month_{month_offset}"
            cohorts[cohort_key]["months"].setdefault(month_label, Decimal("0"))
            cohorts[cohort_key]["months"][month_label] += revenue

    # Convert to retention percentages
    result = {}
    for cohort_key, data in sorted(cohorts.items()):
        initial = data["initial_revenue"]
        retention = {}
        for month_label, revenue in data["months"].items():
            pct = float(revenue / initial * 100) if initial > 0 else 0.0
            retention[month_label] = {
                "revenue": str(revenue.quantize(Decimal("0.01"))),
                "retention_pct": round(pct, 1),
            }
        result[cohort_key] = {
            "merchants": data["merchants"],
            "initial_revenue": str(initial.quantize(Decimal("0.01"))),
            "retention": retention,
        }

    return {"cohorts": result, "months_tracked": months}


def compute_customer_ltv(
    transactions: list[dict],
    events: list[dict],
) -> dict:
    """Compute LTV, ARPU, average lifespan, and top/bottom merchants.

    LTV = ARPU x Average lifespan (simple model).
    ARPU = Total net revenue / Active merchants.

    Args:
        transactions: Revenue transaction list.
        events: App event list (for context).

    Returns:
        Dict with keys: ltv, arpu, avg_lifespan_months,
        total_merchants, total_revenue, top_merchants,
        bottom_merchants.
    """
    # Revenue per merchant
    merchant_revenue: dict[str, Decimal] = defaultdict(Decimal)
    merchant_first_txn: dict[str, date] = {}
    merchant_last_txn: dict[str, date] = {}

    for txn in transactions:
        shop = txn.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        merchant_revenue[shop] += amount

        date_str = txn.get("createdAt", "")
        if date_str:
            try:
                txn_date = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                ).date()
                if (
                    shop not in merchant_first_txn
                    or txn_date < merchant_first_txn[shop]
                ):
                    merchant_first_txn[shop] = txn_date
                if shop not in merchant_last_txn or txn_date > merchant_last_txn[shop]:
                    merchant_last_txn[shop] = txn_date
            except ValueError:
                continue

    if not merchant_revenue:
        return {
            "ltv": "0.00",
            "arpu": "0.00",
            "avg_lifespan_months": 0,
            "total_merchants": 0,
            "top_merchants": [],
            "bottom_merchants": [],
            "message": "No transaction data available for LTV calculation.",
        }

    # Average lifespan in months
    lifespans: list[float] = []
    for shop in merchant_first_txn:
        if shop in merchant_last_txn:
            days = (merchant_last_txn[shop] - merchant_first_txn[shop]).days
            lifespans.append(max(days / 30.0, 1.0))  # Minimum 1 month

    avg_lifespan = mean(lifespans) if lifespans else 1.0
    total_revenue = sum(merchant_revenue.values())
    merchant_count = len(merchant_revenue)

    # ARPU: use active merchants when events available, all merchants otherwise
    if events:
        active = _active_merchants_from_events(events)
        active_revenue = {s: r for s, r in merchant_revenue.items() if s in active}
        active_count = len(active_revenue) if active_revenue else 1
        arpu = sum(active_revenue.values()) / active_count
        arpu_method = "active_only"
    else:
        arpu = total_revenue / merchant_count if merchant_count > 0 else Decimal("0")
        arpu_method = "all_merchants"

    # Simple LTV = ARPU x average lifespan
    ltv = arpu * Decimal(str(avg_lifespan))

    # Top and bottom merchants by revenue
    sorted_merchants = sorted(
        merchant_revenue.items(), key=lambda x: x[1], reverse=True
    )
    top = [
        {"shop": s, "revenue": str(r.quantize(Decimal("0.01")))}
        for s, r in sorted_merchants[:5]
    ]
    bottom = [
        {"shop": s, "revenue": str(r.quantize(Decimal("0.01")))}
        for s, r in sorted_merchants[-5:]
    ]

    return {
        "ltv": str(ltv.quantize(Decimal("0.01"))),
        "arpu": str(arpu.quantize(Decimal("0.01"))),
        "arpu_method": arpu_method,
        "avg_lifespan_months": round(avg_lifespan, 1),
        "total_merchants": merchant_count,
        "total_revenue": str(total_revenue.quantize(Decimal("0.01"))),
        "top_merchants": top,
        "bottom_merchants": bottom,
    }


# =============================================================================
# Insight / Anomaly Detection
# =============================================================================


def find_revenue_anomalies(
    transactions: list[dict],
    lookback_days: int = 90,
) -> list[dict]:
    """Detect unusual revenue patterns using statistical deviation.

    Flags days where revenue is > 2 standard deviations from the
    rolling mean. Also flags drops to zero after sustained revenue.

    Args:
        transactions: Revenue transaction list.
        lookback_days: Number of days to analyze. Defaults to 90.

    Returns:
        List of anomaly dicts with keys: date, amount, expected,
        deviation_std, type (spike or drop). Empty if insufficient
        data (< 7 days).
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    current = _filter_by_date(transactions, start, end)

    # Group revenue by day
    daily_revenue: dict[str, float] = defaultdict(float)
    for txn in current:
        date_str = txn.get("createdAt", "")[:10]
        if date_str:
            amount = float(_to_decimal(txn.get("netAmount", {}).get("amount", "0")))
            daily_revenue[date_str] += amount

    if len(daily_revenue) < 7:
        return []  # Not enough data for meaningful analysis

    # Fill in zero-revenue days
    all_days: list[str] = []
    current_day = start
    while current_day <= end:
        all_days.append(current_day.isoformat())
        current_day += timedelta(days=1)

    values = [daily_revenue.get(d, 0.0) for d in all_days]
    avg = mean(values) if values else 0.0
    std = stdev(values) if len(values) > 1 else 0.0

    anomalies = []
    if std > 0:
        for day_str, amount in zip(all_days, values, strict=True):
            deviation = (amount - avg) / std
            if abs(deviation) > 2.0:
                anomalies.append(
                    {
                        "date": day_str,
                        "amount": round(amount, 2),
                        "expected": round(avg, 2),
                        "deviation_std": round(deviation, 1),
                        "type": "spike" if deviation > 0 else "drop",
                    }
                )

    return anomalies


# =============================================================================
# Enhanced Analytics (Sprint 2)
# =============================================================================


def compute_trial_funnel(
    events: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Compute trial-to-paid conversion funnel.

    Tracks: install to first charge timing, conversion rate, average
    days to convert, and drop-off analysis.

    Args:
        events: App event list (installs and charge events).
        period_start: Start of the analysis period.
        period_end: End of the analysis period.

    Returns:
        Dict with keys: funnel (total_installs, converted,
        not_converted, conversion_rate_pct), timing (avg/median/
        fastest/slowest days), converted_merchants (top 10),
        unconverted_merchants (top 10), period.
    """
    installs = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_INSTALLED"],
        period_start,
        period_end,
        date_field="occurredAt",
    )
    charges = [
        e
        for e in events
        if e.get("type")
        in (
            "SUBSCRIPTION_CHARGE_ACCEPTED",
            "SUBSCRIPTION_CHARGE_ACTIVATED",
        )
    ]

    # Build install → first charge mapping
    install_map: dict[str, date] = {}
    for e in installs:
        shop = e.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        try:
            install_date = datetime.fromisoformat(
                e.get("occurredAt", "").replace("Z", "+00:00")
            ).date()
            if shop not in install_map or install_date < install_map[shop]:
                install_map[shop] = install_date
        except ValueError:
            continue

    # Find first charge per merchant
    charge_map: dict[str, date] = {}
    for e in charges:
        shop = e.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        try:
            charge_date = datetime.fromisoformat(
                e.get("occurredAt", "").replace("Z", "+00:00")
            ).date()
            if shop not in charge_map or charge_date < charge_map[shop]:
                charge_map[shop] = charge_date
        except ValueError:
            continue

    # Calculate conversion metrics
    total_installs = len(install_map)
    converted = []
    not_converted = []
    days_to_convert: list[int] = []

    for shop, install_date in install_map.items():
        if shop in charge_map:
            days = (charge_map[shop] - install_date).days
            if days >= 0:
                converted.append(
                    {
                        "shop": shop,
                        "installed": install_date.isoformat(),
                        "first_charge": charge_map[shop].isoformat(),
                        "days_to_convert": days,
                    }
                )
                days_to_convert.append(days)
        else:
            days_since_install = (date.today() - install_date).days
            not_converted.append(
                {
                    "shop": shop,
                    "installed": install_date.isoformat(),
                    "days_since_install": days_since_install,
                }
            )

    conversion_rate = (
        round(len(converted) / total_installs * 100, 1) if total_installs > 0 else 0.0
    )
    avg_days = round(mean(days_to_convert), 1) if days_to_convert else 0.0
    median_days = (
        sorted(days_to_convert)[len(days_to_convert) // 2] if days_to_convert else 0
    )

    return {
        "funnel": {
            "total_installs": total_installs,
            "converted": len(converted),
            "not_converted": len(not_converted),
            "conversion_rate_pct": conversion_rate,
        },
        "timing": {
            "avg_days_to_convert": avg_days,
            "median_days_to_convert": median_days,
            "fastest_conversion_days": min(days_to_convert) if days_to_convert else 0,
            "slowest_conversion_days": max(days_to_convert) if days_to_convert else 0,
        },
        "converted_merchants": sorted(converted, key=lambda x: x["days_to_convert"])[
            :10
        ],
        "unconverted_merchants": sorted(
            not_converted, key=lambda x: x["days_since_install"], reverse=True
        )[:10],
        "period": f"{period_start} to {period_end}",
    }


def compute_churn_risk(
    events: list[dict],
    transactions: list[dict],
) -> list[dict]:
    """Score merchants by churn risk using heuristic signals.

    Risk signals: deactivation events, subscription cancellations,
    reinstall patterns (instability), declining charge amounts,
    prolonged inactivity. Scored 0-100 -> low/medium/high.

    Args:
        events: App event list (full history).
        transactions: Revenue transaction list (full history).

    Returns:
        List of merchant dicts sorted by risk_score descending,
        each with keys: shop, risk_level, risk_score, factors.
    """
    # Scoring is heuristic, not ML -- weights chosen to surface obvious
    # risk signals first
    today = date.today()

    # Build merchant event timeline
    merchant_events: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        shop = e.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        try:
            event_date = datetime.fromisoformat(
                e.get("occurredAt", "").replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue
        merchant_events[shop].append(
            {
                "type": e.get("type", ""),
                "date": event_date,
            }
        )

    # Build merchant transaction timeline
    merchant_charges: dict[str, list[dict]] = defaultdict(list)
    for txn in transactions:
        if txn.get("__typename") != "AppSubscriptionSale":
            continue
        shop = txn.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        try:
            txn_date = datetime.fromisoformat(
                txn.get("createdAt", "").replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue
        amount = float(_to_decimal(txn.get("netAmount", {}).get("amount", "0")))
        merchant_charges[shop].append({"date": txn_date, "amount": amount})

    # Find currently active merchants using full two-track status model
    active_merchants = _active_merchants_from_events(events)

    # Score each active merchant
    risk_results = []
    for shop in active_merchants:
        risk_score = 0
        factors: list[str] = []
        evts = merchant_events.get(shop, [])
        charges = merchant_charges.get(shop, [])

        # Signal 1: Deactivation events
        deactivations = [e for e in evts if e["type"] == "RELATIONSHIP_DEACTIVATED"]
        if deactivations:
            risk_score += 30
            factors.append(f"{len(deactivations)} deactivation(s)")

        # Signal 2: Reinstall pattern (uninstall then reinstall)
        install_count = sum(1 for e in evts if e["type"] == "RELATIONSHIP_INSTALLED")
        if install_count > 1:
            risk_score += 20
            factors.append(f"Reinstalled {install_count - 1} time(s) -- unstable")

        # Signal 3: Subscription canceled
        cancels = [e for e in evts if e["type"] == "SUBSCRIPTION_CHARGE_CANCELED"]
        if cancels:
            recent_cancel = max(c["date"] for c in cancels)
            days_since = (today - recent_cancel).days
            if days_since < 30:
                risk_score += 25
                factors.append(f"Subscription canceled {days_since} days ago")
            elif days_since < 90:
                risk_score += 10
                factors.append(f"Subscription canceled {days_since} days ago")

        # Signal 4: Declining charge amounts
        if len(charges) >= 2:
            sorted_charges = sorted(charges, key=lambda x: x["date"])
            last_two = sorted_charges[-2:]
            if last_two[1]["amount"] < last_two[0]["amount"]:
                risk_score += 15
                factors.append(
                    f"Revenue declined: "
                    f"${last_two[0]['amount']:.2f} -> "
                    f"${last_two[1]['amount']:.2f}"
                )

        # Signal 5: Long time since last activity
        all_dates = [e["date"] for e in evts]
        if charges:
            all_dates.extend(c["date"] for c in charges)
        if all_dates:
            last_activity = max(all_dates)
            inactive_days = (today - last_activity).days
            if inactive_days > 60:
                risk_score += 20
                factors.append(f"No activity for {inactive_days} days")
            elif inactive_days > 30:
                risk_score += 10
                factors.append(f"No activity for {inactive_days} days")

        # Classify risk level
        if risk_score >= 40:
            risk_level = "high"
        elif risk_score >= 20:
            risk_level = "medium"
        else:
            risk_level = "low"

        risk_results.append(
            {
                "shop": shop,
                "risk_level": risk_level,
                "risk_score": risk_score,
                "factors": factors if factors else ["No risk signals detected"],
            }
        )

    # Sort by risk score descending
    risk_results.sort(key=lambda x: x["risk_score"], reverse=True)
    return risk_results


def compute_merchant_health(
    events: list[dict],
    transactions: list[dict],
) -> list[dict]:
    """Score merchants with a composite health grade (A-F).

    Dimensions (0-25 points each, 0-100 total):
        Tenure -- how long installed (longer = healthier).
        Revenue -- total and recent charges (more = healthier).
        Stability -- no deactivations/reinstalls (stable = healthier).
        Engagement -- recent activity (active = healthier).

    Args:
        events: App event list (full history).
        transactions: Revenue transaction list (full history).

    Returns:
        List of merchant dicts sorted by score descending, each with
        keys: shop, grade (A-F), score (0-100), dimensions.

    Each dimension scored 0-25, total 0-100 -> grade A/B/C/D/F.
    """
    # Scoring thresholds are calibrated for typical Shopify app merchant profiles
    today = date.today()

    # Build merchant profiles from events
    merchant_install_date: dict[str, date] = {}
    merchant_event_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    merchant_last_activity: dict[str, date] = {}

    for e in events:
        shop = e.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        try:
            event_date = datetime.fromisoformat(
                e.get("occurredAt", "").replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue

        event_type = e.get("type", "")
        merchant_event_counts[shop][event_type] += 1

        if event_type == "RELATIONSHIP_INSTALLED" and (
            shop not in merchant_install_date
            or event_date < merchant_install_date[shop]
        ):
            merchant_install_date[shop] = event_date

        if (
            shop not in merchant_last_activity
            or event_date > merchant_last_activity[shop]
        ):
            merchant_last_activity[shop] = event_date

    # Build revenue data from transactions
    merchant_revenue: dict[str, Decimal] = defaultdict(Decimal)
    merchant_recent_revenue: dict[str, Decimal] = defaultdict(Decimal)
    thirty_days_ago = today - timedelta(days=30)

    for txn in transactions:
        shop = txn.get("shop", {}).get("myshopifyDomain", "")
        if not shop:
            continue
        amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        merchant_revenue[shop] += amount

        date_str = txn.get("createdAt", "")
        if date_str:
            try:
                txn_date = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                ).date()
                if txn_date >= thirty_days_ago:
                    merchant_recent_revenue[shop] += amount
                if (
                    shop not in merchant_last_activity
                    or txn_date > merchant_last_activity[shop]
                ):
                    merchant_last_activity[shop] = txn_date
            except ValueError:
                continue

    # Find active merchants using full two-track status model
    active = _active_merchants_from_events(events)
    results = []

    for shop in active:
        # Tenure score (0-25): >180 days = 25, 90-180 = 20, 30-90 = 15, <30 = 10
        install_date = merchant_install_date.get(shop, today)
        tenure_days = (today - install_date).days
        if tenure_days > 180:
            tenure_score = 25
        elif tenure_days > 90:
            tenure_score = 20
        elif tenure_days > 30:
            tenure_score = 15
        else:
            tenure_score = 10

        # Revenue score (0-25): based on total revenue
        total_rev = float(merchant_revenue.get(shop, Decimal("0")))
        if total_rev > 100:
            revenue_score = 25
        elif total_rev > 50:
            revenue_score = 20
        elif total_rev > 10:
            revenue_score = 15
        elif total_rev > 0:
            revenue_score = 10
        else:
            revenue_score = 5

        # Stability score (0-25): no deactivations/reinstalls = 25
        counts = merchant_event_counts.get(shop, {})
        deactivations = counts.get("RELATIONSHIP_DEACTIVATED", 0)
        reinstalls = max(counts.get("RELATIONSHIP_INSTALLED", 0) - 1, 0)
        cancels = counts.get("SUBSCRIPTION_CHARGE_CANCELED", 0)
        stability_score = 25
        stability_score -= min(deactivations * 10, 15)
        stability_score -= min(reinstalls * 8, 10)
        stability_score -= min(cancels * 5, 10)
        stability_score = max(stability_score, 0)

        # Engagement score (0-25): recent activity
        last_active = merchant_last_activity.get(shop, install_date)
        days_inactive = (today - last_active).days
        if days_inactive < 7:
            engagement_score = 25
        elif days_inactive < 14:
            engagement_score = 20
        elif days_inactive < 30:
            engagement_score = 15
        elif days_inactive < 60:
            engagement_score = 10
        else:
            engagement_score = 5

        total_score = tenure_score + revenue_score + stability_score + engagement_score

        if total_score >= 85:
            grade = "A"
        elif total_score >= 70:
            grade = "B"
        elif total_score >= 55:
            grade = "C"
        elif total_score >= 40:
            grade = "D"
        else:
            grade = "F"

        results.append(
            {
                "shop": shop,
                "grade": grade,
                "score": total_score,
                "dimensions": {
                    "tenure": {"score": tenure_score, "days": tenure_days},
                    "revenue": {"score": revenue_score, "total": round(total_rev, 2)},
                    "stability": {"score": stability_score},
                    "engagement": {
                        "score": engagement_score,
                        "days_inactive": days_inactive,
                    },
                },
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def compute_business_digest(
    events: list[dict],
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Generate a business digest summarizing key metrics for a period.

    Provides a snapshot with highlights, notable changes, and
    comparison to the previous period. Designed for daily/weekly
    check-ins.

    Args:
        events: App event list (full history for comparison).
        transactions: Revenue transaction list (spanning both periods).
        period_start: Start of the current period.
        period_end: End of the current period.

    Returns:
        Dict with keys: period, summary (installs, uninstalls,
        net_growth, mrr, total_revenue, active_merchants),
        vs_previous_period, highlights, new_installs,
        recent_churns.
    """
    prev_start, prev_end = previous_period(period_start, period_end)

    # Current period events
    installs_current = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_INSTALLED"],
        period_start,
        period_end,
        date_field="occurredAt",
    )
    uninstalls_current = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_UNINSTALLED"],
        period_start,
        period_end,
        date_field="occurredAt",
    )

    # Previous period events
    installs_prev = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_INSTALLED"],
        prev_start,
        prev_end,
        date_field="occurredAt",
    )
    uninstalls_prev = _filter_by_date(
        [e for e in events if e.get("type") == "RELATIONSHIP_UNINSTALLED"],
        prev_start,
        prev_end,
        date_field="occurredAt",
    )

    # Revenue
    rev_current = compute_revenue_summary(
        transactions, period_start, period_end, events=events
    )
    rev_prev = compute_revenue_summary(
        transactions, prev_start, prev_end, events=events
    )

    # Highlights
    highlights: list[str] = []
    installs_count = len(installs_current)
    uninstalls_count = len(uninstalls_current)
    net = installs_count - uninstalls_count

    if installs_count > len(installs_prev):
        highlights.append(
            f"Installs up: {installs_count} vs {len(installs_prev)} last period"
        )
    elif installs_count < len(installs_prev):
        highlights.append(
            f"Installs down: {installs_count} vs {len(installs_prev)} last period"
        )

    if uninstalls_count > len(uninstalls_prev):
        highlights.append(
            f"Churn increased: {uninstalls_count} uninstalls "
            f"vs {len(uninstalls_prev)} last period"
        )

    if net > 0:
        highlights.append(f"Net positive growth: +{net} merchants")
    elif net < 0:
        highlights.append(f"Net negative growth: {net} merchants")

    current_mrr = _to_decimal(rev_current.get("mrr", "0"))
    prev_mrr = _to_decimal(rev_prev.get("mrr", "0"))
    if current_mrr > prev_mrr:
        highlights.append(f"MRR grew: ${rev_prev['mrr']} → ${rev_current['mrr']}")
    elif current_mrr < prev_mrr:
        highlights.append(f"MRR declined: ${rev_prev['mrr']} → ${rev_current['mrr']}")

    if not highlights:
        highlights.append("No notable changes this period")

    # New installs list
    new_installs = [
        {
            "shop": e.get("shop", {}).get("myshopifyDomain", "N/A"),
            "date": e.get("occurredAt", "")[:10],
        }
        for e in installs_current
    ]

    # Recent churns
    recent_churns = [
        {
            "shop": e.get("shop", {}).get("myshopifyDomain", "N/A"),
            "date": e.get("occurredAt", "")[:10],
            "reason": e.get("reason", "No reason provided"),
        }
        for e in uninstalls_current
    ]

    return {
        "period": f"{period_start} to {period_end}",
        "summary": {
            "installs": installs_count,
            "uninstalls": uninstalls_count,
            "net_growth": net,
            "mrr": rev_current["mrr"],
            "total_revenue": rev_current["total_net_revenue"],
            "active_merchants": rev_current["active_merchants"],
        },
        "vs_previous_period": {
            "installs_change": installs_count - len(installs_prev),
            "uninstalls_change": uninstalls_count - len(uninstalls_prev),
            "mrr_change": str((current_mrr - prev_mrr).quantize(Decimal("0.01"))),
        },
        "highlights": highlights,
        "new_installs": new_installs[:10],
        "recent_churns": recent_churns[:10],
    }


# =============================================================================
# Sprint 3: Full Coverage Analytics
# =============================================================================


def compute_revenue_forecast(
    transactions: list[dict],
    forecast_months: int = 6,
) -> dict:
    """Project future MRR based on recent growth and churn trends.

    Uses last 6 months of MRR data to calculate monthly growth rate,
    then projects forward. Simple linear model -- not ML.

    Args:
        transactions: Subscription transaction history (6+ months).
        forecast_months: Number of months to project. Defaults to 6.

    Returns:
        Dict with keys: current_mrr, monthly_growth_rate_pct,
        historical (last 6 months), projections (future months),
        projected_arr_end, model, caveat.
    """
    # Linear projection -- intentionally simple, caveat included in output
    today = date.today()

    # Calculate MRR for each of the last 6 months
    monthly_mrr: list[dict] = []
    for i in range(6, 0, -1):
        # Walk back i months from today's month start to get exact boundaries
        first_of_current = today.replace(day=1)
        target_month = (first_of_current.month - i - 1) % 12 + 1
        target_year = first_of_current.year + (first_of_current.month - i - 1) // 12
        month_start = date(target_year, target_month, 1)
        # month_end = last day of that month = day before next month's 1st
        next_month = target_month % 12 + 1
        next_year = target_year + (1 if next_month == 1 else 0)
        month_end = date(next_year, next_month, 1) - timedelta(days=1)

        month_txns = _filter_by_date(transactions, month_start, month_end)
        sub_txns = [
            t for t in month_txns if t.get("__typename") == "AppSubscriptionSale"
        ]

        merchants: dict[str, Decimal] = {}
        for txn in sub_txns:
            shop = txn.get("shop", {}).get("myshopifyDomain", "")
            if not shop:
                continue
            amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
            billing = txn.get("billingInterval", "EVERY_30_DAYS")
            monthly = amount / 12 if billing == "ANNUAL" else amount
            merchants[shop] = monthly

        mrr = sum(merchants.values(), Decimal("0"))
        monthly_mrr.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "mrr": float(mrr),
                "subscribers": len(merchants),
            }
        )

    # Calculate average monthly growth rate from last 3 months with data
    mrr_values = [m["mrr"] for m in monthly_mrr if m["mrr"] > 0]
    if len(mrr_values) >= 2:
        growth_rates = []
        for i in range(1, len(mrr_values)):
            if mrr_values[i - 1] > 0:
                rate = (mrr_values[i] - mrr_values[i - 1]) / mrr_values[i - 1]
                growth_rates.append(rate)
        avg_growth = mean(growth_rates) if growth_rates else 0.0
    else:
        avg_growth = 0.0

    current_mrr = mrr_values[-1] if mrr_values else 0.0

    # Project forward
    projections = []
    projected_mrr = current_mrr
    for i in range(1, forecast_months + 1):
        projected_mrr *= 1 + avg_growth
        future_month_num = (today.month - 1 + i) % 12 + 1
        future_year = today.year + (today.month - 1 + i) // 12
        projections.append(
            {
                "month": f"{future_year}-{future_month_num:02d}",
                "projected_mrr": round(projected_mrr, 2),
            }
        )

    # Annual projection
    projected_arr = projected_mrr * 12 if projections else current_mrr * 12

    return {
        "current_mrr": round(current_mrr, 2),
        "monthly_growth_rate_pct": round(avg_growth * 100, 1),
        "historical": monthly_mrr,
        "projections": projections,
        "projected_arr_end": round(projected_arr, 2),
        "model": "linear (avg monthly growth rate from last 6 months)",
        "caveat": (
            "Simple projection -- does not account for seasonality or market changes."
        ),
    }


def compute_merchant_timeline(
    events: list[dict],
    transactions: list[dict],
    shop_domain: str,
) -> dict:
    """Build a full timeline for a specific merchant.

    Shows every event and transaction in chronological order.
    Useful for support tickets and merchant relationship review.

    Args:
        events: App event list (full history).
        transactions: Revenue transaction list (full history).
        shop_domain: The merchant's myshopify.com domain.

    Returns:
        Dict with keys: shop, status (active/churned), first_seen,
        last_seen, total_revenue, total_events, installs,
        uninstalls, timeline (chronological list).
    """
    timeline: list[dict] = []

    # Add events
    for e in events:
        shop = e.get("shop", {}).get("myshopifyDomain", "")
        if shop != shop_domain:
            continue
        entry: dict = {
            "date": e.get("occurredAt", ""),
            "type": "event",
            "event_type": e.get("type", "Unknown"),
        }
        if e.get("reason"):
            entry["reason"] = e["reason"]
        if e.get("description"):
            entry["description"] = e["description"]
        if e.get("charge"):
            charge = e["charge"]
            entry["charge_amount"] = charge.get("amount", {}).get("amount", "")
            entry["charge_currency"] = charge.get("amount", {}).get("currencyCode", "")
        timeline.append(entry)

    # Add transactions
    for txn in transactions:
        shop = txn.get("shop", {}).get("myshopifyDomain", "")
        if shop != shop_domain:
            continue
        net = txn.get("netAmount", {})
        entry = {
            "date": txn.get("createdAt", ""),
            "type": "transaction",
            "transaction_type": txn.get("__typename", "Unknown"),
            "net_amount": net.get("amount", "0"),
            "currency": net.get("currencyCode", "USD"),
        }
        if txn.get("billingInterval"):
            entry["billing_interval"] = txn["billingInterval"]
        timeline.append(entry)

    # Sort chronologically
    timeline.sort(key=lambda x: x.get("date", ""))

    # Summary
    total_revenue = sum(
        float(_to_decimal(t.get("net_amount", "0")))
        for t in timeline
        if t["type"] == "transaction"
    )
    install_count = sum(
        1 for t in timeline if t.get("event_type") == "RELATIONSHIP_INSTALLED"
    )
    uninstall_count = sum(
        1 for t in timeline if t.get("event_type") == "RELATIONSHIP_UNINSTALLED"
    )

    first_seen = timeline[0]["date"][:10] if timeline else "N/A"
    last_seen = timeline[-1]["date"][:10] if timeline else "N/A"
    status = "active" if install_count > uninstall_count else "churned"

    return {
        "shop": shop_domain,
        "status": status,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "total_revenue": round(total_revenue, 2),
        "total_events": len(timeline),
        "installs": install_count,
        "uninstalls": uninstall_count,
        "timeline": timeline,
    }


def compute_growth_velocity(
    events: list[dict],
    transactions: list[dict],
    weeks: int = 12,
) -> dict:
    """Calculate week-over-week growth trends with acceleration signals.

    Shows install velocity, revenue velocity, and whether growth is
    accelerating, decelerating, or stable.

    Args:
        events: App event list (recent history).
        transactions: Revenue transaction list (recent history).
        weeks: Number of weeks to analyze. Defaults to 12.

    Returns:
        Dict with keys: summary (total_installs, total_uninstalls,
        net_growth, avg_weekly_installs, install_trend),
        weekly_data, weeks_analyzed.
    """
    today = date.today()

    # Weekly data
    weekly_data: list[dict] = []
    for i in range(weeks, 0, -1):
        week_end = today - timedelta(days=7 * (i - 1))
        week_start = week_end - timedelta(days=6)

        installs = _filter_by_date(
            [e for e in events if e.get("type") == "RELATIONSHIP_INSTALLED"],
            week_start,
            week_end,
            date_field="occurredAt",
        )
        uninstalls = _filter_by_date(
            [e for e in events if e.get("type") == "RELATIONSHIP_UNINSTALLED"],
            week_start,
            week_end,
            date_field="occurredAt",
        )
        week_txns = _filter_by_date(transactions, week_start, week_end)
        revenue = sum(
            (
                float(_to_decimal(t.get("netAmount", {}).get("amount", "0")))
                for t in week_txns
            ),
            0.0,
        )

        weekly_data.append(
            {
                "week": f"{week_start} to {week_end}",
                "installs": len(installs),
                "uninstalls": len(uninstalls),
                "net_growth": len(installs) - len(uninstalls),
                "revenue": round(revenue, 2),
            }
        )

    # Calculate velocity (rate of change)
    install_velocities: list[float] = []
    revenue_velocities: list[float] = []
    for i in range(1, len(weekly_data)):
        prev_installs = weekly_data[i - 1]["installs"]
        curr_installs = weekly_data[i]["installs"]
        if prev_installs > 0:
            install_velocities.append(
                (curr_installs - prev_installs) / prev_installs * 100
            )

        prev_rev = weekly_data[i - 1]["revenue"]
        curr_rev = weekly_data[i]["revenue"]
        if prev_rev > 0:
            revenue_velocities.append((curr_rev - prev_rev) / prev_rev * 100)

    # Acceleration (is velocity increasing or decreasing?)
    install_acceleration = "stable"
    if len(install_velocities) >= 3:
        recent = install_velocities[-3:]
        if all(recent[i] > recent[i - 1] for i in range(1, len(recent))):
            install_acceleration = "accelerating"
        elif all(recent[i] < recent[i - 1] for i in range(1, len(recent))):
            install_acceleration = "decelerating"

    # Totals for the full period
    total_installs = sum(w["installs"] for w in weekly_data)
    total_uninstalls = sum(w["uninstalls"] for w in weekly_data)
    avg_weekly_installs = round(total_installs / weeks, 1) if weeks > 0 else 0

    return {
        "summary": {
            "total_installs": total_installs,
            "total_uninstalls": total_uninstalls,
            "net_growth": total_installs - total_uninstalls,
            "avg_weekly_installs": avg_weekly_installs,
            "install_trend": install_acceleration,
        },
        "weekly_data": weekly_data,
        "weeks_analyzed": weeks,
    }


def compute_install_patterns(
    events: list[dict],
) -> dict:
    """Analyze install patterns by day of week and time of month.

    Helps identify best times for marketing pushes and feature
    releases.

    Args:
        events: App event list (install events only, or full list
            which will be filtered internally).

    Returns:
        Dict with keys: total_installs_analyzed, by_day_of_week,
        best_day, by_month_period, monthly_trend.
    """
    day_counts: dict[str, int] = defaultdict(int)
    month_period_counts: dict[str, int] = defaultdict(int)
    monthly_counts: dict[str, int] = defaultdict(int)

    installs = [e for e in events if e.get("type") == "RELATIONSHIP_INSTALLED"]

    for e in installs:
        date_str = e.get("occurredAt", "")
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            # Day of week
            day_name = dt.strftime("%A")
            day_counts[day_name] += 1

            # Period of month
            day_num = dt.day
            if day_num <= 10:
                month_period_counts["early (1-10)"] += 1
            elif day_num <= 20:
                month_period_counts["mid (11-20)"] += 1
            else:
                month_period_counts["late (21-31)"] += 1

            # Monthly trend
            monthly_counts[dt.strftime("%Y-%m")] += 1
        except ValueError:
            continue

    # Sort days by count
    day_order = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    days_sorted = {d: day_counts.get(d, 0) for d in day_order}
    best_day = max(days_sorted, key=days_sorted.get) if days_sorted else "N/A"

    # Sort months
    monthly_sorted = dict(sorted(monthly_counts.items()))

    return {
        "total_installs_analyzed": len(installs),
        "by_day_of_week": days_sorted,
        "best_day": best_day,
        "by_month_period": dict(month_period_counts),
        "monthly_trend": monthly_sorted,
    }


def compute_referral_revenue(
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Track referral revenue from ReferralTransaction type.

    Shopify pays partners for referring merchants to the platform.

    Args:
        transactions: Revenue transaction list (includes referrals).
        period_start: Start of the analysis period.
        period_end: End of the analysis period.

    Returns:
        Dict with keys: total_referral_revenue, referral_count,
        previous_period_revenue, referrals (list), period.
    """
    current = _filter_by_date(transactions, period_start, period_end)
    referrals = [t for t in current if t.get("__typename") == "ReferralTransaction"]

    total_gross = Decimal("0")
    shops: list[dict] = []
    for txn in referrals:
        gross = _to_decimal(txn.get("grossAmount", {}).get("amount", "0"))
        total_gross += gross
        shops.append(
            {
                "shop": txn.get("shop", {}).get("myshopifyDomain", "N/A"),
                "amount": str(gross.quantize(Decimal("0.01"))),
                "date": txn.get("createdAt", "")[:10],
            }
        )

    prev_start, prev_end = previous_period(period_start, period_end)
    prev_referrals = [
        t
        for t in _filter_by_date(transactions, prev_start, prev_end)
        if t.get("__typename") == "ReferralTransaction"
    ]
    prev_total = sum(
        (
            _to_decimal(t.get("grossAmount", {}).get("amount", "0"))
            for t in prev_referrals
        ),
        Decimal("0"),
    )

    return {
        "total_referral_revenue": str(total_gross.quantize(Decimal("0.01"))),
        "referral_count": len(referrals),
        "previous_period_revenue": str(prev_total.quantize(Decimal("0.01"))),
        "referrals": shops,
        "period": f"{period_start} to {period_end}",
    }


def compute_credits_adjustments(
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Track credits, adjustments, and refunds.

    Monitors AppSaleCredit and AppSaleAdjustment transactions --
    how much revenue is being given back.

    Args:
        transactions: Revenue transaction list (includes
            credits/adjustments).
        period_start: Start of the analysis period.
        period_end: End of the analysis period.

    Returns:
        Dict with keys: total_credits, total_adjustments, combined,
        credit_count, adjustment_count, giveback_pct_of_revenue,
        details (list), period.
    """
    current = _filter_by_date(transactions, period_start, period_end)
    credits = [t for t in current if t.get("__typename") == "AppSaleCredit"]
    adjustments = [t for t in current if t.get("__typename") == "AppSaleAdjustment"]

    total_credits = sum(
        (_to_decimal(t.get("netAmount", {}).get("amount", "0")) for t in credits),
        Decimal("0"),
    )
    total_adjustments = sum(
        (_to_decimal(t.get("netAmount", {}).get("amount", "0")) for t in adjustments),
        Decimal("0"),
    )

    # Total gross revenue for context
    all_revenue = sum(
        (
            _to_decimal(t.get("netAmount", {}).get("amount", "0"))
            for t in current
            if t.get("__typename")
            in ("AppSubscriptionSale", "AppUsageSale", "AppOneTimeSale")
        ),
        Decimal("0"),
    )

    giveback_pct = (
        float(abs(total_credits + total_adjustments) / all_revenue * 100)
        if all_revenue > 0
        else 0.0
    )

    credit_details = [
        {
            "shop": t.get("shop", {}).get("myshopifyDomain", "N/A"),
            "app": t.get("app", {}).get("name", "N/A"),
            "amount": t.get("netAmount", {}).get("amount", "0"),
            "date": t.get("createdAt", "")[:10],
        }
        for t in credits + adjustments
    ]

    return {
        "total_credits": str(total_credits.quantize(Decimal("0.01"))),
        "total_adjustments": str(total_adjustments.quantize(Decimal("0.01"))),
        "combined": str((total_credits + total_adjustments).quantize(Decimal("0.01"))),
        "credit_count": len(credits),
        "adjustment_count": len(adjustments),
        "giveback_pct_of_revenue": round(giveback_pct, 1),
        "details": credit_details,
        "period": f"{period_start} to {period_end}",
    }
