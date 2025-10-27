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
docker compose up -d
```

then, on the user side to run queries:

```sh
curl -c "/tmp/my-tentris-cookie"  --data "username=test&password=test" http://localhost:9080/login
```

```sh
curl -b "/tmp/my-tentris-cookie" -H "Content-Type: application/sparql-update"  --data "INSERT DATA { <s> <p> <o> }" http://localhost:9080/update
```                                                                                     
