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

# Functions to check network interface status.
# This is to ensure that the network interface is up before running the DHCP
# service(s) as they need the interface(s) to be up before binding to them.
is_interface_up() {
    local INTERFACE=$1
    case $SERVICE in
      dhcp4)
        ip -4 link show "$INTERFACE" | grep -q "state UP"
        ;;
      dhcp6|radvd)
        ip -6 link show "$INTERFACE" | grep -q "state UP"
        ;;
    esac
}

function check_network_interface_status {
  local KEA_INTERFACES=("$@")
  local ALL_UP=false
  while [ "$ALL_UP" = false ]; do
    ALL_UP=true
    for INTERFACE in $KEA_INTERFACES ; do
      echo -e "checking interface $INTERFACE"
      if ! is_interface_up "$INTERFACE"; then
        echo "Waiting 30 seconds for $INTERFACE to be up..."
        ALL_UP=false
        sleep 30
        break
      fi
    done
  done
}

# Function to start service
start_service() {
  local EXEC_COMMAND
  local INTERFACES=()

  case "$SERVICE" in
    dhcp4)
      check_vars CONFIG_FILE
      INTERFACES=($(jq -r '.Dhcp4["interfaces-config"].interfaces[]' "$CONFIG_FILE"))
      EXEC_COMMAND="/usr/sbin/kea-dhcp4 -c $CONFIG_FILE"
      ;;
    dhcp6)
      check_vars CONFIG_FILE
      local INTERFACES_RAW=($(jq -r '.Dhcp6["interfaces-config"].interfaces[]' "$CONFIG_FILE"))
      for INTERFACE in "${INTERFACES_RAW[@]}"; do
        INTERFACES+=("${INTERFACE%%/*}")
      done
      EXEC_COMMAND="/usr/sbin/kea-dhcp6 -c $CONFIG_FILE"
      ;;
    radvd)
      check_vars CONFIG_FILE
      while IFS= read -r LINE; do
        INTERFACE=$(echo "$LINE" | grep -oP '^interface\s+\K\S+')
        INTERFACES+=("$INTERFACE")
      done < <(grep '^interface' "$CONFIG_FILE")
      EXEC_COMMAND="/usr/sbin/radvd --nodaemon -C $CONFIG_FILE"
      ;;
  esac

  if [[ "${#INTERFACES[@]}" -gt 0 ]]; then
    check_network_interface_status "${INTERFACES[@]}"
  fi

  exec $EXEC_COMMAND
}

OPTIONS=$(getopt -o '' --long dhcp4,dhcp6,radvd -- "$@")

eval set -- "$OPTIONS"

# Handle the options
while true; do
  case "$1" in
    --dhcp4)
      SERVICE=${1#--}
      start_service
      exit 0
      ;;
    --dhcp6)
      SERVICE=${1#--}
      start_service
      exit 0
      ;;
    --radvd)
      SERVICE=${1#--}
      start_service
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
