"""Tests for analytics.py -- pure computation functions."""

from datetime import date, timedelta
from decimal import Decimal

from analytics import (
    _filter_by_date,
    _to_decimal,
    compute_churn_analysis,
    compute_churn_risk,
    compute_credits_adjustments,
    compute_customer_ltv,
    compute_growth_velocity,
    compute_install_patterns,
    compute_merchant_health,
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

# ---- Fixtures: realistic Shopify Partner API data ----


def _tx(
    type_name: str = "AppSubscriptionSale",
    net: str = "10.00",
    gross: str = "12.50",
    fee: str = "2.50",
    currency: str = "USD",
    shop: str = "store-a.myshopify.com",
    app: str = "My App",
    app_id: str = "gid://partners/App/111",
    days_ago: int = 5,
    billing: str = "EVERY_30_DAYS",
    charge_id: str = "gid://partners/Charge/1",
) -> dict:
    """Build a fake transaction dict matching Shopify Partner API shape."""
    created = (date.today() - timedelta(days=days_ago)).isoformat() + "T00:00:00Z"
    base = {
        "__typename": type_name,
        "id": f"gid://partners/Transaction/{days_ago}",
        "createdAt": created,
        "app": {"id": app_id, "name": app},
        "shop": {
            "id": f"gid://partners/Shop/{hash(shop) % 9999}",
            "name": shop.split(".")[0],
            "myshopifyDomain": shop,
        },
        "netAmount": {"amount": net, "currencyCode": currency},
        "grossAmount": {"amount": gross, "currencyCode": currency},
        "shopifyFee": {"amount": fee, "currencyCode": currency},
    }
    if type_name == "AppSubscriptionSale":
        base["billingInterval"] = billing
        base["chargeId"] = charge_id
    return base


def _event(
    event_type: str = "RELATIONSHIP_INSTALLED",
    shop: str = "store-a.myshopify.com",
    days_ago: int = 5,
    reason: str | None = None,
    charge_amount: str | None = None,
) -> dict:
    """Build a fake event dict matching Shopify Partner API shape."""
    occurred = (date.today() - timedelta(days=days_ago)).isoformat() + "T00:00:00Z"
    ev = {
        "type": event_type,
        "occurredAt": occurred,
        "shop": {
            "id": f"gid://partners/Shop/{hash(shop) % 9999}",
            "name": shop.split(".")[0],
            "myshopifyDomain": shop,
        },
    }
    if reason is not None:
        ev["reason"] = reason
        ev["description"] = reason
    if charge_amount is not None:
        ev["charge"] = {
            "id": "gid://partners/Charge/1",
            "amount": {"amount": charge_amount, "currencyCode": "USD"},
        }
    return ev


# ---- parse_period ----


class TestParsePeriod:
    def test_30d(self):
        start, end = parse_period("30d")
        assert end == date.today()
        assert (end - start).days == 30

    def test_7d(self):
        start, end = parse_period("7d")
        assert (end - start).days == 7

    def test_90d(self):
        start, end = parse_period("90d")
        assert (end - start).days == 90

    def test_1y(self):
        start, end = parse_period("1y")
        assert (end - start).days == 365

    def test_custom_range(self):
        start, end = parse_period("2025-01-01:2025-03-31")
        assert start == date(2025, 1, 1)
        assert end == date(2025, 3, 31)

    def test_empty_defaults_30d(self):
        start, end = parse_period("")
        assert (end - start).days == 30

    def test_invalid_defaults_30d(self):
        start, end = parse_period("xyz")
        assert (end - start).days == 30


# ---- previous_period ----


class TestPreviousPeriod:
    def test_30_day_period(self):
        start = date(2025, 3, 1)
        end = date(2025, 3, 31)
        prev_start, prev_end = previous_period(start, end)
        assert prev_end == date(2025, 2, 28)
        assert (prev_end - prev_start).days == 30

    def test_7_day_period(self):
        end = date.today()
        start = end - timedelta(days=7)
        prev_start, prev_end = previous_period(start, end)
        assert prev_end == start - timedelta(days=1)
        assert (prev_end - prev_start).days == 7


# ---- _to_decimal ----


class TestToDecimal:
    def test_valid_string(self):
        assert _to_decimal("10.50") == Decimal("10.50")

    def test_zero(self):
        assert _to_decimal("0") == Decimal("0")

    def test_negative(self):
        assert _to_decimal("-5.25") == Decimal("-5.25")

    def test_invalid_returns_zero(self):
        assert _to_decimal("not_a_number") == Decimal("0")

    def test_empty_returns_zero(self):
        assert _to_decimal("") == Decimal("0")


# ---- _filter_by_date ----


class TestFilterByDate:
    def test_filters_within_range(self):
        items = [
            {"createdAt": "2025-03-15T12:00:00Z"},
            {"createdAt": "2025-03-25T12:00:00Z"},
            {"createdAt": "2025-04-05T12:00:00Z"},
        ]
        result = _filter_by_date(items, date(2025, 3, 10), date(2025, 3, 31))
        assert len(result) == 2

    def test_empty_list(self):
        assert _filter_by_date([], date(2025, 1, 1), date(2025, 12, 31)) == []

    def test_missing_date_field(self):
        items = [{"other": "value"}]
        assert _filter_by_date(items, date(2025, 1, 1), date(2025, 12, 31)) == []

    def test_custom_date_field(self):
        items = [{"occurredAt": "2025-03-15T00:00:00Z"}]
        result = _filter_by_date(
            items, date(2025, 3, 1), date(2025, 3, 31), date_field="occurredAt"
        )
        assert len(result) == 1


# ---- compute_revenue_summary ----


class TestRevenueSummary:
    def test_basic_revenue(self):
        txns = [
            _tx(net="100.00", gross="125.00", fee="25.00", days_ago=5),
            _tx(net="50.00", gross="62.50", fee="12.50", days_ago=10),
        ]
        result = compute_revenue_summary(txns, *parse_period("30d"))
        assert float(result["total_net_revenue"]) == 150.0
        assert float(result["total_gross_revenue"]) == 187.5
        assert float(result["total_shopify_fees"]) == 37.5
        assert result["transaction_count"] == 2

    def test_empty_transactions(self):
        result = compute_revenue_summary([], *parse_period("30d"))
        assert float(result["mrr"]) == 0
        assert result["active_merchants"] == 0

    def test_mrr_from_subscriptions(self):
        txns = [
            _tx(
                type_name="AppSubscriptionSale",
                net="30.00",
                shop="a.myshopify.com",
                days_ago=3,
                charge_id="c1",
            ),
            _tx(
                type_name="AppSubscriptionSale",
                net="30.00",
                shop="b.myshopify.com",
                days_ago=7,
                charge_id="c2",
            ),
        ]
        result = compute_revenue_summary(txns, *parse_period("30d"))
        assert float(result["mrr"]) == 60.0

    def test_annual_billing_divides_by_12(self):
        txns = [
            _tx(
                net="120.00",
                billing="ANNUAL",
                days_ago=5,
                charge_id="c1",
            ),
        ]
        result = compute_revenue_summary(txns, *parse_period("30d"))
        assert float(result["mrr"]) == 10.0


# ---- compute_mrr_movement ----
# Signature: (current_transactions, previous_transactions)


class TestMrrMovement:
    def test_new_mrr(self):
        current = [
            _tx(net="50.00", shop="a.myshopify.com", days_ago=5, charge_id="c1"),
        ]
        previous = []
        result = compute_mrr_movement(current, previous)
        assert "net_new_mrr" in result
        assert "current_subscribers" in result

    def test_empty_data(self):
        result = compute_mrr_movement([], [])
        assert float(result["net_new_mrr"]) == 0


# ---- compute_payout_summary ----


class TestPayoutSummary:
    def test_basic_payout(self):
        txns = [
            _tx(net="80.00", gross="100.00", fee="20.00", days_ago=5),
        ]
        result = compute_payout_summary(txns, *parse_period("30d"))
        assert float(result["total_net_payout"]) == 80.0
        assert float(result["total_gross"]) == 100.0
        assert float(result["total_shopify_fees"]) == 20.0

    def test_empty(self):
        result = compute_payout_summary([], *parse_period("30d"))
        assert float(result["total_net_payout"]) == 0


# ---- compute_churn_analysis ----
# Signature: (events, transactions, period_start, period_end)
# Returns: logo_churn, revenue_churn, uninstall_reasons, period


class TestChurnAnalysis:
    def test_with_uninstalls(self):
        events = [
            _event("RELATIONSHIP_INSTALLED", "a.myshopify.com", days_ago=60),
            _event("RELATIONSHIP_INSTALLED", "b.myshopify.com", days_ago=60),
            _event(
                "RELATIONSHIP_UNINSTALLED",
                "b.myshopify.com",
                days_ago=5,
                reason="Too expensive",
            ),
        ]
        txns = [
            _tx(net="30.00", shop="a.myshopify.com", days_ago=5, charge_id="c1"),
        ]
        result = compute_churn_analysis(events, txns, *parse_period("90d"))
        assert "logo_churn" in result
        assert "revenue_churn" in result
        assert "uninstall_reasons" in result

    def test_no_churn(self):
        events = [
            _event("RELATIONSHIP_INSTALLED", "a.myshopify.com", days_ago=10),
        ]
        result = compute_churn_analysis(events, [], *parse_period("30d"))
        assert "logo_churn" in result


# ---- compute_customer_ltv ----
# Signature: (transactions, events)


class TestCustomerLtv:
    def test_basic_ltv(self):
        txns = [
            _tx(net="50.00", shop="a.myshopify.com", days_ago=5),
            _tx(net="50.00", shop="a.myshopify.com", days_ago=35),
            _tx(net="30.00", shop="b.myshopify.com", days_ago=10),
        ]
        events = [
            _event("RELATIONSHIP_INSTALLED", "a.myshopify.com", days_ago=60),
            _event("RELATIONSHIP_INSTALLED", "b.myshopify.com", days_ago=30),
        ]
        result = compute_customer_ltv(txns, events)
        assert result["total_merchants"] >= 1
        assert "ltv" in result
        assert "arpu" in result

    def test_empty(self):
        result = compute_customer_ltv([], [])
        assert result["total_merchants"] == 0


# ---- compute_plan_performance ----


class TestPlanPerformance:
    def test_with_subscriptions(self):
        txns = [
            _tx(net="10.00", days_ago=5, charge_id="c1"),
            _tx(net="10.00", days_ago=10, charge_id="c2"),
        ]
        result = compute_plan_performance(txns, *parse_period("30d"))
        assert "plans" in result

    def test_empty(self):
        result = compute_plan_performance([], *parse_period("30d"))
        assert result["plans"] == [] or result["plans"] == {}


# ---- compute_trial_funnel ----
# Signature: (events, period_start, period_end)
# Returns: funnel, timing, converted_merchants, unconverted_merchants, period


class TestTrialFunnel:
    def test_conversion(self):
        events = [
            _event("RELATIONSHIP_INSTALLED", "a.myshopify.com", days_ago=30),
            _event("RELATIONSHIP_INSTALLED", "b.myshopify.com", days_ago=25),
            _event(
                "SUBSCRIPTION_CHARGE_ACCEPTED",
                "a.myshopify.com",
                days_ago=20,
                charge_amount="10.00",
            ),
        ]
        result = compute_trial_funnel(events, *parse_period("60d"))
        assert "funnel" in result
        assert "converted_merchants" in result

    def test_no_installs(self):
        result = compute_trial_funnel([], *parse_period("30d"))
        assert "funnel" in result


# ---- compute_churn_risk ----
# Signature: (events, transactions) -> list[dict]


class TestChurnRisk:
    def test_scores_merchants(self):
        events = [
            _event("RELATIONSHIP_INSTALLED", "a.myshopify.com", days_ago=90),
            _event("RELATIONSHIP_INSTALLED", "b.myshopify.com", days_ago=10),
        ]
        txns = [
            _tx(net="50.00", shop="a.myshopify.com", days_ago=5, charge_id="c1"),
        ]
        result = compute_churn_risk(events, txns)
        assert isinstance(result, list)
        for m in result:
            assert m["risk_level"] in ("low", "medium", "high")

    def test_empty(self):
        result = compute_churn_risk([], [])
        assert result == []


# ---- compute_merchant_health ----
# Signature: (events, transactions) -> list[dict]


class TestMerchantHealth:
    def test_grades_merchants(self):
        events = [
            _event("RELATIONSHIP_INSTALLED", "a.myshopify.com", days_ago=180),
        ]
        txns = [
            _tx(net="30.00", shop="a.myshopify.com", days_ago=i * 30, charge_id=f"c{i}")
            for i in range(1, 5)
        ]
        result = compute_merchant_health(events, txns)
        assert isinstance(result, list)
        for m in result:
            assert m["grade"] in ("A", "B", "C", "D", "F")

    def test_empty(self):
        result = compute_merchant_health([], [])
        assert result == []


# ---- find_revenue_anomalies ----
# Signature: (transactions, lookback_days=90) -> list[dict]


class TestRevenueAnomalies:
    def test_no_anomalies_with_steady_revenue(self):
        txns = [_tx(net="100.00", days_ago=i * 7) for i in range(8)]
        result = find_revenue_anomalies(txns)
        assert isinstance(result, list)

    def test_empty(self):
        result = find_revenue_anomalies([])
        assert result == []


# ---- compute_growth_velocity ----
# Signature: (events, transactions, weeks=12) -> dict
# Returns: summary, weekly_data, weeks_analyzed


class TestGrowthVelocity:
    def test_with_events(self):
        events = [
            _event("RELATIONSHIP_INSTALLED", f"store-{i}.myshopify.com", days_ago=i * 7)
            for i in range(6)
        ]
        txns = []
        result = compute_growth_velocity(events, txns)
        assert "weeks_analyzed" in result
        assert "weekly_data" in result

    def test_empty(self):
        result = compute_growth_velocity([], [])
        assert "summary" in result


# ---- compute_install_patterns ----
# Signature: (events) -> dict
# Returns: total_installs_analyzed, by_day_of_week, best_day, ...


class TestInstallPatterns:
    def test_finds_patterns(self):
        events = [
            _event("RELATIONSHIP_INSTALLED", f"s-{i}.myshopify.com", days_ago=i)
            for i in range(14)
        ]
        result = compute_install_patterns(events)
        assert result["total_installs_analyzed"] >= 1
        assert "by_day_of_week" in result

    def test_empty(self):
        result = compute_install_patterns([])
        assert result["total_installs_analyzed"] == 0


# ---- compute_retention_cohorts ----
# Signature: (transactions, months=12) -> dict


class TestRetentionCohorts:
    def test_with_data(self):
        txns = [
            _tx(net="30.00", shop="a.myshopify.com", days_ago=80, charge_id="c1"),
            _tx(net="30.00", shop="a.myshopify.com", days_ago=50, charge_id="c2"),
        ]
        result = compute_retention_cohorts(txns)
        assert "cohorts" in result

    def test_empty(self):
        result = compute_retention_cohorts([])
        assert result["cohorts"] == [] or result["cohorts"] == {}


# ---- compute_revenue_forecast ----
# Returns: current_mrr, projections (not forecast), ...


class TestRevenueForecast:
    def test_projects_months(self):
        txns = [_tx(net="100.00", days_ago=i * 30, charge_id=f"c{i}") for i in range(4)]
        result = compute_revenue_forecast(txns)
        assert "current_mrr" in result
        assert "projections" in result

    def test_empty(self):
        result = compute_revenue_forecast([])
        assert float(result["current_mrr"]) == 0


# ---- compute_referral_revenue ----


class TestReferralRevenue:
    def test_with_referrals(self):
        txns = [
            _tx(type_name="ReferralTransaction", net="25.00", days_ago=5),
        ]
        result = compute_referral_revenue(txns, *parse_period("30d"))
        assert "total_referral_revenue" in result

    def test_empty(self):
        result = compute_referral_revenue([], *parse_period("30d"))
        assert float(result["total_referral_revenue"]) == 0


# ---- compute_credits_adjustments ----
# Returns: total_credits, total_adjustments, credit_count, ...


class TestCreditsAdjustments:
    def test_with_credits(self):
        txns = [
            _tx(type_name="AppSaleCredit", net="-10.00", days_ago=5),
            _tx(type_name="AppSaleAdjustment", net="-5.00", days_ago=10),
        ]
        result = compute_credits_adjustments(txns, *parse_period("30d"))
        assert "total_credits" in result
        assert "total_adjustments" in result

    def test_empty(self):
        result = compute_credits_adjustments([], *parse_period("30d"))
        assert result["credit_count"] == 0
        assert result["adjustment_count"] == 0
