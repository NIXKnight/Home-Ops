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

# Function to check and initialize PowerDNS Server
function check_init_powerdns_server {
  check_vars PDNS_DB_BACKEND PDNS_DB_HOST PDNS_DB_USERNAME PDNS_DB_PASSWORD PDNS_DB_DATABASE
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
  echo "Starting PowerDNS Authoritative Server..."
  exec /usr/sbin/pdns_server --config-dir=/etc/powerdns/
}

# Function to start PowerDNS Recursor
function start_recursor {
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
