# Chapter 9: WAL & Crash Recovery

## Plan

### 1. The problem — why pages alone aren't enough
Theory: atomicity (partial page writes corrupt state) and performance (flushing every dirty page on commit = random I/O bottleneck). The write-ahead rule: WAL record must reach disk before the dirty page.

### 2. What a WAL record looks like
Theory: three parts — header (LSN, xid, resource manager, record type), block references (relation, fork, block number, data/FPI), main data (operation-specific metadata).
pgvis: `pgvis wal show "SQL"` — run a statement, see WAL records with annotations.

### 3. FPI — Full Page Images
Theory: torn page problem (OS writes 4KB of 8KB page). FPI = complete page copy in WAL on first modification after checkpoint. Recovery overwrites torn page with clean FPI, replays forward.
pgvis: compared first INSERT (with FPI, 517 bytes) vs second INSERT (no FPI, 168 bytes).

### 4. LSN — connecting pages to WAL
Theory: every page stores LSN of last WAL record that modified it. Recovery compares page LSN vs record LSN to skip already-applied changes. Idempotent replay.
pgvis: `pgvis wal lsn [table page]` — compare page LSN with current WAL position.

### 5. COMMIT = WAL fsync
Theory: COMMIT flushes wal_buffers to disk via fsync. Client doesn't see "COMMIT OK" until WAL is physically durable. No window between commit and fsync — they are the same operation. Crash after fsync but before client notification: transaction IS committed, client doesn't know — application must handle with idempotent operations.

### 6. Checkpoints
Theory: flush all dirty pages to disk, record REDO point. Recovery starts from last checkpoint. Tradeoff: frequent checkpoints = shorter recovery + more I/O. checkpoint_timeout (default 5 min).
pgvis: `pgvis wal checkpoint` — checkpoint state, recovery window, stats.

### 7. Crash recovery
Theory: startup finds last checkpoint REDO LSN → replay WAL forward → for each record, compare page LSN → apply if stale, skip if current → FPI overwrites torn pages → uncommitted transactions marked aborted in CLOG → MVCC makes them invisible.

### 8. WAL segments
Theory: 16 MB files in pg_wal/, named by position in WAL stream. Recycled after checkpoint (pre-allocated for reuse). WAL archiving enables PITR and replication.
pgvis: `pgvis wal segments` — list segment files, current segment, total size.

### 9. wal_level
Theory: controls how much detail in WAL. minimal (crash recovery only), replica (streaming replication, default), logical (logical decoding, CDC). Each level includes the one below.

### What you'll know after this chapter
- Why write-ahead logging exists and what guarantee it provides
- What WAL records contain and how to read them
- How FPI protects against torn pages and its cost
- How LSN connects pages to the WAL timeline
- How the WAL buffer, flushing, and commit interact
- What checkpoints do and the recovery time tradeoff
- How crash recovery replays WAL idempotently
- How WAL segments are managed
- What wal_level controls

### Out of scope
- Streaming replication internals → Replication chapter
- Logical decoding / logical replication → Replication chapter
- WAL compression
- Synchronous commit tradeoffs (mentioned briefly)

---

## Findings

### Why WAL exists
Two problems: (1) crash consistency — partial page writes leave the database in a state that never existed; (2) performance — flushing every dirty page on commit is random I/O. WAL converts random page writes into sequential log appends. On COMMIT, only the log is fsynced — dirty pages are flushed lazily by the checkpointer.

### The write-ahead rule
WAL record for a change must be flushed to disk BEFORE the dirty page is written to disk. Enforced at page flush time — checkpointer checks page LSN and ensures WAL is flushed at least that far.

### WAL record structure
Three parts: header (LSN, xid, resource manager, record type, length), block references (relation filenode, fork, block number, data bytes, optional FPI), main data (operation-specific metadata). Resource managers (Heap, Btree, Transaction, etc.) are modular — each defines its own record types and replay logic.

### WAL is physical
Records describe which bytes changed on which page, not the SQL operation. Physical WAL is simpler for crash recovery but can't be replayed across different architectures. Logical decoding converts physical WAL to logical changes for replication.

### Full Page Images (FPI)
On first modification to a page after a checkpoint, the entire page is included in the WAL record. Protects against torn pages (OS writes 4KB of 8KB page). Observed: first INSERT = 517 bytes (376 bytes FPI, 73% of record). Second INSERT to same page = 168 bytes (no FPI). FPI resets at each checkpoint.

### COMMIT semantics
COMMIT = fsync of wal_buffers to disk. Client sees "COMMIT OK" only after WAL is physically durable. If crash happens after fsync but before client notification, the transaction IS committed — the client must handle the ambiguity (idempotent operations, check-after-reconnect).

### Checkpoints
Flush all dirty pages, record REDO point. Recovery starts from last checkpoint. Tradeoff: checkpoint_timeout (default 5 min). More frequent = shorter recovery window + more I/O + more FPI resets. Observed: 2,270 timed checkpoints, 4 requested, recovery window ~0.1 MB.

### Crash recovery
Find last checkpoint REDO LSN → replay WAL records forward → compare page LSN vs record LSN (skip if already applied, apply if stale) → FPI records overwrite entire page (handles torn pages) → transactions without COMMIT record marked aborted in CLOG → MVCC makes their changes invisible. Idempotent — safe to replay multiple times.

### WAL segments
16 MB files in pg_wal/, named by position in WAL stream. Observed: 10 segments, 160 MB total. Segments after current are pre-allocated/recycled. Old segments recyclable after checkpoint. WAL archiving preserves segments for PITR and replication.

### wal_level
Controls WAL detail level. minimal (crash recovery only), replica (streaming replication, default), logical (logical decoding, CDC). Each includes the level below. More detail = more WAL volume.

### Tools built
- `pgvis wal show "SQL"` — run statement, show WAL records with annotations (type, size, FPI, block references)
- `pgvis wal lsn [table page]` — WAL position and page LSN comparison
- `pgvis wal checkpoint` — checkpoint state, recovery window, stats
- `pgvis wal segments` — WAL segment files, current segment, total size

---

## Retro

### Summary
Deep dive into Write-Ahead Logging: why it exists (atomicity + performance), the write-ahead rule, WAL record structure (header, block references, main data, resource managers), Full Page Images for torn page protection, checkpoints as the recovery starting point, crash recovery replay with LSN-based idempotency, WAL segments and recycling, COMMIT = WAL fsync semantics, and wal_level settings. Built four pgvis tools for WAL inspection.

### Key Takeaways
- WAL converts random page writes into sequential log appends — the fundamental performance trick.
- The write-ahead rule is the one invariant: WAL on disk before dirty page on disk. Everything else follows.
- COMMIT = fsync of WAL. No gap between "committed" and "durable." But there IS a gap between "durable" and "client notified."
- FPI dominates WAL volume (73% of record size) but protects against torn pages. Resets at each checkpoint.
- Checkpoints are the tradeoff knob: recovery time vs I/O cost vs FPI volume.
- Crash recovery is idempotent — LSN comparison ensures each change is applied exactly once.
- Without a COMMIT record in WAL, a transaction's changes are physically present but invisible via MVCC — same mechanism as abort.

### Connections
- **Ch 1 (Physical Storage)**: LSN field on every page header — now understood as the link between pages and WAL.
- **Ch 2 (Shared Buffers)**: dirty pages, checkpointer, background writer — now understood as the lazy write side of WAL.
- **Ch 7 (MVCC)**: "both changes in one WAL record" for atomic page modifications. CLOG marks uncommitted transactions as aborted during recovery.
- **Ch 8 (Vacuum)**: VM and FSM updates generate WAL records. VACUUM FREEZE generates WAL.
- **Replication (future)**: streaming replication = shipping WAL segments. Logical replication = logical decoding of physical WAL.

### Open Questions
- What does synchronous_commit = off actually risk? How much data can you lose?
- How does WAL compression work? Which parts are compressed?
- What does a B-tree page split look like in WAL? Multiple records?
