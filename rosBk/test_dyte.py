import httpx
import os
from dotenv import load_dotenv

load_dotenv()

api_token = os.getenv("DYTE_API_KEY") 
account_id = os.getenv("DYTE_ORGANIZATION_ID")
app_id = os.getenv("CLOUDFLARE_APP_ID")

headers = {
    "Authorization": f"Bearer {api_token}",
    "Content-Type": "application/json"
}

print("Probando creación de reunión y registro en Cloudflare...")

# 1. Crear reunión
meetings_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/realtime/kit/{app_id}/meetings"
meeting_payload = {
    "title": "Reunión de Prueba"
}

try:
    with httpx.Client() as client:
        print("\n--- 1. Creando reunión ---")
        response = client.post(meetings_url, json=meeting_payload, headers=headers, timeout=10.0)
        print(f"Código de estado HTTP: {response.status_code}")
        print(f"Respuesta JSON completa de Creación: {response.text}")
        
        if response.status_code == 201 or response.status_code == 200:
            meeting_data = response.json()
            # Inspeccionar si la respuesta contiene "data" o "result"
            meeting_details = meeting_data.get("data") or meeting_data.get("result")
            if meeting_details:
                meeting_id = meeting_details["id"]
                print(f"\nReunión creada con ID: {meeting_id}")
                
                # 2. Agregar participante
                participants_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/realtime/kit/{app_id}/meetings/{meeting_id}/participants"
                participant_payload = {
                    "name": "Profesor de Prueba",
                    "preset_name": "group_call_host",
                    "client_specific_id": "test_prof_123"
                }
                
                print("\n--- 2. Agregando participante ---")
                p_response = client.post(participants_url, json=participant_payload, headers=headers, timeout=10.0)
                print(f"Código de estado HTTP: {p_response.status_code}")
                print(f"Respuesta JSON completa del Participante: {p_response.text}")
            else:
                print("\nNo se encontró 'data' ni 'result' en la respuesta de la reunión.")
        else:
            print("\nFallo al crear la reunión.")
except Exception as e:
    print(f"Error: {e}")
