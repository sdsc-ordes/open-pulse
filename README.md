# open-pulse

## How to deploy neo4J

```
docker run -d \
  --name neo4j \
  -p7474:7474 -p7687:7687 \
  -v ./data:/data \
  -e NEO4J_AUTH=neo4j/your_password \
  neo4j:latest
```
