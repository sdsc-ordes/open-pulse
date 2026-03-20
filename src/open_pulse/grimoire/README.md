# How To Integrate Mordred Extraction to OpenSearchDashboards

This guide presumes the docker compose is successfully running.

Adapt all your credentials to the following commands.

For any interactions with GitHub, copy `.env.dist` to `.env` in this folder and set `GITHUB_API_TOKEN` there, along with the other GrimoireLab credentials used by Docker Compose and the generated Mordred `setup.cfg`.

## Launch Grimoire

Checklist before launching:

- credentials are configured (or default are being used)
- `.env` has been filled from `.env.dist` with the required GitHub and optional GitLab tokens
- all projects that need to be extracted are in projects.json under the right categories (git, github and gitlab)

For exposing the open search dashboards on a specific port, configure the nginx ports in docker compose.

Go to docker compose folder and do `docker compose up -d`. Give it some time (10-20 min), Mordred will extract all repos.

## Integrate the Dashboards (Sigils, OpenSearch) with the Data Extraction (Mordred, Sorting Hat)

Do this process after the first deployment of the GrimoireLab via docker compose.

### 1. Make / Get the data sources (index patterns)

This command is relevant for creating the index patterns for git, github and gitlab. 

- Git covers: Commits, files, authors
- Github covers: Issues, PRs, reviews, comments

```bash
curl -u admin:GrimoireLab.1 -X POST "http://localhost:5601/api/saved_objects/index-pattern" \
    -H "osd-xsrf: true" \
    -H "Content-Type: application/json" \
    -d '
    {
      "attributes": {
        "title": "github*"
      }
    }'
```

There is also a script in `scripts` which can be run with `./bootstrap_host_setup.sh`. It installs the intake cronjob on the host and runs the OpenSearch preparation service that creates the index patterns and uploads the sigils. If you prefer Python for the host tooling, run `python3 ./python-scripts/bootstrap_host_setup.py` instead.

### 2. Make the SIGIL dashboards

```bash
curl -u admin:GrimoireLab.1 -X POST "http://localhost:5601/api/saved_objects/_import?overwrite=true" \
  -H "osd-xsrf:true" \
  --form file=@git.ndjson
```

[All SIGIL dashboards can be found here](https://github.com/chaoss/grimoirelab-sigils/tree/main/panels/json/opensearch_dashboards).

There is also a script in `scripts` which can be run with `./bootstrap_host_setup.sh`, and it will trigger the dashboard preparation flow that uploads all sigils relevant to git, GitHub and GitLab. The same host bootstrap path is available in Python at `python3 ./python-scripts/bootstrap_host_setup.py`.

### 3. Custom Bug Fixes

#### A. Aliases

If any of the aliases are missing in dashboards with a message such as `Opensearch index does not exist: INDEX`, you can create it with the following command (either cli or in Dev Tools in the dashboard).

```bash
POST /_aliases
{
  "actions": [
    {
      "add": {
        "index": "github_enriched",
        "alias": "github_issues"
      }
    }
  ]
}
```

The bootstrap flows now automate this as part of the setup scripts. You can also run it directly with `./scripts/create_opensearch_aliases.sh` or `python3 ./python-scripts/create_opensearch_aliases.py`.

#### B. Dashboards

If there are issues in the GitHub PR and Issues dashboards with fields like Submitters then the following bug fix is necessary. Follow these community recommendations: the fix is the following: https://github.com/chaoss/grimoirelab-sigils/issues/517

## Updating projects

1. Edit the `projects.json` file. 
2. Restart the mordred container with `docker compose restart mordred`
3. Remember to `Refresh` the Dashboard

For automatic updating, run `./scripts/bootstrap_host_setup.sh` or `python3 ./python-scripts/bootstrap_host_setup.py`. They install a host cron entry that calls the watcher using absolute paths, so it keeps working regardless of the current working directory from which Docker Compose was started.

The cron schedule is controlled by `GRIMOIRE_INTAKE_CRON_SCHEDULE` in `.env`, and the watcher can optionally `git pull` before checking `projects.json` via `GRIMOIRE_ENABLE_GIT_PULL=true`.

## Utils for debugging

### List data sources / index patterns

Sanity check. It can also be seen on OpenSearch UI under Dashboard Management - Index patterns.

```bash
curl -u admin:GrimoireLab.1 -X GET "http://localhost:5601/api/saved_objects/_find?type=index-pattern" \
  -H "osd-xsrf: true"
```

### Check the indexes and aliases

This allows to confirm the Mordred extraction went well for all your projects in projects.json. You can also confirm by seeing `collection finished` in the Mordred docker logs. 

It can also be seen on OpenSearch UI under Index Management - Indexes.

```bash
curl -k -u admin:GrimoireLab.1 -X GET "https://localhost:9200/_cat/indices?s=i"
```

```bash
curl -k -u admin:GrimoireLab.1 -X GET "https://localhost:9200/_cat/aliases?v"
```

(ignore check of certificates with `-k`)

### Get the dashboards

It can also be seen on OpenSearch UI under Dashbaords Management - Saved Objects.

```bash
curl -u admin:GrimoireLab.1 -X GET "http://localhost:5601/api/saved_objects/_find?type=dashboard" \   
  -H "osd-xsrf: true"
```
