import click

from pgvis.commands import buffers, clog, explain, fsm, index, join, locks, page, sql, vm, wal
from pgvis.core import DEFAULT_DSN


@click.group()
@click.option("--dsn", default=DEFAULT_DSN, envvar="PGVIS_DSN", help="PostgreSQL connection string.")
@click.pass_context
def cli(ctx, dsn: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["dsn"] = dsn


cli.add_command(page)
cli.add_command(fsm)
cli.add_command(vm)
cli.add_command(buffers)
cli.add_command(locks)
cli.add_command(clog)
cli.add_command(sql)
cli.add_command(explain)
cli.add_command(index)
cli.add_command(join)
cli.add_command(wal)
