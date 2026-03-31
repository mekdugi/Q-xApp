"""
BS-UE Matching 결과 시각화
- 회로 다이어그램
- 확률 분포
- 네트워크 토폴로지
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle, FancyArrowPatch
import numpy as np
import json
import os

# ============================================================================
# 설정 및 결과 로드
# ============================================================================

# Load quantum results from JSON
quantum_data = None
if os.path.exists("quantum_results.json"):
    with open("quantum_results.json", "r") as f:
        quantum_data = json.load(f)

NUM_BS = quantum_data["num_bs"] if quantum_data else 3
NUM_UE = quantum_data["num_ue"] if quantum_data else 4
MAX_UE_PER_BS = 2
RANDOM_SEED = 42

# Channel model parameters
TX_POWER_DBM = 23.0
NOISE_POWER_DBM = -104.0
BANDWIDTH_HZ = 10e6
PATH_LOSS_EXPONENT = 3.76
D0 = 10.0
PL_D0 = 128.1 + 37.6 * np.log10(D0 / 1000.0)
AREA_SIZE = quantum_data["area_size"] if quantum_data else 500.0

# ============================================================================
# 채널 계산
# ============================================================================

# Load positions from quantum data or generate
if quantum_data and "bs_positions" in quantum_data and "ue_positions" in quantum_data:
    bs_positions = np.array([[pos["x"], pos["y"]] for pos in quantum_data["bs_positions"]])
    ue_positions = np.array([[pos["x"], pos["y"]] for pos in quantum_data["ue_positions"]])
else:
    np.random.seed(RANDOM_SEED)
    bs_positions = np.array([[100, 250], [400, 250], [250, 425]])
    ue_positions = np.random.rand(NUM_UE, 2) * AREA_SIZE

def calc_rate(ue, bs):
    dist = np.linalg.norm(ue_positions[ue] - bs_positions[bs])
    if dist < D0:
        dist = D0
    pl = PL_D0 + 10 * PATH_LOSS_EXPONENT * np.log10(dist / D0)
    snr_dB = TX_POWER_DBM - pl - NOISE_POWER_DBM
    snr_lin = 10 ** (snr_dB / 10)
    return BANDWIDTH_HZ * np.log2(1 + snr_lin) / 1e6

channel_rate = np.array([[calc_rate(ue, bs) for bs in range(NUM_BS)] for ue in range(NUM_UE)])
max_rate = channel_rate.max()

# ============================================================================
# Helper functions
# ============================================================================

def get_bs_from_state(state, ue):
    return (state >> (ue * 2)) & 0x3

def has_prohibited(state):
    for ue in range(NUM_UE):
        if get_bs_from_state(state, ue) == 3:
            return True
    return False

def count_ues_for_bs(state, bs):
    return sum(1 for ue in range(NUM_UE) if get_bs_from_state(state, ue) == bs)

def satisfies_capacity(state):
    return all(count_ues_for_bs(state, bs) <= MAX_UE_PER_BS for bs in range(NUM_BS))

def is_valid(state):
    return not has_prohibited(state) and satisfies_capacity(state)

def calc_objective(state):
    total = 0
    for ue in range(NUM_UE):
        bs = get_bs_from_state(state, ue)
        if bs < NUM_BS:
            total += channel_rate[ue][bs]
    return total

def state_to_str(state):
    bits = format(state, '08b')
    return f"|{bits[0:2]} {bits[2:4]} {bits[4:6]} {bits[6:8]}⟩"

# ============================================================================
# Figure 1: Quantum Circuit Diagram
# ============================================================================

def draw_circuit():
    fig, ax = plt.subplots(1, 1, figsize=(16, 10))
    ax.set_xlim(-1, 20)
    ax.set_ylim(-1, 12)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_title('BS-UE Matching Quantum Circuit (1 Grover Iteration)\n18 qubits: 8 data + 10 ancilla', 
                 fontsize=14, fontweight='bold')
    
    # Qubit labels
    qubit_labels = ['ue[0]', 'ue[1]', 'ue[2]', 'ue[3]', 'ue[4]', 'ue[5]', 'ue[6]', 'ue[7]',
                    'cnt0₀', 'cnt0₁', 'cnt1₀', 'cnt1₁', 'cnt2₀', 'cnt2₁',
                    'cons[0]', 'cons[1]', 'cons[2]', 'viol']
    
    y_positions = np.linspace(10.5, 0.5, 18)
    
    # Draw qubit lines
    for i, (label, y) in enumerate(zip(qubit_labels, y_positions)):
        color = 'blue' if i < 8 else 'gray'
        ax.hlines(y, 0, 19, color=color, linewidth=0.8, alpha=0.7)
        ax.text(-0.8, y, label, ha='right', va='center', fontsize=8, color=color)
    
    # Gate positions
    gate_x = [1, 3, 5, 8, 11, 14, 17]
    gate_labels = ['State\nPrep', 'Count\nUEs', 'Check\nCons', 'Phase\nFlip', 'Obj\nOracle', 'Uncompute', 'Diffusion']
    gate_colors = ['#90EE90', '#FFB6C1', '#FFB6C1', '#FF6B6B', '#87CEEB', '#DDA0DD', '#98FB98']
    
    for gx, label, color in zip(gate_x, gate_labels, gate_colors):
        # Box
        rect = FancyBboxPatch((gx-0.4, 0.2), 0.8, 10.6, 
                               boxstyle="round,pad=0.05", 
                               facecolor=color, edgecolor='black', alpha=0.7)
        ax.add_patch(rect)
        ax.text(gx, 5.5, label, ha='center', va='center', fontsize=8, fontweight='bold')
    
    # Gate details (small text)
    details = [
        (1, 'Ry, CRy'),
        (3, 'CCX×24'),
        (5, 'CCX×3'),
        (8, 'Z'),
        (11, 'CP×12'),
        (14, 'CCX×27'),
        (17, 'MCX')
    ]
    for x, txt in details:
        ax.text(x, -0.3, txt, ha='center', va='top', fontsize=7, style='italic')
    
    # Barriers
    for bx in [2, 4, 7, 10, 13, 16]:
        ax.vlines(bx, 0.3, 10.7, color='black', linestyle='--', linewidth=0.5, alpha=0.5)
    
    # Legend
    legend_elements = [
        mpatches.Patch(facecolor='#90EE90', edgecolor='black', label='State Preparation'),
        mpatches.Patch(facecolor='#FFB6C1', edgecolor='black', label='Constraint Oracle'),
        mpatches.Patch(facecolor='#FF6B6B', edgecolor='black', label='Phase Flip'),
        mpatches.Patch(facecolor='#87CEEB', edgecolor='black', label='Objective Oracle'),
        mpatches.Patch(facecolor='#DDA0DD', edgecolor='black', label='Uncompute'),
        mpatches.Patch(facecolor='#98FB98', edgecolor='black', label='Diffusion'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=8)
    
    # Annotations
    ax.annotate('', xy=(19, 5.5), xytext=(18.5, 5.5),
                arrowprops=dict(arrowstyle='->', color='black'))
    ax.text(19.2, 5.5, 'Measure', va='center', fontsize=9)
    
    plt.tight_layout()
    return fig

# ============================================================================
# Figure 2: Probability Distribution
# ============================================================================

def draw_probability_distribution():
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    
    if quantum_data is None:
        # Fallback: Simulate probability distribution
        num_states = 256
        probs = np.zeros(num_states)
        
        valid_states = []
        for s in range(num_states):
            if is_valid(s):
                rate = calc_objective(s)
                probs[s] = (rate / max_rate) ** 2 * 0.01 + 0.005
                valid_states.append((s, rate, probs[s]))
        
        for s in range(num_states):
            if not has_prohibited(s) and not satisfies_capacity(s):
                probs[s] = 0.003
        
        probs = probs / probs.sum()
        valid_states.sort(key=lambda x: x[1], reverse=True)
    else:
        # Use actual quantum results - load ALL state probabilities
        probs = np.zeros(256)
        
        # Load all state probabilities from JSON
        if "all_state_probs" in quantum_data:
            for item in quantum_data["all_state_probs"]:
                state = item["state"]
                prob = item["prob"]
                probs[state] = prob
        
        # Build valid states list
        valid_states = []
        for s in range(256):
            if is_valid(s):
                rate = calc_objective(s)
                valid_states.append((s, rate, probs[s]))
        
        # Sort by rate (descending)
        valid_states.sort(key=lambda x: x[1], reverse=True)
    
    # Left: Full distribution
    ax1 = axes[0]
    colors = []
    for s in range(256):
        if has_prohibited(s):
            colors.append('#FF6B6B')  # Red
        elif not satisfies_capacity(s):
            colors.append('#FFD93D')  # Yellow
        else:
            colors.append('#6BCB77')  # Green
    
    ax1.bar(range(256), probs, color=colors, width=1.0)
    ax1.set_xlabel('State Index', fontsize=10)
    ax1.set_ylabel('Probability', fontsize=10)
    ax1.set_title(f'Probability Distribution (All {len(valid_states)} Valid States)', fontsize=12, fontweight='bold')
    
    legend_elements = [
        mpatches.Patch(facecolor='#6BCB77', label='Valid'),
        mpatches.Patch(facecolor='#FFD93D', label='Capacity Violation'),
        mpatches.Patch(facecolor='#FF6B6B', label='Prohibited (|11⟩)'),
    ]
    ax1.legend(handles=legend_elements, fontsize=8)
    
    # Right: All valid states (sorted by rate)
    ax2 = axes[1]
    all_valid_states = valid_states  # Show ALL valid states
    
    labels = []
    probs_list = []
    for s, rate, prob in all_valid_states:
        assignment = [get_bs_from_state(s, ue) for ue in range(NUM_UE)]
        labels.append(f"UE→BS{assignment}")
        probs_list.append(prob)
    
    y_pos = np.arange(len(all_valid_states))
    colors_bar = ['#4ECDC4'] + ['#6BCB77'] * (len(all_valid_states) - 1)  # First is optimal
    ax2.barh(y_pos, probs_list, color=colors_bar)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(labels, fontsize=8)
    ax2.set_xlabel('Detection Probability', fontsize=10)
    ax2.set_title(f'All {len(all_valid_states)} Valid Assignments', fontsize=12, fontweight='bold')
    ax2.invert_yaxis()
    
    plt.tight_layout()
    return fig

# ============================================================================
# Figure 3: Network Topology
# ============================================================================

def draw_network_topology():
    fig, ax = plt.subplots(1, 1, figsize=(10, 8))
    
    # Use quantum results if available, otherwise calculate
    if quantum_data:
        best_rate = quantum_data['optimal_rate']
        optimal_assignment = quantum_data['optimal_assignment']
    else:
        # Find optimal assignment
        best_state = 0
        best_rate = 0
        for s in range(256):
            if is_valid(s):
                rate = calc_objective(s)
                if rate > best_rate:
                    best_rate = rate
                    best_state = s
        
        optimal_assignment = [get_bs_from_state(best_state, ue) for ue in range(NUM_UE)]
    
    # Draw BS
    bs_colors = ['#FF6B6B', '#4ECDC4', '#45B7D1']
    for i, (pos, color) in enumerate(zip(bs_positions, bs_colors)):
        circle = Circle(pos, 30, facecolor=color, edgecolor='black', linewidth=2, alpha=0.8)
        ax.add_patch(circle)
        ax.text(pos[0], pos[1], f'BS{i}', ha='center', va='center', fontsize=12, fontweight='bold')
        # Coverage circle
        coverage = Circle(pos, 200, facecolor='none', edgecolor=color, linewidth=1, linestyle='--', alpha=0.3)
        ax.add_patch(coverage)
    
    # Draw UE
    ue_colors = ['#FFE66D', '#95E1D3', '#F38181', '#AA96DA']
    for i, (pos, color) in enumerate(zip(ue_positions, ue_colors)):
        circle = Circle(pos, 15, facecolor=color, edgecolor='black', linewidth=1.5)
        ax.add_patch(circle)
        ax.text(pos[0], pos[1], f'UE{i}', ha='center', va='center', fontsize=9, fontweight='bold')
    
    # Draw optimal connections
    for ue in range(NUM_UE):
        bs = optimal_assignment[ue]
        ue_pos = ue_positions[ue]
        bs_pos = bs_positions[bs]
        
        ax.annotate('', xy=bs_pos, xytext=ue_pos,
                    arrowprops=dict(arrowstyle='->', color=bs_colors[bs], linewidth=2, alpha=0.8))
        
        # Rate label
        mid = (ue_pos + bs_pos) / 2
        rate = channel_rate[ue][bs]
        ax.text(mid[0], mid[1], f'{rate:.1f}', fontsize=8, 
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    
    ax.set_xlim(-50, AREA_SIZE + 50)
    ax.set_ylim(-50, AREA_SIZE + 50)
    ax.set_aspect('equal')
    ax.set_xlabel('X (m)', fontsize=10)
    ax.set_ylabel('Y (m)', fontsize=10)
    ax.set_title(f'Optimal Assignment: Total Rate = {best_rate:.2f} Mbps\n'
                 f'Assignment: {optimal_assignment}', fontsize=12, fontweight='bold')
    ax.grid(True, alpha=0.3)
    
    # Channel matrix table
    ax_table = fig.add_axes([0.65, 0.02, 0.33, 0.2])
    ax_table.axis('off')
    
    table_data = [[''] + [f'BS{i}' for i in range(NUM_BS)]]
    for ue in range(NUM_UE):
        row = [f'UE{ue}'] + [f'{channel_rate[ue][bs]:.1f}' for bs in range(NUM_BS)]
        table_data.append(row)
    
    table = ax_table.table(cellText=table_data, loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.2, 1.5)
    
    plt.tight_layout()
    return fig

# ============================================================================
# Main
# ============================================================================

if __name__ == '__main__':
    print("Generating visualizations...")
    print(f"\nChannel Rate Matrix (Mbps):")
    print(f"{'':>6} {'BS0':>10} {'BS1':>10} {'BS2':>10}")
    for ue in range(NUM_UE):
        print(f"UE{ue}: {channel_rate[ue][0]:>10.2f} {channel_rate[ue][1]:>10.2f} {channel_rate[ue][2]:>10.2f}")
    
    # Find optimal
    best_state = 0
    best_rate = 0
    for s in range(256):
        if is_valid(s):
            rate = calc_objective(s)
            if rate > best_rate:
                best_rate = rate
                best_state = s
    
    print(f"\nOptimal Assignment:")
    print(f"  State: {state_to_str(best_state)}")
    print(f"  Assignment: {[get_bs_from_state(best_state, ue) for ue in range(NUM_UE)]}")
    print(f"  Total Rate: {best_rate:.2f} Mbps")
    
    # Show quantum results status
    if quantum_data:
        print(f"\n✓ Loaded quantum_results.json")
        print(f"  Valid probability: {quantum_data['probabilities']['valid']:.4f}")
        print(f"  Optimal rate: {quantum_data['optimal_rate']:.2f} Mbps")
    else:
        print(f"\n⚠ quantum_results.json not found - using simulated data")
    
    # Generate figures
    fig1 = draw_circuit()
    fig1.savefig('quantum_circuit_diagram.png', dpi=150, bbox_inches='tight', facecolor='white')
    print("\nSaved: quantum_circuit_diagram.png")
    plt.close(fig1)
    
    fig2 = draw_probability_distribution()
    fig2.savefig('probability_distribution.png', dpi=150, bbox_inches='tight', facecolor='white')
    print("Saved: probability_distribution.png")
    plt.close(fig2)
    
    fig3 = draw_network_topology()
    fig3.savefig('network_topology.png', dpi=150, bbox_inches='tight', facecolor='white')
    print("Saved: network_topology.png")
    plt.close(fig3)
    
    print("\nDone!")
