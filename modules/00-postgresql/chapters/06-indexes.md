# Chapter 6: Indexes

## Plan

### 1. What an index is physically
An index is a separate file (its own relfilenode, its own set of pages) that maps column values to heap TIDs (page, offset). It's maintained alongside the heap — every INSERT/UPDATE/DELETE must update the index too. Look at the files on disk, compare sizes.

*Hands-on:* Check `pg_relation_filepath` and `pg_relation_size` for a table and its indexes. See that they're separate files with separate page counts.

### 2. B-tree structure
The default and most common index type. A balanced tree where:
- **Leaf pages** hold the actual index entries: (key value, heap TID) sorted by key
- **Internal pages** hold pointers to child pages, used to navigate the tree
- **Root page** — the entry point, either a leaf (tiny index) or an internal page

Tree depth is typically 2-4 levels for millions of rows. Each level is one page read during a lookup.

*Hands-on:* Use `pageinspect` to look inside a B-tree index. Read the metapage (root location, tree depth), then walk from root to leaf. See the actual key values and TIDs.

### 3. Index page layout
Each B-tree page has the same 8KB structure as heap pages but different contents:
- Page header (24 bytes) — same as heap
- Special area — right-sibling pointer (leaf pages form a linked list for range scans)
- Item pointers → index tuples (key + TID)

Leaf pages are linked left-to-right so range scans can walk sideways without going back up the tree.

*Hands-on:* Use `bt_page_stats` and `bt_page_items` from pageinspect to inspect leaf and internal pages. See the key values, TIDs, and page links.

### 4. How a B-tree lookup works
Step by step: start at root, compare key, follow pointer to child, repeat until leaf. On the leaf, find the matching entry (or range of entries), follow TID to heap.

For range scans (`WHERE id > 100`): find the starting leaf entry, then walk the leaf linked list rightward until the condition no longer matches.

For `ORDER BY`: the B-tree is already sorted, so an index scan produces rows in order for free — no sort node needed.

*Hands-on:* Run a query with EXPLAIN that uses an index scan. Trace the lookup path manually through pageinspect pages. Verify the number of page reads matches the tree depth + 1 (heap).

### 5. Index-only scans and covering indexes
If the index contains ALL columns the query needs, Postgres can skip the heap entirely. But it still needs to check the visibility map — if a page isn't all-visible, it must fetch the heap page anyway.

A **covering index** (`CREATE INDEX ... INCLUDE (col1, col2)`) adds extra columns to the leaf entries without including them in the sort key. This enables index-only scans for queries that need those columns.

*Hands-on:* Create a covering index, run a query, see Index Only Scan. Check `Heap Fetches` in EXPLAIN ANALYZE — should be 0 if all pages are all-visible. Run VACUUM to set visibility bits, compare before and after.

### 6. Multi-column indexes and column order
A B-tree on `(a, b)` is sorted by `a` first, then `b` within each `a` value. This means:
- `WHERE a = 5` → uses the index (leading column)
- `WHERE a = 5 AND b = 10` → uses the index (both columns)
- `WHERE b = 10` → cannot use the index efficiently (not the leading column)

Column order matters. The "leftmost prefix" rule.

*Hands-on:* Create a multi-column index, test queries on different column combinations. See which ones use the index and which fall back to seq scan.

### 7. Partial and expression indexes
**Partial index** (`CREATE INDEX ... WHERE condition`) — only indexes rows matching the condition. Smaller, faster, good for filtering on a common predicate.

**Expression index** (`CREATE INDEX ... ON (lower(email))`) — indexes the result of an expression, not the raw column value. The query must use the same expression.

*Hands-on:* Create a partial index on active users, see the size difference vs full index. Create an expression index, test with matching and non-matching queries.

### 8. Other index types
Brief survey — when each beats B-tree:
- **Hash** — equality only, no range queries. Rarely better than B-tree in practice.
- **GIN** — inverted index for multi-valued columns (arrays, full-text, JSONB). One entry per element, points to all rows containing it.
- **GiST** — generalized search tree for geometric/range data. Supports containment, overlap, nearest-neighbor.
- **BRIN** — Block Range Index. Stores min/max per block range. Tiny index, good when data is physically sorted.

*Hands-on:* Create a BRIN index on a naturally-sorted column, compare size to B-tree. See the dramatic size difference.

### 9. Write amplification and index maintenance
Every row INSERT/UPDATE/DELETE must update every index on the table. More indexes = slower writes. HOT (Heap-Only Tuples) updates avoid index updates when the indexed columns don't change and there's room on the same page.

*Hands-on:* Measure insert throughput with 0, 1, 3, 5 indexes. See the write amplification.

### What you'll know after this chapter
- What a B-tree looks like physically — pages, entries, tree structure
- How lookups, range scans, and ordered retrieval work internally
- When and why to use covering indexes, partial indexes, expression indexes
- The leftmost prefix rule for multi-column indexes
- When other index types (GIN, GiST, BRIN) beat B-tree
- The write cost of indexes

### Out of scope
- B-tree page splits and compaction internals → deep dive backlog
- GIN/GiST internals (page structure, algorithms)
- Index-only scan visibility map mechanics in detail (covered briefly)
- Concurrent index creation (CREATE INDEX CONCURRENTLY)
- Index bloat and REINDEX

---

## Findings

### What an index is physically
A separate file on disk with its own pages. `big_users` heap: 4,672 pages (36.5 MB), `big_users_pkey` B-tree: 1,374 pages (10.7 MB), `big_users_score` B-tree: 432 pages (3.5 MB). Score index is smaller due to B-tree deduplication — 101 distinct values across 500K rows, so keys are stored once with multiple TIDs.

### B-tree structure
Three levels for 500K rows: root (1 page) → internal (~5 pages) → leaves (~1,368 pages). Each page holds ~350 entries for int4 keys (8KB page ÷ ~16 bytes per entry). Fan-out of 350 means depth barely grows: depth 3 handles up to 43M rows, depth 4 handles up to 15B rows. Adding one level multiplies capacity by 350× but adds only 1 page read.

### Index page layout
Same 8KB structure as heap pages: header → item pointers → free space → index tuples → special area. Item pointers (4 bytes each: offset + length + flags) point to index tuples. Index tuples contain the actual data — leaf entries store (key value + heap TID), internal entries store (key boundary + child page number). Special area has B-tree metadata: right-sibling link (for leaf linked list), level, flags.

High key: first entry on non-rightmost pages is the upper bound for all keys on that page. Navigation entries start after the high key.

Built `pgvis index page` command with full explanations of every field. Supports `root`, `leaf`, `internal` aliases.

### How a B-tree lookup works
Root page: compare key against boundaries (signposts), follow the right child pointer. Internal page: same. Leaf page: binary search among sorted entries (~9 comparisons for 367 entries), find the matching key → heap TID. Heap page: fetch the tuple, check MVCC (xmin/xmax). Total: 4 page reads.

The pointer to a child page is just a page number — Postgres multiplies by 8192 to get the byte offset in the file. Buffer manager checks shared_buffers first.

Built `pgvis index lookup` and `pgvis index tree` commands with visual diagrams showing the traversal.

### Index-only scans
When all needed columns are in the index, skip the heap. Must check visibility map (1 bit per heap page: is it all-visible?). VM check replaces the MVCC heap check. `Heap Fetches: 0` confirms no heap reads. `count(*)` scanned 432 index pages instead of 4,672 heap pages — 10× fewer.

Cache effects matter more than page count: Seq Scan of warm heap (4,672 pages, all cached) was faster than Index Only Scan of cold pkey index (1,374 pages, loaded from disk). The planner estimates this via `effective_cache_size`.

### Multi-column indexes
Index on `(score, name)` is sorted by score first, then name within each score. Leftmost prefix rule: fast for `WHERE score = 42` (leading column, 8 pages), fast for `WHERE score = 42 AND name = 'x'` (both columns, 3 pages), slow for `WHERE name = 'x'` alone (non-leading, 310 pages — must search each score group separately, 103 index searches).

### Partial indexes
`CREATE INDEX ... WHERE condition` indexes only matching rows. Index on `status WHERE status = 'pending'` for a table with 99% 'completed' rows: index is ~100× smaller, and inserts of 'completed' rows don't touch it at all. The WHERE condition is evaluated before the index insertion — skip means zero B-tree work.

### Index types comparison

**B-tree**: default, handles =, <, >, BETWEEN, ORDER BY, Index Only Scan. Depth 3-4 for any practical table size. Almost always the right choice.

**Hash**: O(1) lookup instead of O(log N). Stores hash(key) + TID in buckets, NOT the original key — must always read heap to verify (no Index Only Scan). No range queries, no ordering. Saves 1-2 page reads per lookup. Only worth it for very high-throughput equality lookups on expensive-to-compare keys (UUIDs, long text). In practice almost nobody uses them.

**BRIN**: stores min/max per block range (~128 pages). Tiny index (~2 pages vs 1,374 for B-tree). Only works when data is physically sorted by the indexed column. Good for timestamped event tables. Eliminates block ranges, doesn't pinpoint individual rows.

**GIN**: inverted index for multi-valued columns (arrays, JSONB, full-text). One entry per element pointing to all rows containing it. Enables containment queries (`@>`, `@@`).

### Write amplification
Every INSERT updates every index: 1 heap write + N index writes. UPDATE is worse: dead entries stay in indexes, new entries added — effectively 2N index operations. HOT updates skip index maintenance when indexed columns don't change and there's room on the same page.

### MVCC and indexes
Index entries have NO xmin/xmax. Dead heap tuples leave ghost entries in the index. Queries following ghost entries to the heap discover the tuple is dead — wasted I/O. VACUUM cleans up dead index entries. Index entries can be marked "killed" during traversal to avoid repeated heap fetches, but physical removal waits for VACUUM. This is index bloat.

### CREATE INDEX CONCURRENTLY
Regular CREATE INDEX takes SHARE lock (blocks writes). CONCURRENTLY avoids this:
1. Register index in catalog, mark not valid
2. Set index as "ready for maintenance" — concurrent writes start adding entries
3. First scan: sort all existing keys, build B-tree bottom-up (sort-then-build, sequential I/O, no page splits)
4. Second scan: clean up inconsistencies from concurrent modifications
5. Mark index as valid

Bulk build uses sort-then-build: scan heap → sort keys → write leaf pages sequentially → build internal pages on top. Much faster than one-by-one insertion. Never blocks reads or writes.

### Metapage — how scans find the root
Every B-tree has a metapage at block 0. It stores `btm_root` (current root block number), `btm_level` (tree depth), and `btm_fastroot` (shortcut when the true root has only one child). Every scan starts: open index → read block 0 → follow `btm_root`. When the root splits, a new root is created and the metapage is atomically updated. Concurrent readers are never lost.

### How the planner picks an index
Three stages:

1. **Applicability filter** — cheap reject. Does the WHERE clause reference the leading column? Does the query satisfy a partial index predicate? Does it use the exact expression for an expression index? Indexes that fail are discarded without costing.

2. **Cost estimation** — each surviving index gets costed. Index side: tree depth page reads + leaf range. Heap side: selectivity × reltuples gives rows to fetch, but the key variable is **correlation** (`pg_stats.correlation`, -1.0 to 1.0). High correlation (e.g. `id`, inserted in order, ~1.0) means fetched rows are on consecutive heap pages — nearly sequential I/O. Low correlation (e.g. `score`, random, ~0.0) means rows are scattered — random I/O at `random_page_cost` (4.0). Same selectivity, very different cost. The Mackert-Lohman formula estimates distinct pages touched: `~2*B*N / (2*B+N)`. Each index type provides its own cost function via the `amcostestimate` hook — the planner doesn't know B-tree internals directly.

3. **Path competition** — every applicable index becomes a path. These compete against Seq Scan, other index scans, and Bitmap paths (which can combine multiple indexes via BitmapAnd/BitmapOr). The planner keeps Pareto-optimal paths by cost and sort order — a slightly more expensive index scan that produces ORDER BY order can win by eliminating a Sort node.

### Tools built
- `pgvis index tree <name>` — B-tree overview with "what's inside a page" explanation
- `pgvis index page <name> {meta|root|leaf|internal|N}` — physical page layout with every field explained (meta shows the metapage at block 0)
- `pgvis index lookup <name> <value>` — full lookup trace with visual diagrams and MVCC check
- `pgvis index range <name> <lo> <hi>` — range scan trace through leaf linked list

---

## Retro

### Summary
Deep dive into B-tree index internals: physical structure (pages, entries, tree levels), how lookups work (root → internal → leaf → heap → MVCC), index-only scans (skip heap via visibility map), multi-column index column order, partial indexes, and four index types (B-tree, Hash, BRIN, GIN) with concrete comparisons. Covered write amplification, MVCC interaction (no xmin/xmax in indexes, ghost entries, VACUUM cleanup), and CREATE INDEX CONCURRENTLY (sort-then-build, two-pass scan, never blocks writes). Built pgvis tools for visualizing tree structure, page layout, and lookup traces.

### Key Takeaways
- B-tree depth is 3-4 for any practical table size. Adding one level multiplies capacity by 350×. That's why hash index's 1-2 page savings barely matter.
- Index entries are just (key, pointer) — no MVCC info. Dead entries accumulate until VACUUM. This is index bloat.
- Item pointers and index tuples are separate: pointers are 4-byte indirection (offset+length), tuples hold the actual data. Same pattern as heap pages.
- Index-only scans trade heap access for visibility map check. Cache warmth matters more than page count — Seq Scan of warm heap beat Index Only Scan of cold index.
- Multi-column index column order matters: `(A, B)` is fast for `WHERE A=?` but slow for `WHERE B=?` alone (must search every A group).
- Partial indexes eliminate write overhead for rows that don't match the condition — the index evaluates the WHERE and skips entirely.
- Hash indexes lose range queries, ordering, Index Only Scans, and are often larger than B-trees. Only useful for extreme-throughput equality lookups.
- CREATE INDEX CONCURRENTLY uses sort-then-build (sequential, no splits) + concurrent maintenance (new writes add entries from the start). Never blocks writes.

### Connections
- **Ch 1 (Physical Storage)**: index pages use the same 8KB layout as heap pages. Visibility map enables index-only scans.
- **Ch 2 (Shared Buffers)**: index pages cached in shared_buffers same as heap. Cache warmth affects planner's choice between index and seq scan.
- **Ch 3 (Transactions)**: MVCC check at the end of every index lookup. Index entries have no xmin/xmax — must check heap.
- **Ch 5 (Query Execution)**: index scan types (Index Scan, Index Only Scan, Bitmap Heap Scan) now understood at the physical level.
- **Ch 10 (Vacuum)**: VACUUM removes dead index entries. Without it, indexes bloat with ghost entries causing wasted heap fetches.

### Open Questions
- How does page split work in detail? Split point selection, parent update, concurrent readers during split.
- How does GIN handle updates to array/JSONB columns? Pending list optimization?
- What does BRIN look like internally? Summary pages, revmap?
