# FileStorage — HTTP API (P1 + declared P2)


<!-- toc -->

- [Two planes](#two-planes)
- [P1 — Control plane (`/api/file-storage/v1`)](#p1--control-plane-apifile-storagev1)
- [P1 — Sidecar (signed-URL authorized)](#p1--sidecar-signed-url-authorized)
- [P2 — Multipart upload (declared, not implemented in P1)](#p2--multipart-upload-declared-not-implemented-in-p1)
- [Upload, bind, and the conflict retry](#upload-bind-and-the-conflict-retry)
- [Signed URLs](#signed-urls)
- [Conditional headers](#conditional-headers)
- [Range support](#range-support)
- [Response headers (download + HEAD, on the sidecar)](#response-headers-download--head-on-the-sidecar)
- [Status code summary](#status-code-summary)

<!-- /toc -->

FileStorage is split into a **control plane** (metadata + signed-URL issuance; never carries content) and a **sidecar**
data plane (the only thing that moves bytes, addressed only by control-issued signed URLs). See
[ADR-0003](./ADR/0003-cpt-cf-file-storage-adr-sidecar-data-plane.md) and [DESIGN.md](./DESIGN.md). Every content
operation is at least two requests: a control request to obtain a signed URL, then a data request against the sidecar.

## Two planes

- **Control plane** base URL: `/api/file-storage/v1` — JWT enforced by API Gateway; standard owner/tenant
  authorization applies. **JSON only — no request or response body ever contains file content.**
- **Sidecar**: its own domain; endpoints are reachable only with a valid control-issued **signed URL** (and, when the
  URL carries a token-claim predicate, a valid platform JWT). The signed URL always points at the sidecar, never at a
  backend.

Encoding conventions:
- Control bodies are `application/json`. The sidecar `PUT` body is the **raw** object bytes (no `multipart/form-data`).
- All error responses follow RFC 7807 (`application/problem+json`).
- `file_id` and `version_id` are UUIDs. A backend object lives at `/{file_id}/{version_id}` and is immutable.

## P1 — Control plane (`/api/file-storage/v1`)

```text
1.  POST   /files                          create file + return a signed upload URL (JSON: metadata; gts_file_type required)
2.  POST   /files/{id}/versions            presign a new-version upload (JSON, optional If-Match) → signed upload URL
3.  POST   /files/{id}/bind                bind/rebind content_id := version_id                          — If-Match
4.  GET    /files/{id}/download-url         issue a signed download URL (pins current content_id, or ?version_id=)
5.  PATCH  /files/{id}                      update custom metadata (JSON Merge Patch)        — If-Match, If-Match-Metadata?
6.  GET    /files/{id}                      file metadata (JSON)                                          — If-None-Match
7.  HEAD   /files/{id}                      file metadata headers                                         — If-None-Match
8.  DELETE /files/{id}                      delete file + all versions                                    — If-Match
9.  GET    /files                           list files (filters, paginated; JSON array of metadata)
10. GET    /files/{id}/versions             list versions (version_id, size, hash, created_at, is_current)
11. GET    /storages                        list storages + capabilities inline
12. GET    /storages/{storage_id}           one storage + capabilities
```

Notes:
- `POST /files` and `POST /files/{id}/versions` return `{ file_id, version_id, upload_url }`. The client `PUT`s the
  bytes to `upload_url` on the sidecar; the sidecar pre-registers the `pending` version, streams to the backend, and
  **auto-binds** it. The client may instead bind explicitly via `POST /files/{id}/bind` (and must, to recover from a
  bind `412` — see below).
- `GET /files/{id}/download-url` returns `{ download_url, etag, metadata }`. By default it pins the current
  `content_id`; `?version_id=<v>` pins a specific version.
- Restoring a prior version is `POST /files/{id}/bind` with that `version_id` (a pointer swap, no re-upload).

## P1 — Sidecar (signed-URL authorized)

```text
S1. PUT    <signed upload url>             upload the new version's bytes (raw body)               — If-Match (relayed)
S2. GET    <signed download url>           download content                                        — If-None-Match, Range
S3. HEAD   <signed download url>           content headers (full-file)                             — If-None-Match
```

The sidecar verifies the Ed25519 signature and the URL constraints (and a platform JWT when a token-claim predicate is
present) before serving. On `PUT` it pre-registers + binds against the control plane on the user's behalf.

## P2 — Multipart upload (declared, not implemented in P1)

Multipart is **server-authoritative**: the client sends desired parameters and the control plane returns the exact
parts plan (sizes/offsets) with **one signed URL per part** pointing at the sidecar.

```text
P2-1. POST /files/{id}/multipart            initiate (JSON: size, preferred part size, concurrency); returns the parts plan + per-part signed URLs
P2-2. PUT  <signed part url>                upload one part to the sidecar (raw body)
P2-3. POST /files/{id}/multipart/{upload_id}/complete   finalize (combine BLAKE3 subtree hashes → root); binds the version  — If-Match
P2-4. DELETE /files/{id}/multipart/{upload_id}          abort; parts discarded
P2-5. GET  /files/{id}/multipart/{upload_id}            list uploaded parts (introspection)
```

For a `multipart_native` backend the sidecar drives the backend multipart API; otherwise it offset-writes each part
into the single new-version object. Per-part BLAKE3 subtree hashes are persisted in `multipart_upload_parts.part_hash`
and combined into the root at `complete`. Detailed envelope/error shapes are owned by the P2 FEATURE.

## Upload, bind, and the conflict retry

Content is an immutable blob per version; a file's live content is the `content_id` pointer, swapped under optimistic
CAS. The flow:

1. **Control**: `POST /files` (or `POST /files/{id}/versions`) → `{ file_id, version_id, upload_url }`.
2. **Data**: `PUT upload_url` to the sidecar with `If-Match: "<current content ETag>"`. The sidecar pre-registers the
   `pending` version (checking `If-Match` as an early fail — if the file already moved on, it errors **before** the
   bytes are uploaded), streams to the backend computing size + SHA-256, then **binds** `content_id := version_id`
   under `If-Match`.
3. On a **bind conflict** the sidecar returns `412` **and the `version_id`**; the client re-reads the current ETag and
   replays `POST /files/{id}/bind` with that `version_id` and the fresh `If-Match` — **no byte re-upload**, because the
   version already exists.

`If-Match` is therefore checked twice (opportunistically at pre-register, authoritatively at bind). Backend content is
never mutated in place; a replacement is always a new version + a pointer swap.

## Signed URLs

- **Ed25519, stateless.** The control plane signs with the private key (sole minter); the sidecar verifies with the
  public key. No DB lookup to verify. No per-URL revocation — emergency revocation is the platform auth module's token
  revocation. P1 uses one static keypair (no rotation; keyset + rotation is P2).
- **Parameters** (query, `X-FS-*`): `X-FS-Algorithm=Ed25519`, `X-FS-Expires=<unix>` (exp), `X-FS-Op`, the version pin
  `X-FS-ContentId` (download; `X-FS-VersionId` for a version-specific URL), constraints (`X-FS-Ip`, `X-FS-Tok-<claim>`,
  and upload-only `X-FS-MaxSize`/`X-FS-ExactSize`, `X-FS-ExpectedHash`; P2: `X-FS-MaxRate`, `X-FS-MaxConns`), baked
  response headers (`X-FS-Rh-<name>`), and `X-FS-Signature`. `file_id` is the URL **path**, not a param; `backend_id`,
  path, and size are **not** in the URL — the sidecar resolves them from the version row. P1 carries no key id (single
  static key; `X-FS-KeyId` returns in P2 with rotation).
- **Canonicalization**: signature covers `method` + `host` + `path` + every `X-FS-*` except `X-FS-Signature`, sorted
  and percent-encoded. `host` is signed (no cross-sidecar replay). The **operation is signed** both as `method` and as
  `X-FS-Op`, and the sidecar checks they match — a download URL cannot upload, or vice versa. Signing all params means
  a client cannot add, remove, or weaken a constraint.
- **Not signed**: `Range`, conditional headers, and the `PUT` body — so one signed download URL serves many ranges, and
  `PUT` body integrity is enforced by the size/hash constraints during the stream and by the hash at bind.
- **Constraints (AND-combined; all optional except `exp`)**:
  | Constraint | Param(s) | Req. | Phase | Applies | Violation |
  |---|---|---|---|---|---|
  | expiry | `X-FS-Expires` | yes | P1 | all | `403` |
  | client address | `X-FS-Ip` | no | P1 | all | `403` |
  | token claim | `X-FS-Tok-<claim>` | no | P1 | all (needs JWT) | `403` |
  | operation | `X-FS-Op` | yes | P1 | all | `403` |
  | max size | `X-FS-MaxSize` | no | P1 | upload | `413` |
  | exact size | `X-FS-ExactSize` | no | P1 | upload | `413`/`400` |
  | expected hash | `X-FS-ExpectedHash` = `<alg>:<hex>` | no | P1 | upload | `422` |
  | max rate | `X-FS-MaxRate` | no | P2 | up/down | throttle |
  | max connections | `X-FS-MaxConns` | no | P2 | up/down | `429` |
- **`exp` is mandatory and capped** by config `max_url_ttl` (recommended **7 days**), enforced by the control plane at
  signing (no `iat` in the URL; the sidecar checks `now ≤ exp`). A token-claim predicate requires a valid platform
  JWT, which the sidecar validates and matches. "Available to everyone for 5 minutes" = only `exp`, no token.
- **`X-FS-MaxSize` and `X-FS-ExactSize` are mutually exclusive** (both → `400` at presign / `403` at the sidecar).
- **`X-FS-ExpectedHash`** `<alg>` must be in the backend allow-list (P1: `SHA-256`); lowercase hex digest; baked by
  the control plane (may carry a client-supplied value from the presign request).
- **`X-FS-MaxRate` / `X-FS-MaxConns` are P2** (declared from P1; enforcement P2). Scoped to one `(file_id, op)`;
  cross-instance coordination across the sidecar fleet is an open P2 design point.
- **Baked response headers**: the sidecar echoes the `X-FS-Rh-<name>` set verbatim on the served response (e.g.
  `Content-Disposition`, `Content-Type` override, `Cache-Control`) — no control round-trip.

## Conditional headers

- `If-Match`: required on **bind** (`POST /files/{id}/bind`, and relayed on the sidecar `PUT` for the embedded
  pre-register/bind) and on `DELETE`. Mismatch → `412 Precondition Failed`.
- `If-Match-Metadata: <u64>`: **optional** on metadata-only `PATCH`; matched against the current `meta_version` (the
  value published as `X-FS-Metadata-Revision`). Mismatch → `412`. Absent → last-write-wins (back-compatible default);
  clients keeping meaningful state in custom metadata opt in.
- `If-None-Match`: optional on `GET`/`HEAD` (control metadata and sidecar download); match → `304 Not Modified`.
- ETag is opaque, derived from `(file_id, content_id)`, content-only, and explicitly **not** equal to the content
  hash. It changes exactly when content is (re)bound; a metadata-only `PATCH` does not change it. The content hash is
  exposed separately as `X-FS-Hash-Algorithm` + `X-FS-Hash-Value` (P1: SHA-256, per ADR-0002).

## Range support

Served by the **sidecar**.

- `GET <signed url>` accepts `Range: bytes=<start>-<end>`, `bytes=<start>-`, and `bytes=-<suffix-length>`. A
  well-formed, satisfiable range returns `206 Partial Content` with `Content-Range: bytes <s>-<e>/<n>`. A well-formed
  but **unsatisfiable** range (e.g. `start ≥ size`) returns `416` with `Content-Range: bytes */<n>`.
- A syntactically invalid / unparseable `Range` is **ignored** (RFC 7233 §3.1): `200 OK` with the full body.
- Because `Range` is not part of the signature, **one signed download URL serves many ranges** (random access). Every
  download response includes `Accept-Ranges: bytes`. `HEAD` ignores `Range` and returns full-file headers.
- Multi-range requests are parsed but P1 returns `200 OK` with the full body (no `Content-Range`); `multipart/byteranges`
  may be added later as a backward-compatible upgrade.

## Response headers (download + HEAD, on the sidecar)

```text
ETag: "<opaque>"                                       # (file_id, content_id)-derived
Content-Type: <mime>
Content-Length: <bytes>             # full file on HEAD/200; range bytes on 206
Content-Range: bytes <s>-<e>/<n>    # only on 206
Accept-Ranges: bytes
Last-Modified: <RFC 7231 date>
X-FS-File-Id: <uuid>
X-FS-Version-Id: <uuid>                                # the version being served (current content_id, or pinned version)
X-FS-GTS-File-Type: gts.cf.fstorage.file.type.v1~...
X-FS-Hash-Algorithm: SHA-256                           # of content
X-FS-Hash-Value: <hex>                                 # of content
X-FS-Metadata-Revision: <u64>                          # meta_version; for If-Match-Metadata
X-FS-Owner-Kind: user|app
X-FS-Owner-Id: <uuid>
X-FS-Created-At: <ISO 8601>
<baked X-FS-Rh-* headers echoed verbatim>              # e.g. Content-Disposition, Cache-Control
X-FS-Meta-<key>: <value>                               # one header per custom metadata key
```

## Status code summary

- `200 OK` — successful control read, metadata `PATCH` with change, bind, presign, or sidecar full download.
- `201 Created` — successful `POST /files` (file created; body carries the upload URL).
- `204 No Content` — successful `DELETE`. The metadata rows (file + all versions) are removed before the best-effort
  backend deletes; re-`DELETE` of an already-deleted `file_id` returns `404` (idempotent).
- `206 Partial Content` — successful range read (sidecar).
- `304 Not Modified` — `If-None-Match` matched the current ETag.
- `400 Bad Request` — malformed request (invalid JSON, missing required fields); an `X-FS-ExactSize` upload whose
  final length is short; or a malformed signed URL minted at presign (e.g. both `X-FS-MaxSize` and `X-FS-ExactSize`).
- `403 Forbidden` — authorization denied (control), or signed-URL verification failed at the sidecar: bad signature,
  expired (`now > exp`), `ip` mismatch, method ≠ `X-FS-Op`, missing/invalid token or unmatched token-claim predicate,
  or a malformed (mutually-exclusive) constraint set. (The `max_url_ttl` cap is enforced at signing, not re-checked here.)
- `404 Not Found` — file or version does not exist.
- `409 Conflict` — multipart state conflicts (e.g., complete on an aborted upload) (P2).
- `412 Precondition Failed` — `If-Match` (content ETag) mismatch on bind/delete, or `If-Match-Metadata` mismatch
  against the current `meta_version`. On a bind `412` the response carries the uploaded `version_id` for rebind.
- `413 Payload Too Large` — upload exceeds the signed `X-FS-MaxSize` / `X-FS-ExactSize` cap (sidecar; aborted mid-stream).
- `415 Unsupported Media Type` — declared mime does not match magic-bytes detection (sidecar, on upload).
- `416 Range Not Satisfiable` — a well-formed `Range` that cannot be satisfied against the size (sidecar). An
  unparseable `Range` is **not** a `416` — it is ignored and the full body is served with `200`.
- `422 Unprocessable Entity` — semantic validation failure (e.g., invalid GTS file type format), or an upload whose
  content does not match the signed `X-FS-ExpectedHash` (sidecar; not bound).
- `429 Too Many Requests` — (P2) the signed `X-FS-MaxConns` for this `(file_id, op)` is exceeded.
- `507 Insufficient Storage` — backend or quota limit exceeded.
