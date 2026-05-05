set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

pre-commit:
    npx markdownlint-cli2 --config .markdownlint.jsonc --fix "infra/services/**/*.md"
    uv run --with pre-commit pre-commit run --all-files

# Re-render the grimoire setup.cfg (after editing
# data/grimoirelab/projects-conf/projects.json or any GRIMOIRE_* /
# OPENSEARCH_* / SORTINGHAT_* var in .env), then restart mordred so it
# picks up the new config. The init container is one-shot — `Exited (0)`
# is its normal resting state; --force-recreate triggers a fresh render.
regen-grimoire-config:
    docker compose --project-name open-pulse --project-directory "$PWD" \
        -f infra/services/grimoirelab/docker-compose.yml --env-file .env \
        up -d --force-recreate --no-deps prepare-grimoire-config
    docker restart open-pulse-mordred-1
