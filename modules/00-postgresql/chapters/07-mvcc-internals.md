# Chapter 7: MVCC Under the Hood

## Plan

### 1. Tuple lifecycle — watching versions appear and die
Chapter 3 showed that INSERT/UPDATE/DELETE create and mark tuples. Now we observe the full lifecycle on a single page: insert a row, update it twice, delete it. After each step, inspect the page — see the tuple chain grow, see ctid pointers linking old → new versions, see xmax get set. Count how many physical tuples exist for one logical row.

*Hands-on:* Use `pgvis page` after each operation. Track how many tuples are on the page and which ones are alive vs dead. See the update chain via ctid.

### 2. Who cleans up the dead? — dead tuples and why they linger
After UPDATE and DELETE, old tuple versions stay on the page. They take space. Why doesn't Postgres remove them immediately? Because some other transaction might still need to see them (its snapshot is older). The dead tuples linger until no snapshot references them AND vacuum runs.

*Hands-on:* Start a long-running transaction in one session. Update rows in another. Inspect the page — dead tuples can't be cleaned because the old snapshot still needs them. End the long transaction, run VACUUM, inspect again.

### 3. HOT updates — avoiding the index penalty
Every UPDATE creates a new tuple. If the table has indexes, every index must get a new entry pointing to the new tuple — even if the indexed columns didn't change. This is expensive. HOT (Heap-Only Tuple) updates solve this: when the updated column isn't indexed and the new tuple fits on the same page, Postgres skips the index update entirely. The old tuple's ctid points to the new one, and index lookups follow the chain.

*Hands-on:* Create a table with an index on one column. Update a different column — see HOT in action (no new index entry, ctid chain on the page). Then update the indexed column — see HOT fail (new index entry appears). Compare with `pg_stat_user_tables.n_tup_hot_upd`.

### 4. The visibility algorithm
The full decision logic: for every tuple, check xmin (was it born before my snapshot?) and xmax (was it killed before my snapshot?). Committed is not the same as visible — a committed transaction is invisible if it committed after the current snapshot.

*Hands-on:* REPEATABLE READ transaction in session 1, INSERT in session 2 that commits. Session 1 can't see the new row despite it being committed in the CLOG.

### 5. Freezing — making old tuples permanent
The 32-bit xid counter wraps. VACUUM FREEZE sets the XMIN_FROZEN flag so the visibility algorithm bypasses the xmin check entirely. Autovacuum handles this automatically via age thresholds. Emergency shutdown at ~40M xids remaining if autovacuum can't keep up.

*Hands-on:* Insert rows, inspect tuple header, run VACUUM FREEZE, see FROZEN flag appear.

### 6. Subtransactions and savepoints
SAVEPOINT creates a child transaction with its own xid. Rolling back a savepoint makes its changes invisible without aborting the parent. Parent-child relationship lives in pg_subtrans. Performance cliff at 64 subtransactions per transaction. ORMs create savepoints silently.

### 7. Multi-xact — when one xmax isn't enough
SELECT ... FOR SHARE is a shared lock — multiple transactions can hold it simultaneously. But xmax can only store one xid. Multi-xact IDs replace xmax with a pointer to a set of (xid, lock mode) pairs stored in pg_multixact/.

*Hands-on:* Two sessions both FOR SHARE the same row. Inspect xmax — see multi-xact ID and XMAX_IS_MULTI flag.

### What you'll know after this chapter
- How tuple versions chain together physically (ctid chains)
- Why dead tuples linger and what controls their cleanup
- How HOT updates avoid index write amplification
- The full visibility algorithm (xmin/xmax + CLOG + hint bits + snapshot)
- What freezing does to the physical tuple
- How subtransactions and multi-xact IDs work under the hood

### Out of scope
- VACUUM internals (scanning strategy, FSM updates, index cleanup) → Chapter 8
- WAL records for MVCC operations → WAL chapter
- Serializable Snapshot Isolation (SSI) → Isolation chapter

---

## Findings

### Tuple lifecycle
UPDATE creates a new physical tuple on the page. The old tuple's ctid points forward to the new one. A self-referencing ctid means "I'm the latest version." Three UPDATEs = four tuples on the page for one logical row. Each tuple is ~40 bytes of real page space.

### Page reading mechanics
Item pointers (4 bytes each) at the top of the page form a directory. Each pointer has offset, length, and flags (LP_NORMAL, LP_REDIRECT, LP_DEAD, LP_UNUSED). A TID is (page, item_number) — the item_number indexes into this directory. The indirection layer is what makes HOT possible: tuple data moves, but the item pointer slot stays stable.

### Buffer locking
No two backends can modify the same page simultaneously — exclusive buffer lock per page. But this lock is held for microseconds (physical page modification only). Row-level locks (xmax-based) are held for the full transaction duration. The buffer lock is rarely the bottleneck; the row lock is.

### VACUUM and dead tuples
VACUUM reclaims dead tuples: data is freed, item pointer slots become LP_UNUSED (nothing references them) or LP_REDIRECT (index still points to the slot). LP_REDIRECT is a 4-byte stub that keeps the index → tuple connection alive without touching the index.

UPDATE reuses LP_UNUSED slots — no waste.

### READ COMMITTED vs REPEATABLE READ and VACUUM
READ COMMITTED releases snapshots per-statement. Dead tuples are reclaimable as soon as the statement finishes — an open transaction with no active query doesn't block VACUUM.

REPEATABLE READ pins the snapshot for the entire transaction. Dead tuples can't be reclaimed until the transaction ends. This is how long-running REPEATABLE READ transactions cause table bloat.

### HOT updates
When UPDATE doesn't change any indexed column and the new tuple fits on the same page:
- Old tuple gets HOT_UPDATED flag (t_infomask2 bit 0x4000)
- New tuple gets HEAP_ONLY flag (t_infomask2 bit 0x8000) — no index entry
- No index maintenance at all

When the indexed column changes:
- Old tuple gets KEYS_UPDATED flag (t_infomask2 bit 0x2000)
- New tuple has NO HEAP_ONLY — a new index entry is created in every index
- Old index entries become ghost entries cleaned up by VACUUM

### VACUUM FULL vs regular VACUUM
Regular VACUUM reclaims space within existing pages but never moves live tuples. LP_REDIRECT entries persist. VACUUM FULL rewrites the entire table + all indexes (exclusive lock, blocks everything). pg_repack is the production alternative (no exclusive lock).

### The visibility algorithm
For every tuple, two checks:

**xmin check:** Is this tuple born before my snapshot?
1. XMIN_ABORTED hint bit → invisible
2. xmin is my own transaction → visible (unless I deleted it)
3. Check CLOG (or XMIN_COMMITTED hint bit): committed?
4. If committed: is xmin in my snapshot's xip_list or >= snapshot.xmax? → invisible (committed after my snapshot)
5. Otherwise → visible

**xmax check:** Is this tuple killed before my snapshot?
1. xmax = 0 → visible
2. XMAX_ABORTED → visible (delete rolled back)
3. XMAX_LOCK_ONLY → visible (it's a lock, not a delete)
4. xmax is my own transaction → invisible
5. If committed: is xmax in xip_list or >= snapshot.xmax? → visible (delete committed after my snapshot)
6. Otherwise → invisible

Key insight: "committed" is not "visible." A committed INSERT is invisible if it committed after the snapshot. A committed DELETE is ignored if it committed after the snapshot.

### Hint bits
XMIN_COMMITTED, XMAX_COMMITTED, XMAX_ABORTED — cached CLOG lookup results written onto the tuple header by the first reader. Set lazily: not by the committing transaction, but by whoever reads the tuple next. A SELECT can dirty a page by setting hint bits.

### Freezing
VACUUM FREEZE sets the FROZEN flag (XMIN_COMMITTED + XMIN_ABORTED combined = 0x0300) on old tuples. The visibility algorithm sees FROZEN and skips the entire xmin check — the tuple is unconditionally visible.

Autovacuum freezes automatically at age thresholds (50M/150M/200M). At ~40M xids remaining, PostgreSQL refuses new write transactions until VACUUM FREEZE catches up. Long-running transactions pin the freeze horizon — the #1 operational risk.

### Subtransactions
SAVEPOINT creates a child transaction with its own xid. Parent-child mapping in pg_subtrans (SLRU). Each backend tracks sub-xids in a 64-slot array in shared memory (PGPROC). Over 64 subtransactions → overflow → other backends fall back to expensive pg_subtrans lookups.

ORMs create savepoints silently: Django's `transaction.atomic()`, SQLAlchemy's `begin_nested()`, PL/pgSQL EXCEPTION blocks. A loop with 1000 database operations inside a transaction can silently create 1000 subtransactions.

### Multi-xact
SELECT ... FOR SHARE takes a shared row-level lock — multiple transactions can hold it simultaneously. The lock is stored in xmax with XMAX_LOCK_ONLY flag. When a second transaction takes FOR SHARE on the same row, xmax can't hold two xids. PostgreSQL replaces xmax with a multi-xact ID (XMAX_IS_MULTI flag, bit 0x1000 in t_infomask) — an opaque pointer to a set of (xid, lock mode) pairs stored in pg_multixact/.

### Tools updated
- `pgvis page` now decodes t_infomask2 flags: HOT_UPDATED (0x4000), HEAP_ONLY (0x8000), KEYS_UPDATED (0x2000)
- `pgvis page` now shows XMAX_IS_MULTI (0x1000) for multi-xact IDs
- Fixed wrong t_infomask flag: removed obsolete 0x4000 MOVED_OFF that was mislabeled as HOT_UPDATED

---

## Retro

### Summary
Deep dive into MVCC mechanics beyond chapter 3's introduction. Observed tuple lifecycle on real pages: ctid chains linking old → new versions, dead tuples accumulating, VACUUM cleanup to LP_REDIRECT and LP_UNUSED with slot reuse. Demonstrated HOT updates (HEAP_ONLY flag, no index entry) vs non-HOT (KEYS_UPDATED, new index entry in every index). Walked through the full visibility algorithm: xmin/xmax checks with CLOG lookups, hint bit caching, snapshot-based filtering ("committed is not visible"). Covered freezing (FROZEN flag bypasses xmin check), subtransactions (savepoints, ORMs, 64-slot overflow cliff), and multi-xact (FOR SHARE shared locks, XMAX_IS_MULTI). Fixed and extended pgvis to decode t_infomask2 flags.

### Key Takeaways
- The page has a directory (item pointers) that provides indirection — item pointer position is stable, tuple data can move. This is what makes HOT and VACUUM possible.
- HOT avoids all index maintenance when non-indexed columns change. HEAP_ONLY flag = no index entry. After VACUUM, LP_REDIRECT keeps the index connection at 4 bytes.
- READ COMMITTED releases snapshots per-statement; REPEATABLE READ pins them per-transaction. Only REPEATABLE READ blocks VACUUM cleanup.
- "Committed" ≠ "visible." The snapshot determines the cutoff — a committed INSERT after your snapshot is invisible, a committed DELETE after your snapshot is ignored.
- Hint bits are a CLOG cache on the tuple header, set lazily by the first reader. SELECT dirtying pages is a real side effect.
- Long-running transactions pin the freeze horizon — the #1 cause of wraparound emergencies.
- ORMs create subtransactions silently. >64 per transaction degrades performance for all backends.
- FOR SHARE needs multi-xact because xmax can only hold one xid.

### Connections
- **Ch 1 (Physical Storage)**: item pointers and tuple layout — now understood as the indirection layer enabling HOT and VACUUM.
- **Ch 3 (Transactions)**: xmin/xmax, CLOG, hint bits, snapshots — now understood at the algorithm level, not just the concept level.
- **Ch 6 (Indexes)**: HOT avoids index write amplification. KEYS_UPDATED triggers new index entries. Ghost entries cleaned by VACUUM.
- **Ch 8 (Vacuum)**: VACUUM's job is now concrete — reclaim dead tuples, set LP_REDIRECT/LP_UNUSED, freeze old tuples, clean multi-xact entries.

### Open Questions
- How does VACUUM decide which pages to scan? Does it skip all-visible pages?
- What does the pg_subtrans overflow look like in pg_stat_activity?
- How does SSI detect conflicts — what data structures track read/write dependencies?
