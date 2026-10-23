import requests
from requests.auth import HTTPBasicAuth
import urllib3

# Отключаем предупреждения о самоподписанном сертификате
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://192.168.1.109:9999/api/v1/status"  # Обратите внимание: https
auth = HTTPBasicAuth("admin", "admin")

try:
    # verify=False отключает проверку сертификата
    response = requests.get(url, auth=auth, timeout=10, verify=False)
    response.raise_for_status()
    print("Успех!", response.json())
except Exception as e:
    print(f"Ошибка: {e}")
