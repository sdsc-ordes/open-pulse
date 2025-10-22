# Airflow Deployment

## Linux set-up

```
mkdir -p ./dags ./logs ./plugins ./config
echo -e "AIRFLOW_UID=$(id -u)" > .env
```
(One must run these commands before launching the docker compose)

## Docker compose 

```
docker compose up -d
```
(Note: the api server is currently configured for port `8080`.)

## Sending in DAGs 

Dags can be directly placed under the dags folder. Note: if airflow needs to be redeployed, then all folders must be deleted and recreated, DAGs should be backed up somewhere else. 

For executing a API call: 

(if on the machine, keep localhost, else replace with machine IP.)

1. Get your JWT token 
```
curl -X POST http://localhost:8080/auth/token   -H "Content-Type: application/json"   -d '{
    "username": "airflow",
    "password": "airflow"
  }'
```
The response is YOUR_TOKEN mentioned below.

2. Get the DACS 
```
curl -X GET "http://localhost:8080/api/v2/dags" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

3. Trigger a DAG - Coming soon


