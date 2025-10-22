# open-pulse

## How to deploy neo4J

```bash
docker run -d \
  --name neo4j \
  -p7474:7474 -p7687:7687 \
  -v ./data:/data \
  -e NEO4J_AUTH=neo4j/your_password \
  neo4j:latest
```
## How to deploy tentris

```bash
docker run -d \
  -p 9080:9080 \
  --name hackathon-db \
  -v "$(pwd)/tentris-license.toml:/config/tentris-license.toml:ro" \
  -v "$(pwd)/tentris-server-config.toml:/config/tentris-server-config.toml:ro" \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/instance_data.ttl:/app/instance_data.ttl:ro" \
  -e "TENTRIS_RDF_FILE=/app/instance_data.ttl" \
  ghcr.io/tentris/tentris:latest
```