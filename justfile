set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Run the same gates as CI's `python-tests`, `pre-commit-quality-gates`,
# and `shell-script-sanity` jobs in .github/workflows/ci.yml. Matches the
# CI install (uv sync --group dev --group test + pytest-cov) so a green
# run here predicts a green PR.
pre-commit:
    npx markdownlint-cli2 --config .markdownlint.jsonc --fix "**/*.md" "#docs-site/node_modules/**" "#.venv/**" "#.venv-matrix/**"
    uv run --with pre-commit pre-commit run --all-files
    uv sync --group dev --group test
    uv run --with pytest-cov pytest -q --cov=src --cov-report=term-missing --cov-report=xml
    mapfile -t shell_files < <(git ls-files '*.sh'); \
        if [ "${#shell_files[@]}" -eq 0 ]; then \
            echo "No shell scripts found."; \
        elif command -v shellcheck >/dev/null 2>&1; then \
            shellcheck "${shell_files[@]}"; \
        else \
            echo "shellcheck not installed — skipping (CI will still run it). Install: sudo apt-get install -y shellcheck"; \
        fi

# Run pytest across the same Python matrix as CI's `python-tests` job
# (3.11 / 3.12 / 3.13). Each version gets its own venv under
# .venv-matrix/<ver> so the project .venv is left untouched. uv will
# fetch any missing interpreters. Slower than `just pre-commit`; run
# before pushing if you want full CI parity locally.
pre-commit-matrix:
    for v in 3.11 3.12 3.13; do \
        echo "=== Python $v ==="; \
        UV_PROJECT_ENVIRONMENT=".venv-matrix/$v" uv sync --python "$v" --group dev --group test; \
        UV_PROJECT_ENVIRONMENT=".venv-matrix/$v" uv run --python "$v" --with pytest-cov pytest -q --cov=src --cov-report=term-missing; \
    done

# Re-render the grimoire setup.cfg (after editing
# data/grimoirelab/projects-conf/projects.json or any GRIMOIRE_* /
# OPENSEARCH_* / SORTINGHAT_* var in .env), then restart mordred so it
# picks up the new config. The init container is one-shot — `Exited (0)`
# is its normal resting state; --force-recreate triggers a fresh render.
regen-grimoire-config:
    docker compose --project-name open-pulse --project-directory "$PWD" \
        -f infra/open-pulse-stack/docker-compose.grimoirelab.yml \
        --env-file infra/.env \
        up -d --force-recreate --no-deps prepare-grimoire-config
    docker restart open-pulse-mordred-1
