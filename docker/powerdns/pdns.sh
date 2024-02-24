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
  check_vars PDNS_MYSQL_HOST PDNS_MYSQL_USERNAME PDNS_MYSQL_PASSWORD PDNS_MYSQL_DATABASE PDNS_LOCAL_PORT PDNS_API_KEY PDNS_API_ADDRESS PDNS_API_PORT PDNS_API_ALLOW_FROM
  envsubst < /etc/powerdns/template/pdns.conf.template > /etc/powerdns/pdns.conf
}

# Function to generate PowerDNS Recursor configuration
function generate_recursor_config {
  check_vars PDNS_RECURSOR_LOCAL_ZONE PDNS_RECURSOR_LOCAL_DNS_ADDRESS PDNS_RECURSOR_LOCAL_DNS_PORT PDNS_RECURSOR_UPSTREAM_RESOLVERS
  envsubst < /etc/powerdns/template/recursor.conf.template > /etc/powerdns/recursor.conf
}

# Function to start PowerDNS Server
function start_pdns {
  while ! nc -z $PDNS_MYSQL_HOST 3306; do
    echo "Waiting for MySQL ($MYSQL_HOST:3306) to start..."
    sleep 3
  done
  echo "MySQL is reachable. Checking database initialization..."
  TABLE_CHECK=$(mysql -h $PDNS_MYSQL_HOST -u $PDNS_MYSQL_USERNAME -p$PDNS_MYSQL_PASSWORD -D $PDNS_MYSQL_DATABASE -e "SHOW TABLES LIKE 'domains';" 2>/dev/null | grep 'domains')
  # If the table doesn't exist, initialize the database
  if [[ -z "$TABLE_CHECK" ]]; then
    echo "Database not initialized. Initializing..."
    mysql -h $PDNS_MYSQL_HOST -u $PDNS_MYSQL_USERNAME -p$PDNS_MYSQL_PASSWORD -D $PDNS_MYSQL_DATABASE < /usr/share/pdns-backend-mysql/schema/schema.mysql.sql
    if [ $? -eq 0 ] ; then
      echo "Database initialized successfully."
    else
      echo "Error: Database initialization Failed!"
      exit 1
    fi
  else
    echo "Database already initialized. Skipping schema creation."
  fi
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
