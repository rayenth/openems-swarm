import requests

url = "http://127.0.0.1:8000/simulate"

# Example patch antenna dimensions and frequency sweep parameters
payload = {
    "patch_length": 38.0,
    "patch_width": 48.0,
    "substrate_thickness": 1.524,
    "frequency_start": 2.0e9,
    "frequency_stop": 3.0e9,
    "frequency_points": 201
}

try:
    print(f"Sending request to {url}...")
    response = requests.post(url, json=payload)
    
    if response.status_code == 200:
        data = response.json()
        print("\nSimulation Successful!")
        print(f"Status: {data['status']}")
        print(f"Resonant Frequency: {data['resonant_frequency'] / 1e9:.3f} GHz")
        print(f"Minimum S11: {data['min_s11']:.2f} dB")
    else:
        print(f"\nServer Error [{response.status_code}]: {response.text}")

except requests.exceptions.ConnectionError:
    print("Failed to connect to the server. Ensure uvicorn is running on port 8000.")
