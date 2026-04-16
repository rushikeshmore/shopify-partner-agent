# Shopify Partner Agent

[![PyPI](https://img.shields.io/pypi/v/shopify-partner-agent.svg)](https://pypi.org/project/shopify-partner-agent/)
[![CI](https://github.com/rushikeshmore/shopify-partner-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/rushikeshmore/shopify-partner-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776ab.svg)](https://python.org)
[![MCP Tools: 25](https://img.shields.io/badge/MCP_Tools-25-96bf48.svg)](#tools)

Free, open-source MCP server that connects Claude to the Shopify Partner API. 25 tools for revenue analytics, churn analysis, retention cohorts, merchant health scoring, conversion funnels, revenue forecasting, and growth velocity.

Query your app business in natural language -- no dashboards, no monthly fees.

## Why This Exists

Shopify's own [AI Toolkit](https://github.com/Shopify/Shopify-AI-Toolkit) and official MCP servers ([Dev MCP](https://www.npmjs.com/package/@shopify/dev-mcp), Storefront MCP, Customer Account MCP, Checkout MCP) all target the **Admin API** and **Storefront API** -- store operations and shopping experiences. Every third-party Shopify MCP server on GitHub does the same.

**None of them connect to the Partner API.** If you build Shopify apps, your revenue metrics, churn data, and merchant analytics are locked behind the Partner Dashboard with no MCP-compatible way to query them.

Shopify Partner Agent is the only MCP server that gives AI tools access to Partner API analytics -- MRR, churn, retention cohorts, merchant health, and 20 more computed metrics that even the raw API doesn't provide.

Ask Claude things like:
- "What's my MRR and how has it changed this quarter?"
- "Who uninstalled my app this month and why?"
- "Show me retention cohorts for the last 6 months"
- "Which merchants are at high risk of churning?"
- "Give me a weekly business digest"
- "Compare all my apps side by side"

## Tools

### Core Data (5)
| Tool | What it does |
|------|-------------|
| `discover_apps` | Find all apps in your partner account (from config + transaction history) |
| `get_app_details` | App name, GID, and API key for a specific app |
| `get_app_events` | Install, uninstall, charge, credit events with date filtering |
| `get_transactions` | Revenue transactions filtered by app, type, and date range |
| `get_merchants` | Active, churned, or all merchants with install dates and uninstall reasons |

### Revenue Analytics (4)
| Tool | What it does |
|------|-------------|
| `get_revenue_summary` | MRR, ARR, total revenue, growth rate, ARPU, breakdown by app and currency |
| `get_mrr_movement` | New, expansion, contraction, churn, and reactivation MRR breakdown |
| `get_payout_report` | Gross revenue, Shopify fees, net payout with per-type breakdown |
| `get_plan_performance` | Subscribers, revenue, and avg revenue per pricing tier |

### Customer Analytics (4)
| Tool | What it does |
|------|-------------|
| `get_churn_analysis` | Revenue churn %, logo churn %, uninstall reason breakdown |
| `get_retention_cohorts` | Monthly revenue retention cohort table (values above 100% = expansion) |
| `get_customer_ltv` | LTV, ARPU, average lifespan, top and bottom 5 merchants by revenue |
| `get_churned_merchants` | Recent uninstalls with dates and reasons, sorted most recent first |

### Growth & Forecasting (6)
| Tool | What it does |
|------|-------------|
| `get_trial_funnel` | Install-to-paid conversion rate, timing analysis, converted/unconverted lists |
| `get_growth_velocity` | Week-over-week install/uninstall/revenue trends with acceleration signals |
| `get_install_patterns` | Best day of week and time of month for installs, monthly trend |
| `get_revenue_forecast` | Project MRR for next 3-6 months based on historical growth rate |
| `get_referral_revenue` | Shopify merchant referral revenue with period comparison |
| `get_credits_adjustments` | Refunds, credits, adjustments with giveback percentage |

### Merchant Intelligence (4)
| Tool | What it does |
|------|-------------|
| `get_churn_risk` | Score active merchants by churn risk (low/medium/high) with contributing factors |
| `get_merchant_health` | Composite health grade (A-F) based on tenure, revenue, stability, engagement |
| `get_merchant_lookup` | Full chronological timeline for a specific merchant (events + transactions) |
| `get_business_digest` | Weekly or monthly summary with highlights and period-over-period comparison |

### Multi-App (2)
| Tool | What it does |
|------|-------------|
| `get_revenue_anomalies` | Detect unusual revenue patterns using statistical deviation |
| `get_app_comparison` | Side-by-side metrics (revenue, installs, churn, ARPU) across all apps |

## Install

### 1. Get Partner API credentials

1. Go to [Partners Dashboard](https://partners.shopify.com) > Settings > Partner API clients
2. Click "Create API client"
3. Grant permissions: **Manage apps** + **View financials**
4. Copy the access token
5. Grab your app GIDs: Partners Dashboard > Apps > click app > ID is in the URL

### 2. Add to your MCP client

No clone, no venv -- `uvx` installs and runs in one step.

<details open>
<summary><b>Claude Desktop</b></summary>

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "shopify-partner-agent": {
      "command": "uvx",
      "args": ["shopify-partner-agent"],
      "env": {
        "SHOPIFY_ORG_ID": "your_org_id",
        "SHOPIFY_ACCESS_TOKEN": "your_token",
        "SHOPIFY_APP_IDS": "gid://partners/App/1234567"
      }
    }
  }
}
```
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add shopify-partner-agent uvx shopify-partner-agent \
  -e SHOPIFY_ORG_ID=your_org_id \
  -e SHOPIFY_ACCESS_TOKEN=your_token \
  -e SHOPIFY_APP_IDS=gid://partners/App/1234567
```
</details>

<details>
<summary><b>Cursor / Windsurf</b></summary>

```json
{
  "mcpServers": {
    "shopify-partner-agent": {
      "command": "uvx",
      "args": ["shopify-partner-agent"],
      "env": {
        "SHOPIFY_ORG_ID": "your_org_id",
        "SHOPIFY_ACCESS_TOKEN": "your_token",
        "SHOPIFY_APP_IDS": "gid://partners/App/1234567"
      }
    }
  }
}
```
</details>

<details>
<summary><b>pip install (alternative)</b></summary>

```bash
pip install shopify-partner-agent
```

Then point your MCP config at the installed command, or run `shopify-partner-agent` directly.
</details>

### 3. Start using it

```
> Show me my Shopify apps
> What's my churn rate this month?
> Who uninstalled recently and why?
> Give me a full health check of my app business
> Score my merchants by churn risk
```

## Analytics Formulas

All formulas match industry-standard SaaS metrics:

| Metric | Formula |
|--------|---------|
| MRR | Event-aware: subscription + usage MRR per active merchant (annual / 12, usage trailing 30d). Two-track status model: relationship (installed/uninstalled) + subscription (frozen/canceled/expired). Falls back to charge-based when events unavailable. |
| Net New MRR | New + Reactivation + Expansion - Contraction - Churn |
| Quick Ratio | (New + Expansion + Reactivation) / (Contraction + Churn) |
| Reactivation MRR | Revenue from previously-churned merchants who reinstalled |
| Revenue Churn % | (Churned MRR + Contraction) / Starting MRR x 100 |
| Logo Churn % | Uninstalled merchants / Starting merchants x 100 |
| LTV | ARPU x Average lifespan |
| ARPU | Total net revenue / Active merchants (excludes churned when events available) |
| Retention | Revenue in month N / Revenue in month 0 (per cohort) |
| Health Score | Tenure (0-25) + Revenue (0-25) + Stability (0-25) + Engagement (0-25) |
| Churn Risk | 5 heuristic signals: deactivations, cancellations, reinstalls, declining charges, inactivity |
| Growth Velocity | Week-over-week install/revenue trends, revenue grouped by currency |

## Requirements

- Python 3.11+
- Shopify Partner account with API access
- Any MCP-compatible client (Claude Code, Claude Desktop, Cursor, Windsurf)

## License

MIT -- see [LICENSE](LICENSE)
