# PartnerAgent — Shopify Partner MCP Server

## What This Is
MCP server that connects Claude to the Shopify Partner GraphQL API. Gives Claude access to app analytics, revenue metrics, churn data, and merchant info — replacing dashboards like HeyMantle ($49-999/mo).

## Architecture
```
server.py            → FastMCP, 15 @mcp.tool() definitions
shopify_partner.py   → ShopifyPartnerClient (async GraphQL + pagination + rate limiting)
queries.py           → GraphQL query string constants
analytics.py         → Pure computation functions (MRR, churn, ARPU, cohorts, anomalies)
```

## 25 MCP Tools

**Core Data (5):** discover_apps, get_app_details, get_app_events, get_transactions, get_merchants

**Revenue Analytics (4):** get_revenue_summary, get_mrr_movement, get_payout_report, get_plan_performance

**Customer Analytics (4):** get_churn_analysis, get_retention_cohorts, get_customer_ltv, get_churned_merchants

**Insight (2):** get_revenue_anomalies, get_app_comparison

**Enhanced Analytics (4):** get_trial_funnel, get_churn_risk, get_merchant_health, get_business_digest

**Full Coverage (6):** get_revenue_forecast, get_merchant_lookup, get_growth_velocity, get_install_patterns, get_referral_revenue, get_credits_adjustments

## API Details
- Endpoint: `POST https://partners.shopify.com/{org_id}/api/2026-01/graphql.json`
- Auth: Static token via `X-Shopify-Access-Token` header
- Rate limit: 4 req/sec (0.3s sleep between requests)
- Read-only — no mutations

## Key Constraints
- No `apps` query — discover from transactions or configure in `.env`
- App IDs must be GID format: `gid://partners/App/1234`
- Financial amounts are string decimals — use `Decimal` for math
- `billingInterval` can be `EVERY_30_DAYS` or `ANNUAL` — divide annual by 12 for MRR
- GraphQL can return HTTP 200 with `errors` in body — check both

## Stack
Python 3.12, FastMCP (mcp>=1.26.0), httpx, python-dotenv

## Setup
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill .env with Partner API credentials
claude mcp add partner-agent .venv/bin/python server.py
```
