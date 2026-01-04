import requests

API_KEY = "sk-e8riaifpdd7myix682wtzsoas65tnmkf529cnk82h0vkordp"
API_URL = "https://api.xiaomimimo.com/v1/models"

headers = {
    "Authorization": f"Bearer {API_KEY}"
}

try:
    response = requests.get(API_URL, headers=headers)
    print(f"Status: {response.status_code}")
    print(response.text)
except Exception as e:
    print(f"Error: {e}")
