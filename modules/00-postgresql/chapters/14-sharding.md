# Chapter 14: Sharding

> **STATUS: PAUSED (2026-06-17).** Deferred mid-chapter — least immediately useful for the
> user right now. Done: §1 (theory, full), §2 (theory + practice: postgres_fdw wired up
> coordinator→shard1, pushdown verified). §3 has full theory written but no practice; §4–§7
> not started. Lab (`docker-compose.sharding.yml`, 3 nodes) is reproducible from scratch.
> Resume at "§3 practice" — see Findings below for exact state.

## Plan

Hands-on, multi-instance. Where Ch 13 (partitioning) ended — one Postgres, vertical scale
— this chapter crosses the line to **many Postgres instances, horizontal scale**. The
whole chapter is really about one fact: **once data lives on separate instances, the
single-node guarantees we leaned on all study (one snapshot, one WAL, local joins,
one lock table) no longer hold.** Every section is a consequence of that.

We build up from the raw primitive (`postgres_fdw`) to a DIY distributed table, expose
the hard problems directly, then see how Citus and app-level sharding handle them.
Lab = several Postgres containers via Docker Compose. User builds it himself.

### 1. Why & when to shard (and when NOT to)
The problem: data volume, write throughput, or working-set size exceeds one machine.
The cost: distributed-systems problems you didn't have before. **Sharding is a last
resort** — first exhaust a bigger box, partitioning (Ch 13), and read replicas (Ch 11).
The shard-key decision is even more fateful than the partition-key one: getting it wrong
means cross-shard everything, and resharding is brutal. Frame the three approaches:
foreign-data wrappers, Citus, app-level.

### 2. The primitive: `postgres_fdw`
One Postgres instance querying another's table as if it were local. Stand up two
instances; on instance A create a `FOREIGN TABLE` backed by a table on instance B.
Concepts: foreign server, user mapping, `IMPORT FOREIGN SCHEMA`. Watch a query cross the
network. The key question we keep asking: **what gets pushed down to the remote vs pulled
back and processed locally** (`EXPLAIN VERBOSE` shows `Remote SQL`). This is the building
block native sharding is made of.

### 3. DIY distributed table: partitioning + FDW
Combine Ch 13 with §2: a `PARTITION BY HASH` (or RANGE) table whose **partitions are
foreign tables living on different instances**. Now partition pruning decides not just
which partition but which *server* to hit. This is "declarative sharding" — distributed
Postgres by hand. Demonstrate: insert routes to the right shard, a key-filtered SELECT
prunes to one shard, an unfiltered SELECT fans out to all. See the limits: aggregate
pushdown, and where joins fall apart.

### 4. The hard problems, made concrete
The payoff section — show the broken guarantees live:
- **Cross-shard joins / aggregates** — what `postgres_fdw` pushes down vs what it can't;
  the cost of pulling rows back to the coordinator to join.
- **No global snapshot** — a query spanning two shards reads two *independent* snapshots
  (Ch 3/7 MVCC was per-instance). Cross-shard reads aren't a consistent point-in-time.
- **Distributed transactions** — writing two shards atomically. `PREPARE TRANSACTION` /
  `COMMIT PREPARED` (two-phase commit). Demonstrate 2PC, then the failure mode: kill the
  coordinator mid-commit and find an orphaned prepared transaction holding locks.

### 5. Citus — productized distributed Postgres
The extension that automates §3–§4. Coordinator + worker nodes. `create_distributed_table`
(hash-distributed by a shard key), shards as the unit of distribution, **reference tables**
(replicated everywhere for joins), co-location, the shard rebalancer. Stand up a Citus
cluster in Docker, distribute a table, watch routing and a parallel cross-shard aggregate.
Compare honestly to the DIY FDW version. Caveat: it's an extension — not on plain RDS /
Cloud SQL; needs self-host or Azure Cosmos DB for PostgreSQL.

### 6. App-level sharding
Routing in the application instead of the database. Shard-key selection, the shard map /
directory, consistent hashing to limit movement on resize (callback to Ch 13's HASH
discussion — *this* is where consistent hashing actually earns its place). Resharding
pain. When you'd pick this over Citus (precise control, polyglot, cross-service). Likely a
small **Go** shard-router to make it concrete (project default language).

### 7. Choosing & operating
Decision guide: bigger box → partition → read replica → shard, in that order. Shard-key
choice and hotspots. Cross-shard query cost as the tax you pay forever. Rebalancing.
What breaks operationally (backups, schema migrations, monitoring across N instances).

### Tool (decide later, per Ch 13's lesson)
Unlike partitioning, cross-instance routing genuinely *is* hard to see — a small tool that
shows "shard key → shard → instance" and what a cross-shard query fans out to could earn
its keep. Possibly the Go router from §6 doubles as it. Won't build for the sake of it.

### Lab
`docker-compose.sharding.yml` — 2–3 plain Postgres instances for §2–§4; a separate Citus
coordinator+workers compose for §5.

### Out of scope (later chapters / modules)
- General distributed-systems theory (consensus, Raft, CAP) → future distributed-systems
  module; here we stay concrete to Postgres.
- Breaking Postgres / chaos under load → its own chapter.
- Multi-region / geo-distribution latency tuning → mention only.

---

## Findings

### §1 — Why/when/cost (theory)
Sharding = horizontal partitioning across *separate instances* (vs replication = full
copies for read scaling/HA; partitioning = split within one instance). Only sharding
scales **writes** and total dataset beyond one box. **Last resort** — climb the ladder
first: bigger box → partition (Ch 13) → read replicas (Ch 11) → cache. The real cost: every
single-node guarantee breaks — no global snapshot, no atomic cross-shard txn (needs 2PC),
cross-shard joins expensive, global unique only if key contains shard key, N-of-everything
operationally. Shard key is the most fateful decision: high cardinality, even (no monotonic
hotspots), present in queries (else scatter-gather), co-locates related data (classic:
`tenant_id`). Routing: range / hash / directory.

### §2 — postgres_fdw (theory + practice) ✅
Four objects: `CREATE EXTENSION postgres_fdw` → `CREATE SERVER` (coordinates) →
`CREATE USER MAPPING` (local role → remote creds) → `CREATE FOREIGN TABLE` (storage-less
proxy). Built coordinator→shard1 for an `orders` table; queried it transparently from the
coordinator. **Pushdown** (verified via `EXPLAIN (VERBOSE)` → `Remote SQL:`): predicate
(`WHERE` ran on shard1) + column (only referenced cols shipped). Node type `Foreign Scan`
signals execution left the box. Pushdown FAILURE (non-shippable expr) → silently drags all
rows back, filters locally = the perf cliff; reading `Remote SQL` is the reflex.
**Key gap (sets up §4):** FDW gives distributed *queries* but NOT atomic distributed
*commit* — no 2PC in core; crash between remote and local commit = split-brain.

### §3 — Distributed table = partitioning + FDW (THEORY ONLY — practice not done)
Make each partition a foreign table on a different shard (`CREATE FOREIGN TABLE … PARTITION
OF parent … SERVER shardN`). Coordinator holds storage-less parent + foreign partitions;
shards hold real slices. Write: routing becomes network routing. Read: key-equality →
single-shard (prune to one server); non-key → scatter-gather (Append over all shards).
This is what Citus (§5) automates. **Resume practice here.**

### Lab state at pause
`docker-compose.sharding.yml`: 3 containers (shard-coordinator:5441, shard-node1:5442,
shard-node2:5443) on `shardnet`. shard1 has a real `orders` table (4 rows); coordinator has
postgres_fdw + server `shard1` + user mapping + foreign table `orders`. shard2 untouched.
Fully reproducible — fine to `docker compose -f docker-compose.sharding.yml down` and rebuild.

---

## Retro

_(filled in after the chapter)_
