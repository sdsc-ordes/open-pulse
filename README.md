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

then, to update the data via queries:
Login on the Terminal
curl can be used to log in via the terminal.
```
curl -c "/tmp/my-cookie" \
    --data "username=YOUR_USERNAME&password=YOUR_PASSWORD" http://128.178.219.51:7502/login
```
After logging in, you need to send the cookie you received to the server every time you want to issue a query (with -b).
```
curl -b "/tmp/my-cookie" -H "Content-Type: application/sparql-update" \
    --data "CONSTRUCT {<s> <p> <o>} WHERE { ?s ?p ?o }" http://128.178.219.51:7502/update
```
                                                                        
