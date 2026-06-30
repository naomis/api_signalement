import requests

class SignalementManager:
    def __init__(self, api_url, api_key=None):
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key

    def update_signalement_status(self, id_signalement, status, rejection_reason=None):
        """
        Met à jour le status d'un signalement via l'API.
        """
        url = f"{self.api_url}/signalements/{id_signalement}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = {"status": status}
        if status == "IGNORED" and rejection_reason:
            data["rejectionReason"] = rejection_reason
        response = requests.put(url, json=data, headers=headers)
        response.raise_for_status()
        return response.json()
