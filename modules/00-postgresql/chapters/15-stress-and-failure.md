# Chapter 15: Postgres Under Fire — Stress, Failure & Recovery

> **Capstone / finale of the PostgreSQL deep dive.** Combines "Postgres under fire"
> (contention & load) with "Breaking Postgres" (chaos & recovery) into one chapter. After
> this, the PG module is considered complete. Remaining roadmap topics (TOAST, triggers,
> extensions) are deferred as optional future explorations.

## Plan

Scenario-driven, hands-on, theory-first per section (the established rule). This chapter
is where the abstractions from the whole module get stress-tested and broken on purpose —
locks (Ch 10), MVCC/freezing (Ch 3/7), WAL recovery (Ch 9), vacuum (Ch 8), connections
(Ch 12) all reappear under failure conditions. Two parts.

A safety note baked into the lab: the *breaking* experiments (kill -9, disk-full,
corruption) run against a **dedicated disposable container**, never the main `pg-study`
lab that holds prior chapters' data.

---

## Part A — Under fire (contention & load)

### A1. Locks & the lock queue
Lock modes and what conflicts with what (row locks vs table locks vs `ACCESS EXCLUSIVE`;
the conflict matrix). The lock *queue* — a waiter behind a strong lock blocks everyone
behind it. Reading `pg_locks` joined to `pg_stat_activity` (granted vs waiting). Builds on
Ch 10 (isolation/FOR UPDATE) and the existing `pgvis locks`. `lock_timeout` /
`statement_timeout` as guardrails.

### A2. Deadlocks
The wait-for graph and how Postgres detects a cycle (`deadlock_timeout`, default 1s — it's
lazy detection, not prevention). Victim selection and the error. Demonstrate a real
deadlock live (two sessions, opposite lock order). The canonical fix: consistent lock
ordering. Why retry logic is mandatory (ties to Ch 10b OCC retries).

### A3. Hot-row contention at scale
Many writers hammering the same row(s). Lock waits pile up; throughput collapses even
though CPU is idle. Measure it with a **Go load generator** (project default language).
Watch `wait_event` / `wait_event_type` in `pg_stat_activity`. Connects to Ch 10b: this is
exactly when pessimistic locking loses and OCC/queueing wins.

### A4. Connection overload & what breaks first
Too many connections (Ch 12): proc-array scan cost, memory, `max_connections` rejection.
The thundering herd. Why a pooler (PgBouncer) is the fix. Demonstrate degradation as
connections climb.

### A5. Cascading failure
The classic outage shape: one slow query under load → connections held longer → pool/slots
exhausted → healthy queries can't get a connection → whole system stalls. Reproduce a
miniature version and read it in `pg_stat_activity`. Observability: states (active / idle
in transaction / waiting), wait events, `idle_in_transaction_session_timeout`.

---

## Part B — Breaking Postgres (chaos & recovery)

### B1. Crash recovery in practice
`kill -9` the postmaster mid-write. Restart → watch WAL replay from the last checkpoint
(Ch 9 made real). Verify the durability contract: committed survives, in-flight is gone,
no torn pages (FPIs did their job). Also kill mid-checkpoint and mid-vacuum.

### B2. Disk full during writes
Fill the data disk while writing. Observe: writes start failing, but the DB does **not**
corrupt; what happens to WAL when the disk fills (the dreaded "could not write to WAL");
recovery once space is freed. Why you keep WAL on space you can reclaim.

### B3. Transaction ID wraparound
Push toward the 2-billion-xid horizon (with a shortcut so we don't burn real xids for
hours). The escalating warnings, forced "emergency" autovacuum, and the read-only
shutdown at the limit. Why `FREEZE` exists (Ch 3/7) and how to dig out. Likely
theory-heavy with a scoped demo.

### B4. Corruption & recovery tools
Corrupt a heap page with `dd` (bytes on disk, Ch 1). See the error. Tooling:
`pg_amcheck` (detect), `zero_damaged_pages` (last-resort read-through),
`pg_resetwal` (and why it's a foot-gun that can destroy consistency), and the *real*
answer — restore from base backup + PITR (Ch 11). Data checksums (`data_checksums`) and
what they catch.

---

### Tool
Extend `pgvis locks` for the contention work (wait-for graph view), and build a small **Go**
load/chaos harness (`stress/`): concurrent writers for A3/A4, and a failure injector for
Part B. Build only what earns its keep (Ch 13 lesson) — but contention and cascading
failure are genuinely hard to *see* without generated load, so a tool is justified here.

### Lab
- A3/A4/A5 load → can run against the main `pg-study` (read-mostly stress, recoverable).
- Part B (destructive) → a **dedicated `pg-victim` container** with its own volume, so
  kill/fill/corrupt never touches prior chapters' data. Add to a small compose or run ad hoc.

### Out of scope (deferred — module ends here)
- TOAST deep dive, Triggers & rule system, Writing an extension → optional future modules.
- General chaos-engineering tooling / distributed failure → belongs to a future
  distributed-systems module.

---

## Findings

### §A1 — Locks & the lock queue ✅
MVCC handles read-vs-write; locks handle write-vs-write, DDL, explicit coordination.
Two facts explain most incidents: (1) a plain `SELECT` takes `AccessShareLock`, which
conflicts with *only* `AccessExclusiveLock` (DDL); (2) the lock manager's wait queue is
FIFO/fair, so **a queued strong lock blocks even compatible requests behind it**.
Row locks aren't in the lock table — they're `xmax` in the tuple; waiters block on the
holder's `transactionid` lock.
Demonstrated live on `lockdemo`: long reader holds `AccessShareLock` → `ALTER`
(`AccessExclusiveLock`) queues behind it (`granted=f`, wait `Lock`) → an innocent
`SELECT` then blocks behind the `ALTER` despite being compatible with the reader = "one
ALTER takes down all reads". Diagnosed with `pg_blocking_pids()` (root = empty
`blocked_by`); resolved with `pg_terminate_backend()` → cascade cleared, column landed.
Safe-DDL fix: `SET lock_timeout` so the migration yields instead of holding the queue.

### §A2 — Deadlocks ✅
A deadlock is a **cycle** in the wait-for graph (vs §A1's chain, which has a head and
drains). Postgres **detects, not prevents**: a blocked backend waits `deadlock_timeout`
(default 1s) then runs cycle detection; on a cycle it aborts a **victim** (full
transaction rollback, SQLSTATE `40P01`), the rest proceed. App contract: catch `40P01`
and **retry**. Prevent with **consistent lock ordering**.
Demonstrated: s1 updates rows 1→2, s2 updates 2→1; bumped `deadlock_timeout=20s` to catch
the cycle live in `pg_blocking_pids` (s1 `blocked_by{s2}` AND s2 `blocked_by{s1}` — no
head). Detector fired → s1 victim, `ERROR: deadlock detected` with `DETAIL` printing the
two `transactionid ShareLock` waits (xids 995/996, allocated lazily at first UPDATE);
s1's *entire* txn rolled back (its row-1 write vanished), s2 committed both rows.

### §A3 — Hot-row contention at scale ✅
Built a Go load generator (`stress/`, lib/pq, pooled conns). Same 16 workers, 5s:
**contended** (all hit row 1) ≈ 2249 tps vs **spread** (each its own row) ≈ 9679 tps →
**4.3× from key distribution alone**. Wait-event snapshot of contended: 14/16 backends
`active` but parked in `Lock`/`transactionid`, 1 in `IO`/`WalSync` (the committer mid-fsync)
— the "busy but idle CPU" signature. **Negative scaling:** 8→64 workers *dropped* tps
2578→1366 (~47%).
Deep-dived the OS mechanism (Pavel needed fundamentals, see [[feedback_explain_os_fundamentals]]):
process states Running (≤cores) / Ready (run queue) / Sleeping (off-queue, 0 CPU); a lock
waiter is Sleeping on a futex; on release Postgres wakes waiters into Ready (not batches of
8 — that's the OS rationing cores); woken losers re-check, find the row re-taken, go back to
Sleeping = wasted wake+context-switch. Per handoff: 1 useful update + N wasted
wake/schedule/re-sleep cycles → bigger crowd = less throughput. Fix: shorter holds / shard
the key, never more workers. Also covered `fsync` (force WAL durably to disk; the ~0.4ms
hold) and LWLocks (cheap short locks guarding in-memory structures: buffer content lock per
page, 16 lock-partition LWLocks for the lock table).

### §A4 — Connection overload ✅
Each connection = an OS process (Ch 4/12). Three growing costs: memory (~MBs each →
GBs), scheduling churn (more Ready procs than cores → context-switch overhead), and the
Postgres-specific one — the **ProcArray snapshot scan**: every query builds an MVCC
snapshot by scanning a list with one slot per connection, so more connections (even idle!)
slow every active query. `max_connections=100`: low cap → hard `FATAL: too many clients`;
high cap → self-reinforcing slow-death. **Throughput hump measured** (pgbench -S, 16
cores): rose 18k→peak ~165k tps @64 clients → declined to 130k @90. **tps = concurrency /
latency** (Little's Law); past the peak, latency rises faster than client count → tps
falls. Hence **fewer connections can mean higher throughput**; fix = pooling (hold excess
*outside* the DB where waiting is cheap). Methodology: pgbench `-S` = one PK SELECT per
txn, tps = txns/duration; warmed cache; pgbench co-located (numbers approximate, shape
robust).

### §A5 — Cascading failure (theory only, by choice) ✅
A shared bounded pool couples everything. Little's Law: a small hold-time rise (slow query,
lock wait, vacuum) drains the pool — a 10%-of-traffic endpoint slowing 5ms→200ms needs 40
conn-seconds/s against a 20-conn pool, saturating it and starving the healthy 90%. Local
problem → global outage via resource coupling. Feedback loops make it self-sustaining
(metastable): DB slows further under pile-up (§A4), retry amplification, health-check
restarts shrink the fleet. Defenses map to: cap hold-time (timeouts), bound the queue
(load shedding / fail fast), un-share the resource (bulkheads = per-workload pools), plus
circuit breakers + retry budgets (Ch 10b). Recovery usually needs deliberate load shedding.

## Part B — Breaking Postgres (on disposable `pg-victim`, port 5444, own volume)

### §B1 — Crash recovery ✅
`kill -9` the postmaster mid-transaction (`docker kill --signal=KILL` → `Exited (137)`),
restart → automatic recovery. Log: "database system was **not properly shut down**;
automatic recovery in progress" → "**redo starts at** 0/1BEF608" (the last checkpoint's
redo lsn — recovery starts at the checkpoint, NOT the beginning) → "**redo done at**
0/1C1D230" (replayed forward *past* our pre-crash commit LSN 0/1C1D158) → end-of-recovery
checkpoint → ready. **Durability:** the 3 committed rows survived — restored by *replaying
WAL*, even though the crash gave no chance to flush their data pages. **Atomicity:** the
uncommitted row vanished — and Postgres did this with **REDO-only, no undo pass**: the
row's `xid` isn't committed in CLOG, so MVCC simply hides it. Checkpoint frequency =
recovery-time dial (less WAL after the checkpoint = faster redo). FPIs (Ch 9) would heal
torn pages, though our tiny WAL didn't exercise that.

### §B2 — Disk full during writes (theory only)
Postgres fails **safe**, not corrupt. **Data-dir full:** write txns `ERROR: ... No space
left on device`, reads fine, server stays up; free space → writes resume (no loss). **`pg_wal`
full:** far worse — every write needs WAL first, so it `PANIC`s; if still full on restart it
may not even start (recovery itself writes WAL). `pg_wal` fills mainly from a **stuck
replication slot** (dead replica retaining WAL), failed `archive_command`, or stalled
checkpoints — not raw volume. Safe-not-corrupt because WAL+fsync means a failed write simply
isn't committed. Defenses: separate `pg_wal` volume, **`max_slot_wal_keep_size`** (PG13+
valve: invalidate a slot rather than fill the disk), monitor slots/disk, keep a ballast
file; **never** hand-delete WAL.

### §B3 — Transaction ID wraparound (theory only)
32-bit xids wrap at 2³². Visibility is **circular**: a tuple is "past" (visible) only while
its xid is ≤ 2³¹ behind the counter; **> 2³¹ behind flips it to "future" → invisible** =
apparent data loss. (Worked example: counter `N+2³¹`, row `xmin = N−1` is `2³¹+1` behind →
just over the line → invisible.) **Freezing** removes old tuples from the race — a flag
means "always visible, ignore xmin" (modern PG keeps the real xmin for forensics; freezing
also enables CLOG/old-WAL truncation). `autovacuum_freeze_max_age = 200M` forces an
anti-wraparound vacuum (~10× margin before the ~2.1B cliff) and runs even if autovacuum is
"off". Emergencies come from something **pinning the xmin horizon** (long/idle-in-txn,
orphaned slot, prepared txn). At the edge Postgres **refuses new xids (write-read-only)** to
avoid real wraparound. Monitor `age(datfrozenxid)`.

### §B4 — Corruption & recovery tools (theory only)
Corruption = the bytes on disk lie, so WAL replay can't help (its inputs are damaged).
Causes: bad hardware, **lying `fsync`** (acks without persisting — the deadliest), operator
error. **Detect:** data checksums (per-page; default ON in PG18 — turns silent corruption
into a loud error; detect ≠ repair) + `pg_amcheck` (heap/index consistency). **Recover**
(least→most destructive): **restore from backup + PITR (Ch 11) — the real cure**; `REINDEX`
(indexes are derived data); `zero_damaged_pages` (lossy salvage — zeroes bad pages so you
can dump the rest); `pg_resetwal` (foot-gun — force-starts, loses un-replayed WAL, can leave
it inconsistent → dump & rebuild after, never keep running it); single-user mode. Tools
**detect + salvage**; the cure is a **tested backup**. Prevent: checksums, ECC, honest
storage, tested restores, don't touch the data dir.

---

## Retro

Capstone that stress-tested and then deliberately broke everything from the whole module.

**Part A — Under Fire (all hands-on):** lock modes + the FIFO lock queue (a blocked DDL
freezes compatible reads behind it; `pg_blocking_pids` → root, `lock_timeout` as the safe-DDL
fix); deadlocks (cycle vs chain, lazy detection, victim + retry); hot-row contention (built a
Go load generator — 4.3× spread-vs-contended, negative scaling 8→64 workers); connection
overload (the throughput hump via pgbench + Little's Law); cascading failure (Little's Law on
the pool, theory). **Part B — Breaking (B1 hands-on, B2–B4 theory):** crash recovery
(`kill -9` → WAL replay, durability+atomicity proven), disk-full, wraparound, corruption.

**The richest moment** was the deep OS dive in §A3 — process states (Running/Ready/Sleeping),
context switches, futex wakeups, LWLocks, `fsync` — built from fundamentals after Pavel
flagged the jargon. That became a durable rule ([[feedback_explain_os_fundamentals]]).

**Working-style shifts this chapter:** agent runs experiments itself and pastes output inline
(reversed from hands-by-user), strictly one step at a time, and low-level internals must be
built up, not name-dropped.

**Pivots:** Pavel merged the planned "under fire" + "breaking Postgres" chapters into one
finale, and chose to finish Part B as theory once the hands-on rhythm was set — and to **end
the PostgreSQL module here**. **What I'd change:** the B2 disk-full practice (size-capped FS →
hit "No space left") was set up conceptually but deferred; worth doing if we ever resume.

**Connections:** this chapter wove together locks/isolation (Ch 10), MVCC + freezing
(Ch 3/7), vacuum (Ch 8), WAL + crash recovery (Ch 9), connections/pooling (Ch 12),
replication slots (Ch 11). A fitting close — every layer of the engine, observed under stress
and failure. **PostgreSQL module COMPLETE.**
