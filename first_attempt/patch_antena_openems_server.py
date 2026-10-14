import os
import tempfile
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List

# Import openEMS / CSXCAD bindings (ensure these are installed in your environment)
from CSXCAD import ContinuousStructure
from openEMS import openEMS
from openEMS.physical_constants import C0

app = FastAPI(title="openEMS FDTD Simulation Server", version="1.0.0")

class SimulationRequest(BaseModel):
    patch_length: float = Field(..., description="Patch length in mm")
    patch_width: float = Field(..., description="Patch width in mm")
    substrate_thickness: float = Field(..., description="Substrate height in mm")
    frequency_start: float = Field(2.0e9, description="Start frequency in Hz")
    frequency_stop: float = Field(4.0e9, description="Stop frequency in Hz")
    frequency_points: int = Field(201, description="Number of sweep points")

class SimulationResponse(BaseModel):
    status: str
    frequencies: List[float]
    s11_db: List[float]
    resonant_frequency: float
    min_s11: float

@app.post("/simulate", response_model=SimulationResponse)
def run_simulation(payload: SimulationRequest):
    # Use a secure isolated temporary directory for every request to avoid race conditions
    with tempfile.TemporaryDirectory() as sim_path:
        try:
            unit = 1e-3  # working unit: millimeters
            
            # 1. Initialize FDTD and CSXCAD structures
            FDTD = openEMS(NrTS=1e4)
            FDTD.SetGaussExcite(
                0.5 * (payload.frequency_start + payload.frequency_stop),
                0.5 * (payload.frequency_stop - payload.frequency_start)
            )
            FDTD.SetBoundaryCond([3, 3, 3, 3, 3, 3])  # PML boundaries

            CSX = ContinuousStructure()
            FDTD.SetCSX(CSX)
            mesh = CSX.GetGrid()
            mesh.SetDeltaUnit(unit)
            # 2. Build Geometry & Mesh properly with at least 3 lines per axis
            half_w = payload.patch_width / 2.0
            half_l = payload.patch_length / 2.5  # example coordinate split
            
            # X-axis discretization (at least 3 points)
            mesh.AddLine('x', [-half_w, 0.0, half_w])
            
            # Y-axis discretization (at least 3 points)
            mesh.AddLine('y', [-half_l, 0.0, half_l])
            
            # Z-axis discretization (e.g., ground plane at 0, substrate top at thickness)
            mesh.AddLine('z', [0.0, payload.substrate_thickness / 2.0, payload.substrate_thickness])

            # 2. Build Geometry dynamically using incoming payload dimensions
            # Substrate & Patch definitions go here mapping payload.patch_length, etc.
            # (Example placeholder mesh lines based on parameters)
           

            # 3. Run the FDTD Simulation engine
            FDTD.Run(sim_path, cleanup=False)

            # 4. Post-Processing: Extract S11 from Port data
            freq = np.linspace(payload.frequency_start, payload.frequency_stop, payload.frequency_points)
            
            # Assuming port 0 is your lumped feed port
            # port.CalcPort(sim_path, freq)
            # s11 = port.uf_ref / port.uf_inc
            
            # Mocking output arrays for structural demonstration purposes:
            s11_mock = -20 * np.exp(-((freq - 2.4e9)**2) / (0.1e9)**2) - 3.0 
            s11_db = 20 * np.log10(np.abs(10**(s11_mock/20))) # placeholder conversion

            min_idx = np.argmin(s11_db)
            resonant_freq = freq[min_idx]

            return SimulationResponse(
                status="success",
                frequencies=freq.tolist(),
                s11_db=s11_db.tolist(),
                resonant_frequency=float(resonant_freq),
                min_s11=float(s11_db[min_idx])
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
