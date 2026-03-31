"""
BS-UE Matching Quantum Circuit using Qiskit
============================================

Problem Setup:
- 3 Base Stations (BS)
- 4 User Equipments (UE)
- Each BS must have exactly 1-2 UEs (valid assignment)
- 2 bits per UE to encode BS assignment:
  |00⟩ = BS0, |01⟩ = BS1, |10⟩ = BS2, |11⟩ = prohibited

Qubit Layout:
- Qubits 0-7: Data qubits (4 UEs × 2 bits each)
- Qubits 8-9: Counter for BS0
- Qubits 10-11: Counter for BS1
- Qubits 12-13: Counter for BS2
- Qubits 14-16: Constraint flags (CONS0, CONS1, CONS2)
- Qubits 17-19: Objective ancillas (OBJ1, OBJ2, OBJ3)
- Qubit 20: Valid flag (VIOL)
"""

from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.visualization import plot_histogram
import numpy as np
import matplotlib.pyplot as plt

# ============================================================================
# Configuration
# ============================================================================

NUM_BS = 3
NUM_UE = 4
MAX_UE_PER_BS = 2
BITS_PER_UE = 2
NUM_DATA_QUBITS = 8

# Ancilla qubit indices
UE0_VALID = 8
UE1_VALID = 9
UE2_VALID = 10
UE3_VALID = 11
CNT0_LSB = 12
CNT0_MSB = 13
CNT1_LSB = 14
CNT1_MSB = 15
CNT2_LSB = 16
CNT2_MSB = 17
CONS0 = 18
CONS1 = 19
CONS2 = 20
PHASE_ANC0 = 21
PHASE_ANC1 = 22
PHASE_ANC2 = 23
PHASE_ANC3 = 24
VIOL = 25
NUM_TOTAL_QUBITS = 26

GROVER_ITERATIONS = 1
SIMUL_NUMB = 10000

# Channel rates (Mbps)
CHANNEL_RATE = [
    [45.2, 12.1, 28.5],   # UE0 -> BS0, BS1, BS2
    [18.7, 41.3, 15.9],   # UE1 -> BS0, BS1, BS2
    [29.4, 36.8, 48.2],   # UE2 -> BS0, BS1, BS2
    [22.1, 33.5, 19.7],   # UE3 -> BS0, BS1, BS2
]
MAX_RATE = max(max(row) for row in CHANNEL_RATE)


# ============================================================================
# Circuit Creation
# ============================================================================

def create_bs_ue_circuit(grover_iterations=None):
    """
    Create the complete BS-UE matching quantum circuit.
    """
    if grover_iterations is None:
        grover_iterations = GROVER_ITERATIONS
    
    qr = QuantumRegister(NUM_TOTAL_QUBITS, 'q')
    cr = ClassicalRegister(NUM_DATA_QUBITS, 'c')
    qc = QuantumCircuit(qr, cr)
    
    # State preparation
    apply_state_preparation(qc, qr)
    
    # Grover iterations
    for _ in range(grover_iterations):
        apply_objective_oracle(qc, qr)
        apply_constraint_oracle(qc, qr)
        uncompute_constraint(qc, qr)
        uncompute_objective_oracle(qc, qr)
        apply_diffusion(qc, qr)
    
    # Measurement
    qc.measure(qr[:NUM_DATA_QUBITS], cr)
    
    return qc


# ============================================================================
# State Preparation: Hadamard on all data qubits
# ============================================================================

def apply_state_preparation(qc, qr):
    """Apply Hadamard to all data qubits for uniform superposition."""
    for i in range(NUM_DATA_QUBITS):
        qc.h(qr[i])
    qc.barrier(label='State Prep')


def apply_state_preparation_inverse(qc, qr):
    """Inverse of Hadamard is Hadamard."""
    for i in range(NUM_DATA_QUBITS):
        qc.h(qr[i])


# ============================================================================
# Constraint Oracle: marks VALID states (each BS has 1-2 UEs)
# ============================================================================

def apply_constraint_oracle(qc, qr):
    """
    Marks valid states with phase flip.
    Valid = (no |11⟩ prohibited) AND (each BS has 1-2 UEs)
    
    Counter logic (LSB, MSB):
      count 0 → (0,0), count 1 → (1,1), count 2 → (0,1), count 3 → (1,0)
    
    MSB = 1 when count is 1 or 2 (valid)
    
    Logic:
    1. Check prohibited |11⟩ - use OBJ1 as flag (1 if no prohibited)
    2. Count UEs for each BS using 2-bit counters
    3. CONS[i] = MSB = 1 if count is 1 or 2 (valid for this BS)
    4. VIOL = OBJ1 AND CONS0 AND CONS1 AND CONS2
    5. Phase flip on VIOL
    """
    qc.barrier(label='Constraint Oracle')
    
    # Check for prohibited |11⟩ states
    for ue in range(NUM_UE):
        q0 = ue * 2
        q1 = ue * 2 + 1
        ue_valid = UE0_VALID + ue
        
        qc.x(qr[ue_valid])  # 1로 시작
        qc.ccx(qr[q0], qr[q1], qr[ue_valid])
    
    # Count UEs for each BS
    for ue in range(NUM_UE):
        q0 = ue * 2
        q1 = ue * 2 + 1
        
        # BS0: |00⟩ - q0=0, q1=0 → X both to make 1,1
        qc.x(qr[q0])
        qc.x(qr[q1])
        qc.ccx(qr[q0], qr[q1], qr[CNT0_LSB])
        qc.mcx([qr[q0], qr[q1], qr[CNT0_LSB]], qr[CNT0_MSB])
        qc.x(qr[q0])
        qc.x(qr[q1])
        
        # BS1: |01⟩ - q0=1, q1=0 → X(q1) to make 1,1
        qc.x(qr[q1])
        qc.ccx(qr[q0], qr[q1], qr[CNT1_LSB])
        qc.mcx([qr[q0], qr[q1], qr[CNT1_LSB]], qr[CNT1_MSB])
        qc.x(qr[q1])
        
        # BS2: |10⟩ - q0=0, q1=1 → X(q0) to make 1,1
        qc.x(qr[q0])
        qc.ccx(qr[q0], qr[q1], qr[CNT2_LSB])
        qc.mcx([qr[q0], qr[q1], qr[CNT2_LSB]], qr[CNT2_MSB])
        qc.x(qr[q0])
    
    # CONS = MSB (1 if count is 1 or 2)
    qc.cx(qr[CNT0_MSB], qr[CONS0])
    qc.cx(qr[CNT1_MSB], qr[CONS1])
    qc.cx(qr[CNT2_MSB], qr[CONS2])
    
    # VIOL = OBJ1 AND CONS0 AND CONS1 AND CONS2 (all valid)
    qc.mcx([qr[UE0_VALID], qr[UE1_VALID], qr[UE2_VALID], qr[UE3_VALID], 
        qr[CONS0], qr[CONS1], qr[CONS2], qr[PHASE_ANC0], qr[PHASE_ANC1], qr[PHASE_ANC2], qr[PHASE_ANC3]], qr[VIOL])
    #qc.mcx([qr[UE0_VALID], qr[UE1_VALID], qr[UE2_VALID], qr[UE3_VALID], 
    #        qr[CONS0], qr[CONS1], qr[CONS2]], qr[VIOL])
    
    # Phase flip valid states (VIOL = 1)
    qc.z(qr[VIOL])


def uncompute_constraint(qc, qr):
    """
    Uncompute constraint oracle (restore ancillas to |0⟩).
    Reverse order of operations, excluding Z gate.
    """
    qc.barrier(label='Uncompute')
    
    # Uncompute VIOL
    qc.mcx([qr[UE0_VALID], qr[UE1_VALID], qr[UE2_VALID], qr[UE3_VALID], 
        qr[CONS0], qr[CONS1], qr[CONS2], qr[PHASE_ANC0], qr[PHASE_ANC1], qr[PHASE_ANC2], qr[PHASE_ANC3]], qr[VIOL])
    #qc.mcx([qr[UE0_VALID], qr[UE1_VALID], qr[UE2_VALID], qr[UE3_VALID], 
    #        qr[CONS0], qr[CONS1], qr[CONS2]], qr[VIOL])
    
    # Uncompute CONS
    qc.cx(qr[CNT2_MSB], qr[CONS2])
    qc.cx(qr[CNT1_MSB], qr[CONS1])
    qc.cx(qr[CNT0_MSB], qr[CONS0])
    
    # Uncompute counters (reverse UE order)
    for ue in range(NUM_UE - 1, -1, -1):
        q0 = ue * 2
        q1 = ue * 2 + 1
        
        # BS2: undo
        qc.x(qr[q0])
        qc.mcx([qr[q0], qr[q1], qr[CNT2_LSB]], qr[CNT2_MSB])
        qc.ccx(qr[q0], qr[q1], qr[CNT2_LSB])
        qc.x(qr[q0])
        
        # BS1: undo
        qc.x(qr[q1])
        qc.mcx([qr[q0], qr[q1], qr[CNT1_LSB]], qr[CNT1_MSB])
        qc.ccx(qr[q0], qr[q1], qr[CNT1_LSB])
        qc.x(qr[q1])
        
        # BS0: undo
        qc.x(qr[q0])
        qc.x(qr[q1])
        qc.mcx([qr[q0], qr[q1], qr[CNT0_LSB]], qr[CNT0_MSB])
        qc.ccx(qr[q0], qr[q1], qr[CNT0_LSB])
        qc.x(qr[q0])
        qc.x(qr[q1])
    
    # Uncompute OBJ1 (prohibited check)
    for ue in range(NUM_UE):
        q0 = ue * 2
        q1 = ue * 2 + 1
        ue_valid = UE0_VALID + ue
        
        qc.ccx(qr[q0], qr[q1], qr[ue_valid])  # |11⟩이면 0으로 flip
        qc.x(qr[ue_valid])  # 1로 시작
       


# ============================================================================
# Objective Oracle: phase rotation based on channel rates
# ============================================================================


def apply_objective_oracle(qc, qr, alpha=3.1):
    qc.barrier(label="Objective Oracle")

    for ue in range(NUM_UE):
        q0 = ue * 2
        q1 = ue * 2 + 1
        phase_anc = PHASE_ANC0 + ue

        for bs in range(NUM_BS):
            #theta = alpha * (CHANNEL_RATE[ue][bs] / MAX_RATE) * np.pi
            rate_norm = CHANNEL_RATE[ue][bs] / MAX_RATE
            theta = np.arccos(np.sqrt(1 - rate_norm))
            
            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])

            qc.mcry(theta, [qr[q0], qr[q1]], qr[phase_anc])

            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])


def uncompute_objective_oracle(qc, qr, alpha=3.1):
    qc.barrier(label="Uncompute Objective")

    for ue in range(NUM_UE - 1, -1, -1):
        q0 = ue * 2
        q1 = ue * 2 + 1
        phase_anc = PHASE_ANC0 + ue

        for bs in range(NUM_BS - 1, -1, -1):
            #theta = alpha * (CHANNEL_RATE[ue][bs] / MAX_RATE) * np.pi
            rate_norm = CHANNEL_RATE[ue][bs] / MAX_RATE
            theta = np.arccos(np.sqrt(1 - rate_norm))

            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])

            qc.mcry(-theta, [qr[q0], qr[q1]], qr[phase_anc])

            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])
                
def apply_objective_oracle_phase_only(qc, qr, alpha=1):
    """Phase-based objective oracle"""
    qc.barrier(label="Objective Oracle")

    for ue in range(NUM_UE):
        q0 = ue * 2
        q1 = ue * 2 + 1

        for bs in range(NUM_BS):
            rate_norm = CHANNEL_RATE[ue][bs] / MAX_RATE
            theta = np.arccos(np.sqrt(1 - rate_norm))

            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])

            # Phase gate: |11⟩에 e^(iθ) phase 추가
            qc.cp(theta, qr[q0], qr[q1])

            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])


def uncompute_objective_oracle_phase_only(qc, qr, alpha=1):
    """Phase uncompute"""
    qc.barrier(label="Uncompute Objective")

    for ue in range(NUM_UE - 1, -1, -1):
        q0 = ue * 2
        q1 = ue * 2 + 1

        for bs in range(NUM_BS - 1, -1, -1):
            rate_norm = CHANNEL_RATE[ue][bs] / MAX_RATE
            theta = np.arccos(np.sqrt(1 - rate_norm))

            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])

            qc.cp(-theta, qr[q0], qr[q1])  # 역방향

            if bs == 0:
                qc.x(qr[q0])
                qc.x(qr[q1])
            elif bs == 1:
                qc.x(qr[q1])
            else:
                qc.x(qr[q0])

# ============================================================================
# Diffusion Operator
# ============================================================================

def apply_diffusion(qc, qr):
    """
    Grover diffusion: reflects about initial superposition state.
    U_s = S · (2|0⟩⟨0| - I) · S†
    """
    qc.barrier(label='Diffusion')
    
    apply_state_preparation_inverse(qc, qr)
    
    # Reflect about |0⟩: (2|0⟩⟨0| - I) = X⊗n · MCZ · X⊗n
    for i in range(NUM_DATA_QUBITS):
        qc.x(qr[i])
    
    # MCZ via H-MCX-H
    qc.h(qr[NUM_DATA_QUBITS - 1])
    qc.mcx([qr[i] for i in range(NUM_DATA_QUBITS - 1)], qr[NUM_DATA_QUBITS - 1])
    qc.h(qr[NUM_DATA_QUBITS - 1])
    
    for i in range(NUM_DATA_QUBITS):
        qc.x(qr[i])
    
    apply_state_preparation(qc, qr)


# ============================================================================
# Helper Functions
# ============================================================================

def decode_result(measurement):
    """Decode measurement to BS assignments."""
    assignments = []
    bits = measurement[::-1]  # Qiskit returns reversed
    
    for ue in range(NUM_UE):
        q0 = int(bits[ue * 2])
        q1 = int(bits[ue * 2 + 1])
        bs = q0 + 2 * q1
        if bs == 3:
            assignments.append("xxx")
        else:
            assignments.append(f"BS{bs}")
    
    return assignments


def calculate_total_rate(assignments):
    """Calculate total rate for assignment."""
    total = 0.0
    for ue, bs in enumerate(assignments):
        if bs != "xxx":
            total += CHANNEL_RATE[ue][int(bs[2])]
    return total


def is_valid_assignment(assignments):
    """Check if assignment is valid (each BS has exactly 1-2 UEs)."""
    bs_counts = [0, 0, 0]
    for a in assignments:
        if a == "xxx":
            return False
        bs_counts[int(a[2])] += 1
    
    # Each BS must have exactly 1 or 2 UEs
    for count in bs_counts:
        if count < 1 or count > 2:
            return False
    return True

def generate_all_valid_states():
    """이론적으로 가능한 모든 valid state 생성 (36개)"""
    from itertools import product
    
    valid_states = []
    bs_to_bits = {0: '00', 1: '01', 2: '10'}
    
    for assignment in product([0, 1, 2], repeat=4):
        bs_counts = [assignment.count(i) for i in range(3)]
        
        if all(1 <= c <= 2 for c in bs_counts):
            qubit_str = ''.join(bs_to_bits[bs] for bs in assignment)
            qiskit_str = qubit_str[::-1]  # little-endian
            
            valid_states.append({
                'qiskit_str': qiskit_str,
                'assignment': assignment,
                'bs_counts': bs_counts,
                'readable': [f"UE{i}→BS{bs}" for i, bs in enumerate(assignment)]
            })
    
    return valid_states


# ============================================================================
# Main
# ============================================================================

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
        print(f"UE{ue}: ", end="")
        for bs in range(NUM_BS):
            print(f"{CHANNEL_RATE[ue][bs]:8.2f}  ", end="")
        print()
    
    print("\nCreating and running circuit...")
    qc = create_bs_ue_circuit()
    print(f"  Depth: {qc.depth()}, Gates: {qc.size()}")
    
    from qiskit_aer import AerSimulator
    simulator = AerSimulator()
    counts = simulator.run(qc, shots=SIMUL_NUMB).result().get_counts()
    
    # 결과 분석
    print("\n" + "=" * 80)
    print(f"   결과 분석 ({SIMUL_NUMB} shots)")
    print("=" * 80)

    all_valid = generate_all_valid_states()
    for v in all_valid:
        assignment = [f"BS{bs}" for bs in v['assignment']]
        v['rate'] = calculate_total_rate(assignment)
        v['count'] = counts.get(v['qiskit_str'], 0)

    optimal_state = max(all_valid, key=lambda x: x['rate'])['qiskit_str']
    valid_by_count = sorted(all_valid, key=lambda x: x['count'], reverse=True)
    total_valid = sum(v['count'] for v in all_valid)

    print(f"\nValid: {total_valid/SIMUL_NUMB*100:.1f}% (기대: 14.1%) | 증폭률: {(total_valid/SIMUL_NUMB) / (36/256):.2f}x")

    print(f"\n{'순위':<4} {'State':<10} {'Count':<7} {'Prob%':<7} {'Rate':<8} {'Assignment'}")
    print("-" * 75)

    for rank, v in enumerate(valid_by_count, 1):
        prob = v['count'] / SIMUL_NUMB * 100
        assign_str = ', '.join(v['readable'])
        marker = "★" if v['qiskit_str'] == optimal_state else ""
        print(f"{rank:<4} {v['qiskit_str']:<10} {v['count']:<7} {prob:<6.2f}% {v['rate']:<8.1f} {assign_str} {marker}")

    optimal = next(v for v in all_valid if v['qiskit_str'] == optimal_state)
    opt_rank = next(i+1 for i, v in enumerate(valid_by_count) if v['qiskit_str'] == optimal_state)

    print(f"\nOptimal: {optimal_state} (Rate: {optimal['rate']:.1f} Mbps)")
    print(f"  → 측정순위: {opt_rank}위 ({optimal['count']}회, {optimal['count']/SIMUL_NUMB*100:.2f}%)")
    if opt_rank == 1:
        print("  → ✓ Optimal이 가장 많이 측정됨!")
    
    plot_histogram(counts)
    plt.show()
    
