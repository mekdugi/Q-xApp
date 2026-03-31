# Q-xApp: Quantum-Inspired xApp for O-RAN Handover Optimization

A quantum-inspired xApp that performs optimal UE-to-O-RU assignment in an O-RAN network using Grover's algorithm and greedy matching. The system runs on the **ns-O-RAN + FlexRIC** platform with a real-time Docker-based GUI for monitoring.

## Overview

Q-xApp solves the **BS-UE matching problem** — assigning 4 UEs to 3 mmWave O-RUs to maximize total network throughput while satisfying load-balancing constraints (max 2 UEs per O-RU).

The project consists of three main components:

1. **Quantum Circuit Solver** — Encodes the BS-UE assignment as a quantum optimization problem using Grover's search to find the throughput-maximizing assignment among all valid configurations.
2. **FlexRIC xApp Controller** — A C-based xApp that reads real-time SINR measurements from ns-3 via E2 interface, computes the optimal assignment using greedy matching, and sends RC handover commands to execute the assignment.
3. **Real-time GUI** — A Docker-based web dashboard showing UE positions, cell assignments, SINR/KPI charts, and energy consumption on a live simulation grid.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker GUI (port 3000)                 │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌─────────┐ │
│  │ Sim Grid  │  │ SINR Chart│  │ PRB Chart │  │ Retx    │ │
│  │ (UE+O-RU) │  │ (per UE)  │  │ (per Cell)│  │ Chart   │ │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └────┬────┘ │
│        └───────────────┴───────────────┴─────────────┘      │
│                         InfluxDB                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ KPM data
┌──────────────────────────┴──────────────────────────────────┐
│                    FlexRIC nearRT-RIC                        │
│                      (port 36421)                            │
│              ┌────────────┐  ┌───────────────┐              │
│              │  KPM SM    │  │    RC SM       │              │
│              └──────┬─────┘  └───────┬───────┘              │
└─────────────────────┼────────────────┼──────────────────────┘
        SINR reports  │                │ Handover commands
                      │                │
┌─────────────────────┴────────────────┴──────────────────────┐
│              ns-3 mmWave Simulation                          │
│   ┌────────┐  ┌────────┐  ┌────────┐  ┌──────┐            │
│   │ O-RU 1 │  │ O-RU 2 │  │ O-RU 3 │  │ LTE  │            │
│   │ Cell 2 │  │ Cell 3 │  │ Cell 4 │  │ Cell1│            │
│   └────────┘  └────────┘  └────────┘  └──────┘            │
│        ↕           ↕           ↕           ↕                │
│   ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐          │
│   │  UE 1  │  │  UE 2  │  │  UE 3  │  │  UE 4  │          │
│   └────────┘  └────────┘  └────────┘  └────────┘          │
└─────────────────────────────────────────────────────────────┘
        SINR+Position data │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               Q-xApp (this project)                         │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │ Quantum Solver   │  │ Greedy Handover Controller       │ │
│  │ (Grover's alg.)  │  │ (reads CSV → greedy match →     │ │
│  │ bs_ue_matching.py │  │  RC control msg → handover)     │ │
│  └──────────────────┘  └──────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Simulation Topology

```
                   O-RU 1 (Cell 2)
                   (400, 250)
                       ▲
                      / \
                     /   \
                    / 150m \
                   /       \
     O-RU 2 ◄────  Center  ────► LTE (anchor)
    (175,380)     (250,250)       (250,250)
     Cell 3          |
                     |
                  O-RU 3
                 (175,120)
                  Cell 4

    UEs: 4 mobile users, random walk within 150m radius
```

- **3 mmWave O-RUs**: Equally spaced 150m from center at 0°, 120°, 240°
- **1 LTE eNB**: Co-located at center (anchor/fallback only)
- **4 UEs**: Random walk mobility, speed 20-40 m/s
- **Area**: 500m × 500m

## Project Structure

```
Q-xApp/
├── flexric/xApp/
│   └── qxapp_greedy_handover.c    # FlexRIC xApp: real-time greedy HO controller
├── ns3/scenario/
│   └── scenario-zero-with_parallel_loging.cc  # ns-3 mmWave simulation scenario
├── gui/
│   ├── templates/chart.html       # Web GUI: simulation grid + KPI charts
│   └── src/data_controller.py     # FastAPI backend for GUI
├── scripts/
│   ├── collect_qxapp_verification.py  # Verification: distance vs assignment check
│   └── qxapp_verification.csv        # 100-sample validation results
├── bs_ue_matching.py              # Quantum circuit (Qiskit): Grover's search
├── bs_ue_matching2.py             # Quantum circuit variant (Document 7 style)
├── bs_ue_quest.c                  # Quantum solver using QuEST C library
├── QuEST.c / QuEST.h             # QuEST quantum simulator (lightweight C)
├── visualize_results.py           # Result visualization (topology + distributions)
├── quantum_results.json           # Quantum solver output (assignments + probabilities)
├── network_topology.png           # Generated: network topology visualization
├── probability_distribution.png   # Generated: quantum state probability distribution
└── quantum_circuit_diagram.png    # Generated: quantum circuit diagram
```

## Components

### 1. Quantum BS-UE Matching (`bs_ue_matching.py`, `bs_ue_quest.c`)

Solves the combinatorial UE-to-BS assignment problem using quantum computing:

- **Encoding**: 2 qubits per UE (8 data qubits for 4 UEs), encoding BS assignment as |00⟩=BS0, |01⟩=BS1, |10⟩=BS2
- **Constraints**: Each BS serves 1-2 UEs (load balancing). State |11⟩ is prohibited.
- **Objective**: Maximize total Shannon capacity: `C = Σ log₂(1 + SNR_i)`
- **Algorithm**: Grover's search amplifies high-throughput valid assignments
- **Qubits**: 21-23 total (8 data + ancillas for counting, constraint checking, rate comparison)

Two implementations:
- `bs_ue_matching.py` — Qiskit-based, full quantum circuit with measurement
- `bs_ue_quest.c` — QuEST C library, lightweight statevector simulation

### 2. Greedy Handover xApp (`flexric/xApp/qxapp_greedy_handover.c`)

Real-time xApp running on FlexRIC that performs handover optimization:

**Control Loop** (every 5 seconds):
1. **Read SINR** from ns-3 CSV files (`cu-cp-cell-{2,3,4}.txt`)
   - Parses per-UE serving SINR + neighbor SINRs
   - **Timestamp-aware**: only uses the most recent measurement per UE (fixes stale data overwrite bug)
2. **Compute Rate Matrix**: `rate[u][c] = log₂(1 + 10^(SINR_dB/10))` for all UE-cell pairs
3. **Greedy Assignment**: Sort all (rate, UE, cell) tuples descending, assign greedily with max 2 UEs/cell constraint
4. **Send Handover**: RC CONTROL messages via E2 interface to all E2 nodes

**Key fix applied**: The original CSV parser read cell files sequentially (cell-2 → cell-3 → cell-4) and later files would overwrite data from earlier files regardless of timestamp. This caused stale SINR data to dominate, producing incorrect assignments. The fix tracks per-UE timestamps and only keeps the freshest measurement.

### 3. ns-3 Simulation Scenario (`ns3/scenario/`)

Modified `scenario-zero-with_parallel_loging.cc`:
- **3 mmWave + 1 LTE** base stations
- **4 mobile UEs** with random walk
- E2 interface enabled for FlexRIC connection
- KPM (Key Performance Measurements) reporting: SINR, throughput, PRB usage
- RC (RAN Control) support for handover commands
- Position logging to InfluxDB for GUI visualization

### 4. Docker GUI (`gui/`)

Real-time web dashboard (FastAPI + Chart.js):
- **Simulation Grid**: Scatter plot showing O-RU positions (triangles) and UE positions (circles) with color-coded cell assignments
- **SINR Chart**: Per-UE serving SINR over time
- **Serving Cell Chart**: Per-UE cell attachment history
- **PRB Usage Chart**: Per-cell downlink PRB utilization
- **Retransmission Chart**: Per-UE DL error count
- **Energy Bar**: Per-cell power consumption
- **Q-xApp Integration**: Reads `/qxapp-result` endpoint showing quantum/greedy assignment overlay

## Prerequisites

- **ns-O-RAN** with FlexRIC (installed at `/root/flexric/`)
- **ns-3** mmWave module (installed at `/home/wookjin/ns-O-RAN-flexric/`)
- **Docker** + Docker Compose (for GUI: InfluxDB + Grafana + FastAPI)
- **Python 3.8+** with Qiskit (for quantum circuit simulation)
- **GCC** (for QuEST C compilation)

## How to Run

### Step 1: Start FlexRIC nearRT-RIC
```bash
sudo /root/flexric/build/examples/ric/nearRT-RIC
```
Wait until `E2 Setup` listening message appears.

### Step 2: Start ns-3 Simulation
```bash
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 run "scratch/scenario-zero-with_parallel_loging --N_MmWaveEnbNodes=3 --N_Ues=4"
```
Wait until UE position logs appear and E2 connection is established.

### Step 3: Start Q-xApp
```bash
sudo /root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_greedy_handover
```
You should see rate matrix output and handover commands being sent.

### Step 4: Open GUI
Navigate to `http://localhost:3000` in your browser.

## Building from Source

### Q-xApp (FlexRIC xApp)
```bash
cd /root/flexric/build
sudo cmake --build . --target xapp_qxapp_greedy_handover -j$(nproc)
```

### ns-3 Scenario
```bash
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 build
```

### QuEST Quantum Solver
```bash
gcc -O2 -Wall -std=c99 -I. -o bs_ue_quest bs_ue_quest.c QuEST.c -lm
./bs_ue_quest
```

### Quantum Circuit (Qiskit)
```bash
pip install qiskit qiskit-aer matplotlib numpy
python bs_ue_matching.py
```

## Verification

Run the verification script to validate that assignments correlate with distance in LOS:

```bash
python3 scripts/collect_qxapp_verification.py
```

This collects 100 samples comparing:
- **Distance**: Euclidean distance from each UE to each O-RU
- **Rate**: Shannon capacity computed from SINR
- **Assignment**: Greedy algorithm output
- **Match**: Whether the assigned O-RU is the geographically nearest

Sample output:
```
--- Round 14 (ts=107120547442392) [0/4 mismatch] ---
        dist O-RU1 dist O-RU2 dist O-RU3  rate O-RU1 rate O-RU2 rate O-RU3    assign  nearest    ok
  UE1:       75.4      286.3      210.9       16.30       0.00       3.29     O-RU1    O-RU1    OK
  UE2:      236.8       69.3      190.9        3.14      15.42       3.72     O-RU2    O-RU2    OK
  UE3:      162.3      221.8       97.9        4.97       2.78       6.38     O-RU3    O-RU3    OK
  UE4:      237.0       87.5      173.1        0.00       8.20       3.88     O-RU2    O-RU2    OK
```

**Validation Result (100 samples)**: 74.5% match rate (298/400 checks). Mismatches are primarily caused by:
- Missing SINR data (rate=0) triggering fallback assignment
- mmWave beamforming effects causing SINR to not perfectly correlate with distance
- Greedy load balancing distributing UEs across cells

## References

- O-RAN Alliance, "O-RAN Architecture Description", O-RAN.WG1.O-RAN-Architecture-Description
- ns-O-RAN: https://github.com/o-ran-sc/sim-ns3-o-ran-e2
- FlexRIC: https://gitlab.eurecom.fr/mosaic5g/flexric
- QuEST: https://github.com/QuEST-Kit/QuEST

## License

This project builds upon ns-O-RAN and FlexRIC which are licensed under GPL-2.0 and Apache-2.0 respectively. See individual files for specific license headers.
