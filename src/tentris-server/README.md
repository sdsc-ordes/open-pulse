# Tentris Server (Dockerized)

This directory contains the necessary files to run a pre-loaded, read-only Tentris database using the official Docker image.
This setup is intended to provide a central, high-performance SPARQL endpoint for an event like a hackathon. It uses a persistent volume, so the data is loaded only on the first run.
## Quick Start 

1. Prerequisites
Docker must be installed.You must have your instance_data.ttl file (which is not in Git) placed in this directory.
You must have your tentris-license.toml file (also not in Git) placed in this directory.

2. Prepare Directories
Open a terminal and navigate into this directory:cd tentris-server
Create an empty directory named data. This is where the container will store its persistent, indexed database.
`mkdir data`

3. Run the Container
Copy and paste the command below. This will:
- Pull the latest official Tentris image.
- Mount your license, config, and data files.
- Mount the empty data directory for persistence.
- Tell the container to load your instance_data.ttl on its first run.
- Run the container in detached mode

```bash
docker compose up -d
```