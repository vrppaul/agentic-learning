#!/bin/bash
set -e

# Allow replication connections from the Docker network
echo "host replication study all trust" >> "$PGDATA/pg_hba.conf"

# Reload to pick up the change
pg_ctl reload -D "$PGDATA"
