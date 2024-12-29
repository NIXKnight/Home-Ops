#!/bin/bash

# This script assumes that you have installed deye-controller (https://github.com/githubDante/deye-controller) Python library and tools
# and that deye-read is available in PATH

DEYE_INVERTER_ADDRESS=$1
DEYE_INVERTER_SN=$2

# Function to run deye-read with a timeout and JSON validation
run_deye_read() {
  local OUTPUT
  OUTPUT=$(timeout 7s deye-read --json $DEYE_INVERTER_ADDRESS $DEYE_INVERTER_SN 2>/dev/null)

  # Check if the output is valid JSON
  if echo "$OUTPUT" | jq empty > /dev/null 2>&1; then
    echo "$OUTPUT"
  else
    return 1
  fi
}

# Try running the command, with one retry if it fails
DEYE_JSON_OUTPUT=$(run_deye_read)
if [[ $? -ne 0 ]]; then
  DEYE_JSON_OUTPUT=$(run_deye_read)
fi

# If the command still fails, exit with an error
if [[ $? -ne 0 ]]; then
  echo "Failed to retrieve valid JSON data from deye-read after retries." >&2
  exit 1
fi

# Extract all keys and their numerical/float values dynamically and ensure no empty values
echo "$DEYE_JSON_OUTPUT" | jq -r '.data[] | to_entries[] | select(.value.value | type == "number" and . != null) | "deye_inverter,host='"$(hostname)"' \(.key)=\(.value.value)"'
