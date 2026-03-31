/**
 * QuEST-compatible Implementation
 * All gates properly implemented at the matrix level
 */

#include "QuEST.h"

// ============================================================================
// Environment
// ============================================================================

QuESTEnv createQuESTEnv(void) {
    QuESTEnv env = {0, 1};
    return env;
}

void destroyQuESTEnv(QuESTEnv env) {
    (void)env;
}

// ============================================================================
// Qureg Management
// ============================================================================

Qureg createQureg(int numQubits, QuESTEnv env) {
    (void)env;
    Qureg qureg;
    qureg.numQubitsRepresented = numQubits;
    qureg.numAmpsTotal = 1LL << numQubits;
    qureg.stateVec = (Complex*)calloc(qureg.numAmpsTotal, sizeof(Complex));
    if (!qureg.stateVec) {
        fprintf(stderr, "Failed to allocate %lld amplitudes\n", qureg.numAmpsTotal);
        exit(1);
    }
    return qureg;
}

void destroyQureg(Qureg qureg, QuESTEnv env) {
    (void)env;
    free(qureg.stateVec);
}

void initZeroState(Qureg qureg) {
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        qureg.stateVec[i].real = 0.0;
        qureg.stateVec[i].imag = 0.0;
    }
    qureg.stateVec[0].real = 1.0;
}

void initPlusState(Qureg qureg) {
    qreal norm = 1.0 / sqrt((qreal)qureg.numAmpsTotal);
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        qureg.stateVec[i].real = norm;
        qureg.stateVec[i].imag = 0.0;
    }
}

// ============================================================================
// Helper: Apply 2x2 unitary to qubit
// ============================================================================

static void applyMatrix2(Qureg qureg, int target, ComplexMatrix2 u) {
    long long mask = 1LL << target;
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        if ((i & mask) == 0) {
            long long j = i | mask;
            
            Complex a0 = qureg.stateVec[i];
            Complex a1 = qureg.stateVec[j];
            
            // new_a0 = u[0][0]*a0 + u[0][1]*a1
            qureg.stateVec[i].real = u.real[0][0]*a0.real - u.imag[0][0]*a0.imag
                                   + u.real[0][1]*a1.real - u.imag[0][1]*a1.imag;
            qureg.stateVec[i].imag = u.real[0][0]*a0.imag + u.imag[0][0]*a0.real
                                   + u.real[0][1]*a1.imag + u.imag[0][1]*a1.real;
            
            // new_a1 = u[1][0]*a0 + u[1][1]*a1
            qureg.stateVec[j].real = u.real[1][0]*a0.real - u.imag[1][0]*a0.imag
                                   + u.real[1][1]*a1.real - u.imag[1][1]*a1.imag;
            qureg.stateVec[j].imag = u.real[1][0]*a0.imag + u.imag[1][0]*a0.real
                                   + u.real[1][1]*a1.imag + u.imag[1][1]*a1.real;
        }
    }
}

static void applyControlledMatrix2(Qureg qureg, int ctrl, int target, ComplexMatrix2 u) {
    long long ctrlMask = 1LL << ctrl;
    long long targMask = 1LL << target;
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        if ((i & ctrlMask) && ((i & targMask) == 0)) {
            long long j = i | targMask;
            
            Complex a0 = qureg.stateVec[i];
            Complex a1 = qureg.stateVec[j];
            
            qureg.stateVec[i].real = u.real[0][0]*a0.real - u.imag[0][0]*a0.imag
                                   + u.real[0][1]*a1.real - u.imag[0][1]*a1.imag;
            qureg.stateVec[i].imag = u.real[0][0]*a0.imag + u.imag[0][0]*a0.real
                                   + u.real[0][1]*a1.imag + u.imag[0][1]*a1.real;
            
            qureg.stateVec[j].real = u.real[1][0]*a0.real - u.imag[1][0]*a0.imag
                                   + u.real[1][1]*a1.real - u.imag[1][1]*a1.imag;
            qureg.stateVec[j].imag = u.real[1][0]*a0.imag + u.imag[1][0]*a0.real
                                   + u.real[1][1]*a1.imag + u.imag[1][1]*a1.real;
        }
    }
}

// ============================================================================
// Single Qubit Gates
// ============================================================================

void hadamard(Qureg qureg, int target) {
    qreal s = 1.0 / sqrt(2.0);
    ComplexMatrix2 u = {{{s, s}, {s, -s}}, {{0,0},{0,0}}};
    applyMatrix2(qureg, target, u);
}

void pauliX(Qureg qureg, int target) {
    ComplexMatrix2 u = {{{0, 1}, {1, 0}}, {{0,0},{0,0}}};
    applyMatrix2(qureg, target, u);
}

void pauliY(Qureg qureg, int target) {
    ComplexMatrix2 u = {{{0, 0}, {0, 0}}, {{0, -1}, {1, 0}}};
    applyMatrix2(qureg, target, u);
}

void pauliZ(Qureg qureg, int target) {
    ComplexMatrix2 u = {{{1, 0}, {0, -1}}, {{0,0},{0,0}}};
    applyMatrix2(qureg, target, u);
}

void sGate(Qureg qureg, int target) {
    // S = diag(1, i)
    ComplexMatrix2 u = {{{1, 0}, {0, 0}}, {{0, 0}, {0, 1}}};
    applyMatrix2(qureg, target, u);
}

void tGate(Qureg qureg, int target) {
    // T = diag(1, e^{i*pi/4})
    qreal c = cos(M_PI/4.0);
    qreal s = sin(M_PI/4.0);
    ComplexMatrix2 u = {{{1, 0}, {0, c}}, {{0, 0}, {0, s}}};
    applyMatrix2(qureg, target, u);
}

void rotateX(Qureg qureg, int target, qreal angle) {
    qreal c = cos(angle/2.0);
    qreal s = sin(angle/2.0);
    ComplexMatrix2 u = {{{c, 0}, {0, c}}, {{0, -s}, {-s, 0}}};
    applyMatrix2(qureg, target, u);
}

void rotateY(Qureg qureg, int target, qreal angle) {
    qreal c = cos(angle/2.0);
    qreal s = sin(angle/2.0);
    ComplexMatrix2 u = {{{c, -s}, {s, c}}, {{0,0},{0,0}}};
    applyMatrix2(qureg, target, u);
}

void rotateZ(Qureg qureg, int target, qreal angle) {
    qreal c = cos(angle/2.0);
    qreal s = sin(angle/2.0);
    ComplexMatrix2 u = {{{c, 0}, {0, c}}, {{-s, 0}, {0, s}}};
    applyMatrix2(qureg, target, u);
}

void phaseShift(Qureg qureg, int target, qreal angle) {
    // P(θ) = diag(1, e^{iθ})
    qreal c = cos(angle);
    qreal s = sin(angle);
    ComplexMatrix2 u = {{{1, 0}, {0, c}}, {{0, 0}, {0, s}}};
    applyMatrix2(qureg, target, u);
}

// ============================================================================
// Two Qubit Gates
// ============================================================================

void controlledNot(Qureg qureg, int ctrl, int target) {
    ComplexMatrix2 u = {{{0, 1}, {1, 0}}, {{0,0},{0,0}}};
    applyControlledMatrix2(qureg, ctrl, target, u);
}

void controlledPauliY(Qureg qureg, int ctrl, int target) {
    ComplexMatrix2 u = {{{0, 0}, {0, 0}}, {{0, -1}, {1, 0}}};
    applyControlledMatrix2(qureg, ctrl, target, u);
}

void controlledPhaseShift(Qureg qureg, int ctrl, int target, qreal angle) {
    long long ctrlMask = 1LL << ctrl;
    long long targMask = 1LL << target;
    qreal c = cos(angle);
    qreal s = sin(angle);
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        if ((i & ctrlMask) && (i & targMask)) {
            qreal re = qureg.stateVec[i].real;
            qreal im = qureg.stateVec[i].imag;
            qureg.stateVec[i].real = c*re - s*im;
            qureg.stateVec[i].imag = s*re + c*im;
        }
    }
}

void controlledRotateY(Qureg qureg, int ctrl, int target, qreal angle) {
    qreal c = cos(angle/2.0);
    qreal s = sin(angle/2.0);
    ComplexMatrix2 u = {{{c, -s}, {s, c}}, {{0,0},{0,0}}};
    applyControlledMatrix2(qureg, ctrl, target, u);
}

void controlledRotateZ(Qureg qureg, int ctrl, int target, qreal angle) {
    qreal c = cos(angle/2.0);
    qreal s = sin(angle/2.0);
    ComplexMatrix2 u = {{{c, 0}, {0, c}}, {{-s, 0}, {0, s}}};
    applyControlledMatrix2(qureg, ctrl, target, u);
}

void swapGate(Qureg qureg, int q1, int q2) {
    long long m1 = 1LL << q1;
    long long m2 = 1LL << q2;
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        int b1 = (i & m1) ? 1 : 0;
        int b2 = (i & m2) ? 1 : 0;
        if (b1 != b2 && b1 < b2) {  // Only swap once per pair
            long long j = (i ^ m1) ^ m2;  // Swap bits
            Complex tmp = qureg.stateVec[i];
            qureg.stateVec[i] = qureg.stateVec[j];
            qureg.stateVec[j] = tmp;
        }
    }
}

// ============================================================================
// Toffoli (CCX) - Properly Decomposed into 1 and 2 qubit gates
// Using standard decomposition: 6 CNOT + 7 T/Tdg + 2 H
// ============================================================================

static void tDagger(Qureg qureg, int target) {
    qreal c = cos(-M_PI/4.0);
    qreal s = sin(-M_PI/4.0);
    ComplexMatrix2 u = {{{1, 0}, {0, c}}, {{0, 0}, {0, s}}};
    applyMatrix2(qureg, target, u);
}

void ccx_decomposed(Qureg qureg, int ctrl1, int ctrl2, int target) {
    // Standard Toffoli decomposition
    hadamard(qureg, target);
    controlledNot(qureg, ctrl2, target);
    tDagger(qureg, target);
    controlledNot(qureg, ctrl1, target);
    tGate(qureg, target);
    controlledNot(qureg, ctrl2, target);
    tDagger(qureg, target);
    controlledNot(qureg, ctrl1, target);
    tGate(qureg, ctrl2);
    tGate(qureg, target);
    hadamard(qureg, target);
    controlledNot(qureg, ctrl1, ctrl2);
    tGate(qureg, ctrl1);
    tDagger(qureg, ctrl2);
    controlledNot(qureg, ctrl1, ctrl2);
}

// ============================================================================
// Multi-Controlled Gates
// ============================================================================

void multiControlledPhaseFlip(Qureg qureg, int *ctrls, int numCtrls) {
    long long ctrlMask = 0;
    for (int i = 0; i < numCtrls; i++) {
        ctrlMask |= (1LL << ctrls[i]);
    }
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        if ((i & ctrlMask) == ctrlMask) {
            qureg.stateVec[i].real = -qureg.stateVec[i].real;
            qureg.stateVec[i].imag = -qureg.stateVec[i].imag;
        }
    }
}

void multiControlledMultiQubitNot(Qureg qureg, int *ctrls, int numCtrls, int *targets, int numTargets) {
    long long ctrlMask = 0;
    for (int i = 0; i < numCtrls; i++) {
        ctrlMask |= (1LL << ctrls[i]);
    }
    
    long long targMask = 0;
    for (int i = 0; i < numTargets; i++) {
        targMask |= (1LL << targets[i]);
    }
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        if ((i & ctrlMask) == ctrlMask) {
            long long j = i ^ targMask;
            if (i < j) {
                Complex tmp = qureg.stateVec[i];
                qureg.stateVec[i] = qureg.stateVec[j];
                qureg.stateVec[j] = tmp;
            }
        }
    }
}

// ============================================================================
// Measurement & State Inspection
// ============================================================================

qreal getProbAmp(Qureg qureg, long long index) {
    qreal re = qureg.stateVec[index].real;
    qreal im = qureg.stateVec[index].imag;
    return re*re + im*im;
}

qreal calcProbOfOutcome(Qureg qureg, int qubit, int outcome) {
    long long mask = 1LL << qubit;
    qreal prob = 0.0;
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        int bit = (i & mask) ? 1 : 0;
        if (bit == outcome) {
            prob += getProbAmp(qureg, i);
        }
    }
    return prob;
}

int measure(Qureg qureg, int qubit) {
    qreal prob0 = calcProbOfOutcome(qureg, qubit, 0);
    qreal r = (qreal)rand() / RAND_MAX;
    int outcome = (r < prob0) ? 0 : 1;
    
    // Collapse state
    long long mask = 1LL << qubit;
    qreal norm = (outcome == 0) ? prob0 : (1.0 - prob0);
    norm = 1.0 / sqrt(norm);
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        int bit = (i & mask) ? 1 : 0;
        if (bit == outcome) {
            qureg.stateVec[i].real *= norm;
            qureg.stateVec[i].imag *= norm;
        } else {
            qureg.stateVec[i].real = 0;
            qureg.stateVec[i].imag = 0;
        }
    }
    return outcome;
}

int measureWithStats(Qureg qureg, int qubit, qreal *outcomeProb) {
    qreal prob0 = calcProbOfOutcome(qureg, qubit, 0);
    qreal r = (qreal)rand() / RAND_MAX;
    int outcome = (r < prob0) ? 0 : 1;
    *outcomeProb = (outcome == 0) ? prob0 : (1.0 - prob0);
    
    // Collapse
    long long mask = 1LL << qubit;
    qreal norm = 1.0 / sqrt(*outcomeProb);
    
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        int bit = (i & mask) ? 1 : 0;
        if (bit == outcome) {
            qureg.stateVec[i].real *= norm;
            qureg.stateVec[i].imag *= norm;
        } else {
            qureg.stateVec[i].real = 0;
            qureg.stateVec[i].imag = 0;
        }
    }
    return outcome;
}

Complex getAmp(Qureg qureg, long long index) {
    return qureg.stateVec[index];
}

void getStateVectorAmp(Qureg qureg, long long index, qreal *reOut, qreal *imOut) {
    *reOut = qureg.stateVec[index].real;
    *imOut = qureg.stateVec[index].imag;
}

qreal calcTotalProb(Qureg qureg) {
    qreal total = 0.0;
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        total += getProbAmp(qureg, i);
    }
    return total;
}

// ============================================================================
// Reporting
// ============================================================================

void reportQuregParams(Qureg qureg) {
    printf("Qureg: %d qubits, %lld amplitudes\n", 
           qureg.numQubitsRepresented, qureg.numAmpsTotal);
}

void reportState(Qureg qureg) {
    printf("State vector (non-zero amplitudes):\n");
    for (long long i = 0; i < qureg.numAmpsTotal; i++) {
        qreal prob = getProbAmp(qureg, i);
        if (prob > 1e-10) {
            printf("  |%lld> : %.6f + %.6fi  (prob=%.6f)\n",
                   i, qureg.stateVec[i].real, qureg.stateVec[i].imag, prob);
        }
    }
}
