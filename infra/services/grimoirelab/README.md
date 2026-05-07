# How To Integrate Mordred Extraction to OpenSearchDashboards

This guide describes how the current Grimoire Docker Compose setup behaves.

For any interactions with GitHub, copy `.env.dist` to `.env` in this folder and set `GITHUB_API_TOKEN` there, along with the other GrimoireLab credentials used by Docker Compose and the generated Mordred `setup.cfg`.

## Launch Grimoire

Checklist before launching:

- credentials are configured (or default are being used)
- `.env` has been filled from `.env.dist` with the required GitHub and optional GitLab tokens
- all projects that need to be extracted are in `config/projects.json` under the right categories

For exposing the OpenSearch Dashboards UI on a specific port, configure the `nginx` ports in `docker-compose.yml`.

Start the stack from this folder:

```bash
docker compose up -d
```

## What Docker Compose Does

When you run `docker compose up -d`, the compose file starts the main services plus two helper services:

- `prepare-grimoire-config`
- `prepare-opensearch`

### `prepare-grimoire-config`

This one-shot container runs first and:

- reads `.env`
- renders `config/setup.cfg.template` into a concrete `setup.cfg`
- copies `config/projects.json`
- writes both files into the shared `grimoire-conf` Docker volume

`mordred` then mounts that generated config from `/home/grimoire/conf`.

### `prepare-opensearch`

This one-shot container waits for:

- the OpenSearch API
- the OpenSearch Dashboards API

Then it executes every shell script in `scripts/` in filename order, except the host-only scripts that are explicitly skipped.

At the moment, the compose-driven setup path uses the shell scripts under `scripts/`, not the Python equivalents under `python-scripts/`.

The scripts in `scripts/` are used for:

- creating aliases
- creating index patterns
- uploading dashboard NDJSON files

Because these scripts are executed in filename order, failures in an earlier script can prevent later setup steps from running.

## What Docker Compose Does Not Do

`docker compose up -d` does not install any host cronjob.

The project watcher for `projects.json` is a host-side script and must be installed manually. This is separate from the container startup flow.

## Manual Cronjob Installation

To install the watcher cronjob on the host, run one of these manually:

```bash
bash ./scripts/install_intake_cronjob.sh
```

or:

```bash
python3 ./python-scripts/install_intake_cronjob.py
```

These commands create a host cron entry that periodically runs the watcher script which:

- optionally performs `git pull`
- checks whether `config/projects.json` changed
- restarts the `mordred` service if it did

The cron schedule is controlled by `GRIMOIRE_INTAKE_CRON_SCHEDULE` in `.env`, and the watcher can optionally `git pull` before checking `projects.json` via `GRIMOIRE_ENABLE_GIT_PULL=true`.

To confirm the cronjob was installed:

```bash
crontab -l
```

## Dashboards and SIGIL Setup

The compose startup path runs the setup scripts automatically through `prepare-opensearch`.

You can also run individual scripts directly if you need to retry a step:

- `./scripts/make_index_patterns.sh`
- `./scripts/upload_sigils_to_opensearch.sh`
- `./scripts/create_opensearch_aliases.sh`

Python equivalents also exist under `python-scripts/`.

[All SIGIL dashboards can be found here](https://github.com/chaoss/grimoirelab-sigils/tree/main/panels/json/opensearch_dashboards).

If there are issues in the GitHub PR and Issues dashboards with fields like Submitters then the relevant fix is tracked here: [chaoss/grimoirelab-sigils#517](https://github.com/chaoss/grimoirelab-sigils/issues/517)

## Updating Projects

1. Edit `config/projects.json`.
2. Restart `mordred` manually with `docker compose restart mordred`, or let the manually installed cronjob detect the change.
3. Refresh OpenSearch Dashboards.

## Utils for debugging

### List data sources / index patterns

Sanity check. It can also be seen on OpenSearch UI under Dashboard Management - Index patterns.

```bash
curl -u admin:YOUR_PASSWORD -X GET "http://localhost:5601/api/saved_objects/_find?type=index-pattern" \
  -H "osd-xsrf: true"
```

### Check the indexes and aliases

This allows to confirm the Mordred extraction went well for all your projects in projects.json. You can also confirm by seeing `collection finished` in the Mordred docker logs.

It can also be seen on OpenSearch UI under Index Management - Indexes.

```bash
curl -k -u admin:YOUR_PASSWORD -X GET "https://localhost:9200/_cat/indices?s=i"
```

```bash
curl -k -u admin:YOUR_PASSWORD -X GET "https://localhost:9200/_cat/aliases?v"
```

(ignore check of certificates with `-k`)

### Get the dashboards

It can also be seen on OpenSearch UI under Dashbaords Management - Saved Objects.

```bash
curl -u admin:YOUR_PASSWORD -X GET "http://localhost:5601/api/saved_objects/_find?type=dashboard" \
  -H "osd-xsrf: true"
```
