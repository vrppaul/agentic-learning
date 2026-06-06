# Chapter 10b: Optimistic Concurrency Control

## Plan

### 1. Pessimistic vs optimistic — the fundamental tradeoff
Pessimistic: lock first, then work. Optimistic: work first, check at commit. When each wins.

### 2. The overhead of locking
FOR UPDATE modifies the tuple header (xmax + XMAX_LOCK_ONLY), dirties the page, generates WAL. Plain SELECT (OCC read phase) changes nothing. At high throughput, the difference matters.

### 3. The version column pattern
Read version → compute → UPDATE WHERE version = N. Zero database overhead beyond the normal UPDATE. Application detects conflicts via affected row count.

### 4. Failure modes — retry storms
Under high contention, OCC degrades: most transactions fail and retry, wasting work. Rule of thumb: OCC wins below ~5% conflict rate. Above that, pessimistic locking wastes less total work.

### 5. Retry strategies
Immediate retry, exponential backoff, jitter, retry limits. Production systems need all four.

### 6. Why distributed systems prefer OCC
No locks held during network calls. Works across multiple databases and services. Version columns are universal — not tied to PostgreSQL's lock manager or isolation levels.

### 7. SSI vs application-level OCC
SSI: database handles detection, single-database only, imprecise (false positives), invisible to developers. Application OCC: explicit in code, works across services, precise (exact row), flexible conflict handling. Most production systems prefer application-level OCC.

---

## Findings

### The overhead of FOR UPDATE
FOR UPDATE writes xid into xmax with XMAX_LOCK_ONLY flag. This dirties the page, generates a WAL record, and requires eventual VACUUM cleanup. A plain SELECT (OCC read) touches nothing — no dirty page, no WAL, no cleanup. At 10,000 tx/sec, the difference is 10,000 extra page modifications and WAL records.

### Version column pattern
Add `version int` column. Read: `SELECT balance, version`. Write: `UPDATE ... SET version = version + 1 WHERE version = N`. If affected rows = 0, conflict detected — retry. Works at READ COMMITTED, no special isolation level needed.

### Contention behavior
Low contention (<5% conflict rate): OCC wins — no blocking, maximum concurrency, rare retries. High contention: OCC degrades into retry storms — work is wasted on every failed attempt. Pessimistic locking (FOR UPDATE) queues transactions — no wasted work, but reduced concurrency.

### Retry strategies for production
1. Exponential backoff — spread retry waves over time
2. Jitter — randomize backoff to prevent synchronized retries
3. Retry limit — prevent infinite loops
4. Conflict-specific handling — merge, queue, or alert instead of blind retry

### Why not SSI?
SSI only works within a single PostgreSQL database. Application-level OCC works across services, databases, and storage engines. SSI uses imprecise SIREAD locks (false-positive aborts). Version columns are exact. SSI gives no control over conflict handling (abort only). Application OCC lets the code decide (retry, merge, queue, alert).

### Architecture first
OCC is not an excuse for bad architecture. Transactions should be short regardless — read, compute, write, no external calls during the transaction. OCC's advantage is reduced database overhead (no locks, no WAL for reads), not enabling long transactions.

---

## Retro

### Summary
Deep dive into optimistic concurrency control: the pessimistic vs optimistic tradeoff, FOR UPDATE overhead at the tuple level (xmax + XMAX_LOCK_ONLY, dirty page, WAL), version column pattern, retry storms under high contention, retry strategies (backoff, jitter, limits), why distributed systems prefer OCC over locks, and why application-level OCC beats SSI in practice (cross-service, precise, explicit, flexible).

### Key Takeaways
- OCC's advantage is NOT enabling long transactions — it's reduced database overhead on the read path (no WAL, no dirty pages, no lock manager).
- Version columns are simple, precise, and universal. They work at READ COMMITTED, across any database, with zero special configuration.
- OCC degrades under high contention. Below ~5% conflict rate it wins. Above that, pessimistic locking wastes less total work.
- SSI is database-level OCC but is imprecise, single-database, and gives no control over conflict handling. Application-level OCC is preferred in production.
- Good architecture comes first. Short transactions, no external calls during transactions. OCC and locking are tools within good architecture, not substitutes for it.

### Connections
- **Ch 7 (MVCC)**: XMAX_LOCK_ONLY flag — FOR UPDATE reuses xmax for locking, same field as UPDATE/DELETE.
- **Ch 9 (WAL)**: FOR UPDATE generates WAL records because it modifies the tuple header. OCC reads generate no WAL.
- **Ch 10a (Isolation)**: SSI as database-level OCC. SERIALIZABLE = optimistic (proceed, check at commit). FOR UPDATE = pessimistic (lock, then proceed).
- **Distributed Systems (future)**: sagas, outbox pattern, distributed locks — all related to cross-service concurrency control.
