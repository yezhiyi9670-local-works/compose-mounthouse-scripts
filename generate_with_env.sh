#!/bin/bash

set -eu -o pipefail

env_file=${1:-.env}

if [ -f "$env_file" ]; then
    # https://github.com/hattan/dot_env_bash/blob/main/lib/dot_env.sh
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments
        [[ $line =~ ^[[:space:]]*$ ]] && continue
        [[ $line =~ ^[[:space:]]*# ]] && continue
        # Remove inline comments and export
        line=$(echo "$line" | sed 's/#.*//')
        # Export the variable
        export "$line"
    done <"$env_file"
fi

python3 mounthouse/scripts/main.py generate
