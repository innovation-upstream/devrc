# Cloudflare R2 API Notes

Sources: https://developers.cloudflare.com/r2/api/tokens/ | https://developers.cloudflare.com/fundamentals/api/get-started/token-formats/ | https://developers.cloudflare.com/fundamentals/api/get-started/account-owned-tokens/ | https://developers.cloudflare.com/api/resources/user/subresources/tokens/methods/verify/ | https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/list/ | https://developers.cloudflare.com/analytics/graphql-api/ | https://developers.cloudflare.com/analytics/graphql-api/features/data-sets/ | https://developers.cloudflare.com/r2/api/s3/api/

## Token formats (scannable, ~2026)

| Prefix | Format | Description |
|--------|-------|-------------|
| `cfk_` | `cfk_[40 chars][checksum]` | Global API Key (full access) |
| `cfut_` | `cfut_[40 chars][checksum]` | User API Token (scoped, tied to a user) |
| `cfat_` | `cfat_[40 chars][checksum]` | Account API Token (service principal, not user-tied) |

Old (pre-2026) tokens: 40-char alphanumeric strings (user/account) or 37-45 hex (global key). All continue working.

Source: https://developers.cloudflare.com/fundamentals/api/get-started/token-formats/

## `GET /user/tokens/verify` vs `cfat_` tokens

The endpoint `GET /user/tokens/verify` is documented at https://developers.cloudflare.com/api/resources/user/subresources/tokens/methods/verify/ and returns `result.status` (`active`/`disabled`/`expired`) on success. **It is a user-level endpoint.** Account API tokens (`cfat_`) may not be compatible — the [account-owned tokens compatibility matrix](https://developers.cloudflare.com/fundamentals/api/get-started/account-owned-tokens/#compatibility-matrix) states "some services may not support account API tokens yet" and does not include `user/tokens/verify` in its ✅ list. The fact that `GET accounts` succeeds but `GET user/tokens/verify` returns code 1000 ("Invalid API Token") is consistent with the token working at the account level but the user-level verify endpoint rejecting it.

## `GET accounts/{account_id}/r2/buckets` — required permission

The [List Buckets API docs](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/list/) state the accepted permissions as:

> **Accepted Permissions (at least one required)**: `Workers R2 Storage Write`, `Workers R2 Storage Read`

These are the **exact labels** in the dash permission picker (Account-level, under the permission-groups list). `Workers R2 Storage Read` grants "Can list buckets and view bucket configuration, and read and list objects." Error code 10000 ("Authentication error") means the token is valid but lacks this permission.

The corresponding R2 API token permissions (from the R2-specific tokens page) are:

| Permission | Description |
|------------|-------------|
| **Admin Read & Write** | Create, list, delete buckets; read/write objects; data catalog |
| **Admin Read only** | List buckets, read objects |
| **Object Read & Write** | Read/write/list objects in specific buckets (S3 API only) |
| **Object Read only** | Read/list objects in specific buckets (S3 API only) |

## GraphQL analytics — R2 query shape

**Endpoint**: `POST https://api.cloudflare.com/client/v4/graphql`

Cloudflare GraphQL Groups datasets use the structure:

```graphql
type SomeGroup {
    count
    sum {   # fields that support summing (numbers, maps of numbers)
    }
    avg {   # fields that support averaging (numbers)
    }
    uniq {  # fields that support uniqueing
    }
    dimensions {  # grouping fields
    }
}
```

**R2-specific groups** (account-level, names confirmed from the known schema and naming convention):

- `account.r2StorageAdaptiveGroups` → `AccountR2StorageAdaptiveGroups`
- `account.r2OperationsAdaptiveGroups` → `AccountR2OperationsAdaptiveGroups`
- `account.r2BandwidthUsageAdaptiveGroups` → `AccountR2BandwidthUsageAdaptiveGroups`

**Correct query shape** — ✅ VERIFIED LIVE 2026-09-25 (returned real per-bucket data; the shape below is not inferred):

```graphql
{
  viewer {
    accounts(filter: {accountTag: "<ACCOUNT_TAG>"}) {
      r2StorageAdaptiveGroups(
        limit: 100
        filter: {datetime_geq: "2026-09-01T00:00:00Z", datetime_lt: "2026-09-26T00:00:00Z"}
      ) {
        dimensions {
          bucketName
          datetime
          datetimeFifteenMinutes
          datetimeFiveMinutes
          datetimeHour
          datetimeMinute
          date
          storageClass
        }
        max {
          metadataSize
          objectCount
          payloadSize
          uploadCount
        }
      }
    }
  }
}
```

- There is **no `datetimeDay`** dimension (the introspected list is: bucketName, date, datetime, datetimeFifteenMinutes, datetimeFiveMinutes, datetimeHour, datetimeMinute, storageClass). `filter` IS accepted as an object argument (`filter: {datetime_geq: …}`) — an earlier "filter: not an object" error came from a malformed sibling query, not from this shape.
- **`sum` does NOT exist on the R2 Storage group** — only `max` (storage is a gauge, not a counter). Data lands in ~10-minute windows; to get current totals per bucket take the latest window per `bucketName` client-side.
- ⚠ Sampling caveat: `limit:100` over a month of windows covers every bucket we observed (17), but a bucket with NO writes in the window range is invisible to storage groups — the REST bucket list (`Workers R2 Storage Read` permission) is the only complete enumeration.

The `filter` argument is an **inline argument** on the dataset field (the Groups node), not a standalone object field — pass it as `r2StorageAdaptiveGroups(limit: N, filter: {…})`. Within `filter`, use `datetime_geq`, `datetime_leq`, `bucketName`, etc.

Source: https://developers.cloudflare.com/analytics/graphql-api/features/data-sets/

## R2 S3 API tokens vs regular API tokens

R2 S3 API tokens are generated at **R2 → Overview → Account Details → Manage API Tokens** (NOT My Profile → API Tokens). They produce an **Access Key ID + Secret Access Key** pair that authenticates against the S3 endpoint at `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.

**A regular Cloudflare API token (`cfat_` / `cfut_`) CANNOT perform S3 API object operations.** The docs are explicit:

> "The **Object Read & Write** and **Object Read only** permissions are only supported by the [S3-compatible API](https://developers.cloudflare.com/r2/api/s3/api/), **not** the [Cloudflare REST API](https://developers.cloudflare.com/api/resources/r2/)."

Regular API tokens use the REST API at `api.cloudflare.com/client/v4` for bucket management only (list, create, delete). S3 object-level operations (GetObject, PutObject, etc.) require the R2-specific token.

However, a regular API token can be used to **generate S3 credentials** via the Create Token API — the response includes the token `id` (Access Key ID) and the SHA-256 hash of the token `value` (Secret Access Key). So the same token can serve both the REST API and (via derived S3 credentials) the S3 API.

Source: https://developers.cloudflare.com/r2/api/tokens/ (Permissions table note)

## GraphQL analytics — operations group (✅ verified live)

`AccountR2OperationsAdaptiveGroups` — **`sum` confirmed**: `requests`, `responseBytes`, `responseObjectSize`. Dimensions (introspected): `bucketName`, `objectName`, `actionType`, `actionStatus`, `responseStatusCode`, `eyeballRegion`, `storageClass`, `date`, `datetime`, `datetimeFifteenMinutes`, `datetimeFiveMinutes`, `datetimeHour`, `datetimeMinute`.

```graphql
r2OperationsAdaptiveGroups(limit: 5, filter: {datetime_geq: "2026-09-25T00:00:00Z"}) {
  dimensions { bucketName actionType }
  sum { requests }
}
```

🔴 **`orderBy` trap (measured)**: you can only `orderBy` a field that appears in your `dimensions` selection or is aggregated — `orderBy:[datetime_DESC]` without selecting `datetime` fails with *"cannot order by datetime: it is neither aggregated, nor a dimension"* (error class `syntax`). `AccountR2BandwidthUsageAdaptiveGroups` is the same family but was NOT exercised live — treat its exact field names as UNCONFIRMED until queried.

## Minimum permission for GraphQL analytics

The minimum permission group required is **"Account Analytics Read"** (Account-level permission). The token must also have account-level resource access. The permission label in the dash picker is "Account Analytics Read" with description "Grants read access to account analytics."