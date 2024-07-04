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
    ip link show "$INTERFACE" | grep -q "state UP"
}

function check_network_interface_status {
  local KEA_INTERFACES=("$@")
  local ALL_UP=false
  while [ "$ALL_UP" = false ]; do
    ALL_UP=true
    for INTERFACE in $KEA_INTERFACES ; do
      if ! is_interface_up "$INTERFACE"; then
        echo "Waiting for $INTERFACE to be up..."
        ALL_UP=false
        sleep 1
        break
      fi
    done
  done
}

# Function to start Kea DHCP4 Server
function start_kea_dhcp4 {
  check_vars KEA_DHCP4_CONFIG_FILE
  local KEA_INTERFACES=($(jq -r '.Dhcp4["interfaces-config"].interfaces[]' "$KEA_DHCP4_CONFIG_FILE"))
  check_network_interface_status "${KEA_INTERFACES[@]}"
  exec /usr/sbin/kea-dhcp4 -c $KEA_DHCP4_CONFIG_FILE
}

OPTIONS=$(getopt -o '' --long dhcp4 -- "$@")

eval set -- "$OPTIONS"

# Handle the options
while true; do
  case "$1" in
    --dhcp4)
      start_kea_dhcp4
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
