/**
 * QuEST-compatible API (Minimal Implementation)
 * Real gate operations without cheating
 */

#ifndef QUEST_H
#define QUEST_H

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>

#define qreal double
#define M_PI 3.14159265358979323846

typedef struct {
    qreal real;
    qreal imag;
} Complex;

typedef struct {
    int numQubitsRepresented;
    long long numAmpsTotal;
    Complex *stateVec;
} Qureg;

typedef struct {
    // Environment (placeholder for multi-node)
    int rank;
    int numRanks;
} QuESTEnv;

typedef struct {
    qreal real[2][2];
    qreal imag[2][2];
} ComplexMatrix2;

typedef struct {
    qreal real[4][4];
    qreal imag[4][4];
} ComplexMatrix4;

// Environment
QuESTEnv createQuESTEnv(void);
void destroyQuESTEnv(QuESTEnv env);

// Qureg management
Qureg createQureg(int numQubits, QuESTEnv env);
void destroyQureg(Qureg qureg, QuESTEnv env);
void initZeroState(Qureg qureg);
void initPlusState(Qureg qureg);

// Single qubit gates
void hadamard(Qureg qureg, int targetQubit);
void pauliX(Qureg qureg, int targetQubit);
void pauliY(Qureg qureg, int targetQubit);
void pauliZ(Qureg qureg, int targetQubit);
void sGate(Qureg qureg, int targetQubit);
void tGate(Qureg qureg, int targetQubit);
void rotateX(Qureg qureg, int targetQubit, qreal angle);
void rotateY(Qureg qureg, int targetQubit, qreal angle);
void rotateZ(Qureg qureg, int targetQubit, qreal angle);
void phaseShift(Qureg qureg, int targetQubit, qreal angle);

// Two qubit gates
void controlledNot(Qureg qureg, int controlQubit, int targetQubit);
void controlledPauliY(Qureg qureg, int controlQubit, int targetQubit);
void controlledPhaseShift(Qureg qureg, int controlQubit, int targetQubit, qreal angle);
void controlledRotateY(Qureg qureg, int controlQubit, int targetQubit, qreal angle);
void controlledRotateZ(Qureg qureg, int controlQubit, int targetQubit, qreal angle);
void sqrtSwapGate(Qureg qureg, int qubit1, int qubit2);
void swapGate(Qureg qureg, int qubit1, int qubit2);

// Multi-controlled gates
void multiControlledPhaseFlip(Qureg qureg, int *controlQubits, int numControlQubits);
void multiControlledMultiQubitNot(Qureg qureg, int *controls, int numControls, int *targets, int numTargets);

// Toffoli (CCX) - properly decomposed
void ccx_decomposed(Qureg qureg, int ctrl1, int ctrl2, int target);

// Measurement
qreal getProbAmp(Qureg qureg, long long index);
qreal calcProbOfOutcome(Qureg qureg, int measureQubit, int outcome);
int measure(Qureg qureg, int measureQubit);
int measureWithStats(Qureg qureg, int measureQubit, qreal *outcomeProb);

// State inspection
void getStateVectorAmp(Qureg qureg, long long index, qreal *reOut, qreal *imOut);
Complex getAmp(Qureg qureg, long long index);
qreal calcTotalProb(Qureg qureg);

// Reporting
void reportState(Qureg qureg);
void reportQuregParams(Qureg qureg);

#endif
