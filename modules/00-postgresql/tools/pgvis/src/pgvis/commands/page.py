import click
import psycopg
from rich.text import Text

from pgvis.core import PAGE_SIZE, connect, console
from pgvis.format import PREVIEW_COUNT, PanelBuilder, centered_dim, ellipsis, pick_preview, section_bar

INFOMASK_FLAGS = [
    (0x0100, "XMIN_COMMITTED", "green"),
    (0x0200, "XMIN_ABORTED", "red"),
    (0x0400, "XMAX_COMMITTED", "green"),
    (0x0800, "XMAX_ABORTED", "red"),
    (0x0080, "XMAX_LOCK_ONLY", "magenta"),
    (0x1000, "XMAX_IS_MULTI", "magenta"),
    (0x2000, "UPDATED", "yellow"),
]

INFOMASK2_FLAGS = [
    (0x2000, "KEYS_UPDATED", "red"),
    (0x4000, "HOT_UPDATED", "yellow"),
    (0x8000, "HEAP_ONLY", "yellow"),
]


@click.command()
@click.argument("table")
@click.argument("page_num", type=int)
@click.option("--no-data", is_flag=True, help="Skip fetching decoded row data.")
@click.option("--all", "show_all", is_flag=True, help="Show all tuples instead of preview.")
@click.pass_context
def page(ctx, table: str, page_num: int, no_data: bool, show_all: bool) -> None:
    """Visualize a heap page: header, item pointers, tuples."""
    with connect(ctx.obj["dsn"]) as conn:
        info = _fetch_relation_info(conn, table)
        if page_num >= info["num_pages"]:
            console.print(
                f"[red]Page {page_num} out of range. "
                f"Table '{table}' has {info['num_pages']} pages (0-{info['num_pages'] - 1}).[/red]"
            )
            raise SystemExit(1)

        console.print(
            f"\n[bold]{table}[/bold] — "
            f"{info['size_bytes']} bytes, {info['num_pages']} pages "
            f"({info['filepath']})\n"
        )

        header = _fetch_page_header(conn, table, page_num)
        items = _fetch_heap_items(conn, table, page_num)

        tuple_data = {}
        if not no_data:
            tuple_data = _fetch_tuple_data(conn, table, page_num, items)

        _render(header, items, tuple_data, show_all=show_all)


# ── Queries ──────────────────────────────────────────────────────────────────


def _fetch_relation_info(conn: psycopg.Connection, table: str) -> dict:
    return conn.execute(
        """
        SELECT pg_relation_filepath(%s) AS filepath,
               pg_relation_size(%s) AS size_bytes,
               pg_relation_size(%s) / 8192 AS num_pages
        """,
        (table, table, table),
    ).fetchone()


def _fetch_page_header(conn: psycopg.Connection, table: str, page_num: int) -> dict:
    return conn.execute(
        "SELECT * FROM page_header(get_raw_page(%s, %s))", (table, page_num)
    ).fetchone()


def _fetch_heap_items(conn: psycopg.Connection, table: str, page_num: int) -> list[dict]:
    return conn.execute(
        "SELECT * FROM heap_page_items(get_raw_page(%s, %s))", (table, page_num)
    ).fetchall()


def _fetch_tuple_data(
    conn: psycopg.Connection, table: str, page_num: int, items: list[dict]
) -> dict[int, dict]:
    active_lps = [item["lp"] for item in items if item["lp_off"] > 0]
    if not active_lps:
        return {}

    ctid_array = ", ".join(f"'({page_num},{lp})'::tid" for lp in active_lps)
    rows = conn.execute(
        f"SELECT ctid, * FROM {table} WHERE ctid = ANY(ARRAY[{ctid_array}])"  # noqa: S608
    ).fetchall()

    results = {}
    for row in rows:
        ctid_str = str(row.pop("ctid"))
        item_num = int(ctid_str.strip("()").split(",")[1])
        results[item_num] = row
    return results


# ── Rendering ────────────────────────────────────────────────────────────────


def _render(
    header: dict, items: list[dict], tuple_data: dict[int, dict], *, show_all: bool = False
) -> None:
    lower = header["lower"]
    upper = header["upper"]
    special = header["special"]
    free_bytes = upper - lower
    pointer_bytes = lower - 24
    tuple_bytes = special - upper
    active = [i for i in items if i["lp_off"] > 0 and i["t_xmin"] is not None]

    p = PanelBuilder()

    _add_header_section(p, header)
    _add_pointer_section(p, items, pointer_bytes, show_all)
    _add_free_section(p, lower, free_bytes)
    _add_tuple_section(p, upper, active, tuple_data, tuple_bytes, show_all)
    p.add(section_bar(PAGE_SIZE, "END OF PAGE", "8192", "dim"))
    _add_space_bar(p, pointer_bytes, free_bytes, tuple_bytes)

    p.print(border_style="blue")


def _add_header_section(p: PanelBuilder, header: dict) -> None:
    p.add(section_bar(0x0, "PAGE HEADER", "24 bytes", "cyan"))
    p.blank()

    pairs = [
        ("LSN", str(header["lsn"])),
        ("Checksum", str(header["checksum"])),
        ("Flags", str(header["flags"])),
        ("Page Size", str(header["pagesize"])),
        ("Lower", f"{header['lower']}  (end of item pointers)"),
        ("Upper", f"{header['upper']}  (start of tuples)"),
        ("Special", str(header["special"])),
    ]
    for key, val in pairs:
        line = Text()
        line.append(f"  {'':>6}    ", style="dim")
        line.append(f"{key:>12}", style="cyan")
        line.append("  │  ", style="dim")
        line.append(val, style="white")
        p.add(line)

    p.blank()


def _add_pointer_section(p: PanelBuilder, items: list[dict], pointer_bytes: int, show_all: bool) -> None:
    num = len(items)
    p.add(section_bar(24, "ITEM POINTERS  ↓", f"{pointer_bytes} bytes, {num} items", "yellow"))
    p.blank()

    flags_map = {0: "unused", 1: "normal", 2: "redirect", 3: "dead"}
    show = items if show_all else pick_preview(items)

    for entry in show:
        if entry is None:
            p.add(ellipsis(len(items) - PREVIEW_COUNT * 2))
            continue

        flags = flags_map.get(entry["lp_flags"], str(entry["lp_flags"]))
        flag_style = {
            "normal": "green",
            "dead": "red bold",
            "redirect": "magenta",
            "unused": "dim",
        }.get(flags, "dim")

        line = Text()
        line.append(f"  {'':>6}    ", style="dim")
        line.append(f"LP {entry['lp']:>4}", style="yellow bold")
        line.append("  →  ", style="dim")
        line.append(f"offset {entry['lp_off']:>5}", style="white")
        line.append("  │  ", style="dim")
        line.append(f"len {entry['lp_len']:>3}", style="white")
        line.append("  │  ", style="dim")
        line.append(flags, style=flag_style)
        p.add(line)

    p.blank()


def _add_free_section(p: PanelBuilder, lower: int, free_bytes: int) -> None:
    pct = free_bytes * 100 / PAGE_SIZE
    p.add(section_bar(lower, "FREE SPACE", f"{free_bytes} bytes, {pct:.1f}%", "dim"))
    p.blank()


def _add_tuple_section(
    p: PanelBuilder, upper: int, active: list[dict], tuple_data: dict[int, dict],
    tuple_bytes: int, show_all: bool,
) -> None:
    p.add(section_bar(upper, "TUPLES  ↑", f"{tuple_bytes} bytes, {len(active)} tuples", "green"))
    p.blank()

    if not active:
        p.add(centered_dim("(empty)"))
        p.blank()
        return

    sorted_by_offset = sorted(active, key=lambda i: i["lp_off"])
    show = sorted_by_offset if show_all else pick_preview(sorted_by_offset)

    for i, entry in enumerate(show):
        if entry is None:
            p.add(ellipsis(len(active) - PREVIEW_COUNT * 2))
            continue

        lp = entry["lp"]
        xmax = entry["t_xmax"] or 0

        hdr = Text()
        hdr.append(f"  {'':>6}    ", style="dim")
        hdr.append("┌─ ", style="dim")
        hdr.append(f"LP {lp}", style="yellow bold")
        hdr.append("  │  ", style="dim")
        hdr.append(f"offset {entry['lp_off']}", style="dim")
        hdr.append("  │  ", style="dim")
        hdr.append(f"{entry['lp_len']} bytes", style="dim")
        p.add(hdr)

        meta = Text()
        meta.append(f"  {'':>6}    ", style="dim")
        meta.append("│  ", style="dim")
        meta.append("xmin=", style="dim")
        meta.append(f"{entry['t_xmin']}", style="cyan bold")
        meta.append("   xmax=", style="dim")
        meta.append(f"{xmax}", style="red bold" if xmax != 0 else "dim")
        meta.append("   ctid=", style="dim")
        meta.append(f"{entry['t_ctid']}", style="white")
        meta.append("   hoff=", style="dim")
        meta.append(f"{entry['t_hoff']}", style="white")
        p.add(meta)

        infomask = entry.get("t_infomask", 0) or 0
        infomask2 = entry.get("t_infomask2", 0) or 0
        flags = _decode_infomask(infomask)
        flags2 = [(name, style) for bit, name, style in INFOMASK2_FLAGS if infomask2 & bit]
        all_flags = flags + flags2
        if all_flags:
            fl = Text()
            fl.append(f"  {'':>6}    ", style="dim")
            fl.append("│  ", style="dim")
            frozen = (infomask & 0x0300) == 0x0300
            if frozen:
                fl.append("FROZEN", style="cyan bold")
                remaining = [(name, style) for bit, name, style in INFOMASK_FLAGS
                             if bit not in (0x0100, 0x0200) and infomask & bit]
                remaining += flags2
                for name, style in remaining:
                    fl.append(f"  {name}", style=style)
            else:
                first_flag = True
                for name, style in all_flags:
                    if not first_flag:
                        fl.append("  ", style="dim")
                    fl.append(name, style=style)
                    first_flag = False
            p.add(fl)

        if lp in tuple_data:
            row = tuple_data[lp]
            data = Text()
            data.append(f"  {'':>6}    ", style="dim")
            data.append("│  ", style="dim")
            first = True
            for k, v in row.items():
                if not first:
                    data.append("  │  ", style="dim")
                data.append(f"{k}", style="dim")
                data.append("=", style="dim")
                data.append(f"{v}", style="white bold")
                first = False
            p.add(data)

        bot = Text()
        bot.append(f"  {'':>6}    ", style="dim")
        bot.append("└─────", style="dim")
        p.add(bot)

        if i < len(show) - 1:
            p.blank()

    p.blank()


def _add_space_bar(p: PanelBuilder, pointer_bytes: int, free_bytes: int, tuple_bytes: int) -> None:
    p.blank()

    bar_width = 64
    total = PAGE_SIZE

    h_w = max(1, round(24 / total * bar_width))
    p_w = max(1, round(pointer_bytes / total * bar_width))
    f_w = max(1, round(free_bytes / total * bar_width)) if free_bytes > 0 else 0
    t_w = max(1, round(tuple_bytes / total * bar_width))

    used = h_w + p_w + f_w + t_w
    t_w += bar_width - used

    bar = Text()
    bar.append("  [", style="dim")
    bar.append("█" * h_w, style="cyan")
    bar.append("█" * p_w, style="yellow")
    bar.append("░" * f_w, style="dim")
    bar.append("█" * t_w, style="green")
    bar.append("]", style="dim")
    p.add(bar)

    legend = Text()
    legend.append("   ", style="dim")
    legend.append("█", style="cyan")
    legend.append(f" hdr ({24}B)  ", style="dim")
    legend.append("█", style="yellow")
    legend.append(f" ptrs ({pointer_bytes}B)  ", style="dim")
    legend.append("░", style="dim")
    legend.append(f" free ({free_bytes}B)  ", style="dim")
    legend.append("█", style="green")
    legend.append(f" tuples ({tuple_bytes}B)", style="dim")
    p.add(legend)


def _decode_infomask(infomask: int) -> list[tuple[str, str]]:
    return [(name, style) for bit, name, style in INFOMASK_FLAGS if infomask & bit]
