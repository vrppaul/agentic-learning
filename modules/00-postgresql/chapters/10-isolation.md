# Chapter 10: Isolation Levels

## Plan

### 1. Why isolation exists
Concurrent transactions see each other's changes. Without rules, data shifts mid-transaction. Isolation defines what one transaction can see of another's changes.

### 2. The anomalies
Dirty read (reading uncommitted data — PostgreSQL never allows), non-repeatable read (same row returns different values), phantom read (new rows appear), write skew (cross-row logical conflict).

### 3. READ COMMITTED
Snapshot per statement. Pros: maximum concurrency, almost never aborts. Cons: inconsistent reads within a transaction. Use case: most OLTP applications, web requests.

### 4. REPEATABLE READ
Snapshot per transaction. Catches same-row write conflicts (via xmax check). Pros: consistent reads. Cons: abort + retry needed on conflicts, pins snapshot (blocks VACUUM). Use case: reports, batch jobs, read-heavy consistency.

### 5. SERIALIZABLE
Same snapshot + SSI tracking. Catches cross-row conflicts (write skew). Pros: strongest guarantee, no concurrency bugs. Cons: higher abort rate, memory overhead (SIREAD locks), retry complexity. Use case: complex invariants, low contention, correctness-critical operations.

### 6. SSI internals
SIREAD locks (predicate locks) track reads. Write hits a SIREAD lock → rw-conflict recorded. Two rw-conflicts forming a cycle → dangerous structure → abort. Conservative — false positives possible.

### 7. FOR UPDATE as alternative
Pessimistic locking: lock rows explicitly before modifying. Blocks instead of aborting. Simpler retry logic but less concurrency. Observed with pgvis locks.

### 8. Practical guidelines
READ COMMITTED + FOR UPDATE for most applications. REPEATABLE READ for reports. SERIALIZABLE for complex invariants with acceptable abort rates.

---

## Findings

### Isolation levels define snapshot boundaries
READ COMMITTED: snapshot per statement. REPEATABLE READ: snapshot per transaction. SERIALIZABLE: per-transaction snapshot + read/write dependency tracking. Same MVCC machinery underneath — the difference is when the snapshot is taken and what extra tracking is done.

### PostgreSQL never allows dirty reads
Even READ UNCOMMITTED behaves as READ COMMITTED in PostgreSQL. MVCC visibility check (XMIN_COMMITTED) prevents it naturally.

### Non-repeatable read
Demonstrated under READ COMMITTED: two SELECTs in the same transaction returned different values after another transaction committed between them. REPEATABLE READ prevents it — frozen snapshot ignores concurrent commits.

### REPEATABLE READ write conflict detection
Uses the existing xmax field. When a REPEATABLE READ transaction tries to UPDATE a row whose xmax was set by a concurrent committed transaction, it aborts. READ COMMITTED handles the same situation by re-reading the latest version instead of aborting.

### Write skew — the anomaly only SERIALIZABLE catches
Two transactions read overlapping data (oncall table), each delete a different row. REPEATABLE READ allows both — different rows, no conflict. SERIALIZABLE detects the rw-conflict cycle (both read what the other wrote) and aborts one.

### SSI implementation
SIREAD locks track reads (tuple/page/relation granularity, promotes when too many). Writes checked against SIREAD locks of other serializable transactions. rw-conflict edges recorded. Two rw-conflicts forming a cycle = dangerous structure = abort the "pivot" transaction. Conservative — may abort transactions that would have been safe.

### FOR UPDATE — pessimistic alternative
Locks rows at read time by writing xid into xmax with XMAX_LOCK_ONLY. Other transactions block (wait for the lock holder to commit/abort). Observed with pgvis locks: blocked transaction waits on ShareLock on the holder's transaction ID.

### Practical guidelines
- READ COMMITTED (95% of apps): simple, fast, rarely fails. Use FOR UPDATE for critical sections.
- REPEATABLE READ: reports and batch jobs needing consistent snapshots. Retry on same-row conflict.
- SERIALIZABLE: complex cross-row invariants. Requires retry logic, higher abort rate, memory overhead from SIREAD locks.
- Application-level OCC (version columns): alternative for single-row protection without isolation level changes → deferred to separate chapter.

---

## Retro

### Summary
Isolation levels from the ground up: anomalies (dirty read, non-repeatable read, phantom, write skew), three levels with practical tradeoffs, SSI internals (SIREAD locks, rw-conflicts, dangerous structures), FOR UPDATE as the pessimistic alternative. Demonstrated each anomaly and protection with live experiments across two sessions. Visualized lock contention with pgvis locks.

### Key Takeaways
- Isolation levels = when the snapshot is taken. Same MVCC, different boundaries.
- READ COMMITTED adapts to concurrent changes (re-reads). REPEATABLE READ rejects them (aborts). Different reactions to the same xmax check.
- Write skew is the subtle anomaly — different rows, no single-row conflict, only SERIALIZABLE catches it.
- SERIALIZABLE is optimistic: proceed without blocking, check at commit. FOR UPDATE is pessimistic: block first, then proceed safely.
- Most production systems use READ COMMITTED + explicit FOR UPDATE locks, not SERIALIZABLE.
- SSI is conservative — it tracks read-write dependency patterns, not business rules. It can abort transactions that would have been fine.

### Connections
- **Ch 3 (Transactions)**: snapshots (xmin:xmax:xip_list) — now understood as the mechanism behind isolation levels.
- **Ch 7 (MVCC)**: visibility algorithm, xmax for locking (XMAX_LOCK_ONLY, multi-xact). FOR UPDATE uses the same xmax mechanism.
- **Ch 8 (Vacuum)**: REPEATABLE READ pins snapshots, blocking VACUUM cleanup — operational risk.
- **OCC chapter (future)**: application-level OCC, version columns, retry strategies, SSI as database-level OCC.

### Open Questions
- What does SIREAD lock memory usage look like under high-read workloads?
- How does PostgreSQL choose which transaction to abort in a serialization conflict?
- What does advisory locking look like and when does it beat row-level locks?
