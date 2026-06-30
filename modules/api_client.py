import requests
from typing import List, Dict, Any

class BaseAPIClient:
    def __init__(self, base_url: str, route_suffix: str):
        self.base_url = base_url.rstrip('/')
        self.route_suffix = route_suffix.strip('/')

    def fetch_items(self, codes_insee: List[str], status: str = "PENDING", limit: int = 100, source_id: str = None) -> List[Dict[str, Any]]:
        """Récupère tous les items pour une liste de codes INSEE, paginés par lots de 20."""
        all_data = []
        def chunked(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]
        for codes_chunk in chunked(codes_insee, 20):
            page = 1
            while True:
                params = [("status", status), ("limit", limit), ("page", page)]
                params += [("codeCommunes", code) for code in codes_chunk]
                if source_id:
                    params.append(("sourceIds", source_id))
                url = f"{self.base_url}/{self.route_suffix}"
                # Affiche l'URL complète appelée pour debug
                req = requests.Request('GET', url, params=params).prepare()
                print(f"[DEBUG] Appel API: {req.url}")
                response = requests.get(url, params=params)
                try:
                    result = response.json()
                except Exception as e:
                    print("Erreur lors du décodage JSON:", e)
                    print("Status code:", response.status_code)
                    print("Contenu brut de la réponse:", response.text)
                    raise
                data = result.get("data", [])
                if not data:
                    break
                all_data.extend(data)
                if len(data) < limit:
                    break
                page += 1
        return all_data

class SignalementAPIClient(BaseAPIClient):
    def __init__(self, base_url: str):
        super().__init__(base_url, "signalements")

    def fetch_signalements(self, codes_insee: List[str], status: str = "PENDING", limit: int = 100) -> List[Dict[str, Any]]:
        return self.fetch_items(codes_insee, status=status, limit=limit)

class AlertAPIClient(BaseAPIClient):
    def __init__(self, base_url: str):
        super().__init__(base_url, "alerts")

    def fetch_alerts(self, codes_insee: List[str], status: str = "PENDING", limit: int = 100) -> List[Dict[str, Any]]:
        return self.fetch_items(codes_insee, status=status, limit=limit)
