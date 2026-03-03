set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

pre-commit:
    npx markdownlint-cli2 --config .markdownlint.jsonc --fix "infra/services/**/*.md"
    uv run --with pre-commit pre-commit run --all-files
