# Fig. 5 v50: real weighted-AA portfolios, lambda = 5.5

## What changed from v49

- The repeated re-execution arrows span their full round intervals with
  no extra padding, matching the first labeled arrow.

## What changed from v48

- Arrow labels and legend text step down from 22 to 18, matching the
  tick label size.

## What changed from v47

- The two arrow labels and the legend use the axis-label size 22. The
  coordination label is left-anchored at L = 1 so the larger text stays
  inside the axes.

## What changed from v46

- The two arrow families use slightly different yellows. Coordination is
  bright amber and re-execution is darker mustard.
- Unlabeled re-execution arrows repeat for every round from 2 to 9. The
  legend covers the tail ones past round 6.

## What changed from v45

- The long arrow reads "xApp re-execution", removing the unit duplication
  with the axis and drawing the III-B contrast between stored-output
  coordination and re-execution directly.
- Start markers at L = 1 are added for all three methods. The gray and
  green markers overlap there because both start from the same GBLS safe
  core.

## What changed from v44

- The x-axis reads "Near-RT negotiation rounds, L". One unit is defined
  in the caption as one inter-xApp negotiation exchange through the
  near-RT control loop, matching the negotiation-round unit established
  in the automated negotiation literature.
- The second arrow reads "One negotiation round" and both arrows and
  labels are amber.

## What changed from v43

- The single-local bars and their label are removed. The within-2% rates
  (96.5% quantum, 91.4% GBLS) stay in this file and move to the body
  text, which now carries the local-quality claim alone.
- The horizontal range is tightened to start just left of L = 1 and the
  vertical range now ends at 101.5.

## What changed from v42

- The gray legend entry reads "O-RAN negotiation-based ConMit", anchored
  to the E2SM-level negotiation solution in the WG3 ConMit TR. Internal
  data keys keep the original method name.
- The y-axis reads "Utility (% of optimum)" and the x-axis reads
  "Near-RT coordination rounds, L".
- The bar label reads "Prob. of gap <= 2% from optimum".
- The coordination arrow and its label are amber and read
  "Coordination (delta_c)".

## What changed from v41

- Bar label reads "Prob. within 2% of optimum".
- The y-axis label reads "Utility (% of exact optimum)" and the x-axis
  label reads "Near-RT decision rounds, L".
- The gap and crossover annotations and the L=2 guide line are removed.
  The corresponding numbers stay in this file for the body text.
- Two horizontal arrows mark the coordination interval delta_c and one
  re-optimization rerun.
- Markers appear only at completed coordination or re-optimization
  outputs, so the gray L=1 marker is gone.
- Content is otherwise identical to v41. The red hybrid curve and the red
  bar come from real weighted-amplitude-amplification outputs (ideal
  measurement statistics, Qiskit Statevector, encoding scale lambda = 5.5,
  1024 shots, top-16 retained per domain). The surrogate is not used
  anywhere. Gray and green results are unchanged because no classical
  method consumes any Q-xApp output.

## Validation gate

Replaying the coordination path on the stored surrogate portfolios
reproduces the stored v34 hybrid endpoints for all 100
seeds. Max abs diff safe core 0.00e+00,
final 0.00e+00.

## Displayed values

- Red bar (single-local within 2%): 96.5%
- Gray bar: 91.4%
- Hybrid safe core at L=1: 69.4510% (CI 68.08 to 70.74)
- Hybrid after coordination: 97.5884% (CI 97.31 to 97.85)
- Gap at L=2 versus GBLS: +9.97 pp (CI +9.37 to +10.57)
- First integer stage where GBLS exceeds Hybrid: L=8
- Exact top-1 rate: 70.4%
- Optimum retained in top-16: 99.8%
- First-peak rounds min/median/max: [41, 15447, 18561907]
- Mean initial conflicting boundary variables (real input): 7.70

## Caption draft

Conflict-free network utility over near-RT negotiation rounds for ten
local optimization domains. Quantum outputs are drawn
from the ideal measurement distribution of the utility-weighted amplitude
amplification circuit with encoding scale lambda set to 5.5. Curves show conflict-free
network-wide utility normalized by a stored offline centralized exact
benchmark. The red hybrid and green priority methods complete stored-output
coordination atomically at the symbolic time 1 + delta_c. For the
negotiation-based ConMit, completed rerun r is shown at L = 1 + r. One unit of L
corresponds to one inter-xApp negotiation exchange through the near-RT control loop and the interval up to L = 2 is
widened for readability. Markers indicate the initial safe core and each completed
coordination or re-optimization output. Horizontal arrows mark the stored-output
coordination interval and the xApp re-execution interval, repeated
without labels for the following rounds. No classical method
consumes any Q-xApp output.
The vertical axis begins at 50%.
