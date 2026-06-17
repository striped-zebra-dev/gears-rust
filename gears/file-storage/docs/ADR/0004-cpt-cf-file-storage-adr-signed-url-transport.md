---
status: accepted
date: 2026-06-17
---

# ADR-0004: Signed-URL Field & Signature Transport

<!-- toc -->

- [Context and Problem Statement](#context-and-problem-statement)
- [Decision Drivers](#decision-drivers)
- [Considered Options](#considered-options)
- [Decision Outcome](#decision-outcome)
  - [Consequences](#consequences)
  - [Confirmation](#confirmation)
- [Pros and Cons of the Options](#pros-and-cons-of-the-options)
  - [A. Discrete query parameters (chosen)](#a-discrete-query-parameters-chosen)
  - [B. Single opaque query token](#b-single-opaque-query-token)
  - [C. Discrete HTTP headers](#c-discrete-http-headers)
  - [D. Single opaque HTTP-header token](#d-single-opaque-http-header-token)
- [More Information](#more-information)
- [Option Comparison](#option-comparison)
- [Traceability](#traceability)

<!-- /toc -->

**ID**: `cpt-cf-file-storage-adr-signed-url-transport`

## Context and Problem Statement

FileStorage authorizes every content operation with an Ed25519 **signed URL** verified by the sidecar
(`cpt-cf-file-storage-adr-sidecar-data-plane`, `cpt-cf-file-storage-design-signed-urls`). A signed URL is a set of
**fields** — algorithm, expiry, operation, the resource (`file_id` in the path; `content_id` / `version_id` pin),
constraints (`ip`, token-claim predicates, upload size/hash, P2 rate/conns), baked response headers — plus the
**signature** over their canonical form. (Backend id and a key id are deliberately not in the URL — see ADR-0003 /
§4.5: the sidecar resolves the backend from the version row, and P1 uses a single key.)

Where those fields and the signature physically live on the wire is a separate decision from *what* they contain.
Two axes: **location** (URL query string vs HTTP headers) and **shape** (discrete named entries vs a single opaque
token blob). This ADR fixes that transport so the canonicalization, the SDK, the sidecar verifier, and any CDN/proxy
in front of the sidecar all agree.

## Decision Drivers

* Works as a **bare, shareable URL** — a download link must be usable directly by a browser, an `<img>`/`<video>` tag,
  `curl <url>`, or a media player issuing `Range` requests, with **no out-of-band headers** the embedder cannot set
* Canonical / interoperable — reusing a battle-tested, widely-understood scheme lowers implementation and review risk
  and matches operator/tooling expectations
* Cache-friendliness with the CDN offload (`cpt-cf-file-storage-nfr-bandwidth`) — how the transport interacts with
  cache keys when a URL's signature is re-issued
* Debuggability / observability — fields visible and greppable in logs and traces vs an opaque blob
* Verifiability without parsing ceremony — the sidecar must reconstruct the canonical string deterministically
* Secret hygiene — the signature is a short-lived, constrained credential; where it appears (URL logs, Referer,
  browser history) matters

## Considered Options

* **A. Discrete query parameters** — each field is its own `X-FS-*` query parameter; signature in `X-FS-Signature` (S3 SigV4 style)
* **B. Single opaque query token** — all fields + signature packed into one base64 blob in a single query parameter
* **C. Discrete HTTP headers** — each field is its own request header; signature in a header
* **D. Single opaque HTTP-header token** — all fields + signature packed into one header value (bearer-token shape)

## Decision Outcome

Chosen option: **"A. Discrete query parameters"**. The signed URL carries each field as an `X-FS-*` query parameter and
the signature as `X-FS-Signature`, exactly as specified in `cpt-cf-file-storage-design-signed-urls`. This is the
**canonical AWS S3 SigV4 query-signing scheme**, in production use for ~20 years.

The decisive driver is the **bare-URL property**: a FileStorage download URL must be directly usable as a plain URL —
pasted into a browser, set as `<img src>` / `<video src>`, fetched by `curl`, or seeked by a media player via `Range`
— **without the caller attaching any headers**. Only the query-string form gives that; any header-based form
(C/D) cannot be embedded as a bare URL and forces every consumer to attach headers, which rules it out for the primary
use case. Discrete fields (over a token blob) keep the scheme canonical, debuggable, and verifiable without an extra
decode step, and let a CDN reason about individual parameters when needed.

**Header-based discrete fields (C) are the acknowledged runner-up**, with one genuine advantage: the URL path+query is
**stable across signature re-issue** (re-presigning only changes headers, not the URL), which keeps a CDN cache key
clean and avoids cache fragmentation when a link is re-signed. We accept the query-form caching trade-off instead
(mitigations below) because the bare-URL property outweighs it. C may be offered later as an **optional alternate
transport** for header-capable, cache-sensitive callers (SDK/server-to-server), but P1 commits to A as the one
canonical form.

**Token forms (B and D) are rejected outright** — in any location they add an opaque blob that must be decoded before
verification, destroy log/trace debuggability, and are non-standard, while providing **no benefit**: the signature
already makes the payload tamper-evident, so opacity buys nothing.

### Consequences

* `cpt-cf-file-storage-design-signed-urls` is locked to the discrete `X-FS-*` query-parameter form; the canonical
  string remains `method + host + path + sorted(X-FS-* except X-FS-Signature)`. No header-based signing path in P1.
* **Caching trade-off (accepted).** Re-presigning a download (new `exp`) yields a new query string and therefore a new
  CDN cache key. Mitigations: (1) within a URL's lifetime the query is stable, so repeat reads hit cache; (2) the
  recommended `max_url_ttl` of 7 days (`cpt-cf-file-storage-fr-signed-urls`) keeps URLs — and their cached
  representations — long-lived; (3) deployments may configure CDN **cache-key normalization** to drop the signing
  parameters from the key while still forwarding them to the sidecar for verification; (4) the content-only ETag lets
  conditional revalidation succeed across re-signs.
* **Secret-in-URL hygiene (accepted).** The signature appears in the URL and may land in access logs, `Referer`, and
  browser history — the same exposure as S3 presigned URLs. It is mitigated by short, capped `exp`, the
  fully-constrained (ip/token/op/size/hash) scope of each URL, and the fact that it is not a reusable bearer credential
  beyond `exp`. Operators SHOULD avoid logging full query strings for the sidecar domain.
* The SDK builds and parses only the query form; the sidecar verifier reads `X-FS-*` query params only.
* Leaves the door open to add transport **C** later as an opt-in without changing field semantics (only the
  canonicalization source would differ), should caching pressure justify it.

### Confirmation

* Code review confirming the SDK emits and the sidecar verifies the `X-FS-*` **query-parameter** form, with no
  header-based signing path.
* Integration tests confirming a signed download URL is consumable as a **bare URL** (browser/`curl`/`<img>`) and that
  `Range` requests against it succeed without extra headers.
* Integration tests confirming re-presigning changes only the query (not path) and that CDN cache-key normalization (where
  configured) serves repeat reads without re-transiting the sidecar.

## Pros and Cons of the Options

### A. Discrete query parameters (chosen)

* Good, because the result is a **bare, shareable URL** — embeddable in HTML, openable in a browser, fetchable by
  `curl`, seekable by media players, with no headers required
* Good, because it is the **canonical S3 SigV4 scheme** — ~20 years in production, universally understood, well-tooled
* Good, because discrete fields are **debuggable** (visible/greppable in logs) and verified without an extra decode step
* Bad, because re-presigning changes the URL → CDN cache-key churn unless normalized
* Bad, because the signature appears in URLs (logs/Referer/history) — accepted, same as S3

### B. Single opaque query token

* Good, because it is compact and hides internal structure
* Bad, because it must be base64-decoded and parsed before verification — extra ceremony, easy to get wrong
* Bad, because it destroys log/trace debuggability and is non-standard
* Bad, because opacity buys nothing — the signature already makes the payload tamper-evident
* Bad, because it still carries the secret-in-URL exposure of query form without query form's interop benefits

### C. Discrete HTTP headers

* Good, because the URL (path+query) is **stable across re-issue** → clean CDN cache key, no fragmentation
* Good, because the signature stays out of the URL (not in access logs / Referer / history)
* Bad, because it is **not a bare URL** — cannot be embedded as `<img src>` or opened in a browser; every consumer must
  attach headers, which fails the primary download use case
* Bad, because it diverges from the canonical query-signing mental model
* Neutral — a reasonable **optional** transport for header-capable SDK/S2S callers; deferred

### D. Single opaque HTTP-header token

* Good, because URL is stable and the secret is out of the URL
* Bad, because it combines the worst of B and C: opaque blob (no debuggability, decode ceremony, no benefit) **and**
  not a bare URL (headers required)
* Bad, because a bearer-token-in-a-header shape invites treating it as a reusable credential, which it is not

## More Information

This aligns FileStorage's wire transport with the AWS S3 SigV4 **query** (`X-Amz-*`) presigning convention rather than
the SigV4 **header** (`Authorization`) convention. S3 itself offers both for the same reasons captured here: the query
form exists precisely to produce a self-contained, shareable URL; the header form exists for programmatic callers that
prefer a stable URL and headers. We adopt the query form as the single P1 transport and keep the header form as a
possible future opt-in.

## Option Comparison

✓ = yes / good · ✗ = no / bad · ~ = partial

| Aspect | A · query fields | B · query token | C · headers | D · header token |
|---|---|---|---|---|
| Bare, shareable URL (browser / `<img>` / `curl` / Range) | ✓ | ✓ | ✗ | ✗ |
| Canonical (S3 SigV4) / well-tooled | ✓ | ✗ | ~ | ✗ |
| Stable URL on re-sign → clean CDN cache key | ✗ | ✗ | ✓ | ✓ |
| Discrete, debuggable fields (logs/traces) | ✓ | ✗ | ✓ | ✗ |
| Verify without a decode step | ✓ | ✗ | ✓ | ✗ |
| Signature kept out of URL (logs / Referer / history) | ✗ | ✗ | ✓ | ✓ |
| **Verdict** | **Chosen** | Rejected | Deferred (opt-in) | Rejected |

## Traceability

- **PRD**: [PRD.md](../PRD.md)
- **DESIGN**: [DESIGN.md](../DESIGN.md)
- **Related**: [ADR-0003: Split the Data Plane into a Signed-URL Sidecar](./0003-cpt-cf-file-storage-adr-sidecar-data-plane.md)

This decision directly addresses the following requirements or design elements:

* `cpt-cf-file-storage-fr-signed-urls` — fixes the on-the-wire transport of the signed-URL fields and signature
* `cpt-cf-file-storage-design-signed-urls` — locks the canonical form to discrete `X-FS-*` query parameters
* `cpt-cf-file-storage-principle-signed-urls` — the control-minted, sidecar-verified URL is a bare, query-signed URL
* `cpt-cf-file-storage-nfr-bandwidth` — documents the CDN cache-key interaction and its mitigations
