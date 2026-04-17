# Q-xApp: Quantum-Inspired xApp Framework for O-RAN Near-RT Control

A quantum-inspired xApp framework for near-real-time control in O-RAN networks. Supports three use cases through a unified assignment optimization engine based on the paper's Fig. 2 pipeline architecture. Runs on **ns-O-RAN + FlexRIC** with a real-time Docker-based GUI.

## Overview

Q-xApp demonstrates that diverse O-RAN near-RT RIC use cases share a common **inter-entity assignment** structure, enabling a single computational engine to serve multiple use cases without separate decision pipelines.

Implemented use cases:
1. **Traffic Steering (TS)** — Inter-cell UE-to-Cell assignment maximizing total throughput
2. **Network Energy Saving (NES)** — UE concentration on minimum cells + idle cell sleep/wake
3. **QoS-based Resource Allocation (QoS-RA)** — Intra-cell UE-to-DRB matching with 5QI-based priority

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker GUI (port 8000)                  │
│  ┌───────────┐  ┌────────────┐  ┌─────────────────────┐    │
│  │ Sim Grid  │  │ Throughput │  │ Cell Power + RETX   │    │
│  │ (Map+UE+  │  │ (per UE)   │  │ (per Cell / per UE) │    │
│  │  O-RU)    │  │            │  │                     │    │
│  └───────────┘  └────────────┘  └─────────────────────┘    │
│  Network Settings | A1 Policy Manager [Use Case ▼]          │
│                         InfluxDB                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ KPM data + mode/policy files
┌──────────────────────────┴──────────────────────────────────┐
│                    FlexRIC nearRT-RIC                         │
│              ┌────────────┐  ┌───────────────┐               │
│              │  KPM SM    │  │    RC SM       │               │
│              └──────┬─────┘  └───────┬───────┘               │
└─────────────────────┼────────────────┼──────────────────────┘
        SINR reports  │                │ HO + Energy + DRB cmds
┌─────────────────────┴────────────────┴──────────────────────┐
│              ns-3 mmWave Simulation (simTime: 1800s)         │
│   O-RU 1 (Cell 2)   O-RU 2 (Cell 3)   O-RU 3 (Cell 4)     │
│        UE 1    UE 2    UE 3    UE 4    LTE (anchor)         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│           Q-xApp Unified Controller (Fig. 2 Pipeline)        │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ Use-Case Encoder│→│ Assignment   │→│ Output         │ │
│  │ (SINR→rate,     │  │ Algorithm    │  │ Interpreter    │ │
│  │  A1 policy)     │  │ (greedy/NES/ │  │ (RC Control:   │ │
│  │                 │  │  QoS-DRB)    │  │  HO/Sleep/DRB) │ │
│  └─────────────────┘  └──────────────┘  └────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## E2 Interface and Data Flow

The simulation platform integrates [FlexRIC](https://gitlab.eurecom.fr/mosaic5g/flexric) and [ns-O-RAN](https://github.com/Orange-OpenSource/ns-O-RAN-flexric) to realize a complete O-RAN near-RT control loop.

### Connection Establishment
1. **E2 Setup**: ns-O-RAN (E2 node) establishes an SCTP connection with the nearRT-RIC by exchanging E2 Setup Request/Response messages (E2AP v1.01). Each mmWave gNB registers its supported RAN functions (KPM, RC) with the RIC.
2. **E42 Interface**: The RIC connects to the Q-xApp via the E42 interface, a temporary communication link defined in FlexRIC for RIC–xApp integration, as the O-RAN standard interface (E2T) remains under development.
3. **Subscription**: The Q-xApp initiates a subscription procedure through the RIC. Upon acceptance, ns-O-RAN begins periodically reporting Key Performance Measurements (KPM).

### Closed-Loop Control Cycle (every 5 seconds)

```
ns-O-RAN (E2 Node)          nearRT-RIC (FlexRIC)          Q-xApp
      │                            │                          │
      │──── E2 Setup Req/Resp ────→│                          │
      │                            │←── E42 Connect ──────────│
      │                            │←── RIC Subscription ─────│
      │                            │                          │
      │  ┌─── Control Loop ────────────────────────────────┐  │
      │  │                                                  │  │
      │──┼─── RIC INDICATION ─────→│                        │  │
      │  │   (KPM: SINR, PRB,     │──── KPM Report ───────→│  │
      │  │    UE position, RETX)   │                        │  │
      │  │                         │                        │  │
      │  │                         │    [Use-Case Encoder]  │  │
      │  │                         │    [Assignment Algo.]  │  │
      │  │                         │    [Output Interpreter] │  │
      │  │                         │                        │  │
      │  │                         │←── RIC CONTROL REQ ────│  │
      │←─┼─── RIC CONTROL REQ ────│    (E2SM-RC)           │  │
      │  │   Execute:              │                        │  │
      │  │   - Handover (style=3)  │                        │  │
      │  │   - Sleep/Wake (300)    │                        │  │
      │  │   - DRB Control (1)     │                        │  │
      │──┼─── RIC CONTROL ACK ───→│──── CONTROL ACK ──────→│  │
      │  │                                                  │  │
      │  └──────────────────────────────────────────────────┘  │
      │                            │                          │
      │                            │←── RIC Sub Delete ───────│
```

### E2SM Service Models

| Service Model | Version | Function ID | Purpose |
|--------------|---------|-------------|---------|
| **E2SM-KPM** | v3.00 | 2 | Periodic measurement reporting: L3 serving SINR, neighbor SINR, PRB usage, throughput, RETX, UE position |
| **E2SM-RC** | v1.03 | 3 | RAN control actions via RIC CONTROL REQUEST |

### RC Control Styles (E2SM-RC)

| Style | ID | Actions | Use Case |
|-------|-----|---------|----------|
| Radio_Bearer_Control | 1 | DRB priority assignment (act_id 1-4 → scheduler weight) | QoS-RA |
| Connected_Mode_Mobility | 3 | Handover (act_id=1), Conditional HO (2), DAPS HO (3) | TS, NES, QoS-RA |
| Energy_state | 300 | Cell sleep (act_id=1, TxPower=0) / wake (act_id=2, TxPower=30) | NES |

### Data Files (ns-3 → InfluxDB → GUI)

| File | Content | Writer | Reader |
|------|---------|--------|--------|
| `ue_position.txt` | UE ID, x, y, type, serving cell, simID | ns-3 | Data pusher → InfluxDB |
| `gnbs.txt` | Cell ID, x, y, ES state, energy | ns-3 | Data pusher → InfluxDB |
| `cu-cp-cell-{2,3,4}.txt` | Per-UE SINR, neighbor SINR | ns-3 | Data pusher → InfluxDB, Q-xApp |
| `energyfilecell{2,3,4}.csv` | Time, NetEnergy, DiffEnergy | ns-3 | Q-xApp |
| `qxapp_result.json` | Assignment, DRB, energy, mode | Q-xApp | GUI |
| `xapp_mode.txt` | Current use case (ts/nes/qos) | GUI | Q-xApp |
| `xapp_sleep_config.txt` | Sleep cell ID | GUI | Q-xApp |
| `xapp_qos_config.txt` | Per-UE QoS weights | GUI | Q-xApp |

## Supported Use Cases

### Traffic Steering (TS)
- **Assignment**: UE ↔ Cell (inter-cell), max 2 UEs per cell
- **Objective**: Maximize total Shannon capacity
- **RC Control**: Connected_Mode_Mobility (style=3) — handover

### Network Energy Saving (NES)
- **Assignment**: UE ↔ Cell (inter-cell), concentrate on fewest cells
- **Objective**: Minimize active cells while maintaining connectivity
- **RC Control**: style=3 (handover) + Energy_state (style=300, sleep/wake)
- **GUI**: Select which O-RU to sleep, real-time cell power monitoring

### QoS-based Resource Allocation (QoS-RA)
- **Assignment**: UE ↔ DRB (intra-cell), 2-UE × 4-DRB matching per cell
- **DRB Pool**: d1(GBR,5QI=2), d2(GBR,5QI=4), d3(NGBR,5QI=7), d4(NGBR,5QI=9)
- **Objective**: Maximize weighted utility under GBR PRB constraints
- **RC Control**: style=3 (handover) + Radio_Bearer_Control (style=1, scheduler weight)
- **GUI**: Per-UE High/Low priority selection

## Project Structure

```
Q-xApp/
├── flexric/xApp/
│   ├── qxapp_common.h              # Shared: RC SM messages, CSV parsing, SINR/rate
│   ├── qxapp_unified.c             # Unified xApp: TS + NES + QoS-RA (Fig. 2 pipeline)
│   ├── qxapp_greedy_handover.c     # Standalone TS xApp
│   └── qxapp_energy_saving.c       # Standalone NES xApp
├── ns3/
│   ├── scenario-zero-with_parallel_loging.cc  # ns-3 simulation scenario
│   ├── mmwave-enb-net-device.cc    # RC handler: HO + Energy_state + Radio_Bearer_Control
│   ├── mmwave-flex-tti-pf-mac-scheduler.cc  # PF scheduler with per-UE weight
│   └── mmwave-flex-tti-pf-mac-scheduler.h
├── gui/
│   ├── templates/chart.html        # Web GUI: grid + charts + A1 policy controls
│   ├── src/data_controller.py      # FastAPI: data + use case/policy/DRB APIs
│   ├── src/simulation.py           # InfluxDB + txt fallback data manager
│   ├── static/univmap.png          # Campus map background
│   ├── docker-compose.yml          # Docker Compose configuration
│   ├── Dockerfile                  # Auto-starts data pusher + uvicorn
│   └── start.sh                    # Entrypoint: pusher (auto-retry) + uvicorn
├── bs_ue_matching.py               # Quantum circuit (Qiskit) — future integration
└── scripts/
    └── collect_qxapp_verification.py  # Verification script
```

## Prerequisites and Installation

### Base Platform

This project requires two external repositories as the simulation base:

| Component | Repository | Branch | Role |
|-----------|-----------|--------|------|
| **ns-O-RAN** | https://github.com/Orange-OpenSource/ns-O-RAN-flexric | `main` | ns-3 mmWave simulator with E2 interface |
| **FlexRIC** | https://gitlab.eurecom.fr/mosaic5g/flexric | `oie-ric-taap-xapps` | nearRT-RIC with E2AP/E2SM support |

Follow the installation guides in each repository first. Tested on Ubuntu 24.04 (WSL2).

### Applying Q-xApp Modifications

After installing ns-O-RAN and FlexRIC, copy the modified files from this repo:

```bash
# ns-3 scenario
cp ns3/scenario/scenario-zero-with_parallel_loging.cc \
   <ns-O-RAN>/scratch/

# ns-3 mmWave model modifications
cp ns3/mmwave-enb-net-device.cc \
   <ns-O-RAN>/src/mmwave/model/
cp ns3/mmwave-flex-tti-pf-mac-scheduler.cc \
   ns3/mmwave-flex-tti-pf-mac-scheduler.h \
   <ns-O-RAN>/src/mmwave/model/

# Q-xApp source
cp flexric/xApp/qxapp_common.h \
   flexric/xApp/qxapp_unified.c \
   flexric/xApp/qxapp_greedy_handover.c \
   flexric/xApp/qxapp_energy_saving.c \
   <FlexRIC>/examples/xApp/c/ctrl/

# Add build target to FlexRIC CMakeLists.txt
# (add xapp_qxapp_unified target similar to existing xApp targets)

# GUI
cp -r gui/* <ns-O-RAN>/GUI/
```

### Modified Files Summary

| File | Location in Base Repo | Modification |
|------|----------------------|-------------|
| `scenario-zero-with_parallel_loging.cc` | `ns-O-RAN/scratch/` | 3 BS + 4 UE, antenna params, simTime 1800s, energy model |
| `mmwave-enb-net-device.cc` | `ns-O-RAN/src/mmwave/model/` | RC handler: Energy_state sleep/wake, Radio_Bearer_Control DRB weight |
| `mmwave-flex-tti-pf-mac-scheduler.cc/.h` | `ns-O-RAN/src/mmwave/model/` | Per-UE scheduling weight (SetUeSchedulingWeight) |
| `qxapp_unified.c` | `FlexRIC/examples/xApp/c/ctrl/` | Unified xApp with 3 use cases (Fig. 2 pipeline) |
| `qxapp_common.h` | `FlexRIC/examples/xApp/c/ctrl/` | Shared RC SM message generation, CSV parsing |
| `GUI/*` | `ns-O-RAN/GUI/` | Web dashboard, data controller, Docker config |

### Building

```bash
# 1. Build ns-3 (after copying modified files)
cd <ns-O-RAN> && ./ns3 build

# 2. Build Q-xApp (after copying xApp files + adding CMake target)
sudo bash -c 'cd <FlexRIC>/build && cmake .. && cmake --build . --target xapp_qxapp_unified -j$(nproc)'

# 3. Start Docker GUI
cd <ns-O-RAN>/GUI && sudo docker compose up -d
```

## How to Run

```bash
# Terminal 1: nearRT-RIC
sudo <FlexRIC>/build/examples/ric/nearRT-RIC

# Terminal 2: ns-3 Simulation
cd <ns-O-RAN> && ./ns3 run "scratch/scenario-zero-with_parallel_loging --N_MmWaveEnbNodes=3 --N_Ues=4"

# Terminal 3: Unified Q-xApp
sudo <FlexRIC>/build/examples/xApp/c/ctrl/xapp_qxapp_unified
```

Navigate to `http://localhost:8000`. Switch between use cases via the **A1 Policy Manager** panel.

## GUI Features

- **Simulation Grid**: Campus map with O-RU (triangles) and UE (circles), color-coded by assignment. Sleeping O-RUs in gray.
- **Throughput Chart**: Per-UE throughput (Mbps)
- **Cell Power Chart**: Per-cell energy delta (W)
- **RETX Chart**: Per-UE retransmission delta
- **A1 Policy Manager**: Use case switching, Sleep O-RU control (NES), UE priority (QoS-RA), Max UE/Cell (TS)
- **Network Settings**: O-RU count, UE count, bandwidth, center frequency, ISD
- **Remaining Sim. Time**: Countdown timer, auto-kills processes on expiry

## References

- O-RAN Alliance, "O-RAN Architecture Description", O-RAN.WG1.O-RAN-Architecture-Description
- O-RAN WG3, "Use Cases and Requirements", O-RAN.WG3.TS.UCR-R004-v09.00
- ns-O-RAN: https://github.com/o-ran-sc/sim-ns3-o-ran-e2
- FlexRIC: https://gitlab.eurecom.fr/mosaic5g/flexric

## License

This project builds upon ns-O-RAN and FlexRIC which are licensed under GPL-2.0 and Apache-2.0 respectively.
