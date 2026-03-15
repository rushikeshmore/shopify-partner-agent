"""
Analytics computation functions for PartnerAgent.

Pure functions that take raw transaction/event lists and return computed
metrics. No API calls — just math. Independently testable.

All financial math uses Decimal for precision.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import mean, stdev


# =============================================================================
# Period Parsing
# =============================================================================


def parse_period(period: str) -> tuple[date, date]:
    """Parse a period string into (start_date, end_date).

    Supported formats:
        '7d', '30d', '90d', '365d' — relative days from today
        '1y' — 365 days
        'YYYY-MM-DD:YYYY-MM-DD' — custom range

    Returns (start_date, end_date) as date objects.
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
    """Calculate the equivalent previous period for growth comparison."""
    duration = (end - start).days
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=duration)
    return prev_start, prev_end


# =============================================================================
# Financial Helpers
# =============================================================================


def _to_decimal(amount_str: str) -> Decimal:
    """Safely convert a string amount to Decimal."""
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
    """Filter items by date range."""
    result = []
    for item in items:
        date_str = item.get(date_field, "")
        if not date_str:
            continue
        try:
            item_date = datetime.fromisoformat(
                date_str.replace("Z", "+00:00")
            ).date()
            if start <= item_date <= end:
                result.append(item)
        except ValueError:
            continue
    return result


# =============================================================================
# Revenue Analytics
# =============================================================================


def compute_revenue_summary(
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Compute MRR, ARR, total revenue, growth rate, ARPU, and fees.

    Args:
        transactions: Raw transaction list from API (should include both
                      current and previous period for growth calculation).
        period_start: Start of the analysis period.
        period_end: End of the analysis period.

    Returns dict with: mrr, arr, total_net_revenue, total_gross_revenue,
    total_shopify_fees, growth_rate_pct, arpu, active_merchants,
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
    by_app: dict[str, dict] = defaultdict(
        lambda: {"net": Decimal("0"), "count": 0}
    )

    # Track unique merchants with subscription charges for MRR
    subscription_merchants: dict[str, Decimal] = {}
    active_merchants: set[str] = set()

    for txn in current:
        typename = txn.get("__typename", "")
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

        # MRR from subscription charges
        if typename == "AppSubscriptionSale" and shop:
            billing = txn.get("billingInterval", "EVERY_30_DAYS")
            monthly = net / 12 if billing == "ANNUAL" else net
            subscription_merchants[shop] = monthly

    # Calculate MRR and derived metrics
    mrr = sum(subscription_merchants.values(), Decimal("0"))
    arr = mrr * 12
    total_net = sum((c["net"] for c in by_currency.values()), Decimal("0"))
    total_gross = sum((c["gross"] for c in by_currency.values()), Decimal("0"))
    total_fees = sum((c["fees"] for c in by_currency.values()), Decimal("0"))
    merchant_count = len(active_merchants)
    arpu = total_net / merchant_count if merchant_count > 0 else Decimal("0")

    # Growth rate
    prev_total = sum(
        _to_decimal(t.get("netAmount", {}).get("amount", "0")) for t in prev
    )
    growth_pct = (
        float((total_net - prev_total) / prev_total * 100)
        if prev_total > 0
        else None
    )

    return {
        "mrr": str(mrr.quantize(Decimal("0.01"))),
        "arr": str(arr.quantize(Decimal("0.01"))),
        "total_net_revenue": str(total_net.quantize(Decimal("0.01"))),
        "total_gross_revenue": str(total_gross.quantize(Decimal("0.01"))),
        "total_shopify_fees": str(total_fees.quantize(Decimal("0.01"))),
        "active_merchants": merchant_count,
        "arpu": str(arpu.quantize(Decimal("0.01"))),
        "transaction_count": len(current),
        "growth_rate_pct": round(growth_pct, 1) if growth_pct is not None else None,
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
    """
    def _subscription_map(txns: list[dict]) -> dict[str, Decimal]:
        """Map shop domain → monthly subscription amount."""
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
            # Could be new or reactivation — we'd need older history
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
    """Compute payout summary: gross, fees, net, by type."""
    current = _filter_by_date(transactions, period_start, period_end)

    by_type: dict[str, dict] = defaultdict(
        lambda: {"gross": Decimal("0"), "fees": Decimal("0"), "net": Decimal("0"), "count": 0}
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
# =============================================================================


def compute_churn_analysis(
    events: list[dict],
    transactions: list[dict],
    period_start: date,
    period_end: date,
) -> dict:
    """Compute revenue churn %, subscription churn %, and logo churn %.

    Formulas (industry standard, matching HeyMantle):
        Revenue Churn % = (Churned MRR + Contraction) / Starting MRR × 100
        Logo Churn %    = Uninstalled merchants / Starting merchants × 100
        Sub Churn %     = Canceled subscriptions / Starting subs × 100
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
    uninstalled_set = {e.get("shop", {}).get("myshopifyDomain") for e in uninstalls_before}
    starting_merchants = len(installed_set - uninstalled_set)
    churned_merchants = len({
        e.get("shop", {}).get("myshopifyDomain") for e in uninstalls_during
    })

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
        round(float(starting_mrr / prev_mrr * 100), 1)
        if prev_mrr > 0
        else 0.0
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

    Cohorts are based on first payment month (matching HeyMantle).
    Retention = Revenue in month N / Revenue in month 0.
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
            txn_date = datetime.fromisoformat(
                date_str.replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue

        amount = _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
        month_key = txn_date.strftime("%Y-%m")

        if shop not in merchant_first_payment or txn_date < merchant_first_payment[shop]:
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

    LTV = ARPU / Monthly churn rate
    ARPU = Total net revenue / Active merchants
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
                if shop not in merchant_first_txn or txn_date < merchant_first_txn[shop]:
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
    arpu = total_revenue / merchant_count if merchant_count > 0 else Decimal("0")

    # Simple LTV = ARPU × average lifespan
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

    Flags days where revenue is > 2 standard deviations from the rolling mean.
    Also flags: drops to zero after sustained revenue, sudden spikes.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    current = _filter_by_date(transactions, start, end)

    # Group revenue by day
    daily_revenue: dict[str, float] = defaultdict(float)
    for txn in current:
        date_str = txn.get("createdAt", "")[:10]
        if date_str:
            amount = float(
                _to_decimal(txn.get("netAmount", {}).get("amount", "0"))
            )
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
        for day_str, amount in zip(all_days, values):
            deviation = (amount - avg) / std
            if abs(deviation) > 2.0:
                anomalies.append({
                    "date": day_str,
                    "amount": round(amount, 2),
                    "expected": round(avg, 2),
                    "deviation_std": round(deviation, 1),
                    "type": "spike" if deviation > 0 else "drop",
                })

    return anomalies
