from airflow import DAG
from airflow.providers.docker.operators.docker import DockerOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'airflow',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='weekly_inference_dag',
    default_args=default_args,
    description='Trigger ML inference every week',
    schedule_interval='0 9 * * 1',  # Every Monday at 9:00 AM
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['ml', 'neo4j', 'inference'],
) as dag:

    inference_task = DockerOperator(
        task_id='inference',
        image='ghcr.io/sdsc-ordes/open-pulse-airflow:latest',
        auto_remove=True,
        environment={
            'INFERENCE_URL': Variable.get("INFERENCE_URL"),
            'NEO4J_DATABASE': Variable.get("NEO4J_DATABASE"),
            'API_TOKEN': Variable.get("API_TOKEN"), 
        },
    )

    inference_task