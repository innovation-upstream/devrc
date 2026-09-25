# Hetzner Cloud API Notes

Source (OpenAPI 3.1.2 spec): https://docs.hetzner.cloud/cloud.spec.json  
Source (changelog): https://docs.hetzner.cloud/changelog

## Project ID from a token — no API endpoint exists

The Hetzner Cloud API has **no endpoint that exposes which project a token belongs to**. The API spec (https://docs.hetzner.cloud/cloud.spec.json) contains no `/tokens` tag or resource. Tokens are tied to a Project at creation time in the Hetzner Console (https://console.hetzner.com/ → Project → Security → API Tokens).

The API info states: *"A token is bound to a Project, to interact with the API of another Project you have to create a new token inside the Project."*

To identify which project a token is for, you must use the **Hetzner Console**. No response field, header, or meta trick exposes the project ID.

## Token scopes — read-only vs read/write

The error code table in the API spec documents:

| Status | Code | Description |
|--------|------|-------------|
| `401` | `token_readonly` | "The token is only allowed to perform GET requests." |

A read-only token attempting a write operation returns HTTP 401 with `"code": "token_readonly"`. A read/write token with a malformed request (e.g. `POST /ssh_keys` with an empty body) returns `422` with `"code": "invalid_input"`. This is **documented behavior**, not folklore.

So: `POST /ssh_keys` with an empty body → `401 token_readonly` (read-only token) or `422 invalid_input` (read/write token) is confirmed. The error codes table at line ~92 of the spec defines both.

## Pricing endpoint — field names

`GET /pricing` — https://api.hetzner.cloud/v1/pricing

Response structure (`pricing.server_types[]`):

```json
{
  "pricing": {
    "currency": "EUR",
    "vat_rate": "19.00",
    "server_types": [
      {
        "id": 104,
        "name": "cpx22",
        "prices": [
          {
            "location": "fsn1",
            "price_hourly":  { "net": "1.0000", "gross": "1.1900" },
            "price_monthly": { "net": "1.0000", "gross": "1.1900" },
            "included_traffic": 654321,
            "price_per_tb_traffic": {
              "net": "1.0000",
              "gross": "1.1900"
            }
          }
        ]
      }
    ],
    "primary_ips": [ … ],
    "image":        { "price_per_gb_month": … },
    "volume":       { "price_per_gb_month": … },
    "server_backup": { "percentage": "20.00" },
    "load_balancer_types": [ … ],
    "floating_ip_types": [ … ],
    "traffic":      [ … ]
  }
}
```

Key fields for server-type pricing per location:
- `location` — Location name string (e.g. `"fsn1"`, `"hel1"`, `"nbg1"`)
- `price_hourly.net` / `price_hourly.gross`
- `price_monthly.net` / `price_monthly.gross`
- `included_traffic` — Free traffic per month **in bytes** (int64)
- `price_per_tb_traffic.net` / `price_per_tb_traffic.gross` — per-TB overage price

Source: `tool_0d9927993001DboXJTgBvbxbiv:40914-40999` from the OpenAPI spec.

## Data Centers deprecation (2026-10-01)

**Changelog entry (2026-06-02):**

> "The API endpoints `GET /v1/datacenters` and `GET /v1/datacenters/{id}` are now deprecated and will be removed after 1 Oct. 2026. After this date, requests to these endpoints will return `HTTP 410 Gone`."

**OpenAPI spec confirmation** (both endpoints carry `"deprecated": true`):

- `GET /datacenters`: `"**Deprecated:** This endpoint is deprecated as of 01 June 2026 and will be removed after 01 October 2026."`
- `GET /datacenters/{id}`: same deprecation notice.

**Replacement**: Use the **Locations** endpoint (`GET /locations`) and the **Server Types** endpoint (`GET /server_types`). The `server_types.locations` array now provides availability and recommendation info per location, replacing what was previously in the datacenters response. The changelog (April 2026) notes that datacenter fields `server_types.supported`, `server_types.available`, `server_types.available_for_migration`, and `recommendation` are deprecated and accuracy is no longer guaranteed after 1 Oct 2026.

Source: https://docs.hetzner.cloud/changelog#2026-06-02-datacenters-deprecated (and OpenAPI spec at tags "Data Centers").