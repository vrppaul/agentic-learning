# Progress

## Current Focus

**PostgreSQL Deep Dive** (`modules/00-postgresql/`) — Chapters 1–12 complete.

## Completed

### Chapter 1: Physical Storage ✅ (2026-05-03)
Page layout, tuple structure, MVCC fields, relation forks (FSM, VM), filenode vs OID, VACUUM behavior. Built pgvis tool.

### Chapter 2: Shared Buffers & Read/Write Path ✅ (2026-05-04)
shared_buffers as page cache, EXPLAIN BUFFERS (hit vs read), ring buffer, clock-sweep eviction, dirty pages, checkpoint/bgwriter. Added `pgvis buffers`.

### Chapter 2b: OS Page Cache & Tuning ✅ (2026-05-04)
Why Postgres needs its own cache (shared memory, write ordering, pinning, locking), double-caching is unavoidable, page size alignment (8KB = 2× OS 4KB), 25% of RAM rule.

### Chapter 3: Transactions & Basic Concurrency ✅ (2026-05-05)
Transaction IDs (32-bit counter, virtual xids), xmin/xmax mechanics (INSERT/UPDATE/DELETE), CLOG, hint bits (reads dirtying pages), visibility rule, snapshots (xmin:xmax:xip_list), concurrent visibility experiments, row-level locking on concurrent writes, transaction ID wraparound and VACUUM FREEZE. Refactored pgvis into feature-based modules, added `pgvis sql` interactive REPL with panel-based TUI.

### Chapter 4: Postgres Architecture & Query Pipeline ✅ (2026-05-08)
Process model (postmaster, fork-per-connection, signals), connection lifecycle and measured cost (~7ms connect vs ~0.6ms query), backend private memory (work_mem per-operation multiplication), shared memory layout (Buffer Blocks/Descriptors split, XLOG, CLOG, AIO), background processes via pg_stat_activity, query pipeline (parser/analyzer/rewriter/planner/executor proved by triggering errors at each stage). Added timing to pgvis sql.

### Chapter 4b: SQL Parsing Internals ✅ (2026-05-08)
Built full pre-planner pipeline from scratch in Go: lexer (tokenizer), recursive descent parser, analyzer (with lazy catalog cache mirroring Postgres SysCache), rewriter (view expansion). Used pg_query_go to compare with real Postgres parser output. sqlparse tool with 5 commands: parse, lex, myparse, analyze, rewrite.

### Chapter 5: Query Execution & EXPLAIN ✅ (2026-05-11)
EXPLAIN output structure, cost model (verified by hand calculation), three scan types (Seq Scan, Index Scan, Bitmap Heap Scan) and the selectivity crossover, planner statistics (MCVs, histograms, pg_stats), three join strategies (Nested Loop, Hash Join, Merge Join), sorting (external merge vs quicksort vs top-N heapsort), work_mem effects, diagnosing bad plans. Built `pgvis explain` tool with step-by-step annotated output, strategy explanations, and cost formula breakdowns.

### Chapter 5b: Join Internals ✅ (2026-05-12)
Join algorithms visualized step by step: Nested Loop (O(N×M)), Hash Join (build + probe, O(N+M)), Merge Join (sort + merge). Join types (INNER, LEFT, RIGHT, FULL) orthogonal to algorithms. Built `pgvis join` interactive slideshow with ← → navigation, showing data, strategy, step-by-step execution, and when the planner picks each.

### Chapter 6: Indexes ✅ (2026-05-14)
B-tree internals: page layout (item pointers → index tuples, high key, special area), tree structure (depth 3 for 500K rows, fan-out ~350), lookup trace (root → internal → leaf → heap → MVCC). Metapage at block 0 (btm_root, btm_level, fastroot). How the planner picks an index: applicability filter → cost estimation (correlation modulates random vs sequential heap I/O) → path competition. Index-only scans + visibility map. Multi-column leftmost prefix rule. Partial indexes (skip writes for non-matching rows). Index types: Hash (O(1) but loses everything else), BRIN (min/max per block range, tiny), GIN (inverted for arrays/JSONB). Write amplification, MVCC interaction (no xmin/xmax in indexes, ghost entries). CREATE INDEX CONCURRENTLY (sort-then-build, two-pass, never blocks writes). Built `pgvis index` tools: tree, page (with metapage view), lookup, range.

### Chapter 7: MVCC Under the Hood ✅ (2026-05-20)
MVCC mechanics beyond chapter 3: tuple lifecycle on pages (ctid chains, dead tuples, slot reuse), HOT updates (HEAP_ONLY, HOT_UPDATED, KEYS_UPDATED flags — skip index maintenance when non-indexed columns change), full visibility algorithm (xmin/xmax + CLOG + hint bits + snapshot — "committed is not visible"), READ COMMITTED vs REPEATABLE READ snapshot pinning and VACUUM blocking, freezing (FROZEN flag bypasses xmin check, autovacuum thresholds, wraparound emergency), subtransactions (savepoints, ORMs creating them silently, 64-slot overflow cliff), multi-xact (FOR SHARE shared locks, XMAX_IS_MULTI when two transactions lock the same row). Fixed pgvis page to decode t_infomask2 flags.

### Chapter 8: Vacuum & Bloat ✅ (2026-05-21)
VACUUM internals: three-phase operation (heap scan → index cleanup → heap reclaim), visibility map (2-bit bitmap, _vm fork, enables page skipping and index-only scans), free space map (tree-of-trees with binary max-heaps, _fsm fork, O(log N) free page lookup). Autovacuum: threshold formula (50 + 20% × reltuples), worker limits, cost-based delay, why it falls behind on large tables. VACUUM variants: regular (online, doesn't shrink), FREEZE (freeze all tuples), FULL (exclusive lock, full rewrite). maintenance_work_mem controls batch size for multi-pass index scans. Production monitoring via pg_stat_user_tables.

### Chapter 9: WAL & Crash Recovery ✅ (2026-05-26)
Write-ahead logging: why it exists (atomicity + performance), the write-ahead rule, WAL record structure (header, block refs, main data, resource managers), FPI for torn page protection (73% of WAL volume on first write after checkpoint), COMMIT = WAL fsync, checkpoints (REDO point, recovery window, tradeoffs), crash recovery (replay from checkpoint, LSN comparison, idempotent), WAL segments (16 MB files, recycling), wal_level (minimal/replica/logical). Built pgvis wal tools: show, lsn, checkpoint, segments.

### Chapter 10: Isolation Levels ✅ (2026-05-27)
Three isolation levels with practical tradeoffs: READ COMMITTED (snapshot per statement, default, maximum concurrency), REPEATABLE READ (snapshot per transaction, catches same-row write conflicts via xmax), SERIALIZABLE (SSI — tracks read/write dependencies with SIREAD locks, catches write skew). Demonstrated each anomaly live: non-repeatable read, write skew. SSI internals: SIREAD locks, rw-conflict edges, dangerous structures (cycles). FOR UPDATE as pessimistic alternative — visualized lock contention with pgvis locks.

### Chapter 10b: Optimistic Concurrency Control ✅ (2026-05-27)
Pessimistic vs optimistic tradeoff. FOR UPDATE overhead: modifies tuple header (xmax + XMAX_LOCK_ONLY), dirties page, generates WAL. Version column pattern: read version → compute → UPDATE WHERE version = N. Retry storms under high contention (~5% threshold). Retry strategies: exponential backoff, jitter, limits. Why distributed systems prefer app-level OCC over SSI (cross-service, precise, explicit, flexible). Architecture first — short transactions regardless of concurrency strategy.

### Chapter 11: Replication ✅ (2026-06-01)
Streaming replication as continuous WAL replay. Base backup (fuzzy copy + WAL consistency). Replication slots, WAL retention. Hot standby: query conflicts (VACUUM vs standby queries), hot_standby_feedback. Sync vs async tradeoffs. Failover with pg_promote(). Logical replication (publication/subscription, CDC foundation). PITR (base backup + WAL archive + target timestamp). Hands-on: Docker primary+replica, data flowing, failover, async data loss demonstration.

### Chapter 12: Connections & PgBouncer ✅ (2026-06-06)
Connection lifecycle (TCP + forked process, startup handshake, SCRAM-SHA-256). Wire protocol (simple and extended, built pgvis wire tool showing raw bytes). Python adapters (psycopg2 libpq wrapper vs psycopg3 pure Python). Gunicorn + SQLAlchemy (per-process pools, sync vs async workers, preload fork problem). PgBouncer (transaction mode, what breaks). Cloud SQL architecture (auth proxy ≠ pooler, built-in pooling = managed PgBouncer). Connection sizing math (qps × query_time, 2-3× CPU cores).

## Next Up

- Partitioning, tablespaces & sharding, or other topics
