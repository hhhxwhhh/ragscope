#!/usr/bin/env bash
# Start PostgreSQL + Redis on this machine, without Docker.
#
# `make dev-db` assumes Docker. That is the right default for a team, but it is
# also the reason this project could not be run end-to-end on a machine without
# it: no database means no migrations, and no migrations means every integration
# test and every "does retrieval actually work" question stays unanswered. This
# script is the fallback for that machine.
#
# Ports default to 5433 / 6380 (what .env.example ships) so a Dockerised
# PostgreSQL on 5432 is never disturbed.
#
# Env:
#   PG_BIN       directory containing initdb/pg_ctl/psql   (default /d/postgresql/bin)
#   REDIS_BIN    directory containing redis-server/redis-cli (default /d/redis)
#   PG_PORT      default 5433
#   REDIS_PORT   default 6380
#   PG_DATA      default backend/data/.pgdata
#
# Usage:  bash scripts/dev_stack.sh [start|stop|status]
set -euo pipefail

PG_BIN="${PG_BIN:-/d/postgresql/bin}"
REDIS_BIN="${REDIS_BIN:-/d/redis}"
PG_PORT="${PG_PORT:-5433}"
REDIS_PORT="${REDIS_PORT:-6380}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_DATA="${PG_DATA:-$ROOT/backend/data/.pgdata}"
PG_LOG="$ROOT/backend/data/.pglog"

EXE=""
[ -f "$PG_BIN/initdb.exe" ] && EXE=".exe"

pg_alive() { "$PG_BIN/psql$EXE" -h 127.0.0.1 -p "$PG_PORT" -U postgres -tAc "SELECT 1;" >/dev/null 2>&1; }
redis_alive() { "$REDIS_BIN/redis-cli$EXE" -p "$REDIS_PORT" ping >/dev/null 2>&1; }

start_pg() {
  if pg_alive; then echo "postgres: already up on $PG_PORT"; return; fi
  if [ ! -d "$PG_DATA" ]; then
    echo "postgres: initdb -> $PG_DATA"
    mkdir -p "$(dirname "$PG_DATA")"
    "$PG_BIN/initdb$EXE" -D "$PG_DATA" -U postgres --auth=trust -E UTF8 >/dev/null
    # Windows cannot create the unix-socket lock file; PostgreSQL then refuses to
    # boot at all. Disabling the socket directory leaves TCP, which is what we use.
    printf "\n# added by scripts/dev_stack.sh\nunix_socket_directories = ''\n" >>"$PG_DATA/postgresql.conf"
  fi
  echo "postgres: starting on $PG_PORT"
  "$PG_BIN/pg_ctl$EXE" -D "$PG_DATA" -l "$PG_LOG" -o "-p $PG_PORT" start >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do pg_alive && break; sleep 1; done
  pg_alive || { echo "postgres failed to start; see $PG_LOG"; tail -20 "$PG_LOG"; exit 1; }
  "$PG_BIN/psql$EXE" -h 127.0.0.1 -p "$PG_PORT" -U postgres -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname='ragscope';" | grep -q 1 ||
    "$PG_BIN/psql$EXE" -h 127.0.0.1 -p "$PG_PORT" -U postgres -c \
      "CREATE ROLE ragscope LOGIN PASSWORD 'ragscope' CREATEDB;" >/dev/null
  "$PG_BIN/psql$EXE" -h 127.0.0.1 -p "$PG_PORT" -U postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='ragscope';" | grep -q 1 ||
    "$PG_BIN/psql$EXE" -h 127.0.0.1 -p "$PG_PORT" -U postgres -c \
      "CREATE DATABASE ragscope OWNER ragscope;" >/dev/null
  echo "postgres: ready (db=ragscope user=ragscope port=$PG_PORT)"
}

start_redis() {
  if redis_alive; then echo "redis:    already up on $REDIS_PORT"; return; fi
  echo "redis:    starting on $REDIS_PORT"
  "$REDIS_BIN/redis-server$EXE" --port "$REDIS_PORT" --daemonize yes \
    --logfile "$ROOT/backend/data/.redislog" >/dev/null 2>&1 || {
      echo "redis failed to start (is $REDIS_BIN/redis-server correct?)"; return 1
    }
  for _ in $(seq 1 15); do redis_alive && break; sleep 1; done
  redis_alive && echo "redis:    ready (port=$REDIS_PORT)" || { echo "redis not responding"; return 1; }
}

stop() {
  [ -d "$PG_DATA" ] && "$PG_BIN/pg_ctl$EXE" -D "$PG_DATA" stop >/dev/null 2>&1 &&
    echo "postgres: stopped" || echo "postgres: not running"
  redis_alive && "$REDIS_BIN/redis-cli$EXE" -p "$REDIS_PORT" shutdown nosave >/dev/null 2>&1 &&
    echo "redis:    stopped" || echo "redis:    not running"
}

case "${1:-start}" in
  start) start_pg; start_redis ;;
  stop) stop ;;
  status) pg_alive && echo "postgres: up" || echo "postgres: down"
          redis_alive && echo "redis:    up" || echo "redis:    down" ;;
  *) echo "usage: $0 [start|stop|status]"; exit 2 ;;
esac
