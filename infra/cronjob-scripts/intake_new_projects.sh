#!/usr/bin/env bash

WATCHED_FILE="../default-grimoirelab-settings/projects.json"
STATE_FILE="/tmp/projects_file_checksum_mordred"

if [ ! -f "$WATCHED_FILE" ]; then
    echo "The file you want to watch or is being watched does not exist: $WATCHED_FILE"
    exit 1
fi

CURRENT_SUM=$(sha256sum "$WATCHED_FILE" | awk '{print $1}')

# If state file does not exist, create it
if [ ! -f "$STATE_FILE" ]; then
    echo "$CURRENT_SUM" > "$STATE_FILE"
    exit 0
fi

PREVIOUS_SUM=$(cat "$STATE_FILE")

if [ "$CURRENT_SUM" != "$PREVIOUS_SUM" ]; then
    echo "Projects file changed. Restarting docker compose for mordred..."

    docker compose -p docker-compose-mordred-1 restart

    echo "$CURRENT_SUM" > "$STATE_FILE"
fi
