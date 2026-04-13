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
├── bs_ue_matching.py               # Quantum circuit (Qiskit)
├── bs_ue_quest.c                   # Quantum solver (QuEST C library)
└── visualize_results.py            # Result visualization
```

## How to Run

### Step 1: Start Docker GUI
```bash
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/GUI
sudo docker compose up -d
```
Data pusher auto-starts with retry loop. Navigate to `http://localhost:8000`.

### Step 2: Start FlexRIC nearRT-RIC
```bash
sudo /root/flexric/build/examples/ric/nearRT-RIC
```

### Step 3: Start ns-3 Simulation
```bash
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 run "scratch/scenario-zero-with_parallel_loging --N_MmWaveEnbNodes=3 --N_Ues=4"
```

### Step 4: Start Unified Q-xApp
```bash
sudo /root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified
```

Switch between use cases in real-time via the **A1 Policy Manager** panel in the GUI.

## Building from Source

```bash
# Unified Q-xApp
sudo bash -c 'cd /root/flexric/build && cmake .. && cmake --build . --target xapp_qxapp_unified -j$(nproc)'

# ns-3 Scenario
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran && ./ns3 build
```

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
