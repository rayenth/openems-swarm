import requests

url = "http://127.0.0.1:8000/simulate"

# Every parameter is specified by the client (lengths in mm, frequencies in Hz).
# Any field you omit falls back to the server-side default defined in schemas.py.
payload = {
    "patch": {"length": 38.0, "width": 48.0},
    "substrate": {
        "length": 60.0,
        "width": 60.0,
        "thickness": 1.524,
        "epsilon_r": 3.38,
        "loss_tangent": 0.0027,
    },
    "feed": {"offset_x": -6.0, "offset_y": 0.0, "impedance": 50.0},
    "frequency": {"start": 2.0e9, "stop": 3.0e9, "points": 201},
    "solver": {
        "cells_per_wavelength": 20,
        "substrate_cells": 4,
        "smoothing_ratio": 1.4,
        "air_padding": None,      # None -> lambda/4 at centre frequency
        "pml_cells": 8,           # 0 -> MUR boundary
        "max_timesteps": 30000,
        "end_criteria": 1e-4,
        "num_threads": None,
        "verbose": 0,
    },
}

try:
    print(f"Sending request to {url}...")
    response = requests.post(url, json=payload, timeout=3600)  # FDTD can take a while

    if response.status_code == 200:
        data = response.json()
        print("\nSimulation Successful!")
        print(f"Status:             {data['status']}")
        print(f"Resonant Frequency: {data['resonant_frequency'] / 1e9:.3f} GHz")
        print(f"Minimum S11:        {data['min_s11']:.2f} dB")
        bw = data["bandwidth_10db"]
        print(f"-10 dB Bandwidth:   {bw / 1e6:.1f} MHz" if bw else "-10 dB Bandwidth:   n/a (S11 never below -10 dB)")
        print(f"Solver time:        {data['simulation_time_s']:.1f} s")
    else:
        print(f"\nServer Error [{response.status_code}]: {response.text}")

except requests.exceptions.ConnectionError:
    print("Failed to connect to the server. Ensure uvicorn is running on port 8000.")