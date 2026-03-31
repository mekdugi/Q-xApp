/**
 * =============================================================================
 * BS-UE Matching using QuEST (Real Gate Operations)
 * =============================================================================
 * 
 * Compile: gcc -O2 -Wall -std=c99 -I. -o bs_ue_quest bs_ue_quest.c QuEST.c -lm
 * Run: ./bs_ue_quest
 * 
 * =============================================================================
 */

#include "QuEST.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>

// ============================================================================
// Configuration
// ============================================================================

#define NUM_BS 3
#define NUM_UE 4
#define MAX_UE_PER_BS 2
#define BITS_PER_UE 2
#define NUM_DATA_QUBITS 8

// Ancilla layout
#define CNT0_LSB 8
#define CNT0_MSB 9
#define CNT1_LSB 10
#define CNT1_MSB 11
#define CNT2_LSB 12
#define CNT2_MSB 13
#define CONS0 14
#define CONS1 15
#define CONS2 16
#define VIOL 17
#define RATE0 18
#define RATE1 19
#define RATE2 20
#define RATE3 21
#define FLIP_TEMP 22
#define NUM_TOTAL_QUBITS 23

#define RANDOM_SEED 423
#define GROVER_ITERATIONS 2

// Channel model
#define TX_POWER_DBM 23.0
#define NOISE_POWER_DBM -104.0
#define BANDWIDTH_HZ 10e6
#define PATH_LOSS_EXPONENT 3.76
#define D0 10.0
#define PL_D0 (128.1 + 37.6 * log10(D0 / 1000.0))
#define AREA_SIZE 500.0

// ============================================================================
// Global Data
// ============================================================================

typedef struct { double x, y; } Position;

Position bs_positions[NUM_BS];
Position ue_positions[NUM_UE];
double channel_rate[NUM_UE][NUM_BS];
double max_rate;

// ============================================================================
// Channel Model
// ============================================================================

double distance(Position a, Position b) {
    return sqrt((a.x-b.x)*(a.x-b.x) + (a.y-b.y)*(a.y-b.y));
}

void init_channel() {
    bs_positions[0] = (Position){100.0, 250.0};
    bs_positions[1] = (Position){400.0, 250.0};
    bs_positions[2] = (Position){250.0, 425.0};
    
    srand(RANDOM_SEED);
    for (int ue = 0; ue < NUM_UE; ue++) {
        ue_positions[ue].x = (double)rand() / RAND_MAX * AREA_SIZE;
        ue_positions[ue].y = (double)rand() / RAND_MAX * AREA_SIZE;
    }
    
    max_rate = 0.0;
    for (int ue = 0; ue < NUM_UE; ue++) {
        for (int bs = 0; bs < NUM_BS; bs++) {
            double dist = distance(ue_positions[ue], bs_positions[bs]);
            if (dist < D0) dist = D0;
            double pl = PL_D0 + 10.0 * PATH_LOSS_EXPONENT * log10(dist / D0);
            double snr_dB = TX_POWER_DBM - pl - NOISE_POWER_DBM;
            double snr_lin = pow(10.0, snr_dB / 10.0);
            channel_rate[ue][bs] = BANDWIDTH_HZ * log2(1.0 + snr_lin) / 1e6;
            if (channel_rate[ue][bs] > max_rate) max_rate = channel_rate[ue][bs];
        }
    }
}

// ============================================================================
// Assignment Helpers
// ============================================================================

int getBSFromState(long long state, int ue) {
    return (state >> (ue * BITS_PER_UE)) & 0x3;
}

int hasProhibitedState(long long state) {
    for (int ue = 0; ue < NUM_UE; ue++)
        if (getBSFromState(state, ue) == 3) return 1;
    return 0;
}

int countUEsForBS(long long state, int bs) {
    int count = 0;
    for (int ue = 0; ue < NUM_UE; ue++)
        if (getBSFromState(state, ue) == bs) count++;
    return count;
}

int satisfiesCapacity(long long state) {
    for (int bs = 0; bs < NUM_BS; bs++)
        if (countUEsForBS(state, bs) > MAX_UE_PER_BS) return 0;
    return 1;
}

int isValidAssignment(long long state) {
    return !hasProhibitedState(state) && satisfiesCapacity(state);
}

double calculateObjective(long long state) {
    double total = 0.0;
    for (int ue = 0; ue < NUM_UE; ue++) {
        int bs = getBSFromState(state, ue);
        if (bs < NUM_BS) total += channel_rate[ue][bs];
    }
    return total;
}

// ============================================================================
// State Preparation: (|00⟩ + |01⟩ + |10⟩)/√3 per UE pair
// ============================================================================

void applyStatePreparation(Qureg q) {
    double theta1 = 2.0 * acos(sqrt(2.0/3.0));
    double theta2 = M_PI / 2.0;
    
    for (int ue = 0; ue < NUM_UE; ue++) {
        int q0 = ue * 2;
        int q1 = ue * 2 + 1;
        
        rotateY(q, q0, theta1);
        pauliX(q, q0);
        controlledRotateY(q, q0, q1, theta2);
        pauliX(q, q0);
    }
}

void applyStatePreparationInverse(Qureg q) {
    double theta1 = 2.0 * acos(sqrt(2.0/3.0));
    double theta2 = M_PI / 2.0;
    
    for (int ue = NUM_UE - 1; ue >= 0; ue--) {
        int q0 = ue * 2;
        int q1 = ue * 2 + 1;
        
        pauliX(q, q0);
        controlledRotateY(q, q0, q1, -theta2);
        pauliX(q, q0);
        rotateY(q, q0, -theta1);
    }
}

// ============================================================================
// Constraint Oracle using decomposed Toffoli gates
// ============================================================================

void applyConstraintOracle(Qureg q) {
    // Count UEs for each BS using decomposed Toffoli
    for (int ue = 0; ue < NUM_UE; ue++) {
        int q0 = ue * 2;
        int q1 = ue * 2 + 1;
        
        // BS0: |00⟩
        pauliX(q, q0); pauliX(q, q1);
        ccx_decomposed(q, q0, q1, CNT0_LSB);
        ccx_decomposed(q, q0, CNT0_LSB, CNT0_MSB);
        pauliX(q, q0); pauliX(q, q1);
        
        // BS1: |01⟩
        pauliX(q, q0);
        ccx_decomposed(q, q0, q1, CNT1_LSB);
        ccx_decomposed(q, q0, CNT1_LSB, CNT1_MSB);
        pauliX(q, q0);
        
        // BS2: |10⟩
        pauliX(q, q1);
        ccx_decomposed(q, q0, q1, CNT2_LSB);
        ccx_decomposed(q, q0, CNT2_LSB, CNT2_MSB);
        pauliX(q, q1);
    }
    
    // Check if count <= 2 (MSB=1 means count=1 or 2)
    controlledNot(q, CNT0_LSB, CONS0);
    controlledNot(q, CNT1_LSB, CONS1);
    controlledNot(q, CNT2_LSB, CONS2);
    
    // AND of all three: CONS0 AND CONS1 AND CONS2 AND all RATE ancillas → VIOL
    // Using multi-controlled gate
    int cons_ctrls[7] = {CONS0, CONS1, CONS2, RATE0, RATE1, RATE2, RATE3};
    int viol_target = VIOL;
    multiControlledMultiQubitNot(q, cons_ctrls, 7, &viol_target, 1);
    
    // Phase flip
    pauliZ(q, VIOL);
}

void uncomputeConstraint(Qureg q) {
    // Reverse the constraint oracle operations
    
    // Undo phase flip
    pauliZ(q, VIOL);
    
    // Undo multi-controlled AND computation
    int cons_ctrls[7] = {CONS0, CONS1, CONS2, RATE0, RATE1, RATE2, RATE3};
    int viol_target = VIOL;
    multiControlledMultiQubitNot(q, cons_ctrls, 7, &viol_target, 1);
    
    // Undo controlledNot on CONS qubits
    controlledNot(q, CNT0_LSB, CONS0);
    controlledNot(q, CNT1_LSB, CONS1);
    controlledNot(q, CNT2_LSB, CONS2);
    
    // Undo counting (reverse counting loop)
    for (int ue = NUM_UE - 1; ue >= 0; ue--) {
        int q0 = ue * 2;
        int q1 = ue * 2 + 1;
        
        // BS2: |10⟩ reverse
        pauliX(q, q1);
        ccx_decomposed(q, q0, q1, CNT2_LSB);
        ccx_decomposed(q, q0, CNT2_LSB, CNT2_MSB);
        pauliX(q, q1);
        
        // BS1: |01⟩ reverse
        pauliX(q, q0);
        ccx_decomposed(q, q0, q1, CNT1_LSB);
        ccx_decomposed(q, q0, CNT1_LSB, CNT1_MSB);
        pauliX(q, q0);
        
        // BS0: |00⟩ reverse
        pauliX(q, q0); pauliX(q, q1);
        ccx_decomposed(q, q0, q1, CNT0_LSB);
        ccx_decomposed(q, q0, CNT0_LSB, CNT0_MSB);
        pauliX(q, q0); pauliX(q, q1);
    }
}

// ============================================================================
// Objective Oracle - Weighted Grover with ancilla-based rate encoding
// ============================================================================

void applyObjectiveOracle(Qureg q) {
    // For each UE, apply rotation based on selected BS's rate
    for (int ue = 0; ue < NUM_UE; ue++) {
        int q0 = ue * 2;
        int q1 = ue * 2 + 1;
        int rate_ancilla = RATE0 + ue;
        
        // BS0: |00⟩ → flip FLIP_TEMP when q0=0 AND q1=0, then controlled rotate
        pauliX(q, q0); pauliX(q, q1);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        double theta_bs0 = (channel_rate[ue][0] / max_rate) * M_PI;
        controlledRotateY(q, FLIP_TEMP, rate_ancilla, theta_bs0);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);  // Uncompute
        pauliX(q, q0); pauliX(q, q1);
        
        // BS1: |01⟩ → flip FLIP_TEMP when q0=0 AND q1=1, then controlled rotate
        pauliX(q, q0);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        double theta_bs1 = (channel_rate[ue][1] / max_rate) * M_PI;
        controlledRotateY(q, FLIP_TEMP, rate_ancilla, theta_bs1);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        pauliX(q, q0);
        
        // BS2: |10⟩ → flip FLIP_TEMP when q0=1 AND q1=0, then controlled rotate
        pauliX(q, q1);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        double theta_bs2 = (channel_rate[ue][2] / max_rate) * M_PI;
        controlledRotateY(q, FLIP_TEMP, rate_ancilla, theta_bs2);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        pauliX(q, q1);
    }
    
}

// ============================================================================
// Objective Oracle Uncompute
// ============================================================================

void uncomputeObjective(Qureg q) {
    // Reverse order of UEs
    for (int ue = NUM_UE - 1; ue >= 0; ue--) {
        int q0 = ue * 2;
        int q1 = ue * 2 + 1;
        int rate_ancilla = RATE0 + ue;
        
        // BS2: |10⟩ (last in forward, first in reverse)
        pauliX(q, q1);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        double theta_bs2 = (channel_rate[ue][2] / max_rate) * M_PI;
        controlledRotateY(q, FLIP_TEMP, rate_ancilla, -theta_bs2);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        pauliX(q, q1);
        
        // BS1: |01⟩
        pauliX(q, q0);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        double theta_bs1 = (channel_rate[ue][1] / max_rate) * M_PI;
        controlledRotateY(q, FLIP_TEMP, rate_ancilla, -theta_bs1);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        pauliX(q, q0);
        
        // BS0: |00⟩
        pauliX(q, q0); pauliX(q, q1);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        double theta_bs0 = (channel_rate[ue][0] / max_rate) * M_PI;
        controlledRotateY(q, FLIP_TEMP, rate_ancilla, -theta_bs0);
        ccx_decomposed(q, q0, q1, FLIP_TEMP);
        pauliX(q, q0); pauliX(q, q1);
    }
}

// ============================================================================
// Diffusion
// ============================================================================

void applyDiffusion(Qureg q) {
    applyStatePreparationInverse(q);
    
    // Reflect about |0⟩ using multi-controlled gate with RATE ancillas
    // MCZ on data+rate qubits = H + (12-controlled X) + H
    for (int i = 0; i < NUM_DATA_QUBITS; i++) pauliX(q, i);
    
    hadamard(q, NUM_DATA_QUBITS - 1);
    
    // 8-controlled X gate: data qubits only
    int all_ctrls[8] = {0, 1, 2, 3, 4, 5, 6, 7};
    int target = NUM_DATA_QUBITS - 1;
    multiControlledMultiQubitNot(q, all_ctrls, NUM_DATA_QUBITS, &target, 1);
    
    hadamard(q, NUM_DATA_QUBITS - 1);
    
    for (int i = 0; i < NUM_DATA_QUBITS; i++) pauliX(q, i);
    
    applyStatePreparation(q);
}

// ============================================================================
// Main
// ============================================================================

int main() {
    printf("=======================================================\n");
    printf("   BS-UE Matching using QuEST (Real Gates)\n");
    printf("   %d BS, %d UE, max %d UE/BS\n", NUM_BS, NUM_UE, MAX_UE_PER_BS);
    printf("   %d total qubits (8 data + 15 ancilla)\n", NUM_TOTAL_QUBITS);
    printf("   Weighted Grover with 2-controlled rotation\n");
    printf("=======================================================\n\n");
    
    // Initialize
    init_channel();
    
    printf("Channel Matrix (Mbps):\n");
    printf("        BS0       BS1       BS2\n");
    for (int ue = 0; ue < NUM_UE; ue++) {
        printf("UE%d: ", ue);
        for (int bs = 0; bs < NUM_BS; bs++)
            printf("%8.2f  ", channel_rate[ue][bs]);
        printf("\n");
    }
    printf("Max rate: %.2f Mbps\n\n", max_rate);
    
    // Create quantum environment and register
    QuESTEnv env = createQuESTEnv();
    Qureg q = createQureg(NUM_TOTAL_QUBITS, env);
    
    reportQuregParams(q);
    
    // Run quantum algorithm
    printf("\nRunning Grover search...\n");
    
    initZeroState(q);
    applyStatePreparation(q);
    
    for (int iter = 0; iter < GROVER_ITERATIONS; iter++) {
        applyObjectiveOracle(q);      
        applyConstraintOracle(q);     
        uncomputeConstraint(q);      
        uncomputeObjective(q);        
        applyDiffusion(q);
    }
        
    // Analyze results
    printf("\n========== Results ==========\n\n");
    
    long long numDataStates = 1LL << NUM_DATA_QUBITS;
    double *dataProbs = (double*)calloc(numDataStates, sizeof(double));
    
    // Marginalize over ancilla
    for (long long i = 0; i < q.numAmpsTotal; i++) {
        long long dataState = i & ((1LL << NUM_DATA_QUBITS) - 1);
        dataProbs[dataState] += getProbAmp(q, i);
    }
    
    double validProb = 0, prohibitedProb = 0, violProb = 0;
    double optRate = 0;
    long long optState = 0;
    
    for (long long s = 0; s < numDataStates; s++) {
        if (hasProhibitedState(s)) prohibitedProb += dataProbs[s];
        else if (!satisfiesCapacity(s)) violProb += dataProbs[s];
        else {
            validProb += dataProbs[s];
            double rate = calculateObjective(s);
            if (rate > optRate) { optRate = rate; optState = s; }
        }
    }
    
    printf("Probability Distribution:\n");
    printf("  Valid:              %.4f (%.2f%%)\n", validProb, validProb*100);
    printf("  Prohibited |11⟩:    %.4f (%.2f%%)\n", prohibitedProb, prohibitedProb*100);
    printf("  Capacity violation: %.4f (%.2f%%)\n", violProb, violProb*100);
    
    printf("\nOptimal Assignment:\n");
    printf("  ");
    for (int ue = 0; ue < NUM_UE; ue++)
        printf("UE%d->BS%d ", ue, getBSFromState(optState, ue));
    printf("\n");
    printf("  Rate: %.2f Mbps\n", optRate);
    printf("  Probability: %.4f (%.2f%%)\n", dataProbs[optState], dataProbs[optState]*100);
    
    printf("\nTop 5 Valid States:\n");
    for (int rank = 0; rank < 5; rank++) {
        double maxP = -1;
        long long maxS = 0;
        for (long long s = 0; s < numDataStates; s++) {
            if (isValidAssignment(s) && dataProbs[s] > maxP) {
                maxP = dataProbs[s];
                maxS = s;
            }
        }
        if (maxP < 0) break;
        printf("  %d. ", rank+1);
        for (int ue = 0; ue < NUM_UE; ue++)
            printf("UE%d->BS%d ", ue, getBSFromState(maxS, ue));
        printf("| Rate=%.2f | P=%.4f\n", calculateObjective(maxS), maxP);
        dataProbs[maxS] = -1;
    }
    
    printf("\nTotal probability check: %.6f\n", calcTotalProb(q));
    
    // Save results to JSON file for visualization
    FILE *fp = fopen("quantum_results.json", "w");
    if (fp) {
        fprintf(fp, "{\n");
        fprintf(fp, "  \"num_bs\": %d,\n", NUM_BS);
        fprintf(fp, "  \"num_ue\": %d,\n", NUM_UE);
        fprintf(fp, "  \"area_size\": %.1f,\n", AREA_SIZE);
        
        // BS positions
        fprintf(fp, "  \"bs_positions\": [\n");
        for (int i = 0; i < NUM_BS; i++) {
            if (i > 0) fprintf(fp, ",\n");
            fprintf(fp, "    {\"x\": %.1f, \"y\": %.1f}", bs_positions[i].x, bs_positions[i].y);
        }
        fprintf(fp, "\n  ],\n");
        
        // UE positions
        fprintf(fp, "  \"ue_positions\": [\n");
        for (int i = 0; i < NUM_UE; i++) {
            if (i > 0) fprintf(fp, ",\n");
            fprintf(fp, "    {\"x\": %.1f, \"y\": %.1f}", ue_positions[i].x, ue_positions[i].y);
        }
        fprintf(fp, "\n  ],\n");
        
        fprintf(fp, "  \"probabilities\": {\n");
        fprintf(fp, "    \"valid\": %.6f,\n", validProb);
        fprintf(fp, "    \"prohibited\": %.6f,\n", prohibitedProb);
        fprintf(fp, "    \"violated\": %.6f\n", violProb);
        fprintf(fp, "  },\n");
        fprintf(fp, "  \"optimal_state\": %lld,\n", optState);
        fprintf(fp, "  \"optimal_rate\": %.2f,\n", optRate);
        fprintf(fp, "  \"optimal_assignment\": [");
        for (int ue = 0; ue < NUM_UE; ue++) {
            if (ue > 0) fprintf(fp, ", ");
            fprintf(fp, "%d", getBSFromState(optState, ue));
        }
        fprintf(fp, "],\n");
        fprintf(fp, "  \"optimal_probability\": %.6f,\n", dataProbs[optState]);
        fprintf(fp, "  \"top_5_states\": [\n");
        
        // Reset for top-5 calculation
        double tempProbs[256];
        for (long long s = 0; s < numDataStates; s++) tempProbs[s] = dataProbs[s];
        
        for (int rank = 0; rank < 5; rank++) {
            double maxP = -1;
            long long maxS = 0;
            for (long long s = 0; s < numDataStates; s++) {
                if (isValidAssignment(s) && tempProbs[s] > maxP) {
                    maxP = tempProbs[s];
                    maxS = s;
                }
            }
            if (maxP < 0) break;
            
            if (rank > 0) fprintf(fp, ",\n");
            fprintf(fp, "    {\n");
            fprintf(fp, "      \"rank\": %d,\n", rank+1);
            fprintf(fp, "      \"state\": %lld,\n", maxS);
            fprintf(fp, "      \"assignment\": [");
            for (int ue = 0; ue < NUM_UE; ue++) {
                if (ue > 0) fprintf(fp, ", ");
                fprintf(fp, "%d", getBSFromState(maxS, ue));
            }
            fprintf(fp, "],\n");
            fprintf(fp, "      \"rate\": %.2f,\n", calculateObjective(maxS));
            fprintf(fp, "      \"probability\": %.6f\n", maxP);
            fprintf(fp, "    }");
            tempProbs[maxS] = -1;
        }
        
        fprintf(fp, "\n  ],\n");
        
        // Recalculate all probabilities before saving (since top-5 calculation modified dataProbs)
        // Marginalize over ancilla again
        double *finalProbs = (double*)calloc(numDataStates, sizeof(double));
        for (long long i = 0; i < q.numAmpsTotal; i++) {
            long long dataState = i & ((1LL << NUM_DATA_QUBITS) - 1);
            finalProbs[dataState] += getProbAmp(q, i);
        }
        
        // Add all state probabilities for accurate visualization
        fprintf(fp, "  \"all_state_probs\": [\n");
        for (long long s = 0; s < numDataStates; s++) {
            if (s > 0) fprintf(fp, ",\n");
            fprintf(fp, "    {\"state\": %lld, \"prob\": %.8f}", s, finalProbs[s]);
        }
        fprintf(fp, "\n  ]\n");
        
        free(finalProbs);
        
        fprintf(fp, "}\n");
        fclose(fp);
        printf("\nResults saved to quantum_results.json\n");
    }
    
    free(dataProbs);
    destroyQureg(q, env);
    destroyQuESTEnv(env);
    
    return 0;
}
