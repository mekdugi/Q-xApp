# Q-xApp: Quantum-Inspired xApp Framework for O-RAN Near-RT Control

A quantum-inspired xApp framework for near-real-time control in O-RAN networks. Supports three use cases through a unified assignment optimization engine. Runs on **ns-O-RAN + FlexRIC** with a real-time Docker-based GUI.

---

## 1. What is Q-xApp?

Q-xApp demonstrates that diverse O-RAN near-RT RIC use cases share a common **inter-entity assignment** structure. A single xApp handles multiple use cases by switching the assignment algorithm while keeping the same E2 interface pipeline.

**Three use cases, one xApp:**

| Use Case | Assignment Type | What it does |
|----------|----------------|-------------|
| **Traffic Steering (TS)** | UE ↔ Cell (inter-cell) | Assigns UEs to best-SINR cells, max 2 UE/cell |
| **Network Energy Saving (NES)** | UE ↔ Cell (inter-cell) | Packs UEs into fewer cells, sleeps idle O-RUs |
| **QoS-based Resource Allocation** | UE ↔ DRB (intra-cell) | Assigns DRB profiles (GBR/NGBR) per UE priority |

Switch between them in real-time from the GUI — no restart needed.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker GUI (localhost:8000)                │
│  ┌───────────┐  ┌────────────┐  ┌─────────────────────┐    │
│  │ Sim Grid  │  │ Throughput │  │ Cell Power + RETX   │    │
│  │ (Map+UE+  │  │  (Mbps)    │  │     (W / count)     │    │
│  │  O-RU)    │  │            │  │                     │    │
│  └───────────┘  └────────────┘  └─────────────────────┘    │
│  Network Settings: 3 O-RU, 4 UE, 100MHz, ISD 150m          │
│  A1 Policy Manager: [Use Case ▼] [Sleep O-RU / Priority]   │
│                         InfluxDB                             │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                    FlexRIC nearRT-RIC                         │
│              ┌────────────┐  ┌───────────────┐               │
│              │  KPM SM    │  │    RC SM       │               │
│              └──────┬─────┘  └───────┬───────┘               │
└─────────────────────┼────────────────┼──────────────────────┘
        SINR reports  │                │ Control commands
┌─────────────────────┴────────────────┴──────────────────────┐
│              ns-3 mmWave Simulation                           │
│   O-RU 1 (Cell 2)   O-RU 2 (Cell 3)   O-RU 3 (Cell 4)     │
│        UE 1    UE 2    UE 3    UE 4    LTE (anchor)         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│           Q-xApp Unified Controller (Fig. 2 Pipeline)        │
│                                                               │
│   Stage 1              Stage 2              Stage 3          │
│  ┌───────────────┐  ┌────────────────┐  ┌────────────────┐  │
│  │ Use-Case      │  │ Assignment     │  │ Output         │  │
│  │ Encoder       │→│ Algorithm      │→│ Interpreter    │  │
│  │               │  │                │  │                │  │
│  │ Read SINR,    │  │ TS: greedy     │  │ Handover       │  │
│  │ A1 policy,    │  │ NES: energy    │  │ Sleep/Wake     │  │
│  │ QoS config    │  │ QoS: DRB match │  │ DRB weight     │  │
│  └───────────────┘  └────────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Prerequisites

| Component | Repository | Branch |
|-----------|-----------|--------|
| **ns-O-RAN** | https://github.com/Orange-OpenSource/ns-O-RAN-flexric | `main` |
| **FlexRIC** | https://gitlab.eurecom.fr/mosaic5g/flexric | `oie-ric-taap-xapps` |
| **Docker** | Docker + Docker Compose | — |

Install ns-O-RAN and FlexRIC following their respective guides first. Tested on **Ubuntu 24.04 (WSL2)**.

---

## 4. Installation

After the base platform is installed, apply Q-xApp modifications:

```bash
# 1. Copy ns-3 files
cp ns3/scenario/scenario-zero-with_parallel_loging.cc  <ns-O-RAN>/scratch/
cp ns3/mmwave-enb-net-device.cc                        <ns-O-RAN>/src/mmwave/model/
cp ns3/mmwave-flex-tti-pf-mac-scheduler.cc             <ns-O-RAN>/src/mmwave/model/
cp ns3/mmwave-flex-tti-pf-mac-scheduler.h              <ns-O-RAN>/src/mmwave/model/

# 2. Copy xApp files
cp flexric/xApp/qxapp_common.h      <FlexRIC>/examples/xApp/c/ctrl/
cp flexric/xApp/qxapp_unified.c     <FlexRIC>/examples/xApp/c/ctrl/
cp flexric/xApp/qxapp_*.c           <FlexRIC>/examples/xApp/c/ctrl/

# 3. Add build target to <FlexRIC>/examples/xApp/c/ctrl/CMakeLists.txt:
#    add_executable(xapp_qxapp_unified qxapp_unified.c)
#    target_link_libraries(xapp_qxapp_unified PRIVATE e42_xapp pthread sctp dl m)

# 4. Copy GUI
cp -r gui/* <ns-O-RAN>/GUI/

# 5. Build everything
cd <ns-O-RAN> && ./ns3 build
sudo bash -c 'cd <FlexRIC>/build && cmake .. && cmake --build . --target xapp_qxapp_unified -j$(nproc)'
cd <ns-O-RAN>/GUI && sudo docker compose up -d
```

### What Each Modified File Does

| File | Copies to | What was changed |
|------|----------|-----------------|
| `scenario-zero-with_parallel_loging.cc` | `scratch/` | 3 BS, 4 UE, antenna config, 30min simTime, energy model |
| `mmwave-enb-net-device.cc` | `src/mmwave/model/` | Added Energy_state sleep/wake + Radio_Bearer_Control handler |
| `mmwave-flex-tti-pf-mac-scheduler.cc/.h` | `src/mmwave/model/` | Added per-UE scheduling weight for QoS |
| `qxapp_unified.c` | `examples/xApp/c/ctrl/` | Unified xApp: 3 use cases with Fig. 2 pipeline |
| `qxapp_common.h` | `examples/xApp/c/ctrl/` | Shared code: RC messages, CSV parsing, rate computation |
| `GUI/*` | `GUI/` | Web dashboard, Docker config, auto-start data pusher |

---

## 5. Running the Simulation

Open **three terminals** and run in order:

```bash
# Terminal 1: Start the nearRT-RIC
sudo <FlexRIC>/build/examples/ric/nearRT-RIC

# Terminal 2: Start ns-3 network simulation (wait for E2 Setup to complete)
cd <ns-O-RAN> && ./ns3 run "scratch/scenario-zero-with_parallel_loging --N_MmWaveEnbNodes=3 --N_Ues=4"

# Terminal 3: Start Q-xApp controller (after ns-3 connects to RIC)
sudo <FlexRIC>/build/examples/xApp/c/ctrl/xapp_qxapp_unified
```

Then open **http://localhost:8000** in your browser. The Docker GUI and data pusher start automatically.

Simulation runs for **30 minutes**. When time expires, all processes are terminated automatically.

---

## 6. Using the GUI

### Network Settings (top bar)
Fixed simulation parameters: O-RU count, UE count, bandwidth, center frequency, inter-site distance.

### A1 Policy Manager (second bar)
Switch use cases and configure policies in real-time:
- **TS mode**: "Max UE/Cell" selector (display, fixed at 2)
- **NES mode**: "Sleep O-RU" radio buttons — choose which O-RU to put to sleep
- **QoS-RA mode**: Per-UE "High/Low" priority selectors

### Charts
| Chart | What it shows |
|-------|--------------|
| **Simulation Grid** | Campus map with O-RU (triangles, colored) and UE (circles, colored by serving cell). Sleeping O-RUs turn gray. |
| **Throughput** | Per-UE throughput in Mbps over time |
| **Cell Power** | Per-cell energy consumption in Watts (sleep cells show near-zero) |
| **RETX** | Per-UE retransmission count (delta per interval) |

### Remaining Sim. Time
Countdown from 30 minutes. Turns red and auto-kills all processes when expired.

---

## 7. Technical Details

### Use Case Details

**Traffic Steering (TS)**
- Assignment: UE ↔ Cell (inter-cell), max 2 UEs per cell (A1 policy)
- Objective: Maximize total Shannon capacity `C = Σ log₂(1 + SINR)`
- RC Control: Connected_Mode_Mobility (style=3) — handover

**Network Energy Saving (NES)**
- Assignment: UE ↔ Cell (inter-cell), concentrate on fewest cells
- Objective: Minimize active cells while maintaining connectivity
- RC Control: style=3 (handover) + Energy_state (style=300, sleep/wake)

**QoS-based Resource Allocation (QoS-RA)**
- Assignment: UE ↔ DRB (intra-cell), 2-UE × 4-DRB matching per cell
- DRB Pool: d1(GBR, 5QI=2, PRB 0.4), d2(GBR, 5QI=4, PRB 0.2), d3(NGBR, 5QI=7), d4(NGBR, 5QI=9)
- Objective: Maximize weighted utility under GBR PRB constraints (≤ 0.6)
- RC Control: style=3 (handover) + Radio_Bearer_Control (style=1, scheduler weight)

### E2 Connection and Control Loop

The simulation platform integrates [FlexRIC](https://gitlab.eurecom.fr/mosaic5g/flexric) and [ns-O-RAN](https://github.com/Orange-OpenSource/ns-O-RAN-flexric) to realize a complete O-RAN near-RT control loop.

1. **E2 Setup**: ns-O-RAN establishes an SCTP connection with the nearRT-RIC (E2AP v1.01). Each mmWave gNB registers KPM and RC service models.
2. **E42 Interface**: The RIC connects to the Q-xApp via E42, a FlexRIC-specific interface for RIC–xApp communication.
3. **Subscription**: Q-xApp subscribes to KPM reports. ns-O-RAN begins periodically sending measurements.
4. **Control Loop**: Every 5 seconds, Q-xApp reads SINR → computes assignment → sends RC Control → receives ACK.

```
ns-O-RAN (E2 Node)          nearRT-RIC (FlexRIC)          Q-xApp
      │                            │                          │
      │──── E2 Setup Req/Resp ────→│                          │
      │                            │←── E42 Connect ──────────│
      │                            │←── RIC Subscription ─────│
      │                            │                          │
      │  ┌─── Control Loop ────────────────────────────────┐  │
      │──┼── RIC INDICATION ──────→│──── KPM Report ──────→│  │
      │  │  (SINR, PRB, position)  │                        │  │
      │  │                         │   [Encoder→Algo→Interp]│  │
      │  │                         │←── RIC CONTROL REQ ────│  │
      │←─┼── RIC CONTROL REQ ─────│    (E2SM-RC)           │  │
      │──┼── RIC CONTROL ACK ────→│──── CONTROL ACK ──────→│  │
      │  └──────────────────────────────────────────────────┘  │
```

### E2SM Service Models

| Service Model | Version | Function ID | Purpose |
|--------------|---------|-------------|---------|
| **E2SM-KPM** | v3.00 | 2 | Reports: SINR, neighbor SINR, PRB usage, throughput, RETX, UE position |
| **E2SM-RC** | v1.03 | 3 | Control: handover, cell sleep/wake, DRB weight |

### RC Control Styles

| Style | ID | What it controls | Used by |
|-------|-----|-----------------|---------|
| Radio_Bearer_Control | 1 | Per-UE scheduler weight (DRB priority) | QoS-RA |
| Connected_Mode_Mobility | 3 | UE handover between cells | TS, NES, QoS-RA |
| Energy_state | 300 | Cell sleep (TxPower=0) / wake (TxPower=30) | NES |

### Data Files

| File | Written by | Read by | Content |
|------|-----------|---------|---------|
| `ue_position.txt` | ns-3 | Data pusher → InfluxDB | UE positions, serving cell |
| `gnbs.txt` | ns-3 | Data pusher → InfluxDB | Cell positions, energy state |
| `cu-cp-cell-{2,3,4}.txt` | ns-3 | Data pusher + Q-xApp | Per-UE SINR |
| `energyfilecell{2,3,4}.csv` | ns-3 | Q-xApp | Cell energy consumption |
| `qxapp_result.json` | Q-xApp | GUI | Assignment, DRB, energy, mode |
| `xapp_mode.txt` | GUI | Q-xApp | Current use case (ts/nes/qos) |
| `xapp_sleep_config.txt` | GUI | Q-xApp | Which O-RU to sleep |
| `xapp_qos_config.txt` | GUI | Q-xApp | Per-UE priority weights |

---

## 8. Project Structure

```
Q-xApp/
├── flexric/xApp/
│   ├── qxapp_common.h              # Shared: RC SM messages, CSV parsing, SINR/rate
│   ├── qxapp_unified.c             # Unified xApp: TS + NES + QoS-RA (Fig. 2 pipeline)
│   ├── qxapp_greedy_handover.c     # Standalone TS xApp
│   └── qxapp_energy_saving.c       # Standalone NES xApp
├── ns3/
│   ├── scenario/scenario-zero-with_parallel_loging.cc
│   ├── mmwave-enb-net-device.cc
│   ├── mmwave-flex-tti-pf-mac-scheduler.cc
│   └── mmwave-flex-tti-pf-mac-scheduler.h
├── gui/
│   ├── templates/chart.html
│   ├── src/data_controller.py
│   ├── src/simulation.py
│   ├── static/univmap.png
│   ├── docker-compose.yml
│   ├── Dockerfile
│   └── start.sh
├── bs_ue_matching.py               # Quantum circuit (Qiskit) — future integration
├── scripts/collect_qxapp_verification.py
├── CLAUDE.md
└── README.md
```

---

## 9. References

- O-RAN Alliance, "O-RAN Architecture Description", O-RAN.WG1.O-RAN-Architecture-Description
- O-RAN WG3, "Use Cases and Requirements", O-RAN.WG3.TS.UCR-R004-v09.00
- Orange-OpenSource ns-O-RAN: https://github.com/Orange-OpenSource/ns-O-RAN-flexric
- FlexRIC: https://gitlab.eurecom.fr/mosaic5g/flexric

## License

This project builds upon ns-O-RAN and FlexRIC which are licensed under GPL-2.0 and Apache-2.0 respectively.
