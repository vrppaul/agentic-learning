import click
from rich.text import Text

from pgvis.core import connect, console
from pgvis.format import PanelBuilder, section_bar


@click.group()
@click.pass_context
def wal(ctx):
    """Inspect WAL (Write-Ahead Log) records and state."""
    pass


@wal.command(name="show")
@click.argument("sql_text")
@click.pass_context
def wal_show(ctx, sql_text):
    """Run a SQL statement and show the WAL records it generated.

    Example: pgvis wal show "INSERT INTO t VALUES (1, 'hello')"
    """
    dsn = ctx.obj["dsn"]
    with connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_walinspect")

        before = conn.execute("SELECT pg_current_wal_lsn() AS lsn").fetchone()["lsn"]
        conn.execute(sql_text)
        after = conn.execute("SELECT pg_current_wal_lsn() AS lsn").fetchone()["lsn"]

        if before == after:
            console.print("[dim]No WAL records generated.[/dim]")
            return

        records = conn.execute(
            "SELECT * FROM pg_get_wal_records_info(%s::pg_lsn, %s::pg_lsn)",
            [before, after],
        ).fetchall()

        blocks = conn.execute(
            "SELECT * FROM pg_get_wal_block_info(%s::pg_lsn, %s::pg_lsn)",
            [before, after],
        ).fetchall()

        _render_wal_records(sql_text, before, after, records, blocks)


@wal.command(name="lsn")
@click.argument("table", required=False)
@click.argument("page_num", type=int, required=False)
@click.pass_context
def wal_lsn(ctx, table, page_num):
    """Show current WAL position. Optionally compare with a page's LSN.

    Example: pgvis wal lsn
             pgvis wal lsn big_users 0
    """
    dsn = ctx.obj["dsn"]
    with connect(dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_walinspect")

        state = conn.execute("""
            SELECT pg_current_wal_lsn() AS current_lsn,
                   pg_current_wal_insert_lsn() AS insert_lsn,
                   pg_current_wal_flush_lsn() AS flush_lsn
        """).fetchone()

        page_lsn = None
        if table is not None and page_num is not None:
            header = conn.execute(
                "SELECT lsn FROM page_header(get_raw_page(%s, %s))",
                [table, page_num],
            ).fetchone()
            page_lsn = header["lsn"]

        _render_lsn(state, table, page_num, page_lsn)


@wal.command(name="checkpoint")
@click.pass_context
def wal_checkpoint(ctx):
    """Show checkpoint state and recovery window."""
    dsn = ctx.obj["dsn"]
    with connect(dsn) as conn:
        state = conn.execute("""
            SELECT pg_current_wal_lsn() AS current_lsn,
                   pg_current_wal_insert_lsn() AS insert_lsn
        """).fetchone()

        ckpt = conn.execute("""
            SELECT checkpoint_lsn, redo_lsn, redo_wal_file,
                   checkpoint_time, timeline_id
            FROM pg_control_checkpoint()
        """).fetchone()

        ckpt_stats = conn.execute("""
            SELECT num_timed, num_requested, buffers_written
            FROM pg_stat_checkpointer
        """).fetchone()

        _render_checkpoint(state, ckpt, ckpt_stats)


@wal.command(name="segments")
@click.pass_context
def wal_segments(ctx):
    """Show WAL segment files, current segment, and total size."""
    dsn = ctx.obj["dsn"]
    with connect(dsn) as conn:
        state = conn.execute("""
            SELECT pg_current_wal_lsn() AS current_lsn,
                   pg_walfile_name(pg_current_wal_lsn()) AS current_segment,
                   setting AS wal_segment_size
            FROM pg_settings WHERE name = 'wal_segment_size'
        """).fetchone()

        wal_dir = conn.execute("""
            SELECT setting AS data_dir FROM pg_settings WHERE name = 'data_directory'
        """).fetchone()

        segments = conn.execute("""
            SELECT name, size,
                   pg_walfile_name(pg_current_wal_lsn()) = name AS is_current
            FROM pg_ls_waldir()
            ORDER BY name
        """).fetchall()

        ckpt = conn.execute("""
            SELECT redo_wal_file FROM pg_control_checkpoint()
        """).fetchone()

        _render_segments(state, wal_dir, segments, ckpt)


# ── Renderers ──────────────────────────────────────────────────────────────


def _kv(p, key, val, explanation=""):
    line = Text()
    line.append(f"  {'':>6}    ", style="dim")
    line.append(f"{key:>20}", style="cyan")
    line.append("  │  ", style="dim")
    line.append(str(val), style="white")
    p.add(line)
    if explanation:
        note = Text()
        note.append(f"  {'':>6}    ", style="dim")
        note.append(f"{'':>20}  │  ", style="dim")
        note.append(explanation, style="dim italic")
        p.add(note)


def _render_wal_records(sql_text, before, after, records, blocks):
    p = PanelBuilder()

    p.add(section_bar(0x0, "SQL STATEMENT", "", "cyan"))
    p.blank()
    stmt = Text()
    stmt.append(f"  {'':>6}    ", style="dim")
    stmt.append(f"  {sql_text}", style="white bold")
    p.add(stmt)
    p.blank()

    _kv(p, "LSN before", before)
    _kv(p, "LSN after", after)
    _kv(p, "Records", str(len(records)))
    p.blank()

    block_map = {}
    for blk in blocks:
        lsn = blk["start_lsn"]
        block_map.setdefault(lsn, []).append(blk)

    for i, rec in enumerate(records):
        rm = rec["resource_manager"]
        rtype = rec["record_type"]
        xid = rec["xid"]
        length = rec["record_length"]
        fpi = rec["fpi_length"]
        desc = rec["description"]
        lsn = rec["start_lsn"]

        rm_style = {
            "Heap": "green",
            "Heap2": "green",
            "Btree": "yellow",
            "Transaction": "magenta",
            "XLOG": "cyan",
            "Standby": "blue",
        }.get(rm, "white")

        p.add(section_bar(0x0, f"RECORD {i + 1}", f"{rm}/{rtype}", rm_style))
        p.blank()

        _kv(p, "LSN", str(lsn))
        _kv(p, "Type", f"{rm}/{rtype}",
            _explain_record_type(rm, rtype))
        _kv(p, "Transaction", str(xid))
        _kv(p, "Length", f"{length} bytes"
             + (f" (includes {fpi} bytes FPI)" if fpi else " (no FPI)"),
            "FPI = Full Page Image, written on first modification after checkpoint."
            if fpi else "")

        if desc:
            desc_line = Text()
            desc_line.append(f"  {'':>6}    ", style="dim")
            desc_line.append(f"{'Description':>20}", style="cyan")
            desc_line.append("  │  ", style="dim")
            desc_line.append(desc, style="white")
            p.add(desc_line)

        rec_blocks = block_map.get(lsn, [])
        for blk in rec_blocks:
            relnode = blk.get("relfilenode", "?")
            fork = {0: "main", 1: "fsm", 2: "vm"}.get(blk.get("relforknumber", 0), "?")
            blknum = blk.get("relblocknumber", "?")
            data_len = blk.get("block_data_length", 0) or 0
            fpi_len = blk.get("block_fpi_length", 0) or 0

            blk_line = Text()
            blk_line.append(f"  {'':>6}    ", style="dim")
            blk_line.append(f"{'Block':>20}", style="cyan")
            blk_line.append("  │  ", style="dim")
            blk_line.append(f"rel {relnode} fork {fork} blk {blknum}", style="white")
            if fpi_len:
                blk_line.append(f"  FPI {fpi_len}B", style="yellow bold")
            if data_len:
                blk_line.append(f"  data {data_len}B", style="dim")
            p.add(blk_line)

        p.blank()

    total_bytes = sum(r["record_length"] for r in records)
    total_fpi = sum(r["fpi_length"] for r in records)

    summary = Text()
    summary.append(f"  {'':>6}    ", style="dim")
    summary.append(f"  Total: {len(records)} records, {total_bytes} bytes", style="dim")
    if total_fpi:
        summary.append(f" ({total_fpi} bytes FPI)", style="yellow")
    p.add(summary)

    p.print(title="WAL Records", border_style="cyan")


def _render_lsn(state, table, page_num, page_lsn):
    p = PanelBuilder()

    p.add(section_bar(0x0, "WAL POSITION", "", "cyan"))
    p.blank()

    _kv(p, "Insert LSN", str(state["insert_lsn"]),
        "Where the next WAL record will be written (in wal_buffers).")
    _kv(p, "Flush LSN", str(state["flush_lsn"]),
        "Last LSN flushed to disk. Durable — survives crash.")
    _kv(p, "Current LSN", str(state["current_lsn"]),
        "Latest WAL position visible to queries.")
    p.blank()

    if page_lsn is not None:
        _kv(p, "Page LSN", str(page_lsn),
            f"Page {page_num} of {table} was last modified at this WAL position.")

        insert_int = _lsn_to_int(str(state["insert_lsn"]))
        page_int = _lsn_to_int(str(page_lsn))
        gap = insert_int - page_int

        gap_line = Text()
        gap_line.append(f"  {'':>6}    ", style="dim")
        gap_line.append(f"{'Gap':>20}", style="cyan")
        gap_line.append("  │  ", style="dim")
        gap_line.append(f"{gap:,} bytes of WAL since this page was last modified", style="white")
        p.add(gap_line)

    p.print(title="WAL Position", border_style="cyan")


def _render_checkpoint(state, ckpt, ckpt_stats):
    p = PanelBuilder()

    p.add(section_bar(0x0, "CHECKPOINT STATE", "", "magenta"))
    p.blank()

    _kv(p, "Checkpoint LSN", str(ckpt["checkpoint_lsn"]),
        "LSN of the last checkpoint record.")
    _kv(p, "REDO LSN", str(ckpt["redo_lsn"]),
        "Where crash recovery would start replaying from.")
    _kv(p, "Checkpoint time", str(ckpt["checkpoint_time"]))
    _kv(p, "WAL file", str(ckpt["redo_wal_file"]),
        "The WAL segment file containing the REDO point.")
    _kv(p, "Timeline", str(ckpt["timeline_id"]))
    p.blank()

    current_int = _lsn_to_int(str(state["current_lsn"]))
    redo_int = _lsn_to_int(str(ckpt["redo_lsn"]))
    recovery_bytes = current_int - redo_int

    _kv(p, "Recovery window", f"{recovery_bytes:,} bytes ({recovery_bytes / 1024 / 1024:.1f} MB)",
        "WAL that would need to be replayed on crash. Smaller = faster recovery.")
    p.blank()

    p.add(section_bar(0x0, "CHECKPOINT STATS", "", "yellow"))
    p.blank()

    _kv(p, "Timed checkpoints", str(ckpt_stats["num_timed"]),
        "Triggered by checkpoint_timeout (default 5 min).")
    _kv(p, "Requested", str(ckpt_stats["num_requested"]),
        "Triggered by max_wal_size or manual CHECKPOINT.")
    _kv(p, "Buffers written", str(ckpt_stats["buffers_written"]),
        "Pages written by checkpointer.")

    p.print(title="Checkpoint", border_style="magenta")


def _explain_record_type(rm, rtype):
    explanations = {
        ("Heap", "INSERT"): "New tuple added to a heap page.",
        ("Heap", "UPDATE"): "Tuple updated — old version marked dead, new version created.",
        ("Heap", "HOT_UPDATE"): "HOT update — no index entry needed, same page.",
        ("Heap", "DELETE"): "Tuple marked dead (xmax set).",
        ("Heap", "LOCK"): "Row-level lock acquired (FOR UPDATE/SHARE).",
        ("Heap2", "PRUNE"): "Dead tuples pruned from a page (HOT chain cleanup).",
        ("Heap2", "VACUUM"): "VACUUM cleaned dead tuples from a page.",
        ("Heap2", "VISIBLE"): "Visibility map bit set for a page.",
        ("Heap2", "FREEZE_PAGE"): "All tuples on a page frozen.",
        ("Btree", "INSERT_LEAF"): "New entry added to a B-tree leaf page.",
        ("Btree", "DEDUP"): "B-tree deduplication — combine duplicate keys.",
        ("Transaction", "COMMIT"): "Transaction committed — all changes are now durable.",
        ("Transaction", "ABORT"): "Transaction rolled back.",
        ("XLOG", "CHECKPOINT_ONLINE"): "Checkpoint record — all dirty pages flushed.",
        ("XLOG", "CHECKPOINT_SHUTDOWN"): "Clean shutdown checkpoint.",
        ("Standby", "RUNNING_XACTS"): "Snapshot of running transactions (for standby).",
    }
    return explanations.get((rm, rtype), "")


def _render_segments(state, wal_dir, segments, ckpt):
    p = PanelBuilder()

    p.add(section_bar(0x0, "WAL SEGMENTS", "", "cyan"))
    p.blank()

    data_dir = wal_dir["data_dir"]
    _kv(p, "WAL directory", f"{data_dir}/pg_wal/")
    _kv(p, "Segment size", f"{state['wal_segment_size']}")
    _kv(p, "Current LSN", str(state["current_lsn"]))
    _kv(p, "Current segment", str(state["current_segment"]))
    p.blank()

    total_size = sum(s["size"] for s in segments)
    wal_files = [s for s in segments if not s["name"].endswith(".history")]

    _kv(p, "Total segments", str(len(wal_files)))
    _kv(p, "Total size", f"{total_size / 1024 / 1024:.1f} MB")
    p.blank()

    redo_file = ckpt["redo_wal_file"]

    for seg in wal_files:
        line = Text()
        line.append(f"  {'':>6}    ", style="dim")

        name = seg["name"]
        size_mb = seg["size"] / 1024 / 1024

        if seg["is_current"]:
            line.append(f"  {name}", style="cyan bold")
            line.append(f"  {size_mb:.0f} MB", style="dim")
            line.append("  ← current", style="cyan")
        elif name == redo_file:
            line.append(f"  {name}", style="magenta bold")
            line.append(f"  {size_mb:.0f} MB", style="dim")
            line.append("  ← recovery starts here", style="magenta")
        elif name < redo_file:
            line.append(f"  {name}", style="dim")
            line.append(f"  {size_mb:.0f} MB", style="dim")
            line.append("  (recyclable)", style="dim italic")
        else:
            line.append(f"  {name}", style="white")
            line.append(f"  {size_mb:.0f} MB", style="dim")

        p.add(line)

    p.blank()

    note = Text()
    note.append(f"  {'':>6}    ", style="dim")
    note.append("  Segments before the recovery point can be recycled after checkpoint.", style="dim italic")
    p.add(note)

    p.print(title="WAL Segments", border_style="cyan")


def _lsn_to_int(lsn_str):
    parts = lsn_str.split("/")
    return (int(parts[0], 16) << 32) + int(parts[1], 16)
