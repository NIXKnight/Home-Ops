#!/bin/bash

# Function to check if variables are set
function check_vars {
  local all_vars_set=true
  for var in "$@"; do
    if [[ -z "${!var}" ]]; then
      echo "Error: Required environment variable $var is not set."
      all_vars_set=false
    fi
  done

  if [ "$all_vars_set" = "false" ]; then
    exit 1
  fi
}

# Function to generate PowerDNS Server configuration
function generate_pdns_config {
  : ${PDNS_API_ALLOW_FROM:=127.0.0.1}
  export PDNS_API_ALLOW_FROM
  check_vars PDNS_DB_BACKEND PDNS_DB_HOST PDNS_DB_USERNAME PDNS_DB_PASSWORD PDNS_DB_DATABASE PDNS_LOCAL_PORT PDNS_API_KEY PDNS_API_ADDRESS PDNS_API_PORT PDNS_API_ALLOW_FROM
  envsubst < /etc/powerdns/template/pdns.conf.template > /etc/powerdns/pdns.conf
}

# Function to generate PowerDNS Recursor configuration
function generate_recursor_config {
  CPU_CORES=$(nproc)
  HALF_CPU_CORES=$((CPU_CORES / 2))
  if [ "$HALF_CPU_CORES" -lt 2 ]; then
    export PDNS_RECURSOR_THREADS=2
  else
    export PDNS_RECURSOR_THREADS=$HALF_CPU_CORES
  fi
  echo -e "PDNS Recursor threads set to $PDNS_RECURSOR_THREADS"
  check_vars PDNS_RECURSOR_LOCAL_ZONE PDNS_RECURSOR_LOCAL_DNS_ADDRESS PDNS_RECURSOR_LOCAL_DNS_PORT PDNS_RECURSOR_UPSTREAM_RESOLVERS PDNS_RECURSOR_MAX_CACHE_ENTRIES PDNS_RECURSOR_RECEIVE_BUFFER_SIZE PDNS_RECURSOR_SEND_BUFFER_SIZE
  envsubst < /etc/powerdns/template/recursor.conf.template > /etc/powerdns/recursor.conf
}

# Function to check and initialize PowerDNS Server
function check_init_powerdns_server {
  case "$PDNS_DB_BACKEND" in
    gmysql)
      DB_BACKEND_NAME="MySQL"
      DB_PORT=3306
      DB_CHECK_CMD="mysql -h $PDNS_DB_HOST -u $PDNS_DB_USERNAME -p$PDNS_DB_PASSWORD -D $PDNS_DB_DATABASE -e \"SHOW TABLES LIKE 'domains';\" 2>/dev/null | grep -q 'domains'"
      DB_INIT_CMD="mysql -h $PDNS_DB_HOST -u $PDNS_DB_USERNAME -p$PDNS_DB_PASSWORD -D $PDNS_DB_DATABASE < /usr/share/pdns-backend-mysql/schema/schema.mysql.sql"
      ;;
    gpgsql)
      DB_BACKEND_NAME="PostgreSQL"
      DB_PORT=5432
      export PGPASSWORD=$PDNS_DB_PASSWORD
      DB_CHECK_CMD="psql -h $PDNS_DB_HOST -U $PDNS_DB_USERNAME -d $PDNS_DB_DATABASE -tAc \"SELECT 1 FROM pg_tables WHERE tablename = 'domains'\" 2>/dev/null | grep -q '1'"
      DB_INIT_CMD="psql -h $PDNS_DB_HOST -U $PDNS_DB_USERNAME -d $PDNS_DB_DATABASE -f /usr/share/pdns-backend-pgsql/schema/schema.pgsql.sql"
      ;;
    *)
      echo "Database backend not recognized! Exiting."
      exit 1
      ;;
  esac

  echo "Waiting for $DB_BACKEND_NAME ($PDNS_DB_HOST:$DB_PORT) to start..."
  while ! nc -z $PDNS_DB_HOST $DB_PORT; do
    sleep 3
  done
  echo "$DB_BACKEND_NAME is reachable. Checking database initialization..."

  if eval $DB_CHECK_CMD; then
    echo "Database already initialized. Skipping schema creation."
  else
    echo "Database not initialized. Initializing..."
    if eval $DB_INIT_CMD; then
      echo "Database initialized successfully."
    else
      echo "Error: Database initialization failed!"
      exit 1
    fi
  fi
}

# Function to start PowerDNS Server
function start_pdns {
  check_init_powerdns_server
  echo "Generating PowerDNS Authoritative Server configuration..."
  generate_pdns_config
  echo "Starting PowerDNS Authoritative Server..."
  exec /usr/sbin/pdns_server --config-dir=/etc/powerdns/
}

# Function to start PowerDNS Recursor
function start_recursor {
  echo "Generating PowerDNS Recursor configuration..."
  generate_recursor_config
  echo "Starting PowerDNS Recursor..."
  exec /usr/sbin/pdns_recursor --config-dir=/etc/powerdns/
}

OPTIONS=$(getopt -o '' --long pdns,recursor -- "$@")

eval set -- "$OPTIONS"

# Handle the options
while true; do
  case "$1" in
    --pdns)
      start_pdns
      exit 0
      ;;
    --recursor)
      start_recursor
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "Invalid option: $1"
      exit 1
      ;;
  esac
done
