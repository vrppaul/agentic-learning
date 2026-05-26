# PostgreSQL — From Practitioner to Expert

## Goal

Go from "I use Postgres daily and know it well" to "I understand what Postgres is doing at every level — page layout, tuple lifecycle, WAL records, snapshot visibility, index internals, replication mechanics." The kind of understanding where you can diagnose weird production behavior from first principles, not just Stack Overflow.

## Questions

### Storage & Physical Layout
- What does a row actually look like on disk? What's in the tuple header besides data?
- How does TOAST decide when to compress or move data out-of-line? What are the TOAST strategies (PLAIN, EXTENDED, EXTERNAL, MAIN) and when does each apply?
- What's the relationship between the OS page cache and shared_buffers?
- How does page size (default 8KB) affect OLTP vs OLAP workloads? What's the tradeoff?

### Transactions & MVCC
- When two transactions read the same row, what exactly determines which version each sees?
- How does a snapshot get constructed, and what's the cost of a long-running transaction?
- What happens to the transaction ID counter over time — what's the wraparound problem?

### Vacuum & Bloat
- Why can autovacuum fall behind, and what are the real-world consequences?
- What's the difference between vacuum and vacuum full? When does each make sense?
- How does the visibility map interact with vacuum and index-only scans?

### WAL & Crash Recovery
- What actually goes into a WAL record? Is it physical or logical?
- How does crash recovery decide where to start replaying?
- What's the tradeoff between checkpoint frequency and recovery time?

### Isolation & Concurrency
- How does SERIALIZABLE actually detect conflicts? What's SSI (Serializable Snapshot Isolation)?
- What's the real performance cost of SERIALIZABLE vs READ COMMITTED under contention?
- Can advisory locks solve problems that row-level locks can't?

### Optimistic Concurrency Control
- How does OCC compare to pessimistic locking in practice — when does each win?
- Application-level OCC (version columns, conditional UPDATE ... WHERE version = N) — what are the edge cases?
- SSI as database-level OCC: Postgres proceeds optimistically then aborts on conflict — what does the abort rate look like under real contention?
- Retry strategies: when OCC aborts a transaction, what's the right backoff pattern?

### Indexes
- What does the inside of a B-tree page look like? How does a page split work?
- When does GIN beat GiST for the same data type, and why?
- Why can BRIN be orders of magnitude smaller than B-tree, and what's the catch?

### Replication
- What exactly gets shipped in streaming replication — WAL segments or individual records?
- How does logical replication decode WAL into logical changes?
- What determines replication lag, and what can you actually do about it?

### Process Architecture & Connection Management
- Why does Postgres fork a new process per connection instead of using threads? What are the tradeoffs?
- What processes make up a running Postgres instance? (postmaster, background writer, checkpointer, autovacuum, WAL writer, etc.)
- What's the real cost of a new Postgres connection (memory, process fork, catalog loading)?
- How does shared memory (shared_buffers, lock tables, etc.) work across processes?
- Why does PgBouncer transaction mode break prepared statements?
- Where's the breaking point — how many direct connections before Postgres degrades?

### High Load & Deadlocks
- How does Postgres detect deadlocks — what's the detection algorithm and how fast is it?
- What does lock contention actually look like under 1000 concurrent writers hitting the same rows?
- How does Postgres behave when every connection is blocked — what breaks first?
- What real-world access patterns cause deadlocks, and what does `pg_stat_activity` + `pg_locks` look like when it happens?
- How do slow queries create cascading failures under load?

### Breaking Postgres
- What happens when you kill -9 the postmaster mid-checkpoint? Mid-vacuum?
- What does Postgres do when the disk fills up during a write-heavy workload?
- How close can you get to transaction ID wraparound, and what does the emergency autovacuum look like?
- Can you corrupt a data file and still recover? What tools exist (`pg_resetwal`, `pg_amcheck`)?
- What happens when shared_buffers is set absurdly high or low?

### Extensions & Plugins
- What's the anatomy of a Postgres extension — control file, SQL script, shared library?
- How does the hook system work — what can you intercept (planner, executor, auth)?
- Can you write a background worker that runs continuously inside Postgres?
- What's the difference between a C extension and a procedural language extension?

## Prior Knowledge

- Solid daily Postgres user: schemas, queries, indexes, transactions, basic replication concepts
- Knows EXPLAIN ANALYZE but doesn't always know why the planner makes certain choices
- Understands MVCC conceptually ("old versions stick around") but hasn't observed it at the tuple level
- Has used PgBouncer but not benchmarked or stress-tested it
- Hasn't looked at WAL internals, page layout, or pageinspect

## Chapters

Detailed findings and retro for each chapter in `chapters/`:

1. [Physical Storage](chapters/01-physical-storage.md) ✅
2. [Shared Buffers & Read/Write Path](chapters/02-shared-buffers.md) ✅
3. [OS Page Cache, Double-Caching & Tuning](chapters/02b-os-page-cache.md) ✅
4. [Transactions & Basic Concurrency](chapters/03-transactions.md) ✅
5. [Postgres Architecture & Query Pipeline](chapters/04-architecture.md) ✅
6. [SQL Parsing Internals](chapters/04b-parsing-internals.md) (4b) ✅
7. [Query Execution & EXPLAIN](chapters/05-query-execution.md) ✅
8. [Join Internals](chapters/05b-join-internals.md) (5b) ✅
9. [Indexes](chapters/06-indexes.md) ✅
10. [MVCC Under the Hood](chapters/07-mvcc-internals.md) ✅
11. [Vacuum & Bloat](chapters/08-vacuum-and-bloat.md) ✅
12. [WAL & Crash Recovery](chapters/09-wal.md) ✅
13. Isolation levels & OCC
14. Replication
15. PgBouncer
16. Partitioning, tablespaces & sharding
17. Postgres under fire
18. Breaking Postgres
19. Writing a PostgreSQL extension
20. TOAST deep dive
21. Triggers & the rule system

## Tools

- `tools/pgvis/` — PostgreSQL internals visualizer (Python, Click, Rich, psycopg3)
  - `pgvis page <table> <N> [--all] [--no-data]` — heap page layout with infomask flags (hint bits, FROZEN)
  - `pgvis fsm <table> [--tree]` — free space map
  - `pgvis vm <table>` — visibility map
  - `pgvis buffers` — shared_buffers state
  - `pgvis locks` — lock contention and blocking
  - `pgvis clog [start end] [--last N]` — transaction commit status (CLOG)
  - `pgvis sql "QUERY"` / `pgvis sql -i` — pretty SQL output / interactive REPL with tx state
  - `pgvis explain "SQL"` — annotated EXPLAIN with cost breakdowns and strategy explanations
  - `pgvis join <type> <algo>` — interactive step-by-step join algorithm slideshow
  - `pgvis index tree <name>` — B-tree overview (depth, pages per level, sample entries)
  - `pgvis index page <name> {meta|root|leaf|internal|N}` — physical page layout (metapage, leaf, internal)
  - `pgvis index lookup <name> <value>` — full lookup trace with visual diagrams and MVCC check
  - `pgvis index range <name> <lo> <hi>` — range scan trace through leaf linked list
- `tools/sqlparse/` — SQL parsing pipeline (Go, pg_query_go)
  - `sqlparse parse "SQL"` — parse with real Postgres parser (pg_query_go)
  - `sqlparse lex "SQL"` — tokenize with custom lexer
  - `sqlparse myparse "SQL"` — parse with custom lexer + recursive descent parser
  - `sqlparse analyze "SQL" [...]` — parse + analyze against real DB catalog (shows cache hits/misses)
  - `sqlparse rewrite "SQL"` — parse + expand views (shows before/after trees)
