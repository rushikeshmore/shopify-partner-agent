"""
GraphQL query constants for the Shopify Partner API.

All queries are module-level constants. Named: QUERY_{RESOURCE}_{ACTION}.
Keeping these separate from the client keeps both files readable —
GraphQL strings are verbose (30-50 lines each).
"""

# --- App Queries ---

QUERY_APP_DETAILS = """
query GetApp($appId: ID!) {
  app(id: $appId) {
    id
    name
    apiKey
  }
}
"""

# --- Transaction Queries ---

QUERY_TRANSACTIONS = """
query GetTransactions(
  $first: Int!
  $after: String
  $appId: ID
  $createdAtMin: DateTime
  $createdAtMax: DateTime
  $types: [TransactionType!]
) {
  transactions(
    first: $first
    after: $after
    appId: $appId
    createdAtMin: $createdAtMin
    createdAtMax: $createdAtMax
    types: $types
  ) {
    edges {
      node {
        __typename
        id
        createdAt
        ... on AppSubscriptionSale {
          chargeId
          billingInterval
          app { id name }
          shop { id name myshopifyDomain }
          netAmount { amount currencyCode }
          grossAmount { amount currencyCode }
          shopifyFee { amount currencyCode }
        }
        ... on AppUsageSale {
          chargeId
          app { id name }
          shop { id name myshopifyDomain }
          netAmount { amount currencyCode }
          grossAmount { amount currencyCode }
          shopifyFee { amount currencyCode }
        }
        ... on AppOneTimeSale {
          chargeId
          app { id name }
          shop { id name myshopifyDomain }
          netAmount { amount currencyCode }
          grossAmount { amount currencyCode }
          shopifyFee { amount currencyCode }
        }
        ... on AppSaleAdjustment {
          app { id name }
          shop { id name myshopifyDomain }
          netAmount { amount currencyCode }
        }
        ... on AppSaleCredit {
          app { id name }
          shop { id name myshopifyDomain }
          netAmount { amount currencyCode }
        }
        ... on ReferralTransaction {
          shop { id name myshopifyDomain }
        }
        ... on ServiceSale {
          shop { id name myshopifyDomain }
          netAmount { amount currencyCode }
          grossAmount { amount currencyCode }
          shopifyFee { amount currencyCode }
        }
      }
      cursor
    }
    pageInfo {
      hasNextPage
    }
  }
}
"""

# --- App Event Queries ---

QUERY_APP_EVENTS = """
query GetAppEvents(
  $appId: ID!
  $first: Int!
  $after: String
  $types: [AppEventTypes!]
  $occurredAtMin: DateTime
  $occurredAtMax: DateTime
) {
  app(id: $appId) {
    id
    name
    events(
      first: $first
      after: $after
      types: $types
      occurredAtMin: $occurredAtMin
      occurredAtMax: $occurredAtMax
    ) {
      edges {
        node {
          type
          occurredAt
          shop { id name myshopifyDomain }
          ... on RelationshipUninstalled {
            reason
            description
          }
          ... on SubscriptionChargeAccepted {
            charge {
              id
              amount { amount currencyCode }
            }
          }
        }
        cursor
      }
      pageInfo {
        hasNextPage
      }
    }
  }
}
"""
