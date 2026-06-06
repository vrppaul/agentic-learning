# Topics

Status markers: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Databases

### [ ] PostgreSQL Internals — MVCC & WAL
- Observe row versions and transaction IDs via `pg_visibility` and system columns (`xmin`, `xmax`, `ctid`)
- Trigger and observe vacuum behavior, dead tuple accumulation
- Inspect WAL segments with `pg_waldump`, understand checkpoint tuning
- Measure bloat under different write patterns

### [ ] PostgreSQL — Isolation Levels
- Reproduce every anomaly: dirty read, non-repeatable read, phantom read, serialization anomaly
- Compare READ COMMITTED vs REPEATABLE READ vs SERIALIZABLE with concurrent Go clients
- Measure performance cost of stricter isolation under contention
- Demonstrate write skew and how SERIALIZABLE prevents it

### [ ] PostgreSQL — Indexes Deep Dive
- B-tree internals: `pageinspect`, index-only scans, covering indexes
- GIN for full-text search and JSONB, GiST for geometric/range data
- BRIN for time-series/append-only workloads
- Partial and expression indexes
- EXPLAIN ANALYZE everything — measure the actual impact

### [ ] PostgreSQL — Replication & High Availability
- Streaming replication: set up primary + replica, observe WAL shipping
- Logical replication: selective table sync, cross-version replication
- Failover scenarios: simulate primary crash, promote replica
- Synchronous vs asynchronous replication tradeoffs under load

### [ ] PgBouncer & Connection Pooling
- Compare session, transaction, and statement pooling modes
- Benchmark connection overhead: direct Postgres vs PgBouncer
- Load test with hundreds of Go client connections
- Observe failure modes: pool exhaustion, long transactions blocking the pool

### [ ] Redis Deep Dive
- Data structures hands-on: strings, hashes, sorted sets, streams, HyperLogLog
- Persistence: RDB snapshots vs AOF, observe rewrite behavior
- Redis as cache: eviction policies under memory pressure, benchmark hit rates
- Redis Cluster: sharding, slot migration, observe failover
- Pub/Sub vs Streams: when to use which, backpressure behavior
- Lua scripting for atomic operations

### [ ] NoSQL Landscape
- Compare Redis, MongoDB, Cassandra (or ScyllaDB) for different access patterns
- Write a Go benchmark suite hitting each with identical workloads
- CAP theorem in practice: observe what happens during network partitions

### [ ] Distributed Databases & Consensus
- Why single-node databases can't just be "split" across hosts — the fundamental problems (consistency, coordination, partial failure)
- Consensus protocols: Raft from scratch in Go — leader election, log replication, safety guarantees
- Two-phase commit (2PC): how distributed transactions work, why they're slow, the coordinator failure problem
- CAP theorem deeply: consistency vs availability during network partitions, real examples
- Distributed KV store: build one in Go using Raft — put/get/delete across a 3-node cluster
- CockroachDB or TiDB architecture: how they layer SQL on top of distributed KV (Raft groups, range splits, distributed query execution)
- Vector clocks, hybrid logical clocks — ordering events without a global clock
- Sharding strategies: hash vs range, rebalancing, hotspots
- Distributed transactions: 2PC limitations, why holding locks across services is dangerous
- Saga pattern: choreography vs orchestration, compensating transactions, failure handling
- Outbox pattern: reliable event publishing from a database transaction
- Distributed locks: Redis-based (Redlock), ZooKeeper, why to avoid them when possible
- Idempotency: designing operations that are safe to retry, idempotency keys

### [ ] Change Data Capture (CDC)
- What CDC is and why it exists — streaming database changes to external systems
- PostgreSQL logical decoding as the foundation
- Debezium: architecture, connectors, how it reads WAL via logical replication slots
- Outbox pattern with CDC: reliable event publishing without distributed transactions
- CDC to Kafka: schema registry, ordering guarantees, exactly-once semantics
- CDC to data warehouses / analytics: keeping read replicas and materialized views in sync
- Operational concerns: slot management, WAL retention, lag monitoring

---

## Messaging & Event Systems

### [ ] Kafka Internals
- Producer/consumer from scratch in Go
- Partitioning strategies: key-based, round-robin, custom partitioner
- Consumer groups: rebalancing, offset management, exactly-once semantics
- Observe ISR (in-sync replicas), leader election, segment log structure
- Put under load: measure throughput, latency percentiles, back-pressure

### [ ] Kafka Consensus — KRaft & ZooKeeper
- Run Kafka in ZooKeeper mode vs KRaft mode
- Observe leader election, metadata quorum
- Simulate broker failures, measure recovery time

### [ ] NATS
- Core NATS: pub/sub, request/reply, queue groups
- JetStream: persistence, exactly-once delivery, key-value store
- Compare with Kafka: latency, throughput, operational complexity
- Build a real-time notification pipeline

### [ ] RabbitMQ
- AMQP concepts: exchanges, bindings, queues, dead-letter routing
- Compare direct, topic, fanout, headers exchanges
- Observe message flow with management UI
- Benchmark vs Kafka and NATS for different message patterns

### [ ] Event-Driven Architecture Patterns
- CQRS: separate read/write models backed by different stores
- Event Sourcing: build an event store, replay projections
- Saga pattern: distributed transactions across services
- Outbox pattern: reliable event publishing from a database

---

## Distributed Systems

### [ ] Consensus Protocols
- Implement a toy Raft in Go — leader election, log replication
- Observe etcd's Raft implementation under partition
- Compare Raft vs Paxos conceptually, ZAB (ZooKeeper) practically

### [ ] Consistent Hashing
- Implement consistent hashing with virtual nodes in Go
- Simulate node addition/removal, measure key redistribution
- Compare with simple modulo hashing under cluster changes

### [ ] Cache Algorithms & Distributed Caches
- Implement LRU, clock-sweep (Postgres-style), LFU, ARC from scratch in Go
- Benchmark eviction policies under different access patterns (zipfian, uniform, sequential)
- Build a distributed cache: consistent hashing + node eviction + replication
- Compare with Redis, memcached — what do they add beyond the core algorithm?

### [ ] Load Balancers & Rate Limiters
- Build an L4 and L7 load balancer in Go
- Implement algorithms: round-robin, least-connections, weighted, consistent-hash
- Build rate limiters: token bucket, sliding window, leaky bucket
- Benchmark each under realistic traffic patterns

### [ ] gRPC & Protobuf
- Define services with proto3, generate Go code
- Unary, server-streaming, client-streaming, bidirectional RPCs
- Interceptors for auth, logging, metrics
- Load balancing gRPC: client-side vs proxy-based
- Benchmark gRPC vs REST vs WebSocket for different payload patterns

---

## Infrastructure

### [ ] Kubernetes Deep Dive
- Build a multi-node cluster from scratch (kubeadm or the hard way)
- Deploy services, observe pod scheduling, resource limits, QoS classes
- Networking: CNI plugins, Services, Ingress, NetworkPolicies
- Put services under load: HPA, observe scaling behavior
- Simulate failures: node drain, pod eviction, PDB behavior

### [ ] Service Meshes
- Deploy Istio or Linkerd alongside services
- Observe sidecar injection, traffic routing, circuit breaking
- mTLS between services — zero-trust networking in practice
- Compare with eBPF-based mesh (Cilium)

### [ ] Terraform / IaC
- Provision infrastructure: VMs, networks, load balancers
- State management: remote backends, locking, import existing resources
- Modules: build reusable infrastructure components
- Plan/apply workflow, drift detection

### [ ] Ansible
- Configuration management: provision and configure servers
- Playbooks, roles, inventories
- Compare with Terraform: when to use which
- Idempotency in practice

### [ ] DHCP Server
- Implement a basic DHCP server in Go
- DORA process: Discover, Offer, Request, Acknowledge
- Observe packet-level behavior with tcpdump/Wireshark
- Lease management, renewal, rebinding

---

## Security & Auth

### [ ] Authentication & Authorization Fundamentals
- Implement session-based auth, JWT auth, API key auth in Go
- Compare stateful vs stateless session management
- RBAC and ABAC access control models
- Password hashing: bcrypt, argon2, scrypt — benchmark and compare

### [ ] OIDC & IAP
- Set up an OIDC provider (Keycloak or Dex)
- Implement the authorization code flow in Go
- ID tokens, access tokens, refresh tokens — observe the full lifecycle
- Identity-Aware Proxy: protect services without app-level auth code

### [ ] SSO
- SAML vs OIDC for enterprise SSO
- Set up SSO across multiple services via a shared identity provider
- Session propagation, single logout

### [ ] Cryptographic Protocols — Implement from Scratch
- SCRAM-SHA-256: implement the full handshake in Go (client + server side). HMAC, PBKDF2, salting, channel binding. Used by PostgreSQL, MongoDB, XMPP.
- TLS handshake: trace and understand each step (ClientHello, ServerHello, key exchange, certificate verification). Not implement TLS itself, but build understanding of what Wireshark shows.
- Implement HMAC, PBKDF2, SHA-256 from the spec to understand what the higher-level protocols use.

### [ ] mTLS
- Generate CA, server certs, client certs
- Enforce mutual TLS between Go services
- Certificate rotation strategies
- Observe TLS handshake with Wireshark

---

## Networking & Linux

### [ ] Low-Level Data Representation
- Endianness: big-endian (network byte order) vs little-endian (x86). Why it matters for wire protocols, file formats, cross-architecture communication. PostgreSQL wire protocol uses big-endian, heap tuples use the host's native endianness.
- Integer encoding: fixed-width vs variable-length (varint/protobuf). Two's complement for signed integers.
- IEEE 754 floating point: how floats are stored, why 0.1 + 0.2 ≠ 0.3, implications for financial data (use NUMERIC, not FLOAT).
- Text encoding: ASCII, UTF-8, the relationship between client_encoding and server_encoding in PostgreSQL.

### [ ] Linux Networking Internals
- Network namespaces: isolate and connect network stacks
- iptables/netfilter: build firewall rules, NAT, packet mangling
- veth pairs, bridges — understand container networking foundations
- tc (traffic control): simulate latency, packet loss, bandwidth limits

### [ ] eBPF
- Write eBPF programs to observe kernel events
- Trace syscalls, network packets, scheduling decisions
- Build a simple observability tool with libbpf + Go
- Understand how Cilium uses eBPF for networking and security

---

## Python Web Stack Internals

### [ ] Gunicorn + SQLAlchemy Deep Dive
- Gunicorn worker types: sync, gthread, gevent/eventlet — how each handles concurrency
- Process model: prefork, preload, post_fork hooks, worker lifecycle
- SQLAlchemy engine and pool: per-process pool, pool_size, max_overflow, pool_recycle, pool_pre_ping
- Connection lifecycle: creation, checkout, return, disposal, what RESET ALL does
- The fork problem: shared sockets after preload, engine.dispose() fix
- Optimal configuration: matching pool_size to worker concurrency, total connections to PostgreSQL
- Monitoring: pool statistics, overflow events, connection checkout time

---

## Architecture & Patterns

### [ ] DDD (Domain-Driven Design)
- Model a non-trivial domain with aggregates, entities, value objects
- Bounded contexts: separate domain models communicating via events
- Repository pattern, domain events, application services
- Implement in Go — idiomatic Go DDD (not just Java patterns translated)

### [ ] API Gateway Patterns
- Build an API gateway in Go: routing, auth, rate limiting, request transformation
- Compare with off-the-shelf: Kong, Envoy, Traefik
- Observe request flow, measure overhead

---

## Observability

### [ ] OpenTelemetry, Prometheus, Grafana
- Instrument Go services with OpenTelemetry: traces, metrics, logs
- Set up Prometheus scraping, write PromQL queries
- Build Grafana dashboards, alerting rules
- Distributed tracing across multiple services

---

## Languages & Runtimes

### [ ] Python Descriptors
- Implement `__get__`, `__set__`, `__delete__`, `__set_name__`
- Data vs non-data descriptors, method resolution order
- Build a validation framework using descriptors
- Understand how `@property`, `@classmethod`, `@staticmethod` work under the hood

### [ ] Rust Systems Programming / ironkernel
- Rust ownership, borrowing, lifetimes in practice
- Build a minimal OS kernel: bootloader, VGA text mode, interrupts, memory management
- Compare with C kernel development: what Rust prevents at compile time

### [ ] WASM
- Compile Go/Rust to WebAssembly
- Run WASM modules outside the browser: Wasmtime, WasmEdge
- Edge compute use cases: serverless functions in WASM
- Benchmark WASM vs native execution
