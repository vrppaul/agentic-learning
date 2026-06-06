# Chapter 11: Replication

## Plan

### 1. The single server problem
Hardware failure, read scaling, geographic distribution. One server isn't enough.

### 2. How PostgreSQL replicates — WAL shipping
Streaming replication: ship WAL records to a standby, replay them. The standby is a continuously-recovering server.

### 3. Base backup
pg_basebackup takes a "fuzzy" file copy (pages from different moments) + records the WAL position. WAL replay makes it consistent. The replica needs a starting point before it can stream.

### 4. Replication slots and WAL retention
Slots prevent the primary from recycling WAL that a replica hasn't consumed. Without slots or archiving, WAL gaps prevent replica creation. pg_basebackup creates a fresh starting point with no gap.

### 5. Hot standby — reads on the replica
The standby accepts read-only queries. Query conflicts: WAL replay (e.g., VACUUM) can remove tuples a standby query needs. Three solutions: cancel the query (default after 30s), delay replay, or hot_standby_feedback.

### 6. Synchronous vs asynchronous
Async (default): primary doesn't wait for standby. Fast, but data loss possible on failover. Sync: primary waits for standby confirmation. No data loss, but commits are slower and standby outage freezes the primary.

### 7. Failover
Kill primary, promote standby with pg_promote(). Risks: data loss (async), split brain (old primary not dead). Timeline IDs prevent divergent WAL from mixing. Orchestration tools (Patroni) handle detection, promotion, and replica reconfiguration.

### 8. Logical replication
Decodes physical WAL into logical changes (INSERT, UPDATE, DELETE). Publication/subscription model. Use cases: selective table replication, cross-version upgrades, CDC. Limitations: no DDL replication, no sequences, basic conflict handling.

### 9. Read replicas in practice
Connection routing (application-level, proxy, DNS). Read-your-writes problem: user writes, reads from replica that's behind, doesn't see their data.

### 10. PITR — Point-in-Time Recovery
Base backup + archived WAL + target timestamp. Recover to any second. The "undo" button for production mistakes (DROP TABLE, bad UPDATEs). One full backup + continuous WAL archiving = fine-grained recovery without frequent full backups.

---

## Findings

### Streaming replication mechanics
The standby has two processes: WAL receiver (TCP connection to primary, saves WAL to local pg_wal/) and recovery process (reads WAL and replays to shared_buffers pages). Same replay logic as crash recovery, but continuous. Restartpoints (standby checkpoints) flush dirty pages to disk.

### Base backup is a fuzzy copy
pg_basebackup copies data files while the primary is running. Different pages are from different moments. WAL replay from the backup start LSN brings everything to consistency. The primary retains WAL during the backup via a temporary replication slot.

### Replication slots
Track which WAL position each replica has consumed. Prevent WAL recycling. Risk: downed replica causes WAL accumulation on primary. Slots protect existing replicas; new replicas use pg_basebackup (fresh copy, no gap).

### Query conflicts on standby
WAL replay (especially VACUUM) can physically remove tuples that a standby query's snapshot still needs. The primary's VACUUM decisions are based on the primary's snapshot horizon, which doesn't include standby queries. Three solutions: cancel query (max_standby_streaming_delay, default 30s), delay replay (lag grows), hot_standby_feedback (standby sends xmin to primary, but can hold back primary's VACUUM).

### Async vs sync
Async: primary commits immediately, WAL ships in background. Demonstrated data loss: stopped replica, inserted data on primary, killed primary, promoted replica — committed data was missing. Sync: primary waits for standby to confirm WAL receipt before returning COMMIT OK. No data loss, but every commit adds network latency and standby outage freezes the primary.

### Failover
Demonstrated: killed primary with docker kill, promoted replica with pg_promote(), replica accepted writes. Timeline ID incremented to prevent divergent WAL. In production, orchestration tools (Patroni) handle detection, fencing old primary, promoting replica, reconfiguring other replicas.

### Logical replication
Decodes physical WAL into logical operations using table schemas. Publication/subscription model. Enables selective table replication, cross-version replication, CDC (Debezium). Unlike streaming replication, doesn't replicate DDL, sequences, or large objects.

### PITR
Base backup + continuous WAL archiving + recovery_target_time. Restore the backup, replay archived WAL, stop at the target timestamp. Enables recovery to any second without frequent full backups. Requires unbroken WAL archive — one missing segment breaks the chain.

---

## Retro

### Summary
Replication from the ground up: why single servers aren't enough, streaming replication as continuous WAL replay, base backup mechanics (fuzzy copy + WAL consistency), replication slots, hot standby with query conflicts, sync vs async tradeoffs, failover and promotion, logical replication for selective/cross-version replication, read replicas with routing and lag considerations, PITR for disaster recovery. Hands-on: set up primary + replica with Docker, demonstrated data flowing, failover with promotion, and async data loss.

### Key Takeaways
- Streaming replication = continuous crash recovery on another server. Same WAL, same replay, just remote and non-stop.
- Base backup is NOT a consistent snapshot — it's a fuzzy file copy made consistent by WAL replay.
- Async replication can lose committed transactions on failover. The data loss window = replication lag.
- Query conflicts on standbys arise because the primary's VACUUM decisions don't account for standby snapshots.
- Failover is simple (pg_promote), but safe failover requires fencing the old primary (split brain prevention).
- PITR = one full backup + continuous WAL archiving = recovery to any second. Cheaper than frequent full backups.
- PostgreSQL handles replication mechanics. Discovery, failover decisions, and routing are external problems (Patroni, proxies, DNS).

### Connections
- **Ch 9 (WAL)**: replication is built directly on WAL. Streaming replication ships the same records crash recovery replays.
- **Ch 8 (Vacuum)**: VACUUM WAL records cause query conflicts on standbys. hot_standby_feedback connects standby snapshots to primary VACUUM decisions.
- **Ch 7 (MVCC)**: standby queries use the same snapshot/visibility mechanism as the primary.
- **Connections chapter (future)**: PgBouncer and connection routing for primary/replica splitting.
- **CDC (future)**: logical replication as the foundation for change data capture.

### Open Questions
- How does Patroni consensus work for leader election?
- What does cascading replication look like (replica feeds another replica)?
- How does logical replication handle conflicts in multi-primary setups?
