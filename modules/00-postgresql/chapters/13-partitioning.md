# Chapter 13: Partitioning & Tablespaces

## Plan

Use-case driven: we learn the mechanics through the three real-world scenarios that
account for almost all production partitioning. Theory first for each, then hands-on
against the Docker Postgres lab. One session, theory + practice.

### Framing: the problem partitioning solves
Why one giant table hurts: vacuum must scan the whole heap (Ch 8), indexes get deep
(Ch 6), queries touch pages they don't need, and deleting old rows is an expensive
DELETE that leaves bloat behind (Ch 3/7). Partitioning splits one logical table into
physical pieces. The win comes entirely from **partition pruning** using the partition
key — pick the key wrong and every query gets slower, not faster.

### Use Case A — Time-series with retention (RANGE) — the main event
The canonical case: events/logs/metrics tables that grow forever and need old data
dropped. Covers: declarative `PARTITION BY RANGE`, parent-vs-child storage (each
partition is its own filenode/heap/index), tuple routing on INSERT, **pruning** in
`EXPLAIN` (plan-time vs execution-time), and the retention payoff — `DROP`/`DETACH` an
old partition = instant bulk delete, zero vacuum cost. Index & unique-constraint rules
(partition key must be in any unique index). This is most of the chapter.

### Use Case B — Multi-tenant & even spread (LIST / HASH)
LIST partitioning to isolate big tenants / regions / categories; HASH partitioning to
spread one enormous table evenly when there's no natural range key. The `DEFAULT`
partition catch-all and the no-overlap rule. When each type wins.

### Use Case C — Hot/cold storage tiering (tablespaces)
Recent partitions on fast disk, archive partitions on cheap disk. Teaches tablespaces
the practical way: what a tablespace is (a named directory pointer), the `pg_tblspc`
symlink farm, and setting a per-partition `TABLESPACE`. (Faked with two directories in
the container.)

### Where partitioning ends: sharding (just draw the line — full treatment in Ch 17)
Partitioning is one instance (vertical scale); sharding is many instances (horizontal).
In THIS chapter we only mark the boundary. Sharding gets its own dedicated hands-on
chapter (Ch 17): `postgres_fdw` across instances, Citus, app-level sharding, and the
hard problems (cross-shard queries, no global snapshot, distributed txns / 2PC), on a
multi-instance Docker setup. (User wants to build it himself.)

### Tool to build
`pgvis partition` — visualize the partition tree (parent → children with bounds), show
which partition a value routes to, list each partition's filenode/size, and surface
what `EXPLAIN` pruned. Build it early, study with it (visualization rule).

### Out of scope (later chapters)
- Stress/lock behavior under load → Ch "Postgres under fire"
- Partition-wise joins/aggregates → mention only if time permits
- Deep TOAST mechanics → dedicated TOAST chapter
- Replication of partitioned tables → covered conceptually in Ch 11

---

## Findings

### Use Case A — Time-series with retention (RANGE)

**Parent is virtual.** `CREATE TABLE ... PARTITION BY RANGE (created_at)` makes a table
with `relkind = 'p'`, no heap, no filenode — just a schema + routing rule. `\d+` labels
it "Partitioned table" and lists `Partition key: RANGE (created_at)`.

**Partitions are created by hand** in core Postgres (`CREATE TABLE child PARTITION OF
parent FOR VALUES FROM (..) TO (..)`). Bounds are `[lower, upper)`. No auto-creation —
pg_partman / TimescaleDB automate it. Date literals get normalized to full `timestamptz`
at the server TZ.

**Write = routing.** INSERT into the parent; PG reads the key, finds the one child whose
bounds contain it, writes there. Verified with `tableoid::regclass`. Rows for the same
month share one physical child.

**No matching partition → ERROR** ("no partition of relation found for row"), row stored
nowhere. Safety feature, and a real prod incident when the partition-creation cron dies.
Fixes: pre-create, or a `DEFAULT` catch-all partition.

**Read = pruning.** Filter on the partition key → planner drops partitions whose bounds
can't match *before* planning (plan-time pruning); a single surviving partition collapses
the `Append` away. Filter on a non-key column (`user_id`) → no pruning, `Append` scans
*all* partitions — slower than an unpartitioned table. **Core rule: the partition key
must match how you query.** Indexes cascade to all children and rescue non-key queries
(index scan per partition) but can't beat pruning.

**Retention.** `DETACH PARTITION` = O(1) catalog unlink, data survives as a standalone
table (archive-safe). `DROP TABLE` frees the filenode. Both constant-cost regardless of
row count — vs `DELETE` which sets xmax per row, floods WAL, leaves dead tuples for
vacuum (Ch 8). Production steady state = rolling window: create next, drop oldest.

### Use Case B — LIST & HASH

**LIST** — partition by discrete category (`PARTITION BY LIST (region)`). One partition
can own multiple values (`FOR VALUES IN ('us-east','us-west')`). `DEFAULT` partition
catches unlisted values so inserts never error. **DEFAULT tradeoff:** once rows sit in
DEFAULT, creating a new explicit partition for some of those values fails ("updated
partition constraint for default partition would be violated by some row") — PG must
scan + `ACCESS EXCLUSIVE`-lock DEFAULT to validate. High-churn tables often skip DEFAULT
and pre-create instead.

**HASH** — `PARTITION BY HASH (id)`, fixed buckets via `(MODULUS m, REMAINDER r)`. Routes
by `hash(key) mod m`. 10k sequential ids → ~2500 per bucket (even spread, locality
destroyed on purpose). For operational wins (smaller indexes, parallel vacuum, spread
across disks), not category queries.
- **Plain modulo hashing, NOT consistent hashing.** That's why changing modulus is
  costly. Partial escape: split one bucket into multiples (MODULUS 4 R0 → MODULUS 8
  R0+R4) — only that bucket's rows move; moduli can be mixed if they tile the space.
- **Pruning: `=`/`IN` only.** `WHERE id = 42` → 1 partition. `WHERE id > 9000` → Append
  over ALL partitions (hashing kills range locality). HASH is wrong if you range-scan the
  key.
- No retention-by-drop (partitions have no time/category meaning).

**Pick-a-type:** RANGE = continuous + retention (time-series). LIST = discrete category /
multi-tenant. HASH = even spread by id, equality lookups only.

### Use Case C — Hot/cold tiering with tablespaces

A tablespace = named pointer to a filesystem directory. Built-ins: `pg_default` (base/
in PGDATA), `pg_global` (shared catalogs). `CREATE TABLESPACE name LOCATION '/path'`
drops a symlink `$PGDATA/pg_tblspc/<tblspc_oid> -> /path`. Files then live at
`pg_tblspc/<oid>/PG_<ver>_<catver>/<db_oid>/<relfilenode>` — same `<db_oid>/<relfilenode>`
layout as base/ from Ch 1, just relocated.

**Hot/cold:** `ALTER TABLE <partition> SET TABLESPACE cold_disk` ages an old partition to
a cheap disk. Two gotchas:
- It moves **only the heap, not the indexes** — must `ALTER INDEX ... SET TABLESPACE`
  separately. (Why TimescaleDB `move_chunk` takes both heap + index tablespace args.)
- It **physically rewrites** the relation under `ACCESS EXCLUSIVE` lock (a real byte copy,
  unlike DETACH's O(1) catalog flip) — do it in a maintenance window.

This is the native primitive TimescaleDB `move_chunk` / tiered storage automates.

---

## Retro

**What we did.** Use-case driven, hands-on against the `pg-study` container. Three real
scenarios end to end: time-series retention (RANGE), multi-tenant/even-spread (LIST +
HASH), hot/cold tiering (tablespaces). No new tool — Postgres's own `\d+`, `tableoid`,
`EXPLAIN`, and catalog views already make partitioning legible, unlike the binary/
recursive structures earlier chapters needed visualizers for.

**The one idea that ties it together:** a partitioned table is **routing on write,
pruning on read**, over a parent that stores nothing. Every benefit and every gotcha
flows from that:
- Pruning only fires when you filter on the partition key → *choose the key to match
  your queries* (the single most important decision). Wrong key = slower than no
  partitioning.
- Retention is cheap because dropping a partition is a catalog op, not a `DELETE`.
- HASH trades range-prunability for even spread; it's plain modulo, not consistent
  hashing, so resizing is expensive.

**Surprises worth keeping:**
- A single surviving partition collapses the `Append` away entirely — plan looks like a
  plain table.
- `SET TABLESPACE` moves the heap but **not** the index — a real footgun for tiering.
- DEFAULT/auto-creation: the "no partition found" error is a genuine prod incident class;
  pg_partman / TimescaleDB exist to own that risk. Every fancy feature (chunks,
  compression aside, move_chunk, retention policies) is automation over a native
  primitive we did by hand.

**What I'd change.** Could have shown the DEFAULT-fix (drain rows → add partition) and an
index-on-non-key-column rescuing the `user_id` query — both deferred for time. Fine as
follow-ups if they come up.

**Connections.** Heap/filenode + `base/<db>/<relfilenode>` layout (Ch 1) reappeared under
`pg_tblspc`. Pruning is a planner step (Ch 5) before scan costing. Cheap-drop vs
expensive-DELETE is the dead-tuple/vacuum story (Ch 8) and WAL volume (Ch 9). The
partitioning↔sharding line is the bridge to **Ch 17 (Sharding)** — where the single-node
guarantees (one snapshot, one WAL, local joins) we leaned on all study finally break.
