# Chapter 8: Vacuum & Bloat

## Plan

### 1. What VACUUM actually does — the three phases
VACUUM processes a table in three phases: (1) scan the heap to collect dead tuple TIDs, (2) scan each index to remove entries pointing to those dead TIDs, (3) make a second heap pass to reclaim the dead tuple space and update the FSM. Why this order? Index entries must be removed before heap tuples — otherwise an index scan could follow a dangling pointer.

### 2. The visibility map — VACUUM's skip list
One bit per heap page: all-visible or not. If a page is all-visible, VACUUM skips it entirely. A second bit tracks all-frozen. Stored as a flat bitmap in the _vm fork file. Two bits per heap page, so the VM is tiny compared to the heap.

### 3. The Free Space Map — where to INSERT next
After VACUUM reclaims space, it updates the FSM. The FSM is a tree-of-trees: each FSM page contains a binary max-heap (~4,000 leaves), and FSM pages form a higher-level tree. INSERT traverses the tree to find a page with enough room in O(log N).

### 4. Dead tuple identification and the maintenance_work_mem limit
VACUUM collects dead TIDs into an in-memory array limited by maintenance_work_mem. If there are more dead tuples than fit, VACUUM does multiple passes — each re-scanning all indexes.

### 5. Autovacuum — when and why it runs
Threshold formula: dead_tuples > threshold + scale_factor × reltuples. Default: 50 + 20% of table. Why it falls behind: too few workers, cost delay throttling, large tables with high churn, long-running transactions.

### 6. VACUUM variants
Regular VACUUM: reclaim dead tuples, update FSM/VM, freeze old tuples if age threshold met. Does not shrink file on disk.
VACUUM FREEZE: regular VACUUM + freeze all tuples regardless of age. Manual "advance the freeze horizon now."
VACUUM FULL: completely different — exclusive lock, rewrite entire table to new file, rebuild all indexes, shrink to minimum size.

### What you'll know after this chapter
- The three phases of VACUUM and why the order matters
- How the visibility map and FSM work and how VACUUM maintains them
- Why autovacuum falls behind and how to tune it
- The differences between VACUUM, VACUUM FREEZE, and VACUUM FULL
- How to measure bloat and decide when to act

### Out of scope
- Index-specific vacuum details (B-tree page recycling)
- Parallel VACUUM
- pgstattuple hands-on (deferred)

---

## Findings

### Three phases of VACUUM
Phase 1: scan heap, collect dead TIDs (using VM to skip all-visible pages). Phase 2: scan each index, remove entries pointing to dead TIDs. Phase 3: reclaim heap space, update item pointers (LP_REDIRECT/LP_UNUSED), update FSM and VM. Order matters — index cleanup before heap reclaim prevents dangling pointers.

### VACUUM concurrency
Regular VACUUM takes ShareUpdateExclusiveLock — does NOT block reads or writes. Only blocks other VACUUMs and certain DDL. Processes one page at a time, buffer lock held for microseconds. VACUUM FULL takes exclusive lock — blocks everything.

### Snapshot horizon
VACUUM computes the oldest active snapshot across all backends. Dead tuples older than this horizon are safe to remove. Newer dead tuples are left alone. Long-running REPEATABLE READ transactions pin the horizon and prevent cleanup of all recent dead tuples across the entire database.

READ COMMITTED releases snapshots per-statement — an idle BEGIN doesn't block VACUUM. REPEATABLE READ holds the snapshot for the entire transaction duration.

### VACUUM doesn't shrink files
Regular VACUUM reclaims space within pages (updates FSM for reuse by future INSERTs) but does not return pages to the OS. Table file stays the same size on disk. Exception: trailing completely-empty pages can be truncated. VACUUM FULL is needed to actually shrink the file.

### Visibility map
Stored in the _vm fork. Two bits per heap page: all-visible and all-frozen. Flat bitmap structure. One VM page (8KB, minus 24-byte header = 8,168 bytes = 65,344 bits) covers 32,672 heap pages. For a 500M row table: ~5 MB of VM vs ~100 GB of heap.

VACUUM sets bits after confirming a page is clean. Any modification (INSERT/UPDATE/DELETE) clears the bit. Index-only scans also use the all-visible bit to skip heap fetches.

### Free Space Map
Stored in the _fsm fork. One byte per heap page (0-255, in units of ~32 bytes). Organized as a tree-of-trees: each FSM page contains a binary max-heap with ~4,000 leaves, and FSM pages form a higher-level tree. At most 3 levels deep. INSERT traverses the tree in 1-3 page reads to find a page with enough room.

VACUUM updates FSM after reclaiming space. Without FSM updates, INSERTs always append to the end and reclaimed space is wasted.

### maintenance_work_mem and multi-pass
VACUUM collects dead TIDs in memory, limited by maintenance_work_mem (default 64 MB ≈ 11M TIDs). If more dead tuples exist, VACUUM batches: collect what fits → scan all indexes → reclaim heap → repeat. Each batch re-scans every index. Increasing maintenance_work_mem reduces the number of passes and total index I/O.

### Autovacuum
Launcher checks every autovacuum_naptime (1 minute). Threshold: dead_tuples > 50 + 0.2 × reltuples. For a 50M row table: 10M dead tuples before triggering. Default 0.2 scale_factor is too high for large tables — production recommendation: 0.01-0.05 for tables over 1M rows.

Max 3 workers by default. Cost-based delay (2ms default) throttles I/O. Falls behind when: too few workers, cost delay too aggressive, large tables with high churn, locked tables, long-running transactions.

Key monitoring query: pg_stat_user_tables (n_dead_tup, n_live_tup, last_autovacuum, autovacuum_count). Also pg_stat_activity for running autovacuum workers and long-running transactions.

### VACUUM variants
**VACUUM (regular):** online (no locks on reads/writes), reclaims dead tuples within pages, updates FSM/VM, freezes old tuples if age threshold met. Does not shrink file.

**VACUUM FREEZE:** regular VACUUM + freezes all live tuples regardless of age. Uses VM all-frozen bit to skip already-frozen pages. Manual tool for advancing freeze horizon.

**VACUUM FULL:** completely different operation. Exclusive lock → create new file → copy all live tuples → rebuild all indexes → delete old file → release lock. Shrinks table to minimum size. Blocks everything for the duration. Production alternative: pg_repack (online rebuild, no exclusive lock except briefly at swap).

---

## Retro

### Summary
Deep dive into VACUUM mechanics: three-phase operation (heap scan → index cleanup → heap reclaim), why order matters, how the visibility map (2-bit bitmap per page) and free space map (tree-of-trees with binary max-heaps) enable efficient vacuuming and space reuse. Covered autovacuum internals (threshold formula, worker limits, cost-based delay, why it falls behind on large tables), monitoring queries for production databases, and the three VACUUM variants (regular, FREEZE, FULL).

### Key Takeaways
- VACUUM's three phases must run in order: clean indexes before reclaiming heap, otherwise dangling pointers.
- The VM makes VACUUM fast on stable tables: skip all-visible pages, only process dirty ones. A 100 GB table with 1% writes: VACUUM checks 5 MB of VM and touches a handful of pages.
- The FSM is a tree-of-trees enabling O(log N) free page lookup for INSERT. Without FSM updates, tables grow forever even with reclaimed space.
- Default autovacuum scale_factor (0.2) is too high for large tables. 50M rows × 20% = 10M dead tuples before triggering. Lower to 0.01-0.05 in production.
- Regular VACUUM doesn't shrink files — only reclaims space internally. VACUUM FULL rewrites the file but takes exclusive lock.
- Long-running REPEATABLE READ transactions are the #1 cause of bloat — they pin the snapshot horizon and prevent cleanup across all tables.
- maintenance_work_mem controls batch size. Too small = multiple index scans per VACUUM. Worth increasing to 256 MB-1 GB on large tables.

### Connections
- **Ch 1 (Physical Storage)**: VM and FSM are relation forks (_vm, _fsm files). Same page structure as heap.
- **Ch 3 (Transactions)**: snapshot horizon determines what VACUUM can clean. Wraparound thresholds trigger freeze vacuums.
- **Ch 6 (Indexes)**: VACUUM phase 2 scans every index. More indexes = more expensive VACUUM. Ghost entries cleaned here.
- **Ch 7 (MVCC)**: dead tuples, HOT chains (LP_REDIRECT), freezing — all maintained by VACUUM. VM enables index-only scans.

### Open Questions
- How does parallel VACUUM work? Which phases are parallelized?
- What does pgstattuple show for a heavily bloated table vs a clean one?
- How does pg_repack handle concurrent changes during the rewrite?
