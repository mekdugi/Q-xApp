"""
BS-UE Matching Quantum Circuit - Document 7 Style
3 BS, 4 UE, Load Balancing (each BS: 1-2 UEs)
"""

from qiskit import QuantumCircuit
from qiskit.circuit.library import C3XGate, C4XGate
from qiskit.visualization import plot_histogram
import numpy as np
import matplotlib.pyplot as plt

# ============================================================================
# Configuration
# ============================================================================

NUM_BS = 3
NUM_UE = 4
NUM_DATA_QUBITS = 8

# Qubit layout (Document 7 스타일)
# 0-7: Data qubits (4 UE × 2 bits)
# 8-11: UE valid check ancilla (|11⟩ 체크용)
# 12: UE valid 결과 flag
# 13-14: BS0 counter ancilla
# 15: BS0 valid flag
# 16-17: BS1 counter ancilla  
# 18: BS1 valid flag
# 19-20: BS2 counter ancilla
# 21: BS2 valid flag
# 22: Rotation ancilla
# 23-26: Cost qubits (UE당 1개)
# 27: Oracle target

UE_ANC = [8, 9, 10, 11]  # |11⟩ 체크 ancilla
UE_FLAG = 12             # 모든 UE valid 결과

BS0_CNT = [13, 14, 15, 16]       # BS0 counter (LSB, MSB)
BS0_FLAG = 17
BS1_CNT = [18, 19, 20, 21]
BS1_FLAG = 22
BS2_CNT = [23, 24, 25, 26]
BS2_FLAG = 27

ROT_ANC = 28
COST = [29, 30, 31, 32]  # UE당 1개
ORACLE_TARGET = 33

NUM_TOTAL_QUBITS = 34

GROVER_ITERATIONS = 2
SIMUL_NUMB = 10000

CHANNEL_RATE = [
    [45.2, 12.1, 28.5],
    [18.7, 41.3, 15.9],
    [29.4, 36.8, 48.2],
    [22.1, 33.5, 19.7],
]
MAX_RATE = max(max(row) for row in CHANNEL_RATE)


def rate_to_angle(rate):
    """Document 7 방식: 높은 rate → 작은 angle"""
    rate_norm = rate / MAX_RATE
    theta = 2 * np.arccos(np.sqrt(rate_norm))
    return theta


# ============================================================================
# Constraint Functions (Document 7 스타일 - 자체 uncompute 포함)
# ============================================================================

def check_ue_valid(qc):
    """
    |11⟩ 금지 체크 - 결과를 UE_FLAG(12)에 저장
    자체 uncompute 포함
    """
    # Compute: 각 UE가 |11⟩인지 체크
    for ue in range(NUM_UE):
        q0, q1 = ue * 2, ue * 2 + 1
        qc.ccx(q0, q1, UE_ANC[ue])
    
    # |11⟩이 하나라도 있으면 UE_ANC가 1
    # 모두 0이어야 valid → X로 반전 후 C4X
    qc.x(UE_ANC)
    qc.append(C4XGate(), UE_ANC + [UE_FLAG])
    qc.x(UE_ANC)
    
    # Uncompute ancillas
    for ue in range(NUM_UE - 1, -1, -1):
        q0, q1 = ue * 2, ue * 2 + 1
        qc.ccx(q0, q1, UE_ANC[ue])
    
    return qc


def check_bs_count(qc, bs_idx):
    if bs_idx == 0:
        FLAG = BS0_FLAG
        tmp  = BS0_CNT
        pattern = (0,0)
    elif bs_idx == 1:
        FLAG = BS1_FLAG
        tmp  = BS1_CNT
        pattern = (1,0)
    else:
        FLAG = BS2_FLAG
        tmp  = BS2_CNT
        pattern = (0,1)

    # --- UE별 "is this BS?" ---
    for ue in range(NUM_UE):
        q0, q1 = ue*2, ue*2+1
        if pattern[0] == 0: qc.x(q0)
        if pattern[1] == 0: qc.x(q1)
        qc.ccx(q0, q1, tmp[ue])
        if pattern[0] == 0: qc.x(q0)
        if pattern[1] == 0: qc.x(q1)

    # --- exactly 1 UE ---
    for i in range(NUM_UE):
        controls = tmp
        qc.mcx(
            controls,
            FLAG,
            ctrl_state=''.join('1' if j==i else '0' for j in range(NUM_UE))
        )

    # --- exactly 2 UE ---
    for i in range(NUM_UE):
        for j in range(i+1, NUM_UE):
            qc.mcx(
                tmp,
                FLAG,
                ctrl_state=''.join(
                    '1' if k in (i, j) else '0'
                    for k in range(NUM_UE)
                )
            )

    # --- uncompute ---
    for ue in range(NUM_UE-1, -1, -1):
        q0, q1 = ue*2, ue*2+1
        if pattern[0] == 0: qc.x(q0)
        if pattern[1] == 0: qc.x(q1)
        qc.ccx(q0, q1, tmp[ue])
        if pattern[0] == 0: qc.x(q0)
        if pattern[1] == 0: qc.x(q1)

    return qc


# ============================================================================
# Objective Functions (Document 7 스타일)
# ============================================================================

def apply_rotation(qc, ue, bs, angle):
    """특정 UE-BS 조합에 대한 controlled rotation"""
    q0, q1 = ue * 2, ue * 2 + 1
    cost_qubit = COST[ue]
    
    # BS encoding
    if bs == 0:  # |00⟩
        qc.x(q0)
        qc.x(q1)
    elif bs == 1:  # |01⟩
        qc.x(q1)
    else:  # |10⟩
        qc.x(q0)
    
    qc.ccx(q0, q1, ROT_ANC)
    qc.cry(angle, ROT_ANC, cost_qubit)
    qc.ccx(q0, q1, ROT_ANC)
    
    # Undo X gates
    if bs == 0:
        qc.x(q0)
        qc.x(q1)
    elif bs == 1:
        qc.x(q1)
    else:
        qc.x(q0)
    
    return qc


# ============================================================================
# Main Circuit
# ============================================================================

def create_circuit(grover_iterations=GROVER_ITERATIONS):
    qc = QuantumCircuit(NUM_TOTAL_QUBITS, NUM_DATA_QUBITS)
    
    # === Initialize ===
    qc.x(ORACLE_TARGET)
    qc.h(ORACLE_TARGET)
    for i in range(NUM_DATA_QUBITS):
        qc.h(i)
    
    # Cost qubits to |1⟩
    for c in COST:
        qc.x(c)
    
    qc.barrier(label='Init')
    
    # === Grover Iterations ===
    for _ in range(grover_iterations):
        # 1. Objective rotations
        qc.barrier(label='Rotation')
        for ue in range(NUM_UE):
            for bs in range(NUM_BS):
                angle = rate_to_angle(CHANNEL_RATE[ue][bs])
                apply_rotation(qc, ue, bs, angle)
        
        # 2. Constraint checks (각각 자체 uncompute)
        qc.barrier(label='Constraint')
        check_ue_valid(qc)
        check_bs_count(qc, 0)
        check_bs_count(qc, 1)
        check_bs_count(qc, 2)
        
        # 3. MCX + H (Document 7 스타일)
        qc.barrier(label='MCX')
        # Control: UE_FLAG, BS0_FLAG, BS1_FLAG, BS2_FLAG, COST[0-3]
        qc.mcx([UE_FLAG, BS0_FLAG, BS1_FLAG, BS2_FLAG] + COST, ORACLE_TARGET)
        qc.h(ORACLE_TARGET)
        
        # 4. Uncompute constraints (역순으로 다시 호출 - toggle이므로)
        qc.barrier(label='Uncompute')
        check_bs_count(qc, 2)
        check_bs_count(qc, 1)
        check_bs_count(qc, 0)
        check_ue_valid(qc)
        
        # 5. Uncompute rotations (역순, 음수 angle)
        for ue in range(NUM_UE - 1, -1, -1):
            for bs in range(NUM_BS - 1, -1, -1):
                angle = -rate_to_angle(CHANNEL_RATE[ue][bs])
                apply_rotation(qc, ue, bs, angle)
        
        # 6. Diffusion
        qc.barrier(label='Diffusion')
        for i in range(NUM_DATA_QUBITS):
            qc.h(i)
        for i in range(NUM_DATA_QUBITS):
            qc.x(i)
        qc.h(NUM_DATA_QUBITS - 1)
        qc.mcx(list(range(NUM_DATA_QUBITS - 1)), NUM_DATA_QUBITS - 1)
        qc.h(NUM_DATA_QUBITS - 1)
        for i in range(NUM_DATA_QUBITS):
            qc.x(i)
        for i in range(NUM_DATA_QUBITS):
            qc.h(i)
    
    qc.measure(range(NUM_DATA_QUBITS), range(NUM_DATA_QUBITS))
    return qc


# ============================================================================
# Helper & Main
# ============================================================================

def generate_all_valid_states():
    from itertools import product
    valid_states = []
    bs_to_bits = {0: '00', 1: '01', 2: '10'}
    
    for assignment in product([0, 1, 2], repeat=4):
        bs_counts = [assignment.count(i) for i in range(3)]
        if all(1 <= c <= 2 for c in bs_counts):
            qubit_str = ''.join(bs_to_bits[bs] for bs in assignment)
            qiskit_str = qubit_str[::-1]
            total_rate = sum(CHANNEL_RATE[ue][bs] for ue, bs in enumerate(assignment))
            valid_states.append({
                'qiskit_str': qiskit_str,
                'assignment': assignment,
                'rate': total_rate,
                'readable': [f"UE{i}→BS{bs}" for i, bs in enumerate(assignment)]
            })
    return valid_states


if __name__ == "__main__":
    print("=" * 60)
    print("   BS-UE Matching Quantum Circuit (Qiskit)")
    print("=" * 60)
    print(f"\nConfiguration:")
    print(f"  - {NUM_BS} Base Stations, {NUM_UE} User Equipments")
    print(f"  - {NUM_TOTAL_QUBITS} qubits ({NUM_DATA_QUBITS} data + {NUM_TOTAL_QUBITS - NUM_DATA_QUBITS} ancilla)")
    print(f"  - Grover iterations: {GROVER_ITERATIONS}")
    
    print("\nChannel Rates (Mbps):")
    print("        BS0       BS1       BS2")
    for ue in range(NUM_UE):
        print(f"UE{ue}:    {CHANNEL_RATE[ue][0]:<10.2f}{CHANNEL_RATE[ue][1]:<10.2f}{CHANNEL_RATE[ue][2]:<10.2f}")
    
    print("\nCreating and running circuit...")
    qc = create_circuit()
    print(f"  Depth: {qc.depth()}, Gates: {qc.size()}")
    
    from qiskit_aer import AerSimulator
    counts = AerSimulator().run(qc, shots=SIMUL_NUMB).result().get_counts()
    
    print("\n" + "=" * 80)
    print(f"   결과 분석 ({SIMUL_NUMB} shots)")
    print("=" * 80)

    all_valid = generate_all_valid_states()
    for v in all_valid:
        v['count'] = counts.get(v['qiskit_str'], 0)

    optimal_state = max(all_valid, key=lambda x: x['rate'])['qiskit_str']
    valid_by_count = sorted(all_valid, key=lambda x: x['count'], reverse=True)
    total_valid = sum(v['count'] for v in all_valid)

    print(f"\nValid: {total_valid/SIMUL_NUMB*100:.1f}% (기대: 14.1%) | 증폭률: {(total_valid/SIMUL_NUMB) / (36/256):.2f}x")

    print(f"\n{'순위':<5} {'State':<10} {'Count':<7} {'Prob%':<8} {'Rate':<8} {'Assignment'}")
    print("-" * 75)

    for rank, v in enumerate(valid_by_count, 1):
        prob = v['count'] / SIMUL_NUMB * 100
        assign_str = ', '.join(v['readable'])
        marker = " ★" if v['qiskit_str'] == optimal_state else ""
        print(f"{rank:<5} {v['qiskit_str']:<10} {v['count']:<7} {prob:<7.2f}% {v['rate']:<8.1f} {assign_str}{marker}")

    optimal = next(v for v in all_valid if v['qiskit_str'] == optimal_state)
    opt_rank = next(i+1 for i, v in enumerate(valid_by_count) if v['qiskit_str'] == optimal_state)

    print(f"\nOptimal: {optimal_state} (Rate: {optimal['rate']:.1f} Mbps)")
    print(f"  → 측정순위: {opt_rank}위 ({optimal['count']}회, {optimal['count']/SIMUL_NUMB*100:.2f}%)")
    if opt_rank == 1:
        print("  → ✓ Optimal이 가장 많이 측정됨!")
    
    plot_histogram(counts)
    plt.show()
