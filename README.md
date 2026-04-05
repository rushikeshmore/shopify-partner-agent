# PartnerAgent

MCP server that gives Claude access to your Shopify Partner analytics. 25 tools covering revenue metrics, churn analysis, retention cohorts, merchant health scores, conversion funnels, revenue forecasting, and growth velocity -- everything HeyMantle charges $49-999/mo for, powered by Claude's natural language intelligence.

## Why

Existing Shopify MCP servers connect to the **Admin API** (store operations). None connect to the **Partner API** (app developer analytics). PartnerAgent fills this gap.

Ask Claude things like:
- "What's my MRR?"
- "Who uninstalled my app recently and why?"
- "Show me retention cohorts for the last 6 months"
- "Compare all my apps this quarter"

## Tools

### Core Data (5)
| Tool | What it does |
|------|-------------|
| `discover_apps` | Find all apps in your partner account |
| `get_app_details` | Details for a specific app |
| `get_app_events` | Installs, uninstalls, charge events |
| `get_transactions` | Revenue transactions with filtering |
| `get_merchants` | Active and churned merchant list |

### Revenue Analytics (4)
| Tool | What it does |
|------|-------------|
| `get_revenue_summary` | MRR, ARR, total revenue, growth rate, ARPU |
| `get_mrr_movement` | New / expansion / contraction / churn / reactivation breakdown |
| `get_payout_report` | Gross revenue, Shopify fees, net payout by type |
| `get_plan_performance` | Revenue, subscribers, LTV per pricing tier |

### Customer Analytics (4)
| Tool | What it does |
|------|-------------|
| `get_churn_analysis` | Revenue churn %, logo churn %, uninstall reason breakdown |
| `get_retention_cohorts` | Monthly revenue retention cohort table |
| `get_customer_ltv` | LTV, ARPU, average lifespan, top/bottom merchants |
| `get_churned_merchants` | Recent uninstalls with reasons |

### Insight (2)
| Tool | What it does |
|------|-------------|
| `get_revenue_anomalies` | Detect unusual revenue patterns (statistical) |
| `get_app_comparison` | Side-by-side metrics across all your apps |

### Enhanced Analytics (4)
| Tool | What it does |
|------|-------------|
| `get_trial_funnel` | Install → paid conversion rate, timing, unconverted merchants |
| `get_churn_risk` | Score active merchants by churn risk (low/medium/high) with factors |
| `get_merchant_health` | Composite health grade (A-F) based on tenure, revenue, stability, engagement |
| `get_business_digest` | Weekly/monthly summary with highlights and period comparison |

### Full Coverage (6)
| Tool | What it does |
|------|-------------|
| `get_revenue_forecast` | Project MRR for next 3-6 months based on growth trends |
| `get_merchant_lookup` | Full timeline for a specific merchant (events + transactions) |
| `get_growth_velocity` | Week-over-week trends with acceleration/deceleration signals |
| `get_install_patterns` | Best day of week, time of month for installs |
| `get_referral_revenue` | Track revenue from Shopify merchant referrals |
| `get_credits_adjustments` | Track refunds, credits, adjustments and giveback % |

## Setup

### 1. Create Partner API client

1. Go to [Partners Dashboard](https://partners.shopify.com) → Settings → Partner API clients
2. Click "Create API client"
3. Grant permissions: **Manage apps** + **View financials**
4. Copy the access token

### 2. Install

```bash
git clone https://github.com/YourUsername/partner-agent.git
cd partner-agent
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:
```
SHOPIFY_ORG_ID=your_org_id          # From URL: partners.shopify.com/{org_id}
SHOPIFY_ACCESS_TOKEN=your_token     # From step 1
SHOPIFY_APP_IDS=gid://partners/App/1234567  # Comma-separated app GIDs
```

Find your app GIDs: Partners Dashboard → Apps → click app → ID is in the URL.

### 4. Register with Claude Code

```bash
claude mcp add partner-agent \
  /path/to/partner-agent/.venv/bin/python \
  /path/to/partner-agent/server.py
```

### 5. Use it

Start a Claude Code session and ask about your Shopify apps:

```
> Show me my Shopify apps
> What's my churn rate this month?
> Who uninstalled recently and why?
> Give me a full health check of my app business
```

## Analytics Formulas

All formulas match industry standards (HeyMantle, Baremetrics, ChartMogul):

| Metric | Formula |
|--------|---------|
| MRR | Sum of active monthly subscription charges (annual ÷ 12) |
| Net New MRR | New + Reactivation + Expansion - Contraction - Churn |
| Revenue Churn % | (Churned MRR + Contraction) / Starting MRR × 100 |
| Logo Churn % | Uninstalled merchants / Starting merchants × 100 |
| LTV | ARPU × Average lifespan |
| ARPU | Total net revenue / Active merchants |
| Retention | Revenue in month N / Revenue in month 0 (per cohort) |

## Requirements

- Python 3.12+
- Shopify Partner account with API access
- Claude Code (or any MCP-compatible client)

## Architecture

```
server.py            -> FastMCP entry point, 25 tool definitions
shopify_partner.py   → GraphQL client with pagination + rate limiting
queries.py           → GraphQL query constants
analytics.py         → Pure computation functions (no API calls)
```

The Shopify Partner API is **read-only** (no mutations). Rate limit: 4 requests/second.

## License

AGPL-3.0 -- see [LICENSE](LICENSE)
