# Q-xApp: Quantum-Inspired xApp Framework for O-RAN Near-RT Control

A quantum-inspired xApp framework for near-real-time control in O-RAN networks. Supports multiple use cases (Traffic Steering, Network Energy Saving) through a unified assignment optimization engine. The system runs on **ns-O-RAN + FlexRIC** with a real-time Docker-based GUI.

## Overview

Q-xApp demonstrates that diverse O-RAN near-RT RIC use cases share a common **inter-entity assignment** structure, enabling a single computational engine to serve multiple use cases without separate decision pipelines.

Currently implemented use cases:
1. **Traffic Steering (TS)** — UE-to-Cell assignment maximizing total throughput
2. **Network Energy Saving (NES)** — UE concentration on minimum cells + idle cell sleep

The project consists of:
1. **Unified xApp Controller** — A C-based xApp with real-time mode switching between TS and NES via GUI
2. **Quantum Circuit Solver** — Grover's algorithm for optimal assignment search
3. **Real-time GUI** — Dashboard with campus map, use case switching, energy monitoring, and A1 policy controls

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker GUI (port 8000)                  │
│  ┌───────────┐  ┌────────────┐  ┌─────────────────────┐    │
│  │ Sim Grid  │  │ Throughput │  │ Cell Power + RETX   │    │
│  │ (Map+UE+  │  │ (per UE)   │  │ (per Cell / per UE) │    │
│  │  O-RU)    │  │            │  │                     │    │
│  └─────┬─────┘  └─────┬──────┘  └──────────┬──────────┘    │
│        └───────────────┴────────────────────┘               │
│  Use Case: [TS ▼] / [NES ▼]   Sleep O-RU: ○1 ○2 ●3        │
│                         InfluxDB                             │
└──────────────────────────┬──────────────────────────────────┘
                           │ KPM data + mode/policy files
┌──────────────────────────┴──────────────────────────────────┐
│                    FlexRIC nearRT-RIC                         │
│              ┌────────────┐  ┌───────────────┐               │
│              │  KPM SM    │  │    RC SM       │               │
│              └──────┬─────┘  └───────┬───────┘               │
└─────────────────────┼────────────────┼──────────────────────┘
        SINR reports  │                │ HO + Energy_state cmds
┌─────────────────────┴────────────────┴──────────────────────┐
│              ns-3 mmWave Simulation                           │
│   ┌────────┐  ┌────────┐  ┌────────┐  ┌──────┐             │
│   │ O-RU 1 │  │ O-RU 2 │  │ O-RU 3 │  │ LTE  │             │
│   │ Cell 2 │  │ Cell 3 │  │ Cell 4 │  │ Cell1│             │
│   └────────┘  └────────┘  └────────┘  └──────┘             │
│   ↕           ↕           ↕           ↕                      │
│   UE 1        UE 2        UE 3        UE 4                  │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               Q-xApp Unified Controller                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Use-Case Encoder → Assignment Algorithm → Interpreter  │ │
│  │  (SINR→rate matrix)  (greedy/energy)    (RC control)    │ │
│  └─────────────────────────────────────────────────────────┘ │
│  Mode: TS → greedy_match (max 2 UE/cell)                    │
│  Mode: NES → energy_aware_match + Energy_state sleep/wake    │
│  ┌──────────────────┐                                        │
│  │ Quantum Solver    │  (Grover's algorithm, same circuit    │
│  │ bs_ue_matching.py │   for all use cases)                  │
│  └──────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

## Supported Use Cases

### Traffic Steering (TS)
- **Objective**: Maximize total Shannon capacity
- **Assignment**: UE ↔ Cell, max 2 UEs per cell (A1 policy)
- **Control**: RC handover commands (style=3)
- **E2 measurements**: Per-UE SINR, neighbor SINR

### Network Energy Saving (NES)
- **Objective**: Minimize active cells while maintaining connectivity
- **Assignment**: Concentrate UEs on fewest cells, sleep idle cells
- **Control**: RC handover (style=3) + Energy_state sleep/wake (style=300)
- **GUI controls**: Select which O-RU to sleep, real-time cell power monitoring
- **ns-3 support**: SetBSTX(power=0) for sleep, SetBSTX(power=30) for wake

## Project Structure

```
Q-xApp/
├── flexric/xApp/
│   ├── qxapp_common.h                # Shared code: RC SM messages, CSV parsing, SINR/rate
│   ├── qxapp_unified.c               # Unified xApp: TS + NES modes, real-time switching
│   ├── qxapp_greedy_handover.c       # Standalone TS xApp
│   └── qxapp_energy_saving.c         # Standalone NES xApp
├── ns3/
│   ├── scenario-zero-with_parallel_loging.cc  # ns-3 simulation scenario
│   └── mmwave-enb-net-device.cc      # Modified: Energy_state sleep/wake handler
├── gui/
│   ├── templates/chart.html          # Web GUI: grid + charts + use case controls
│   ├── src/data_controller.py        # FastAPI: data + use case/policy APIs
│   ├── src/simulation.py             # InfluxDB + txt fallback data manager
│   ├── static/univmap.png            # Campus map background
│   └── docker-compose.yml            # Docker Compose configuration
├── scripts/
│   ├── collect_qxapp_verification.py # Verification script
│   └── qxapp_verification.csv        # 100-sample validation results
├── bs_ue_matching.py                 # Quantum circuit (Qiskit)
├── bs_ue_quest.c                     # Quantum solver (QuEST C library)
├── QuEST.c / QuEST.h                # QuEST quantum simulator
└── visualize_results.py              # Result visualization
```

## How to Run

### Step 1: Start FlexRIC nearRT-RIC
```bash
sudo /root/flexric/build/examples/ric/nearRT-RIC
```

### Step 2: Start ns-3 Simulation
```bash
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 run "scratch/scenario-zero-with_parallel_loging --N_MmWaveEnbNodes=3 --N_Ues=4"
```

### Step 3: Start Unified Q-xApp
```bash
sudo /root/flexric/build/examples/xApp/c/ctrl/xapp_qxapp_unified
```

### Step 4: Open GUI
```bash
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/GUI
sudo docker compose up -d
```
Navigate to `http://localhost:8000`. Use the **A1 Policy Manager** panel to switch between Traffic Steering and Network Energy Saving.

## GUI Features

- **Simulation Grid**: Campus map with O-RU (triangles) and UE (circles), color-coded by assignment. Sleeping O-RUs shown in gray.
- **Throughput Chart**: Per-UE throughput (Mbps) over time
- **Cell Power Chart**: Per-cell energy consumption (W), shows sleep effect
- **RETX Chart**: Per-UE retransmission delta
- **Use Case Selector**: Switch TS ↔ NES in real-time
- **Sleep O-RU Control**: Select which O-RU to sleep (NES mode)
- **A1 Policy**: Max UE per cell configuration

## Building from Source

### Unified Q-xApp
```bash
sudo bash -c 'cd /root/flexric/build && cmake .. && cmake --build . --target xapp_qxapp_unified -j$(nproc)'
```

### ns-3 Scenario
```bash
cd /home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 build
```

### Quantum Circuit (Qiskit)
```bash
pip install qiskit qiskit-aer matplotlib numpy
python bs_ue_matching.py
```

## References

- O-RAN Alliance, "O-RAN Architecture Description", O-RAN.WG1.O-RAN-Architecture-Description
- O-RAN WG3, "Use Cases and Requirements", O-RAN.WG3.TS.UCR-R004-v09.00
- ns-O-RAN: https://github.com/o-ran-sc/sim-ns3-o-ran-e2
- FlexRIC: https://gitlab.eurecom.fr/mosaic5g/flexric
- QuEST: https://github.com/QuEST-Kit/QuEST

## License

This project builds upon ns-O-RAN and FlexRIC which are licensed under GPL-2.0 and Apache-2.0 respectively. See individual files for specific license headers.
