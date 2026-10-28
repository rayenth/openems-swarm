"""Request / response models for the openEMS patch antenna simulation server.

All lengths are in millimetres, all frequencies in Hz, impedances in ohms.

Coordinate convention used by the server
----------------------------------------
x : patch *length* (resonant dimension), feed offset runs along x
y : patch *width*
z : substrate thickness (ground plane at z = 0, patch at z = thickness)
The patch and substrate are centred on the origin.
"""
from beanie import Document
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Request building blocks
# --------------------------------------------------------------------------- #
class PatchParams(BaseModel):
    length: float = Field(38.0, gt=0, description="Patch length along x (resonant dimension) in mm")
    width: float = Field(48.0, gt=0, description="Patch width along y in mm")


class SubstrateParams(BaseModel):
    length: float = Field(60.0, gt=0, description="Substrate / ground plane size along x in mm")
    width: float = Field(60.0, gt=0, description="Substrate / ground plane size along y in mm")
    thickness: float = Field(1.524, gt=0, description="Substrate height in mm")
    epsilon_r: float = Field(3.38, ge=1.0, description="Relative permittivity")
    loss_tangent: float = Field(0.0027, ge=0.0, description="Dielectric loss tangent (evaluated at centre frequency)")


class FeedParams(BaseModel):
    offset_x: float = Field(-6.0, description="Probe feed x-offset from patch centre in mm")
    offset_y: float = Field(0.0, description="Probe feed y-offset from patch centre in mm")
    impedance: float = Field(50.0, gt=0, description="Port reference impedance in ohms")


class FrequencyParams(BaseModel):
    start: float = Field(2.0e9, gt=0, description="Sweep start frequency in Hz")
    stop: float = Field(3.0e9, gt=0, description="Sweep stop frequency in Hz")
    points: int = Field(201, ge=2, le=100_000, description="Number of frequency points in the result")

    @model_validator(mode="after")
    def _check_order(self):
        if self.stop <= self.start:
            raise ValueError("frequency.stop must be greater than frequency.start")
        return self


class SolverParams(BaseModel):
    cells_per_wavelength: int = Field(20, ge=5, le=100, description="Target mesh cells per wavelength at the stop frequency")
    substrate_cells: int = Field(4, ge=1, le=50, description="Mesh cells through the substrate thickness")
    smoothing_ratio: float = Field(1.4, gt=1.0, le=2.0, description="Maximum neighbouring mesh-cell ratio")
    air_padding: Optional[float] = Field(
        None, gt=0, description="Air gap between the structure and the boundary in mm (default: lambda/4 at centre frequency)"
    )
    pml_cells: int = Field(8, ge=0, le=32, description="PML thickness in cells (0 = use MUR absorbing boundary instead)")
    max_timesteps: int = Field(30_000, ge=100, description="Maximum FDTD timesteps")
    end_criteria: float = Field(1e-4, gt=0, lt=1, description="Energy decay criterion (linear) to stop the simulation")
    num_threads: Optional[int] = Field(None, ge=1, description="Number of solver threads (default: openEMS decides)")
    verbose: int = Field(0, ge=0, le=3, description="openEMS verbosity level")


class SimulationRequest(BaseModel):
    patch: PatchParams = Field(default_factory=PatchParams)
    substrate: SubstrateParams = Field(default_factory=SubstrateParams)
    feed: FeedParams = Field(default_factory=FeedParams)
    frequency: FrequencyParams = Field(default_factory=FrequencyParams)
    solver: SolverParams = Field(default_factory=SolverParams)

    @model_validator(mode="after")
    def _check_geometry(self):
        p, s, f = self.patch, self.substrate, self.feed
        if p.length >= s.length or p.width >= s.width:
            raise ValueError("Patch must be smaller than the substrate (patch.length < substrate.length and patch.width < substrate.width)")
        if abs(f.offset_x) >= p.length / 2 or abs(f.offset_y) >= p.width / 2:
            raise ValueError("Feed point must lie inside the patch")
        return self


# --------------------------------------------------------------------------- #
# Response
# --------------------------------------------------------------------------- #
class SimulationResponse(BaseModel):
    status: str
    frequencies: List[float] = Field(..., description="Frequency axis in Hz")
    s11_db: List[float] = Field(..., description="|S11| in dB")
    zin_real: List[float] = Field(..., description="Re(Zin) in ohms")
    zin_imag: List[float] = Field(..., description="Im(Zin) in ohms")
    resonant_frequency: float = Field(..., description="Frequency of minimum S11 in Hz")
    min_s11: float = Field(..., description="Minimum S11 in dB")
    bandwidth_10db: Optional[float] = Field(None, description="Contiguous -10 dB bandwidth around resonance in Hz (None if S11 never < -10 dB)")
    simulation_time_s: float = Field(..., description="Wall-clock solver + post-processing time in seconds")


# --------------------------------------------------------------------------- #
# Record = request + response + metadata (one row in the `simulations` table)
# --------------------------------------------------------------------------- #
class SimulationMetadata(BaseModel):
    run_id: uuid.UUID = Field(default_factory=uuid.uuid4, description="Unique id of this simulation run")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC time the record was created")
    api_version: Optional[str] = Field(None, description="Version of this server (e.g. app.version)")
    engine: str = Field("openEMS", description="Simulation engine used")
    engine_version: Optional[str] = Field(None, description="Engine version, if known")
    hostname: Optional[str] = Field(default_factory=socket.gethostname, description="Machine that ran the simulation")
    notes: Optional[str] = Field(None, description="Free-form notes")


class SimulationRecord(BaseModel):
    """Bundles the request, the response and metadata into one storable unit."""

    request: SimulationRequest
    response: SimulationResponse
    metadata: SimulationMetadata = Field(default_factory=SimulationMetadata)

    @classmethod
    def from_run(cls, request: SimulationRequest, response: SimulationResponse, **metadata: Any) -> "SimulationRecord":
        """Build a record, e.g. SimulationRecord.from_run(req, res, api_version="2.0.0")."""
        return cls(request=request, response=response, metadata=SimulationMetadata(**metadata))

    def to_row(self) -> Dict[str, Any]:
        """Flat dict whose keys match the columns of the `simulations` table."""
        row: Dict[str, Any] = {}
        for group, values in self.request.model_dump().items():
            prefix = "freq" if group == "frequency" else group  # frequency.start -> freq_start
            for key, value in values.items():
                row[f"{prefix}_{key}"] = value
        row.update(self.response.model_dump())
        row.update(self.metadata.model_dump())
        return row
    class Settings :
        name = "patch_antenna_simulations"


