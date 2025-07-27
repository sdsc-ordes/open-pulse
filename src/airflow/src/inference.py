import requests
import logging
import os

BASE_URL = os.getenv('INFERENCE_URL')
NEO4J_DB = os.getenv('NEO4J_DATABASE')
API_TOKEN = os.getenv('API_TOKEN')

def call_ml_inference():
    url = f"{BASE_URL}/v1/inference/epfl/{NEO4J_DB}"
    headers = {
        'Authorization': f'Bearer {API_TOKEN}'
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        logging.info("Inference API response: %s", result)
    except requests.exceptions.RequestException as e:
        logging.error("Failed to call inference endpoint: %s", str(e))
        raise

if __name__ == "__main__":
    call_ml_inference()
