# Chapter 12: Connections & PgBouncer

## Plan

### 1. What a connection actually is
TCP socket + forked backend process. Postmaster accept() → fork() → child handles client.

### 2. Why connections are expensive
Fork cost, private memory (5-10 MB per backend: catalog cache, work_mem, parser state), proc array registration (scanned on every snapshot — more connections = slower snapshots for everyone).

### 3. Connection limits
max_connections (default 100), instant rejection at limit, superuser_reserved_connections for emergency access. Can't set high — memory and proc array overhead.

### 4. Wire protocol
Binary protocol: [type byte][4-byte length][payload]. Startup message (no type byte), authentication (trust/MD5/SCRAM-SHA-256), simple query protocol (Q → T → D → C → Z), extended query protocol (Parse/Bind/Describe/Execute/Sync — enables prepared statements).

### 5. pgvis wire tool
Built raw socket PostgreSQL client showing every byte decoded. Startup message, SCRAM handshake (4 round trips, zero-knowledge proof), query/response. Both simple and extended protocols.

### 6. Python adapters
psycopg2 wraps libpq (C). psycopg3 can use libpq or pure Python wire protocol. execute() translates to extended protocol messages. Type adapters convert PostgreSQL types to Python objects.

### 7. Gunicorn + SQLAlchemy
Engine and pool are per-process. Sync workers: 1 request at a time, pool_size=1 is enough. Async/threaded workers: concurrent requests need pool. Preload fork problem: shared sockets, need engine.dispose() in post_fork.

### 8. Connection pooling internals
Amortize 7ms connection cost. Pool queue: checkout/return, health checks, state cleanup (DISCARD ALL/RESET ALL). Pool sizing: match to worker concurrency.

### 9. PgBouncer
Server-side pooling: shared across all app instances. Session mode (1:1, useless), transaction mode (connection per transaction, the useful one), statement mode (per statement, breaks transactions). What breaks in transaction mode: prepared statements, SET, temp tables, advisory locks, LISTEN/NOTIFY.

### 10. Cloud SQL architecture
Cloud SQL instance = managed VM running PostgreSQL. Cloud SQL Auth Proxy = sidecar per pod, NOT a pooler (1:1 pass-through, handles auth/encryption). Built-in connection pooling = managed PgBouncer on the instance, enabled via config flag.

### 11. Connection sizing
max_useful_connections ≈ queries_per_second × avg_query_time. Pool size ≈ 2-3× CPU cores. Fewer focused connections outperform many context-switching ones. Pooler queues requests when busy — delay is better than rejection.

---

## Findings

### Connection = TCP socket + OS process
The postmaster listens, accepts connections, forks a child process per client. Each backend holds private memory (catalog cache, parser state, work_mem). Proc array scanned on every snapshot — 200 idle connections slow down every active query's snapshot creation.

### Wire protocol
Built a raw socket PostgreSQL client (pgvis wire) that shows every byte. The startup message is the only message without a type byte. SCRAM-SHA-256 is a 4-message handshake where neither side sends the password — zero-knowledge proof using PBKDF2 (4096 iterations), HMAC, and XOR. Mutual authentication: server proves it knows the password too.

Simple query protocol: 1 client message (Query) → results. Extended query protocol: 5 client messages (Parse/Bind/Describe/Execute/Sync) → enables prepared statement reuse but breaks PgBouncer transaction mode.

### Python adapter architecture
psycopg2 wraps libpq (C library). psycopg3 can work pure Python — implements the wire protocol directly (same as pgvis wire). execute() generates Parse/Bind/Execute messages, type adapters convert wire format to Python objects.

### Gunicorn + SQLAlchemy per-process pools
Each gunicorn worker is a separate OS process with its own SQLAlchemy engine and connection pool. Pools don't share across processes. For sync workers (1 request at a time), pool_size > 1 is wasteful. For async/threaded workers (concurrent requests), pool must match concurrency.

Preload fork problem: if engine is created before fork, child processes inherit socket file descriptors. Multiple processes on the same TCP connection = corruption. Fix: engine.dispose() in post_fork hook.

### PgBouncer transaction mode
Assigns a PostgreSQL connection per transaction, returns it between transactions. 600 client connections can share 20 server connections. What breaks: prepared statements (PREPARE on connection A, Bind on connection B), SET commands, temporary tables, advisory locks — all session state is lost between transactions.

### Cloud SQL architecture
Cloud SQL instance = managed VM with PostgreSQL. Cloud SQL Auth Proxy is a sidecar per pod — handles IAM auth and encryption but does NOT pool (1:1 pass-through). Built-in connection pooling = managed PgBouncer on the instance, enabled via config flag, transparent to the app.

### Connection sizing math
4-vCPU instance, 5ms queries, 8000 req/s with 2 queries each = 16,000 qps. Need ~80 connections doing active work (16000 × 0.005). Pooler at 12 connections can only serve 2,400 qps — undersized. Pool must match actual query throughput, database must have CPU to handle it.

Rule: max_useful_connections ≈ qps × avg_query_time. Pool size ≈ 2-3× database CPU cores as starting point, adjust based on actual query throughput.

### Tools built
- `pgvis wire "SQL"` — raw socket PostgreSQL client with decoded bytes for every message
- `pgvis wire --extended "SQL"` — same with extended query protocol (Parse/Bind/Execute)

---

## Retro

### Summary
Connections deep dive from TCP socket to production architecture. Connection lifecycle (postmaster fork, startup handshake, SCRAM-SHA-256 authentication), wire protocol (simple and extended, built pgvis wire tool showing every byte), Python adapter internals (psycopg2 vs psycopg3), gunicorn worker model and per-process pools, PgBouncer (transaction mode and its limitations), Cloud SQL architecture (instance = managed VM, auth proxy ≠ pooler, built-in pooling), connection sizing math.

### Key Takeaways
- A connection = TCP socket + forked OS process + private memory + proc array slot. Even idle, it costs resources and slows snapshots.
- The wire protocol is simple: type byte + length + payload. pgvis wire proves you can build a PostgreSQL client from scratch with raw sockets.
- SCRAM-SHA-256 is a zero-knowledge proof — password never crosses the wire. Mutual authentication — server proves itself too.
- Gunicorn sync workers need pool_size=1. Larger pools are wasted. Async/threaded workers need pools matching their concurrency.
- PgBouncer transaction mode solves the connection multiplication problem but breaks session state (prepared statements, SET, temp tables).
- Cloud SQL Auth Proxy is NOT a pooler. Built-in Cloud SQL pooling is.
- Fewer focused connections > many context-switching connections. 2-3× CPU cores is the starting point for pool sizing.

### Connections
- **Ch 4 (Architecture)**: process model, fork-per-connection, ~7ms connect cost — now understood at the wire protocol level.
- **Ch 7 (MVCC)**: proc array scanned for snapshots — more connections = slower snapshots.
- **Ch 8 (Vacuum)**: idle-in-transaction blocks VACUUM via snapshot pinning.
- **Ch 10 (Isolation)**: READ COMMITTED takes snapshot per statement — every statement scans proc array.
- **Ch 11 (Replication)**: replication uses the same wire protocol (streaming replication protocol is an extension of the standard protocol).

### Open Questions
- How does PgBouncer handle server connection failures and reconnection?
- What does pgpool-II do differently from PgBouncer?
- How does AlloyDB's connection handling differ architecturally?
