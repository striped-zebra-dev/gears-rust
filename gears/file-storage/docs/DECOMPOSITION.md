Created:  2026-07-02 by Constructor Tech
Updated:  2026-07-02 by Constructor Tech

# Decomposition: File Storage

**Overall implementation status:**
- [ ] `p1` - **ID**: `cpt-cf-file-storage-status-overall`



<!-- toc -->

- [1. Overview](#1-overview)
- [2. Entries](#2-entries)
  - [2.1 Multipart Upload Coordinator - HIGH](#21-multipart-upload-coordinator---high)
  - [2.2 Content-Hash Modes - MEDIUM](#22-content-hash-modes---medium)
- [3. Feature Dependencies](#3-feature-dependencies)

<!-- /toc -->

## 1. Overview

This document originally decomposed the P2 release cycle into a single feature — the server-authoritative multipart
upload path — and that remains the only **shipped** entry with its own FEATURE artifact
([features/multipart-coordinator.md](features/multipart-coordinator.md)). **It understates what actually shipped in
P2.** Beyond multipart, the P2 branch also delivered: the **policy engine** (allowed-types / size / custom-metadata
limits at tenant and user scope), **retention rules + a background cleanup sweep** (whole-file retention pruning and
orphan reconciliation), an **audit outbox** (transactional write-operation audit trail), an **events outbox** (file
lifecycle events, not yet drained to the platform EventBroker — Tier 4 item 4.1 in the P2 remediation plan),
**ownership transfer**, and **backend migration**. None of these have their own FEATURE artifact under
`docs/features/`; their behavior is documented only in code comments, `docs/api.md`, and
[README.md](../README.md)'s "Implementation status" section. A second entry, §2.2, decomposes a **proposed, not yet
implemented** P2+ design — content-hash modes — which does have its own FEATURE artifact
([features/content-hash-modes.md](features/content-hash-modes.md)) even though no code for it exists yet.

**Decomposition Strategy**:

- Only the multipart upload lifecycle (initiate, upload-part via sidecar, complete, abort, and introspect/resume) has
  a dedicated, **shipped** FEATURE decomposition entry (§2.1) and artifact, control-plane/sidecar split per ADR-0003
  and ADR-0004.
- The content-hash-modes decision (§2.2) is a **proposed** FEATURE decomposition entry and artifact — formalized in
  ADR-0006 (`status: proposed`) — covering the two-mode SHA-256 hashing design (whole-object for non-multipart,
  offset-manifest composite for multipart); it is decomposed here ahead of implementation so its ID is a real
  DECOMPOSITION entry, not merely prose.
- The policy engine, retention-cleanup, audit-trail, ownership-transfer, and backend-migration subsystems are real,
  shipped P2 scope that this document does **not** yet decompose into their own entries or FEATURE artifacts. Given
  the compliance weight of at least the audit-trail and ownership-transfer requirements
  (`cpt-cf-file-storage-fr-audit-trail`, `cpt-cf-file-storage-fr-ownership-transfer`), authoring proper FEATURE docs
  for all five — matching `features/multipart-coordinator.md`'s structure (flows, acceptance criteria, `p1`/`p2`
  tags) — is a **recommended follow-up**, tracked as P2 remediation plan item 3.6. This document takes the smaller,
  immediate fix instead: acknowledging the full P2 scope here rather than leaving the "one feature" framing
  uncorrected.
- The multipart feature depends on the P1 upload and versioning foundation (single-shot upload, file_versions table,
  signed-URL infrastructure) already shipped in P1; those P1 capabilities are not re-decomposed here. The
  content-hash-modes feature additionally depends on the multipart feature's part-hash/offset plumbing (§3).
- No shared components or DB tables are introduced by the multipart feature beyond the multipart_uploads and
  multipart_upload_parts tables it owns; the other P2 subsystems listed above have their own tables (see
  `docs/DESIGN.md` §3.7 and the gear's migrations) not enumerated in this single-feature-scoped document. The
  content-hash-modes feature proposes one new table, `version_hash_manifest` (not yet migrated).


## 2. Entries

### 2.1 [Multipart Upload Coordinator](features/multipart-coordinator.md) - HIGH

- [ ] `p2` - **ID**: `cpt-cf-file-storage-feature-multipart-coordinator`

- **Type**: Core
- **Phases**: Single-phase implementation

- **Purpose**: Provide a safe, resumable, server-controlled multipart upload path. The client declares total size and a preferred part size; the control plane computes the exact parts plan and returns one signed sidecar URL per part. The sidecar enforces the per-part size claim before writing. The control plane assembles and hashes the parts at complete and finalizes the new file version; binding it as the file's current content remains a separate, client-issued request.

- **Depends On**: P1 file upload and versioning foundation (single-shot upload, file_versions table, signed-URL infrastructure -- codec-equivalent Ed25519, not literal PASETO, see ADR-0004's Implementation note -- not a formal DECOMPOSITION feature)

- **Scope**:
  - `POST /api/file-storage/v1/files/{id}/multipart` -- initiate: validate MIME/size/quota, compute parts plan, mint signed sidecar URLs, pre-register pending version
  - Sidecar part-upload handler: verify the signed token, enforce per-part size claim (HTTP 413), write part bytes, compute the per-part hash, and report it to the control plane over a token-authenticated callback (the sidecar has no DB connection of its own)
  - `POST .../multipart/{upload_id}/complete` -- verify assembled size against declared_size, assemble + hash the parts, **finalize** the version (`pending -> available`); does **not** bind (`content_id` untouched) and does **not** accept `If-Match` -- see [features/multipart-coordinator.md](features/multipart-coordinator.md) for the tracked gap between this and the richer `If-Match`/`200`-body/missing-parts contract originally specified
  - `DELETE .../multipart/{upload_id}` -- abort: mark session aborted, delete part rows and pending version, abort backend handle for multipart_native backends
  - `GET .../multipart/{upload_id}` -- introspect/resume (p2): return plan recomputed from persisted columns, re-issue fresh signed URLs for missing parts
  - DB migration: add version_id, declared_size, part_size columns to multipart_uploads table

- **Out of scope**:
  - Single-shot upload path (owned by P1 foundation)
  - File download, listing, metadata update, or delete (owned by P1 foundation)
  - Storage quota ledger management (quota is read and *checked* here via the `QuotaClient` port — see
    implementation-status note below; the check itself is a no-op because no client is wired; ledger
    updates owned by P1 foundation)

- **Requirements Covered**:

  - [ ] `p2` - `cpt-cf-file-storage-fr-multipart-upload`
  - [ ] `p2` - `cpt-cf-file-storage-fr-size-limits-policy`
  - [ ] `p2` - `cpt-cf-file-storage-fr-storage-quota` — **implementation status (P2)**: the
    `check_quota_bytes` call site exists in `multipart_service.rs`, but `gear.rs` wires `quota_client: None`
    (Tier 1 item 1.4), so no quota is actually enforced on multipart initiate — permissive/fail-open, blocked
    on a Quota Enforcement SDK crate (`gears/system/quota-enforcement/` is docs-only)

- **Design Principles Covered**:

  - [ ] `p2` - `cpt-cf-file-storage-principle-control-no-content`
  - [ ] `p2` - `cpt-cf-file-storage-principle-signed-urls`

- **Design Constraints Covered**:

  - [ ] `p2` - `cpt-cf-file-storage-constraint-sidecar`
  - [ ] `p2` - `cpt-cf-file-storage-constraint-postgres`

- **Domain Model Entities**:
  - MultipartUpload (session)
  - MultipartUploadPart

- **API**:
  - `POST /api/file-storage/v1/files/{id}/multipart` -- initiate multipart upload
  - `PUT <sidecar signed part URL>` -- upload a single part (sidecar, not control plane)
  - `POST /api/file-storage/v1/files/{id}/multipart/{upload_id}/complete` -- complete upload
  - `DELETE /api/file-storage/v1/files/{id}/multipart/{upload_id}` -- abort upload
  - `GET /api/file-storage/v1/files/{id}/multipart/{upload_id}` -- introspect/resume (p2)

- **Sequences**:

  - None (flow documented inline in `cpt-cf-file-storage-flow-multipart-initiate`, `cpt-cf-file-storage-flow-multipart-upload-part`, `cpt-cf-file-storage-flow-multipart-complete`, `cpt-cf-file-storage-flow-multipart-abort`)

- **Data**:

  - None (tables multipart_uploads and multipart_upload_parts are created by the P1 foundation migration; this feature extends multipart_uploads via migration m20260701_000002_multipart_plan_columns)


### 2.2 [Content-Hash Modes](features/content-hash-modes.md) - MEDIUM

- [ ] `p2` - **ID**: `cpt-cf-file-storage-feature-content-hash-modes`

- **Type**: Core
- **Phases**: Staged implementation (see [features/content-hash-modes.md](features/content-hash-modes.md) §5/§7 -- groundwork, schema migration, multipart-composite-sha256 implementation, docs)

- **Status**: **Proposed, not yet implemented.** Formalized in [ADR-0006](ADR/0006-cpt-cf-file-storage-adr-content-hash-modes.md) (`status: proposed`). No Rust code for this feature exists yet; the code still computes a single whole-object SHA-256 and re-reads the assembled object at multipart `complete`.

- **Purpose**: Replace the single implicit whole-object-SHA-256 hashing shape with exactly two explicit, mode-tagged content-hash modes -- non-multipart whole-object SHA-256 (unchanged) and multipart SHA-256 offset-manifest composite (new) -- computed on-the-fly during upload with no re-read of the stored object, and independently client-verifiable from the object bytes plus a small, durable manifest.

- **Depends On**: `cpt-cf-file-storage-feature-multipart-coordinator` (this feature consumes the multipart plan's per-part offsets and the already-persisted `multipart_upload_parts.part_hash` values; it does not change that feature's endpoints or session lifecycle)

- **Scope**:
  - `HashMode`/`ManifestEntry`/`Manifest` types and the manifest wire-format codec (`to_wire_string`/`from_wire_string`)
  - Schema migration: `file_versions.hash_mode`/`part_count` columns, new `version_hash_manifest` table
  - `StorageBackend::upload_part`/`complete_multipart` trait signature changes so multipart completion builds the manifest/root from already-collected per-part hashes and offsets instead of re-reading the assembled object
  - Mode-aware `Store::verify_content_hash` and `migrate_backend` re-verification
  - Additive `hash_mode`/`part_count`/`manifest` fields in metadata and multipart-complete API responses

- **Out of scope**:
  - Any second hash algorithm, per-request hash-mode preference, or capability-discovery endpoint (ADR-0002's P2 `hash_policy`/`selection_rules` vision -- dropped entirely, not deferred, since SHA-256 is the only algorithm for both modes)
  - Changes to the multipart session state machine or any multipart endpoint's method/path/request shape (owned by `cpt-cf-file-storage-feature-multipart-coordinator`)
  - Cross-mode or cross-split-choice content deduplication (an accepted, documented trade-off -- see [features/content-hash-modes.md](features/content-hash-modes.md) §7 "12. Risks & open decisions")

- **Requirements Covered**:

  - [ ] `p2` - `cpt-cf-file-storage-fr-multipart-upload`
  - [ ] `p2` - `cpt-cf-file-storage-fr-metadata-storage`
  - [ ] `p1` - `cpt-cf-file-storage-fr-get-metadata`

- **Design Principles Covered**:

  - [ ] `p2` - `cpt-cf-file-storage-principle-streaming`
  - [ ] `p2` - `cpt-cf-file-storage-principle-control-no-content`

- **Design Constraints Covered**:

  - [ ] `p2` - `cpt-cf-file-storage-constraint-postgres`

- **Domain Model Entities**:
  - HashMode (enum)
  - Manifest / ManifestEntry

- **API**:
  - `POST /api/file-storage/v1/files/{id}/multipart/{upload_id}/complete` -- response fields only (`hash_mode`, `part_count`, `manifest`); method/path unchanged

- **Sequences**:

  - None (flow documented inline in `cpt-cf-file-storage-flow-content-hash-modes-client-reverify`)

- **Data**:

  - New table `version_hash_manifest` (`version_id` PK/FK into `file_versions`, `manifest text`, `created_at`) -- not yet migrated; `file_versions` gains `hash_mode`/`part_count` columns via a future migration


---

## 3. Feature Dependencies

```text
(P1 file upload / versioning foundation)
    |
    +-- cpt-cf-file-storage-feature-multipart-coordinator
            |
            +-- cpt-cf-file-storage-feature-content-hash-modes (proposed, not yet implemented)
```

**Dependency Rationale**:

- `cpt-cf-file-storage-feature-multipart-coordinator` depends on the P1 upload and versioning foundation: the initiate endpoint pre-registers a pending version in the file_versions table (owned by P1); the complete endpoint finalizes that version (a later, separate `bind` call activates it via the CAS mechanism established in P1); the signed-URL infrastructure (minting and verification -- a codec-equivalent Ed25519 token, not literal PASETO, per ADR-0004's Implementation note) is a P1 capability.
- `cpt-cf-file-storage-feature-content-hash-modes` depends on `cpt-cf-file-storage-feature-multipart-coordinator`: it consumes the multipart plan's per-part byte offsets (`compute_plan`) and the per-part SHA-256 digests multipart-coordinator already persists into `multipart_upload_parts.part_hash`, combining them into the offset-manifest composite at `complete` instead of multipart-coordinator's current re-read-and-rehash. It introduces no new inter-feature dependency beyond this one.
