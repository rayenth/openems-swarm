import tempfile
import traceback
import time
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request
import asyncio


from CSXCAD import ContinuousStructure
from openEMS import openEMS
from openEMS.physical_constants import C0, EPS0

from schemas import SimulationRequest, SimulationResponse, SimulationRecord, SimulationMetadata

from beanie import init_beanie
from contextlib import asynccontextmanager
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient




@asynccontextmanager
async def lifespan(app: FastAPI):
  # --- STARTUP LOGIC ---
  client = AsyncIOMotorClient("mongodb://localhost:27017")
  app.state.db = client.antenna_optimization_db
  yield
  client.close()
  print("MongoDB connection closed.")

app = FastAPI(title="openEMS FDTD Simulation Server", version="2.0.0", lifespan=lifespan)


UNIT = 1e-3  # all geometry is handled in millimetres


def _edge_lines(lo: float, hi: float, res: float) -> list:
    """1/3 - 2/3 mesh rule around metal edges (1/3 outside, 2/3 inside)."""
    return [lo - res / 3, lo + 2 * res / 3, hi + res / 3, hi - 2 * res / 3]


def _bandwidth(freq: np.ndarray, s11_db: np.ndarray, idx: int, threshold: float = -10.0) -> Optional[float]:
    if s11_db[idx] > threshold:
        return None
    lo = hi = idx
    while lo > 0 and s11_db[lo - 1] <= threshold:
        lo -= 1
    while hi < len(freq) - 1 and s11_db[hi + 1] <= threshold:
        hi += 1
    return float(freq[hi] - freq[lo])


async def _simulate(req: SimulationRequest, sim_path: str, db) -> SimulationResponse:
    t0 = time.time()
    p, s, f, fr, sv = req.patch, req.substrate, req.feed, req.frequency, req.solver

    f0 = 0.5 * (fr.start + fr.stop)
    fc = 0.5 * (fr.stop - fr.start)

    # ---- FDTD engine ------------------------------------------------------
    FDTD = openEMS(NrTS=sv.max_timesteps, EndCriteria=sv.end_criteria)
    if sv.num_threads:
        FDTD.SetNumberOfThreads(sv.num_threads)
    FDTD.SetGaussExcite(f0, fc)
    boundary = "MUR" if sv.pml_cells == 0 else f"PML_{sv.pml_cells}"
    FDTD.SetBoundaryCond([boundary] * 6)

    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    mesh = CSX.GetGrid()
    mesh.SetDeltaUnit(UNIT)

    # ---- Mesh parameters --------------------------------------------------
    mesh_res = C0 / fr.stop / UNIT / sv.cells_per_wavelength  # mm
    pad = sv.air_padding if sv.air_padding else C0 / f0 / UNIT / 4.0  # mm
    h = s.thickness

    # ---- Materials & geometry --------------------------------------------
    kappa = 2 * np.pi * f0 * EPS0 * s.epsilon_r * s.loss_tangent
    substrate = CSX.AddMaterial("substrate", epsilon=s.epsilon_r, kappa=kappa)
    substrate.AddBox(
        [-s.length / 2, -s.width / 2, 0],
        [s.length / 2, s.width / 2, h],
        priority=0,
    )

    ground = CSX.AddMetal("gnd")
    ground.AddBox(
        [-s.length / 2, -s.width / 2, 0],
        [s.length / 2, s.width / 2, 0],
        priority=10,
    )

    patch = CSX.AddMetal("patch")
    patch.AddBox(
        [-p.length / 2, -p.width / 2, h],
        [p.length / 2, p.width / 2, h],
        priority=10,
    )

    # ---- Lumped (probe) port ---------------------------------------------
    port = FDTD.AddLumpedPort(
        1, f.impedance,
        [f.offset_x, f.offset_y, 0],
        [f.offset_x, f.offset_y, h],
        "z", 1.0, priority=5, edges2grid="xy",
    )

    # ---- Mesh lines -------------------------------------------------------
    half_x = s.length / 2 + pad
    half_y = s.width / 2 + pad
    mesh.AddLine("x", [-half_x, half_x, -s.length / 2, s.length / 2, f.offset_x])
    mesh.AddLine("x", _edge_lines(-p.length / 2, p.length / 2, mesh_res))
    mesh.AddLine("y", [-half_y, half_y, -s.width / 2, s.width / 2, f.offset_y])
    mesh.AddLine("y", _edge_lines(-p.width / 2, p.width / 2, mesh_res))
    mesh.AddLine("z", np.linspace(0, h, sv.substrate_cells + 1))
    mesh.AddLine("z", [-pad, h + pad])
    mesh.SmoothMeshLines("all", mesh_res, sv.smoothing_ratio)

    # ---- Run --------------------------------------------------------------
    FDTD.Run(sim_path, verbose=sv.verbose, cleanup=True)

    # ---- Post-processing --------------------------------------------------
    freq = np.linspace(fr.start, fr.stop, fr.points)
    port.CalcPort(sim_path, freq)
    s11 = port.uf_ref / port.uf_inc
    zin = port.uf_tot / port.if_tot
    s11_db = 20 * np.log10(np.maximum(np.abs(s11), 1e-12))

    idx = int(np.argmin(s11_db))
    response = SimulationResponse(
        status="success",
        frequencies=freq.tolist(),
        s11_db=s11_db.tolist(),
        zin_real=np.real(zin).tolist(),
        zin_imag=np.imag(zin).tolist(),
        resonant_frequency=float(freq[idx]),
        min_s11=float(s11_db[idx]),
        bandwidth_10db=_bandwidth(freq, s11_db, idx),
        simulation_time_s=time.time() - t0,
    )
    print ("\n hello \n")
    record = SimulationRecord(request= req , response=response)
    
    await db.antenna_simulations.insert_one(record.model_dump(mode="json"))
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


# Plain `def` so FastAPI runs the blocking solver in its threadpool.
@app.post("/simulate", response_model=SimulationResponse)
async def run_simulation(payload: SimulationRequest, request : Request):
    db = request.app.state.db
    # Isolated temp directory per request avoids race conditions between runs
    with tempfile.TemporaryDirectory() as sim_path:
        try:
            response = await _simulate(payload, sim_path, db)
            return response
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            error_trace = traceback.format_exc()
            print("--- CRASH TRACEBACK ---")
            print(error_trace)
            print("-----------------------")
            raise HTTPException(status_code=500, detail=str(e))
