/*
 * Q-xApp Unified Controller
 * Supports Traffic Steering, Network Energy Saving, and QoS-based Resource Allocation modes
 * Mode is read from xapp_mode.txt each round
 *
 * Architecture follows Fig. 2 of the Q-xApp paper:
 *   E2 Measurements -> Use-Case Encoder -> Quantum Assignment Algorithm -> Output Interpreter -> E2 Control
 */

#include "qxapp_common.h"

static int a1_max_ue_per_cell = 2;

/* Config files live in qx_data_dir() (env QXAPP_DATA_DIR, default = historical
 * WSL path; see qxapp_common.h). Only the file names are fixed here. */
#define MODE_FILE_NAME "xapp_mode.txt"
#define SLEEP_CONFIG_FILE_NAME "xapp_sleep_config.txt"
#define A1_POLICY_FILE_NAME "xapp_a1_policy.txt"
#define QOS_CONFIG_FILE_NAME "xapp_qos_config.txt"

/* == Quantum TS assignment (dqna_ts.py, Stage-0 validated) ==================
 * Quantum is the default assignment engine (Q-xApp). The quantum config file
 * first line "0"/"off" forces the legacy greedy placeholder (debug / historic
 * Fig.4 reproduction); missing file = quantum ON. Paths can be overridden via
 * env QXAPP_PY / QXAPP_TS_SCRIPT. TS uses the canonical 4x3 full-state
 * weighted-AA solver; NES uses the 4x2 five-qubit weighted-AA solver.
 * Classical matchers remain explicit failure fallbacks. */
#define QUANTUM_CONFIG_FILE_NAME "xapp_quantum.txt"
#define QXAPP_PY_DEFAULT        "/root/qxapp-venv/bin/python"
#define QXAPP_TS_SCRIPT_DEFAULT "/root/flexric/examples/xApp/c/ctrl/dqna_ts.py"
#define QXAPP_TS_TIMEOUT_S 120

/* == QoS-based Resource Allocation ========================================= */
#define NUM_DRB 4

typedef struct {
    int drb_id;
    int fiveqi;
    double weight;   /* scheduler weight sent to ns-3 */
} drb_profile_t;

/* Global DRB pool (drb_id, 5QI, scheduler weight) */
static drb_profile_t drb_pool[NUM_DRB] = {
    {1, 2, 4.0},
    {2, 4, 3.0},
    {3, 7, 2.0},
    {4, 9, 1.0},
};

/* Cell-specific DRB availability: every O-RU offers all 4 DRBs (the earlier
 * 3-of-4 mask never bound for the Fig.4 5QI set, so assignments are
 * unchanged; full availability matches the 2 UE x 4 DRB QoS matching). */
static int cell_drb_avail[NUM_CELL][NUM_DRB] = {
    {1, 1, 1, 1},  /* O-RU 1 */
    {1, 1, 1, 1},  /* O-RU 2 */
    {1, 1, 1, 1},  /* O-RU 3 */
};

static int ue_drb_assignment[NUM_UE]; /* each UE assigned DRB index (0-3), -1=unassigned */
static int ue_fiveqi[NUM_UE] = {2, 4, 7, 9}; /* per-UE 5QI requirement (from GUI) */

/* read QoS config: per-UE 5QI values from file */
static void read_qos_config(void)
{
  char cfg_path[512];
  FILE *fp = fopen(qx_data_path(cfg_path, sizeof(cfg_path), QOS_CONFIG_FILE_NAME), "r");
  if (!fp) {
    printf("[Q-xApp QoS-RA] Config file not found, using defaults (2,4,7,9)\n");
    ue_fiveqi[0] = 2; ue_fiveqi[1] = 4; ue_fiveqi[2] = 7; ue_fiveqi[3] = 9;
    return;
  }
  char buf[256];
  if (fgets(buf, sizeof(buf), fp)) {
    int idx = 0;
    char *p = buf;
    while (*p && idx < NUM_UE) {
      ue_fiveqi[idx++] = atoi(p);
      char *comma = strchr(p, ',');
      if (!comma) break;
      p = comma + 1;
    }
  }
  fclose(fp);
  printf("[Q-xApp QoS-RA] Loaded 5QI:");
  for (int u = 0; u < NUM_UE; u++) printf(" UE%d=5QI%d", u, ue_fiveqi[u]);
  printf("\n");
}

/* Is the assignment problem feasible? NUM_UE UEs fit in `awake_cells` cells
 * under the per-cell cap iff NUM_UE <= awake_cells*cap. For TS/QoS every cell
 * is awake (awake_cells = NUM_CELL); for NES the forced-sleep cells are not
 * available, so awake_cells = NUM_CELL - n_forced_sleep (Codex §18). cap=1
 * (any mode) and cap=2 + 2 forced-sleep cells are both infeasible. */
static int assignment_feasible_awake(int awake_cells)
{
  return awake_cells >= 1 && NUM_UE <= awake_cells * a1_max_ue_per_cell;
}

/* Common post-hoc validator (Codex re-review blocker 5): every assignment the
 * controller acts on — greedy, quantum TS, quantum 4x2 NES, frozen INIT-TS —
 * must be checked against the per-cell cap here, so a solver result that
 * exceeds the cap is caught in the controller (the quantum math source is not
 * modified). Returns 1 if within cap. `active_only` counts only awake cells
 * for NES (a sleeping cell must carry 0 UEs). */
static int assignment_within_cap(const int assignment[NUM_UE],
                                 const int sleep_cells[], int n_sleep)
{
  int load[NUM_CELL] = {0};
  for (int u = 0; u < NUM_UE; u++) {
    int c = assignment[u];
    if (c < 0 || c >= NUM_CELL) return 0;      /* unassigned/out of range */
    load[c]++;
  }
  for (int c = 0; c < NUM_CELL; c++) {
    if (load[c] > a1_max_ue_per_cell) return 0;
    for (int s = 0; s < n_sleep; s++)
      if (CELL_IDS[c] == sleep_cells[s] && load[c] > 0) return 0; /* on sleep */
  }
  return 1;
}

/* Stage 2 outcome (Codex re-review blocker 4): distinguish "not ready / stale"
 * (transient -> pending) from "mathematically infeasible" (error). */
#define STAGE2_OK        0
#define STAGE2_INFEASIBLE (-1)
#define STAGE2_NOT_READY  (-2)

/* A measurement is usable only if this scan produced a row for the UE AND
 * that row is strictly newer than the anchor captured at the recompute /
 * mode-entry event. read_sinr_from_csv re-reads the last CSV row every scan,
 * so meas_valid alone can be a stale repeat; requiring meas_ts > anchor
 * prevents freezing/converging/controlling on stale data (Codex blocker 5). */
static int meas_fresh_since(int u, uint64_t anchor)
{
  return meas_valid[u] && meas_ts[u] > anchor;
}

/* greedy matching: assign UEs to cells maximising total rate.
 * Returns 0 on success, -1 if the cap cannot be honoured. Unlike the old
 * version it NEVER force-assigns an over-cap UE to cell 0 (R2.4): an
 * infeasible input is reported, not silently violated. */
static int greedy_match(int assignment[NUM_UE])
{
  int cell_load[NUM_CELL] = {0};

  typedef struct { double rate; int ue; int cell; } tuple_t;
  tuple_t tuples[NUM_UE * NUM_CELL];
  int nt = 0;
  for (int u = 0; u < NUM_UE; u++)
    for (int c = 0; c < NUM_CELL; c++) {
      tuples[nt].rate = sinr_matrix[u][c];
      tuples[nt].ue   = u;
      tuples[nt].cell = c;
      nt++;
    }

  for (int i = 0; i < nt - 1; i++)
    for (int j = i + 1; j < nt; j++)
      if (tuples[j].rate > tuples[i].rate) {
        tuple_t tmp = tuples[i]; tuples[i] = tuples[j]; tuples[j] = tmp;
      }

  int assigned[NUM_UE];
  memset(assigned, 0, sizeof(assigned));
  for (int u = 0; u < NUM_UE; u++)
    assignment[u] = -1;

  for (int i = 0; i < nt; i++) {
    int u = tuples[i].ue;
    int c = tuples[i].cell;
    if (assigned[u]) continue;
    if (cell_load[c] >= a1_max_ue_per_cell) continue;
    assignment[u] = c;
    assigned[u] = 1;
    cell_load[c]++;
  }

  for (int u = 0; u < NUM_UE; u++) {
    if (assignment[u] >= 0) continue;
    int best = -1;
    for (int c = 0; c < NUM_CELL; c++) {
      if (cell_load[c] >= a1_max_ue_per_cell) continue;
      if (best < 0 || cell_load[c] < cell_load[best]) best = c;
    }
    if (best < 0) {
      /* every cell is at cap: infeasible, do not violate the cap */
      fprintf(stderr, "[Q-xApp TS] greedy_match: UE %d has no feasible cell "
                      "under cap %d (assignment infeasible)\n",
              u, a1_max_ue_per_cell);
      return -1;
    }
    assignment[u] = best;
    cell_load[best]++;
  }
  return 0;
}

/* == Quantum TS matching implementation ==================================== */
static int  quantum_ts_enabled = 0;
static int  quantum_env_logged = 0;
static int  ts_quantum_state = 0;   /* last TS decision: 0=greedy 1=quantum 2=fallback */
static long quantum_ok_count = 0;
static long quantum_fallback_count = 0;

static const char *qx_py(void)
{
  const char *p = getenv("QXAPP_PY");
  return p ? p : QXAPP_PY_DEFAULT;
}

static const char *qx_ts_script(void)
{
  const char *p = getenv("QXAPP_TS_SCRIPT");
  return p ? p : QXAPP_TS_SCRIPT_DEFAULT;
}

/* Quantum is the Q-xApp assignment engine and therefore the default; greedy
 * is the placeholder from before the circuit existed, kept only as the
 * automatic fallback and as a debug path (write "0"/"off" to the config file
 * to force it). On first enable, log solver provenance. */
static void read_quantum_config(void)
{
  int enabled = 1;
  char cfg_path[512];
  FILE *fp = fopen(qx_data_path(cfg_path, sizeof(cfg_path), QUANTUM_CONFIG_FILE_NAME), "r");
  if (fp) {
    char buf[64] = {0};
    if (fgets(buf, sizeof(buf), fp))
      enabled = !(buf[0] == '0' || strncmp(buf, "off", 3) == 0);
    fclose(fp);
  }
  if (enabled && !quantum_env_logged) {
    quantum_env_logged = 1;
    printf("[Q-xApp TS] quantum enabled: py=%s script=%s timeout=%ds "
           "feas_iter=1 qual_iter=1 qual_lambda=4.0 max_per_cell=%d\n",
           qx_py(), qx_ts_script(), QXAPP_TS_TIMEOUT_S, a1_max_ue_per_cell);
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "sha256sum '%s' 2>/dev/null; '%s' -c 'import qiskit; print(\"qiskit\", qiskit.__version__)' 2>/dev/null",
             qx_ts_script(), qx_py());
    FILE *pp = popen(cmd, "r");
    if (pp) {
      char line[512];
      while (fgets(line, sizeof(line), pp))
        printf("[Q-xApp TS] provenance: %s", line);
      pclose(pp);
    }
  }
  if (enabled != quantum_ts_enabled)
    printf("[Q-xApp TS] quantum path %s\n", enabled ? "ON" : "OFF (greedy placeholder)");
  quantum_ts_enabled = enabled;
}

/* Run dqna_ts.py on the current sinr_matrix via unique temp files.
 * Returns 0 and fills assignment (+ q_score/q_elapsed_ms if non-NULL) on
 * success; -1 on any failure (caller falls back to greedy_match). The
 * distinction "solver no candidate" vs solver error is logged (Codex 12차). */
static int quantum_ts_match(int assignment[NUM_UE], double *q_score, int *q_elapsed_ms)
{
  char fin[]  = "/tmp/qxapp_ts_in_XXXXXX";
  char fout[] = "/tmp/qxapp_ts_out_XXXXXX";
  char ferr[] = "/tmp/qxapp_ts_err_XXXXXX";
  int rc_val = -1;
  int fdi = mkstemp(fin), fdo = mkstemp(fout), fde = mkstemp(ferr);
  if (fdi < 0 || fdo < 0 || fde < 0) {
    fprintf(stderr, "[Q-xApp TS] mkstemp failed\n");
    goto cleanup;
  }

  /* 1. SINR matrix -> JSON */
  {
    FILE *fi = fdopen(fdi, "w");
    if (!fi) goto cleanup;
    fdi = -1; /* owned by stream now */
    fprintf(fi, "{\"sinr\":[");
    for (int u = 0; u < NUM_UE; u++) {
      fprintf(fi, "%s[", u > 0 ? "," : "");
      for (int c = 0; c < NUM_CELL; c++)
        fprintf(fi, "%s%.6f", c > 0 ? "," : "", sinr_matrix[u][c]);
      fprintf(fi, "]");
    }
    fprintf(fi, "]}\n");
    fclose(fi);
  }

  /* 2. Invoke the canonical v5 weighted-AA solver.  Passing the historical
   * --feas-iter/--qual-iter form selects legacy semantics and is deliberately
   * rejected by dqna_ts.py's v5 contract.  Keep only the live A1 cap here;
   * the frozen v5 quality-first defaults remain owned by the solver. */
  {
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "timeout %d '%s' '%s' --max-per-cell=%d "
             "< '%s' > '%s' 2> '%s'",
             QXAPP_TS_TIMEOUT_S, qx_py(), qx_ts_script(), a1_max_ue_per_cell,
             fin, fout, ferr);
    int rc = system(cmd);
    if (rc != 0) {
      char errbuf[256] = {0};
      FILE *fe = fopen(ferr, "r");
      if (fe) {
        size_t n = fread(errbuf, 1, sizeof(errbuf) - 1, fe);
        errbuf[n] = '\0';
        fclose(fe);
      }
      if (strstr(errbuf, "no feasible assignment"))
        fprintf(stderr, "[Q-xApp TS] quantum solver: no candidate in top-20 "
                        "(rc=%d) — not an infeasibility proof\n", rc);
      else
        fprintf(stderr, "[Q-xApp TS] quantum solver error rc=%d stderr: %.200s\n",
                rc, errbuf);
      goto cleanup;
    }
  }

  /* 3. Parse output JSON */
  {
    char buf[1024] = {0};
    FILE *fo = fopen(fout, "r");
    if (!fo) goto cleanup;
    size_t n = fread(buf, 1, sizeof(buf) - 1, fo);
    buf[n] = '\0';
    fclose(fo);

    char *p = strstr(buf, "\"assignment\"");
    if (p) p = strchr(p, '[');
    int tmp[NUM_UE];
    if (!p || sscanf(p, "[%d,%d,%d,%d]", &tmp[0], &tmp[1], &tmp[2], &tmp[3]) != NUM_UE) {
      fprintf(stderr, "[Q-xApp TS] failed to parse solver output: %.100s\n", buf);
      goto cleanup;
    }

    /* 4. Sanity check: cap-only, mirroring greedy_match (no per-cell minimum) */
    int load[NUM_CELL] = {0};
    for (int u = 0; u < NUM_UE; u++) {
      if (tmp[u] < 0 || tmp[u] >= NUM_CELL) {
        fprintf(stderr, "[Q-xApp TS] invalid cell index %d for UE %d\n", tmp[u], u);
        goto cleanup;
      }
      load[tmp[u]]++;
    }
    for (int c = 0; c < NUM_CELL; c++) {
      if (load[c] > a1_max_ue_per_cell) {
        fprintf(stderr, "[Q-xApp TS] cap violated: cell %d load=%d (max %d)\n",
                c, load[c], a1_max_ue_per_cell);
        goto cleanup;
      }
    }

    double score = 0.0;
    char *ps = strstr(buf, "\"score\"");
    if (ps && (ps = strchr(ps, ':'))) sscanf(ps + 1, "%lf", &score);
    int elapsed = -1;
    char *pe = strstr(buf, "\"elapsed_ms\"");
    if (pe && (pe = strchr(pe, ':'))) sscanf(pe + 1, "%d", &elapsed);
    double feas_prob = -1.0;
    char *pf = strstr(buf, "\"feasibility_prob\"");
    if (pf && (pf = strchr(pf, ':'))) sscanf(pf + 1, "%lf", &feas_prob);
    char method[64] = "?";
    char *pm = strstr(buf, "\"method\"");
    if (pm && (pm = strchr(pm, ':')) && (pm = strchr(pm, '"')))
      sscanf(pm + 1, "%63[^\"]", method);
    printf("[Q-xApp TS] solver: method=%s feasibility_prob=%.4f\n", method, feas_prob);

    memcpy(assignment, tmp, NUM_UE * sizeof(int));
    if (q_score) *q_score = score;
    if (q_elapsed_ms) *q_elapsed_ms = elapsed;
    rc_val = 0;
  }

cleanup:
  if (fdi >= 0) close(fdi);
  if (fdo >= 0) close(fdo);
  if (fde >= 0) close(fde);
  if (!getenv("QXAPP_TS_DEBUG")) {
    unlink(fin); unlink(fout); unlink(ferr);
  }
  return rc_val;
}

/* Run dqna_42.py (4 UE x 2 active cells, NES) on sinr_matrix columns cA/cB.
 * On success fills a42[u] in {0,1} (0 -> cA, 1 -> cB) and returns 0; -1 on
 * any failure (caller falls back to the classical packing). */
#define QXAPP_42_SCRIPT_DEFAULT "/root/flexric/examples/xApp/c/ctrl/dqna_42.py"

static int quantum_42_match(int a42[NUM_UE], int cA, int cB, double *q_score)
{
  char fin[]  = "/tmp/qxapp_42_in_XXXXXX";
  char fout[] = "/tmp/qxapp_42_out_XXXXXX";
  char ferr[] = "/tmp/qxapp_42_err_XXXXXX";
  int rc_val = -1;
  int fdi = mkstemp(fin), fdo = mkstemp(fout), fde = mkstemp(ferr);
  if (fdi < 0 || fdo < 0 || fde < 0) goto cleanup;
  {
    FILE *fi = fdopen(fdi, "w");
    if (!fi) goto cleanup;
    fdi = -1;
    fprintf(fi, "{\"sinr\":[");
    for (int u = 0; u < NUM_UE; u++)
      fprintf(fi, "%s[%.6f,%.6f]", u > 0 ? "," : "",
              sinr_matrix[u][cA], sinr_matrix[u][cB]);
    fprintf(fi, "]}\n");
    fclose(fi);
  }
  {
    const char *script = getenv("QXAPP_42_SCRIPT");
    if (!script) script = QXAPP_42_SCRIPT_DEFAULT;
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "timeout %d '%s' '%s' --qual-lambda=4.0 --max-per-cell=%d "
             "< '%s' > '%s' 2> '%s'",
             QXAPP_TS_TIMEOUT_S, qx_py(), script, a1_max_ue_per_cell,
             fin, fout, ferr);
    if (system(cmd) != 0) {
      fprintf(stderr, "[Q-xApp NES] quantum 4x2 solver failed, see stderr file\n");
      goto cleanup;
    }
  }
  {
    char buf[1024] = {0};
    FILE *fo = fopen(fout, "r");
    if (!fo) goto cleanup;
    size_t n = fread(buf, 1, sizeof(buf) - 1, fo);
    buf[n] = '\0';
    fclose(fo);
    char *p = strstr(buf, "\"assignment\"");
    if (p) p = strchr(p, '[');
    int t[NUM_UE];
    if (!p || sscanf(p, "[%d,%d,%d,%d]", &t[0], &t[1], &t[2], &t[3]) != NUM_UE)
      goto cleanup;
    int load = 0;
    for (int u = 0; u < NUM_UE; u++) {
      if (t[u] != 0 && t[u] != 1) goto cleanup;
      load += t[u];
    }
    if (load > a1_max_ue_per_cell || (NUM_UE - load) > a1_max_ue_per_cell)
      goto cleanup;
    if (q_score) {
      char *ps = strstr(buf, "\"score\"");
      if (ps && (ps = strchr(ps, ':'))) sscanf(ps + 1, "%lf", q_score);
    }
    memcpy(a42, t, sizeof(t));
    rc_val = 0;
  }
cleanup:
  if (fdi >= 0) close(fdi);
  if (fdo >= 0) close(fdo);
  if (fde >= 0) close(fde);
  if (!getenv("QXAPP_TS_DEBUG")) {
    unlink(fin); unlink(fout); unlink(ferr);
  }
  return rc_val;
}

/* Run dqna_qos.py on one cell's 2-UE x 4-DRB utility rows. Fills d1/d2
 * (distinct DRB indices) and returns 0; -1 on failure (classical fallback). */
#define QXAPP_QOS_SCRIPT_DEFAULT "/root/flexric/examples/xApp/c/ctrl/dqna_qos.py"

static int quantum_qos_pair(int u1, int u2, double util[NUM_UE][NUM_DRB],
                            int *d1, int *d2)
{
  char fin[]  = "/tmp/qxapp_qos_in_XXXXXX";
  char fout[] = "/tmp/qxapp_qos_out_XXXXXX";
  char ferr[] = "/tmp/qxapp_qos_err_XXXXXX";
  int rc_val = -1;
  int fdi = mkstemp(fin), fdo = mkstemp(fout), fde = mkstemp(ferr);
  if (fdi < 0 || fdo < 0 || fde < 0) goto cleanup;
  {
    FILE *fi = fdopen(fdi, "w");
    if (!fi) goto cleanup;
    fdi = -1;
    fprintf(fi, "{\"utility\":[[");
    for (int d = 0; d < NUM_DRB; d++)
      fprintf(fi, "%s%.6f", d ? "," : "", util[u1][d] > 0 ? util[u1][d] : 0.0);
    fprintf(fi, "],[");
    for (int d = 0; d < NUM_DRB; d++)
      fprintf(fi, "%s%.6f", d ? "," : "", util[u2][d] > 0 ? util[u2][d] : 0.0);
    fprintf(fi, "]]}\n");
    fclose(fi);
  }
  {
    const char *script = getenv("QXAPP_QOS_SCRIPT");
    if (!script) script = QXAPP_QOS_SCRIPT_DEFAULT;
    char cmd[1024];
    snprintf(cmd, sizeof(cmd),
             "timeout %d '%s' '%s' --qual-lambda=4.0 < '%s' > '%s' 2> '%s'",
             QXAPP_TS_TIMEOUT_S, qx_py(), script, fin, fout, ferr);
    if (system(cmd) != 0) {
      fprintf(stderr, "[Q-xApp QoS-RA] quantum 2x4 solver failed\n");
      goto cleanup;
    }
  }
  {
    char buf[512] = {0};
    FILE *fo = fopen(fout, "r");
    if (!fo) goto cleanup;
    size_t n = fread(buf, 1, sizeof(buf) - 1, fo);
    buf[n] = '\0';
    fclose(fo);
    char *p = strstr(buf, "\"assignment\"");
    if (p) p = strchr(p, '[');
    int a, b;
    if (!p || sscanf(p, "[%d,%d]", &a, &b) != 2) goto cleanup;
    if (a < 0 || a >= NUM_DRB || b < 0 || b >= NUM_DRB || a == b) goto cleanup;
    *d1 = a; *d2 = b;
    rc_val = 0;
  }
cleanup:
  if (fdi >= 0) close(fdi);
  if (fdo >= 0) close(fdo);
  if (fde >= 0) close(fde);
  if (!getenv("QXAPP_TS_DEBUG")) {
    unlink(fin); unlink(fout); unlink(ferr);
  }
  return rc_val;
}

/* QoS-based Resource Allocation: UE-DRB matching.
 * Quantum path (default): per-cell matching — every O-RU offers all 4 DRBs;
 * the 2 UEs inside one cell take distinct DRBs (dqna_qos.py), a lone UE takes
 * its best DRB. Classical fallback: the original global greedy matching.
 * Utility = match quality between UE 5QI and DRB 5QI x SINR. */
static void drb_match(int assignment[NUM_UE])
{
  for (int u = 0; u < NUM_UE; u++)
    ue_drb_assignment[u] = -1;

  /* For each UE, find best available DRB in its assigned cell */
  /* Build utility matrix: NUM_UE x NUM_DRB, then greedy match */
  double utility[NUM_UE][NUM_DRB];
  memset(utility, 0, sizeof(utility));

  printf("[Q-xApp QoS-RA] UE-DRB utility matrix:\n");
  printf("         ");
  for (int d = 0; d < NUM_DRB; d++) printf("DRB%d(5QI=%d) ", drb_pool[d].drb_id, drb_pool[d].fiveqi);
  printf("\n");

  for (int u = 0; u < NUM_UE; u++) {
    int c = assignment[u];
    double sr = (c >= 0) ? sinr_matrix[u][c] : 1.0;
    printf("  UE %d (5QI=%d, %s): ", u, ue_fiveqi[u], (c >= 0) ? ORU_NAMES[c] : "?");
    for (int d = 0; d < NUM_DRB; d++) {
      if (c < 0 || !cell_drb_avail[c][d]) {
        utility[u][d] = -1.0; /* DRB not available in this cell */
        printf("  N/A     ");
        continue;
      }
      /* 5QI match: exact match gets highest utility, mismatch penalized */
      int fiveqi_diff = abs(ue_fiveqi[u] - drb_pool[d].fiveqi);
      double match_score = 1.0 / (1.0 + fiveqi_diff);
      utility[u][d] = match_score * drb_pool[d].weight * sr;
      printf("  %-8.1f", utility[u][d]);
    }
    printf("\n");
  }

  /* Quantum per-cell matching first (classical global greedy as fallback) */
  int used_quantum_qos = 0;
  if (quantum_ts_enabled) {
    int ok = 1;
    for (int c = 0; c < NUM_CELL && ok; c++) {
      int us[NUM_UE], nu = 0;
      for (int u = 0; u < NUM_UE; u++)
        if (assignment[u] == c) us[nu++] = u;
      if (nu == 0) continue;
      if (nu == 1) {
        int u = us[0], best = 0;
        for (int d = 1; d < NUM_DRB; d++)
          if (utility[u][d] > utility[u][best]) best = d;
        ue_drb_assignment[u] = best;
      } else if (nu == 2) {
        int d1, d2;
        if (quantum_qos_pair(us[0], us[1], utility, &d1, &d2) == 0) {
          ue_drb_assignment[us[0]] = d1;
          ue_drb_assignment[us[1]] = d2;
        } else ok = 0;
      } else ok = 0; /* >cap UEs in a cell: let the classical matcher handle it */
    }
    for (int u = 0; u < NUM_UE && ok; u++)
      if (assignment[u] < 0 || ue_drb_assignment[u] < 0) ok = 0;
    if (ok) {
      used_quantum_qos = 1;
      for (int u = 0; u < NUM_UE; u++) {
        int d = ue_drb_assignment[u];
        printf("[Q-xApp QoS-RA]   UE %d (5QI=%d) -> DRB %d (5QI=%d, weight=%.1f, "
               "util=%.1f) [quantum]\n", u, ue_fiveqi[u], drb_pool[d].drb_id,
               drb_pool[d].fiveqi, drb_pool[d].weight, utility[u][d]);
      }
    } else {
      for (int u = 0; u < NUM_UE; u++) ue_drb_assignment[u] = -1;
      fprintf(stderr, "[Q-xApp QoS-RA] falling back to classical DRB matching\n");
    }
  }
  if (used_quantum_qos) return;

  /* Greedy matching: sort all (UE, DRB) pairs by utility descending */
  typedef struct { double util; int ue; int drb; } pair_t;
  pair_t pairs[NUM_UE * NUM_DRB];
  int np = 0;
  for (int u = 0; u < NUM_UE; u++)
    for (int d = 0; d < NUM_DRB; d++) {
      if (utility[u][d] < 0) continue; /* skip unavailable */
      pairs[np].util = utility[u][d];
      pairs[np].ue = u;
      pairs[np].drb = d;
      np++;
    }

  for (int i = 0; i < np - 1; i++)
    for (int j = i + 1; j < np; j++)
      if (pairs[j].util > pairs[i].util) {
        pair_t tmp = pairs[i]; pairs[i] = pairs[j]; pairs[j] = tmp;
      }

  int ue_assigned[NUM_UE];
  memset(ue_assigned, 0, sizeof(ue_assigned));
  int drb_assigned[NUM_DRB];
  memset(drb_assigned, 0, sizeof(drb_assigned));

  for (int k = 0; k < np; k++) {
    int u = pairs[k].ue;
    int d = pairs[k].drb;
    if (ue_assigned[u] || drb_assigned[d]) continue;
    ue_drb_assignment[u] = d;
    ue_assigned[u] = 1;
    drb_assigned[d] = 1;
    printf("[Q-xApp QoS-RA]   UE %d (5QI=%d) -> DRB %d (5QI=%d, weight=%.1f, util=%.1f)\n",
           u, ue_fiveqi[u], drb_pool[d].drb_id, drb_pool[d].fiveqi,
           drb_pool[d].weight, pairs[k].util);
  }

  /* Fallback: unassigned UE gets any available DRB */
  for (int u = 0; u < NUM_UE; u++) {
    if (ue_assigned[u]) continue;
    int c = assignment[u];
    for (int d = 0; d < NUM_DRB; d++) {
      if (drb_assigned[d]) continue;
      if (c >= 0 && !cell_drb_avail[c][d]) continue;
      ue_drb_assignment[u] = d;
      ue_assigned[u] = 1;
      drb_assigned[d] = 1;
      printf("[Q-xApp QoS-RA]   UE %d (5QI=%d) -> DRB %d (fallback)\n",
             u, ue_fiveqi[u], drb_pool[d].drb_id);
      break;
    }
  }
}


/* sleep config from GUI */
static int forced_sleep_cells[NUM_CELL];
static int n_forced_sleep = 0;

/* Is cell index c an operator-forced-sleep cell? Such cells are excluded from
 * NES assignment entirely (not merely deprioritised) so a forced cell always
 * ends up with 0 UEs and in the sleep set (Codex §18). */
static int is_forced_sleep_cell(int c)
{
  for (int fs = 0; fs < n_forced_sleep; fs++)
    if (CELL_IDS[c] == forced_sleep_cells[fs]) return 1;
  return 0;
}

/* energy-aware matching: pack UEs into minimum cells */
/* Returns 0 on success, -1 if the awake cells cannot hold all UEs under the
 * cap (R2.6): infeasible NES packing fails closed instead of forcing an
 * over-cap UE onto (possibly sleeping) cell 0. */
static int energy_aware_match(int assignment[NUM_UE],
                              int *out_active, int sleep_cells[], int *out_n_sleep)
{
  typedef struct { double capacity; int cell_idx; } cell_cap_t;
  cell_cap_t caps[NUM_CELL];
  for (int c = 0; c < NUM_CELL; c++) {
    caps[c].cell_idx = c;
    caps[c].capacity = 0.0;
    for (int u = 0; u < NUM_UE; u++)
      caps[c].capacity += sinr_matrix[u][c];
    /* Force sleep cells to lowest priority */
    for (int fs = 0; fs < n_forced_sleep; fs++) {
      if (CELL_IDS[c] == forced_sleep_cells[fs]) {
        caps[c].capacity = -1.0;
        break;
      }
    }
  }

  for (int i = 0; i < NUM_CELL - 1; i++)
    for (int j = i + 1; j < NUM_CELL; j++)
      if (caps[j].capacity > caps[i].capacity) {
        cell_cap_t tmp = caps[i]; caps[i] = caps[j]; caps[j] = tmp;
      }

  printf("[Q-xApp NES] Cell capacity ranking:\n");
  for (int i = 0; i < NUM_CELL; i++)
    printf("  %s (cell_idx=%d): total_capacity=%.4f\n",
           ORU_NAMES[caps[i].cell_idx], caps[i].cell_idx, caps[i].capacity);

  int cell_load[NUM_CELL] = {0};
  int assigned[NUM_UE];
  memset(assigned, 0, sizeof(assigned));
  for (int u = 0; u < NUM_UE; u++)
    assignment[u] = -1;

  /* Quantum 4x2 path: lowest-capacity cell (forced-sleep aware, since forced
   * cells were ranked at -1) is the sleep candidate; the remaining two cells
   * are the 4x2 columns. Fallback = classical packing below. */
  int used_quantum = 0;
  if (quantum_ts_enabled) {
    int cs = caps[NUM_CELL - 1].cell_idx, cA = -1, cB = -1;
    for (int c = 0; c < NUM_CELL; c++) {
      if (c == cs) continue;
      if (cA < 0) cA = c; else cB = c;
    }
    int a42[NUM_UE];
    double q_score = 0.0;
    if (quantum_42_match(a42, cA, cB, &q_score) == 0) {
      for (int u = 0; u < NUM_UE; u++) {
        assignment[u] = a42[u] ? cB : cA;
        cell_load[assignment[u]]++;
      }
      used_quantum = 1;
      printf("[Q-xApp NES] Quantum 4x2 assignment: [%d,%d,%d,%d] score=%.4f "
             "(sleep candidate %s)\n", assignment[0], assignment[1],
             assignment[2], assignment[3], q_score, ORU_NAMES[cs]);
    } else {
      fprintf(stderr, "[Q-xApp NES] falling back to classical packing\n");
    }
  }

  if (!used_quantum)
  for (int ci = 0; ci < NUM_CELL; ci++) {
    int c = caps[ci].cell_idx;
    if (is_forced_sleep_cell(c)) continue;  /* never assign to a forced cell */
    typedef struct { double rate; int ue; } ue_rate_t;
    ue_rate_t candidates[NUM_UE];
    int nc = 0;
    for (int u = 0; u < NUM_UE; u++) {
      if (assigned[u]) continue;
      candidates[nc].rate = sinr_matrix[u][c];
      candidates[nc].ue = u;
      nc++;
    }
    for (int i = 0; i < nc - 1; i++)
      for (int j = i + 1; j < nc; j++)
        if (candidates[j].rate > candidates[i].rate) {
          ue_rate_t tmp = candidates[i];
          candidates[i] = candidates[j];
          candidates[j] = tmp;
        }
    for (int i = 0; i < nc && cell_load[c] < a1_max_ue_per_cell; i++) {
      int u = candidates[i].ue;
      assignment[u] = c;
      assigned[u] = 1;
      cell_load[c]++;
    }
  }

  for (int u = 0; u < NUM_UE; u++) {
    if (assignment[u] >= 0) continue;
    int best = -1;
    for (int c = 0; c < NUM_CELL; c++) {
      if (is_forced_sleep_cell(c)) continue;   /* forced cells stay empty */
      if (cell_load[c] >= a1_max_ue_per_cell) continue;
      if (best < 0 || cell_load[c] < cell_load[best]) best = c;
    }
    if (best < 0) {
      fprintf(stderr, "[Q-xApp NES] no awake cell can hold UE %d under cap %d "
                      "(NES packing infeasible)\n", u, a1_max_ue_per_cell);
      return -1;
    }
    assignment[u] = best;
    cell_load[best]++;
  }

  int active = 0;
  int n_sleep = 0;
  for (int c = 0; c < NUM_CELL; c++) {
    if (cell_load[c] > 0) {
      active++;
    } else {
      sleep_cells[n_sleep++] = CELL_IDS[c];
      printf("[Q-xApp NES] Cell %s (ID=%d) -> SLEEP candidate (0 UEs)\n",
             ORU_NAMES[c], CELL_IDS[c]);
    }
  }

  *out_active = active;
  *out_n_sleep = n_sleep;
  return 0;
}



/* read sleep config from file: comma-separated cell IDs */

static void read_sleep_config(void)
{
  n_forced_sleep = 0;
  char cfg_path[512];
  FILE *fp = fopen(qx_data_path(cfg_path, sizeof(cfg_path), SLEEP_CONFIG_FILE_NAME), "r");
  if (!fp) return;
  char buf[256];
  if (!fgets(buf, sizeof(buf), fp)) { fclose(fp); return; }
  fclose(fp);
  char *tok = strtok(buf, ",\n\r ");
  while (tok && n_forced_sleep < NUM_CELL) {
    int cid = atoi(tok);
    if (cid >= 2 && cid <= 4) {
      forced_sleep_cells[n_forced_sleep++] = cid;
    }
    tok = strtok(NULL, ",\n\r ");
  }
}

/* read NetEnergy from energyfilecell{2,3,4}.csv and compute delta */
static double cell_energy[NUM_CELL]; /* avg power consumption (W, sim time) over last round */
static double prev_net_energy[NUM_CELL] = {0};
static double prev_net_time[NUM_CELL] = {0};
static double g_energy_sample_time = 0.0; /* ns-3 sim-time of last energy sample (GUI x-axis) */
static int energy_initialized = 0;


/* read A1 policy: max UE per cell */

static void read_a1_policy(void)
{
  char cfg_path[512];
  FILE *fp = fopen(qx_data_path(cfg_path, sizeof(cfg_path), A1_POLICY_FILE_NAME), "r");
  if (!fp) { a1_max_ue_per_cell = 2; return; }
  char buf[64];
  if (fgets(buf, sizeof(buf), fp)) {
    int val = atoi(buf);
    if (val >= 1 && val <= NUM_UE) a1_max_ue_per_cell = val;
  }
  fclose(fp);
}

static double read_net_energy_from_csv(int cell_id, double *out_time)
{
  char path[512];
  snprintf(path, sizeof(path), "%s/energyfilecell%d.csv", qx_data_dir(), cell_id);
  FILE *fp = fopen(path, "r");
  if (!fp) return 0.0;
  char last_line[256] = "";
  char line[256];
  while (fgets(line, sizeof(line), fp)) {
    if (line[0] != 'T' && strlen(line) > 5)
      strncpy(last_line, line, sizeof(last_line));
  }
  fclose(fp);
  if (strlen(last_line) == 0) return 0.0;
  /* format: Time,NetEnergy,DiffEnergy */
  char *comma1 = strchr(last_line, ',');
  if (!comma1) return 0.0;
  if (out_time) *out_time = atof(last_line); /* sim time of last sample */
  return atof(comma1 + 1); /* NetEnergy */
}

static void read_cell_energy(void)
{
  double tmax = 0.0;
  for (int c = 0; c < NUM_CELL; c++) {
    double tnow = 0.0;
    double net = read_net_energy_from_csv(CELL_IDS[c], &tnow);
    if (tnow > tmax) tmax = tnow;
    if (!energy_initialized) {
      cell_energy[c] = 0.0;
      prev_net_energy[c] = net;
      prev_net_time[c] = tnow;
    } else {
      double de = net - prev_net_energy[c];
      double dt = tnow - prev_net_time[c];
      /* true average power over the elapsed SIM time (W); matches the
         offline figure semantics instead of joules-per-poll */
      cell_energy[c] = (dt > 1e-9 && de > 0) ? de / dt : 0.0;
      prev_net_energy[c] = net;
      prev_net_time[c] = tnow;
    }
  }
  g_energy_sample_time = tmax;
  energy_initialized = 1;
}

static int g_current_round = 0;
static int g_cycle_finished = 0; /* GUI freeze flag (any terminal recovery outcome) */
static const char *g_cycle_status = "running"; /* running | complete | timeout */
/* write result JSON with mode info */
static void write_result_json_unified(int assignment[NUM_UE], double total_rate,
                                      const char *mode,
                                      int active_cells, int sleep_cells[], int n_sleep)
{
  /* Atomic publish (R4.5, pulled forward): finish a temp file in the same
   * directory then rename() over RESULT_JSON, so the GUI never reads a
   * half-written document. */
  char rj_path[512];
  qx_data_path(rj_path, sizeof(rj_path), RESULT_JSON_NAME);
  char tmp_path[600];
  snprintf(tmp_path, sizeof(tmp_path), "%s.tmp.%d", rj_path, (int)getpid());
  FILE *fp = fopen(tmp_path, "w");
  if (!fp) return;
  fprintf(fp, "{\n");
  fprintf(fp, "  \"mode\": \"%s\",\n", mode);
  fprintf(fp, "  \"timestamp\": %ld,\n", (long)time(NULL));
  fprintf(fp, "  \"round\": %d,\n", g_current_round);
  fprintf(fp, "  \"total_rate_bps_hz\": %.4f,\n", total_rate);
  if (strcmp(mode, "nes") == 0) {
    fprintf(fp, "  \"active_cells\": %d,\n", active_cells);
    fprintf(fp, "  \"sleep_cells\": [");
    for (int i = 0; i < n_sleep; i++)
      fprintf(fp, "%d%s", sleep_cells[i], i < n_sleep - 1 ? ", " : "");
    fprintf(fp, "],\n");
  }
  if (strcmp(mode, "qos") == 0) {
    fprintf(fp, "  \"ue_fiveqi\": [");
    for (int u = 0; u < NUM_UE; u++)
      fprintf(fp, "%d%s", ue_fiveqi[u], u < NUM_UE - 1 ? ", " : "");
    fprintf(fp, "],\n");
    fprintf(fp, "  \"drb_assignment\": [\n");
    for (int u = 0; u < NUM_UE; u++) {
      int d = ue_drb_assignment[u];
      if (d >= 0 && d < NUM_DRB) {
        fprintf(fp, "    {\"ue\": %d, \"drb\": %d, \"fiveqi\": %d, \"weight\": %.1f}%s\n",
                u, drb_pool[d].drb_id, drb_pool[d].fiveqi,
                drb_pool[d].weight,
                u < NUM_UE - 1 ? "," : "");
      } else {
        fprintf(fp, "    {\"ue\": %d, \"drb\": -1, \"fiveqi\": 0, \"weight\": 0.0}%s\n",
                u, u < NUM_UE - 1 ? "," : "");
      }
    }
    fprintf(fp, "  ],\n");
  }
  fprintf(fp, "  \"assignment\": [\n");
  for (int u = 0; u < NUM_UE; u++) {
    fprintf(fp, "    {\"ue\": %d, \"oru\": \"%s\", \"rate\": %.4f}%s\n",
            u, (assignment[u] >= 0 ? ORU_NAMES[assignment[u]] : "unassigned"), (assignment[u] >= 0 ? sinr_matrix[u][assignment[u]] : 0.0),
            u < NUM_UE - 1 ? "," : "");
  }
  fprintf(fp, "  ],\n");
  fprintf(fp, "  \"cell_energy\": {");
  for (int c = 0; c < NUM_CELL; c++) {
    fprintf(fp, "\"%d\": %.1f%s", CELL_IDS[c], cell_energy[c], c < NUM_CELL - 1 ? ", " : "");
  }
  fprintf(fp, "},\n");
  fprintf(fp, "  \"cycle_finished\": %s,\n", g_cycle_finished ? "true" : "false");
  fprintf(fp, "  \"cycle_status\": \"%s\",\n", g_cycle_status);
  fprintf(fp, "  \"energy_sample_time\": %.3f\n}\n", g_energy_sample_time);
  fflush(fp);
  fsync(fileno(fp));
  fclose(fp);
  if (rename(tmp_path, rj_path) != 0) {
    fprintf(stderr, "[Q-xApp] atomic rename to %s failed\n", rj_path);
    unlink(tmp_path);
    return;
  }
  printf("[Q-xApp] Result written to %s\n", rj_path);
}

/* read mode from file */
static void read_mode(char *mode_buf, size_t buf_sz)
{
  char cfg_path[512];
  FILE *fp = fopen(qx_data_path(cfg_path, sizeof(cfg_path), MODE_FILE_NAME), "r");
  if (!fp) {
    strncpy(mode_buf, "ts", buf_sz);
    return;
  }
  if (!fgets(mode_buf, (int)buf_sz, fp)) {
    strncpy(mode_buf, "ts", buf_sz);
  }
  fclose(fp);
  /* trim whitespace */
  char *p = mode_buf;
  while (*p && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
  if (p != mode_buf) memmove(mode_buf, p, strlen(p) + 1);
  size_t len = strlen(mode_buf);
  while (len > 0 && (mode_buf[len-1] == ' ' || mode_buf[len-1] == '\n' || mode_buf[len-1] == '\r' || mode_buf[len-1] == '\t')) {
    mode_buf[--len] = '\0';
  }
  /* default to ts if unrecognized */
  if (strcmp(mode_buf, "ts") != 0 && strcmp(mode_buf, "nes") != 0
      && strcmp(mode_buf, "qos") != 0 && strcmp(mode_buf, "auto") != 0) {
    strncpy(mode_buf, "ts", buf_sz);
  }
}

/* Auto mode: round-based TS → QoS-RA → NES cycling */
#define AUTO_TS_ROUNDS   5
#define AUTO_QOS_ROUNDS  5
#define AUTO_NES_ROUNDS  5
#define AUTO_TOTAL_ROUNDS (AUTO_TS_ROUNDS + AUTO_QOS_ROUNDS + AUTO_NES_ROUNDS)

static void auto_resolve_mode(int round, char *effective, size_t sz)
{
  /* Single cycle: TS -> QoS -> NES -> stay on TS (no repeat) */
  if (round > AUTO_TOTAL_ROUNDS) {
    strncpy(effective, "ts", sz);
    return;
  }
  if (round <= AUTO_TS_ROUNDS)
    strncpy(effective, "ts", sz);
  else if (round <= AUTO_TS_ROUNDS + AUTO_QOS_ROUNDS)
    strncpy(effective, "qos", sz);
  else
    strncpy(effective, "nes", sz);
}

/* =========================================================================
 * Fig. 2 Pipeline Functions
 * ========================================================================= */

/* Stage 1: Use-Case Encoder (Fig. 2) */
static void use_case_encoder(const char *mode)
{
  printf("[Q-xApp] --- Stage 1: Use-Case Encoder ---\n");

  /* Read E2 measurements */
  read_sinr_from_csv();
  read_cell_energy();

  /* UE 3 (IMSI 4) fallback: fill with average if no data */
  {
    int has = 0;
    for (int c = 0; c < NUM_CELL; c++)
      if (sinr_matrix[3][c] > 0.01) has = 1;
    if (!has) {
      for (int c = 0; c < NUM_CELL; c++) {
        double sum = 0; int cnt = 0;
        for (int u = 0; u < 3; u++)
          if (sinr_matrix[u][c] > 0.01) { sum += sinr_matrix[u][c]; cnt++; }
        if (cnt > 0) sinr_matrix[3][c] = sum / cnt;
      }
    }
  }

  /* Print rate matrix */
  printf("[Q-xApp] Rate matrix (bps/Hz):\n");
  printf("         ");
  for (int c = 0; c < NUM_CELL; c++) printf("%-10s", ORU_NAMES[c]);
  printf("\n");
  for (int u = 0; u < NUM_UE; u++) {
    printf("  UE %d:  ", u);
    for (int c = 0; c < NUM_CELL; c++)
      printf("%-10.4f", sinr_matrix[u][c]);
    printf("  (serving: %s%s)\n",
           (meas_valid[u] && serving_cell[u] >= 0 && serving_cell[u] < NUM_CELL)
               ? ORU_NAMES[serving_cell[u]] : "n/a",
           meas_valid[u] ? "" : " [stale]");
  }

  /* Mode-specific configuration */
  if (strcmp(mode, "ts") == 0) {
    read_a1_policy();
    printf("[Q-xApp] A1 policy: max_ue_per_cell=%d\n", a1_max_ue_per_cell);
  } else if (strcmp(mode, "qos") == 0) {
    read_qos_config();
    read_a1_policy();
    printf("[Q-xApp QoS-RA] 5QI config:");
    for (int u = 0; u < NUM_UE; u++) printf(" UE%d=5QI%d", u, ue_fiveqi[u]);
    printf(", max_ue_per_cell=%d\n", a1_max_ue_per_cell);
  } else {
    read_sleep_config();
    /* NES also honours the A1 cap so its awake-cell packing / feasibility
     * gate uses the current policy, not a stale value (Codex §16). */
    read_a1_policy();
    printf("[Q-xApp NES] max_ue_per_cell=%d\n", a1_max_ue_per_cell);
    if (n_forced_sleep > 0) {
      printf("[Q-xApp NES] Forced sleep cells:");
      for (int i = 0; i < n_forced_sleep; i++)
        printf(" %d", forced_sleep_cells[i]);
      printf("\n");
    }
  }
}

/* INIT-TS shared state — defined here so assignment_algorithm can read the
 * frozen assignment; the state machine itself lives below Stage 2. */
static int init_ts_converged = 0;
static int init_ts_target[NUM_UE] = {-1, -1, -1, -1};
/* Manual-QoS freshness anchor (Codex blocker 5): captured on qos entry so
 * grouping only trusts a measurement scan newer than the anchor. */
static uint64_t qos_meas_anchor[NUM_UE] = {0};

/* Stage 2: Quantum Assignment Algorithm (Fig. 2).
 * Returns STAGE2_OK, STAGE2_INFEASIBLE (mathematically over-cap / no packing),
 * or STAGE2_NOT_READY (transient stale/not-ready measurement) — the caller
 * maps these to infeasible vs pending status (Codex blocker 4). */
static int assignment_algorithm(const char *mode, int is_auto,
                                int assignment[NUM_UE],
                                int *active_cells,
                                int sleep_cells[],
                                int *n_sleep)
{
  printf("[Q-xApp] --- Stage 2: Quantum Assignment Algorithm ---\n");

  if (strcmp(mode, "nes") == 0) {
    if (energy_aware_match(assignment, active_cells, sleep_cells, n_sleep) != 0)
      return STAGE2_INFEASIBLE;   /* NES packing infeasible under cap (R2.6) */
    /* validate the (possibly quantum 4x2) result against the cap (blocker 5) */
    if (!assignment_within_cap(assignment, sleep_cells, *n_sleep)) {
      fprintf(stderr, "[Q-xApp NES] assignment exceeds cap or lands on a sleep "
                      "cell — rejecting (fail-closed)\n");
      return STAGE2_INFEASIBLE;
    }
  } else if (strcmp(mode, "qos") == 0) {
    /* QoS must not re-steer. Only AUTO qos reuses the converged frozen
     * INIT-TS assignment. MANUAL qos ALWAYS groups by the ACTUAL measured
     * serving cell, never a stale frozen target — `init_ts_converged` is a
     * global that is not reset after an auto cycle, so keying on it alone
     * would leak the old target into a later manual qos in the same process
     * (Codex blocker 3). */
    if (is_auto && init_ts_converged) {
      for (int u = 0; u < NUM_UE; u++) assignment[u] = init_ts_target[u];
      printf("[Q-xApp QoS-RA] Using frozen INIT-TS assignment (auto)\n");
    } else {
      /* fresh (newer-than-anchor) serving row required for every UE */
      int all_fresh = 1;
      for (int u = 0; u < NUM_UE; u++)
        if (!meas_fresh_since(u, qos_meas_anchor[u]) || serving_cell[u] < 0)
          all_fresh = 0;
      if (!all_fresh) {
        fprintf(stderr, "[Q-xApp QoS-RA] measured serving state not-ready/stale "
                        "— withholding DRB control this round (pending)\n");
        return STAGE2_NOT_READY;   /* transient, not a math infeasibility */
      }
      for (int u = 0; u < NUM_UE; u++) assignment[u] = serving_cell[u];
      printf("[Q-xApp QoS-RA] Grouping by measured serving cells (manual)\n");
    }
    /* validate the QoS cell assignment (frozen auto target or measured
     * serving) against the cap before any DRB control (Codex §15-3): a cell
     * carrying more than cap UEs must fail closed, not proceed to RC. */
    if (!assignment_within_cap(assignment, NULL, 0)) {
      fprintf(stderr, "[Q-xApp QoS-RA] cell assignment exceeds cap — "
                      "withholding DRB control (fail-closed)\n");
      return STAGE2_INFEASIBLE;
    }
    /* QoS-RA xApp: UE-DRB assignment (runs in parallel with TS) */
    drb_match(assignment);
    *active_cells = NUM_CELL;
    *n_sleep = 0;
  } else {
    ts_quantum_state = 0; /* 0=greedy(off) 1=quantum 2=greedy(fallback) */
    if (quantum_ts_enabled) {
      double q_score = 0.0;
      int q_elapsed = -1;
      if (quantum_ts_match(assignment, &q_score, &q_elapsed) == 0) {
        ts_quantum_state = 1;
        quantum_ok_count++;
        /* verification logging (Codex 12차): greedy comparison per decision */
        int g[NUM_UE];
        double g_score = 0.0;
        if (greedy_match(g) == 0)
          for (int u = 0; u < NUM_UE; u++) g_score += sinr_matrix[u][g[u]];
        printf("[Q-xApp TS] quantum decision: assign=[%d,%d,%d,%d] q_score=%.4f "
               "greedy_score=%.4f solver_ms=%d ok=%ld fallback=%ld\n",
               assignment[0], assignment[1], assignment[2], assignment[3],
               q_score, g_score, q_elapsed, quantum_ok_count, quantum_fallback_count);
      } else {
        ts_quantum_state = 2;
        quantum_fallback_count++;
        fprintf(stderr, "[Q-xApp TS] falling back to greedy_match "
                        "(ok=%ld fallback=%ld)\n",
                quantum_ok_count, quantum_fallback_count);
      }
    }
    if (ts_quantum_state != 1) {
      if (greedy_match(assignment) != 0)
        return STAGE2_INFEASIBLE;   /* infeasible: fail closed */
    }
    /* validate the (possibly quantum TS) result against the cap (blocker 5) */
    if (!assignment_within_cap(assignment, NULL, 0)) {
      fprintf(stderr, "[Q-xApp TS] assignment exceeds cap — rejecting "
                      "(fail-closed)\n");
      return STAGE2_INFEASIBLE;
    }
    *active_cells = NUM_CELL;
    *n_sleep = 0;
  }

  /* Compute total rate */
  double total_rate = 0.0;
  for (int u = 0; u < NUM_UE; u++)
    total_rate += sinr_matrix[u][assignment[u]];

  /* Log results */
  if (strcmp(mode, "nes") == 0) {
    printf("[Q-xApp NES] Energy-aware assignment (total rate=%.4f bps/Hz, active=%d, sleep=%d):\n",
           total_rate, *active_cells, *n_sleep);
  } else if (strcmp(mode, "qos") == 0) {
    printf("[Q-xApp QoS-RA] DRB assignment (total rate=%.4f bps/Hz):\n", total_rate);
  } else {
    printf("[Q-xApp TS] %s assignment (total rate=%.4f bps/Hz):\n",
           ts_quantum_state == 1 ? "Quantum" :
           ts_quantum_state == 2 ? "Greedy (quantum fallback)" : "Greedy",
           total_rate);
  }
  for (int u = 0; u < NUM_UE; u++) {
    if (strcmp(mode, "qos") == 0 && ue_drb_assignment[u] >= 0) {
      int d = ue_drb_assignment[u];
      printf("  UE %d -> %s (rate=%.4f bps/Hz, DRB=%d, 5QI=%d, w=%.1f)\n",
             u, ORU_NAMES[assignment[u]], sinr_matrix[u][assignment[u]],
             drb_pool[d].drb_id, drb_pool[d].fiveqi, drb_pool[d].weight);
    } else {
      printf("  UE %d -> %s (rate=%.4f bps/Hz)\n",
             u, ORU_NAMES[assignment[u]], sinr_matrix[u][assignment[u]]);
    }
  }

  /* Write result JSON — EXCEPT for manual ts, where the intended target must
   * not be published as an achieved serving state (Codex blocker 4). The main
   * loop publishes the achieved serving state with an honest status after the
   * MANUAL-TS state machine runs. */
  if (!(!is_auto && strcmp(mode, "ts") == 0))
    write_result_json_unified(assignment, total_rate, mode, *active_cells, sleep_cells, *n_sleep);
  return STAGE2_OK;
}

/* ── mode-entry side effects (Codex §16): reset scheduler weights / wake all
 *    cells. Extracted so the SINGLE effective-entry detector runs them exactly
 *    once for every entry — first process entry, mode-string change, and the
 *    auto->manual boundary alike — with no duplicate RC. ────────────────── */
static void entry_reset_scheduler_weights(e2_node_arr_xapp_t *nodes)
{
  printf("[Q-xApp] Resetting all UE scheduling weights...\n");
  for (int u = 0; u < NUM_UE; u++) {
    uint64_t imsi = (uint64_t)(u + 1);
    ue_id_e2sm_t rst_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);
    rc_ctrl_req_data_t rst_ctrl = {0};
    rst_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, rst_ue_id, 1, 4);
    rst_ctrl.msg = gen_rc_ctrl_msg_drb(FORMAT_1_E2SM_RC_CTRL_MSG, '2');
    for (size_t i = 0; i < nodes->len; i++) {
      control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &rst_ctrl);
      usleep(100000);
    }
    free_rc_ctrl_req_data(&rst_ctrl);
  }
}

static void entry_wake_all_cells(e2_node_arr_xapp_t *nodes)
{
  printf("[Q-xApp] Waking up all cells...\n");
  for (int c = 0; c < NUM_CELL; c++) {
    char wake_cell = '0' + CELL_IDS[c];
    ue_id_e2sm_t wake_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, 1);
    rc_ctrl_req_data_t wake_ctrl = {0};
    wake_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, wake_ue_id, 300, 2);
    wake_ctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, wake_cell);
    for (size_t i = 0; i < nodes->len; i++) {
      control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &wake_ctrl);
      usleep(100000);
    }
    free_rc_ctrl_req_data(&wake_ctrl);
    printf("[Q-xApp] Wake up cell ID=%d\n", CELL_IDS[c]);
  }
}

/* ── common HO send + fresh-measurement confirmation (Codex INIT-TS design):
 *    one judgment rule for INIT-TS / NES / POST-WAKE so the three paths
 *    cannot drift apart. "Confirmed" = a CSV row NEWER than the send shows
 *    the UE serving on the target. ───────────────────────────────────── */
static uint64_t send_rc_ho_tagged(e2_node_arr_xapp_t *nodes, int u, int target_idx,
                                  const char *tag)
{
  uint64_t imsi = (uint64_t)(u + 1);
  char target_cell_char = '0' + CELL_IDS[target_idx];
  ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);
  rc_ctrl_req_data_t rc_ctrl = {0};
  rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, 3, HANDOVER_CONTROL_7_6_4_1);
  rc_ctrl.msg = gen_rc_ctrl_msg(FORMAT_1_E2SM_RC_CTRL_MSG, target_cell_char);
  size_t lte_idx = 0;
  uint32_t min_id = UINT32_MAX;
  for (size_t ii = 0; ii < nodes->len; ii++) {
    if (nodes->n[ii].id.nb_id.nb_id < min_id) {
      min_id = nodes->n[ii].id.nb_id.nb_id;
      lte_idx = ii;
    }
  }
  printf("[%s] HO sent IMSI=%lu source=%s target=%s (char '%c')\n", tag, imsi,
         (meas_valid[u] && serving_cell[u] >= 0) ? ORU_NAMES[serving_cell[u]] : "n/a",
         ORU_NAMES[target_idx], target_cell_char);
  control_sm_xapp_api(&nodes->n[lte_idx].id, SM_RC_ID, &rc_ctrl);
  usleep(100000);
  free_rc_ctrl_req_data(&rc_ctrl);
  usleep(500000); /* staggered: one UE at a time toward the LTE anchor */
  return meas_ts[u];
}

static int ho_confirmed_fresh(int u, int target_idx, uint64_t ts_at_send)
{
  return meas_valid[u] && serving_cell[u] == target_idx && meas_ts[u] > ts_at_send;
}

/* ── INIT-TS state machine (Codex-approved): WAIT_MEAS -> FREEZE -> ENFORCE
 *    -> CONVERGE within the fixed TS window (rounds 1..AUTO_TS_ROUNDS).
 *    Hard deadline = end of round AUTO_TS_ROUNDS; on timeout the run goes
 *    fail-closed (no QoS/NES control) and the batch validator fails it. */
static int init_ts_frozen = 0;
static int init_ts_ho_count[NUM_UE] = {0};   /* sends incl. one retry */
static uint64_t init_ts_send_ts[NUM_UE] = {0};
static int init_ts_sent_round[NUM_UE] = {0};
static int init_ts_confirmed[NUM_UE] = {0};
static int init_ts_failed = 0;
#define INIT_TS_RETRY_ROUND 4 /* one retry no later than this round */

static void init_ts_enforce(int assignment[NUM_UE], e2_node_arr_xapp_t *nodes, int round)
{
  /* S0/S1: freeze the target once, when every UE has a current-scan row */
  if (!init_ts_frozen) {
    int all_valid = 1;
    for (int u = 0; u < NUM_UE; u++)
      if (!meas_valid[u]) all_valid = 0;
    if (!all_valid) {
      printf("[INIT-TS] waiting for valid measurements (round %d)\n", round);
      return;
    }
    for (int u = 0; u < NUM_UE; u++) {
      init_ts_target[u] = assignment[u];
      printf("[INIT-TS] target frozen: UE %d -> %s (serving=%s)\n", u,
             (init_ts_target[u] >= 0) ? ORU_NAMES[init_ts_target[u]] : "none",
             (serving_cell[u] >= 0) ? ORU_NAMES[serving_cell[u]] : "n/a");
    }
    init_ts_frozen = 1;
  }
  if (init_ts_converged) return;

  /* S2/S3: send/confirm against the FROZEN target (never recomputed) */
  int all_conv = 1;
  for (int u = 0; u < NUM_UE; u++) {
    int tgt = init_ts_target[u];
    if (tgt < 0) continue;
    if (init_ts_ho_count[u] == 0) {
      if (meas_valid[u] && serving_cell[u] == tgt) continue; /* no HO needed */
      init_ts_send_ts[u] = send_rc_ho_tagged(nodes, u, tgt, "INIT-TS");
      init_ts_ho_count[u] = 1;
      init_ts_sent_round[u] = round;
      all_conv = 0;
      continue;
    }
    if (init_ts_confirmed[u]) continue;
    if (ho_confirmed_fresh(u, tgt, init_ts_send_ts[u])) {
      init_ts_confirmed[u] = 1;
      printf("[INIT-TS] HO confirmed IMSI=%d serving=%s (fresh measurement)\n",
             u + 1, ORU_NAMES[tgt]);
      continue;
    }
    all_conv = 0;
    if (init_ts_ho_count[u] == 1 && round >= INIT_TS_RETRY_ROUND &&
        round > init_ts_sent_round[u]) {
      printf("[INIT-TS] retry: UE %d still not confirmed, resending\n", u);
      init_ts_send_ts[u] = send_rc_ho_tagged(nodes, u, tgt, "INIT-TS");
      init_ts_ho_count[u] = 2;
      init_ts_sent_round[u] = round;
    }
  }
  if (all_conv) {
    init_ts_converged = 1;
    printf("[INIT-TS] converged at round %d (all UEs assignment==serving)\n", round);
    int cap[NUM_CELL] = {0};
    int tot = 0;
    for (int u = 0; u < NUM_UE; u++) {
      if (serving_cell[u] >= 0 && serving_cell[u] < NUM_CELL) {
        cap[serving_cell[u]]++;
        tot++;
      }
    }
    printf("[INIT-TS] capacity: O-RU 1=%d O-RU 2=%d O-RU 3=%d (sum=%d)\n",
           cap[0], cap[1], cap[2], tot);
  }
}

/* ── MANUAL-TS state machine (R2.1): makes manual `ts` mode an actual control
 *    path instead of an assignment preview. Reuses the SAME verified
 *    primitives as INIT-TS (send_rc_ho_tagged + ho_confirmed_fresh) so the
 *    two paths cannot drift apart. Unlike INIT-TS there is no fixed auto
 *    round window; the target is frozen on an explicit recompute EVENT
 *    (manual-ts entry, or an A1-policy change) and driven to serving state
 *    with a bounded retry. It never declares convergence on stale data and
 *    never publishes success on a timeout — it fails closed and keeps
 *    watching until the next recompute event. */
/* MANUAL-TS status returned each round (Codex blocker 4). */
#define MTS_WAIT      0   /* no fresh-measurement freeze yet */
#define MTS_PENDING   1   /* frozen, HO in flight / not yet confirmed */
#define MTS_CONVERGED 2   /* all UEs confirmed serving == target (fresh) */
#define MTS_TIMEOUT   3   /* deadline reached without convergence */

static int manual_ts_frozen = 0;
static int manual_ts_state = MTS_WAIT;
static int manual_ts_target[NUM_UE] = {-1, -1, -1, -1};
static int manual_ts_ho_count[NUM_UE] = {0};
static uint64_t manual_ts_send_ts[NUM_UE] = {0};
static int manual_ts_sent_round[NUM_UE] = {0};
static int manual_ts_confirmed[NUM_UE] = {0};
static uint64_t manual_ts_anchor[NUM_UE] = {0};   /* freshness anchor per UE */
static int manual_ts_freeze_round = 0;
static int manual_ts_cap_seen = -1;
#define MANUAL_TS_RETRY_AFTER 3   /* rounds after freeze before the one retry */
#define MANUAL_TS_DEADLINE 6      /* rounds after freeze -> fail-closed */

/* Reset so the next manual_ts_enforce() refreezes the target. Called on
 * manual-ts entry and on an A1-policy (cap) change. Captures a per-UE
 * freshness anchor at the event time so freeze/convergence require a NEWER
 * measurement scan. */
static void manual_ts_reset(const char *why)
{
  manual_ts_frozen = 0;
  manual_ts_state = MTS_WAIT;
  for (int u = 0; u < NUM_UE; u++) {
    manual_ts_target[u] = -1;
    manual_ts_ho_count[u] = 0;
    manual_ts_send_ts[u] = 0;
    manual_ts_sent_round[u] = 0;
    manual_ts_confirmed[u] = 0;
    manual_ts_anchor[u] = meas_ts[u];   /* only newer scans count from here */
  }
  printf("[MANUAL-TS] recompute event (%s): target will refreeze on a fresh "
         "measurement scan\n", why);
}

/* Returns the MANUAL-TS status this round. */
static int manual_ts_enforce(int assignment[NUM_UE], e2_node_arr_xapp_t *nodes,
                             int round)
{
  /* An A1-policy change is an explicit recompute event. */
  if (manual_ts_cap_seen != a1_max_ue_per_cell) {
    if (manual_ts_cap_seen != -1) manual_ts_reset("A1 cap changed");
    manual_ts_cap_seen = a1_max_ue_per_cell;
  }

  /* Freeze once per event, only when EVERY UE has a fresh (newer-than-anchor)
   * measurement — never on a stale repeat of the previous scan. */
  if (!manual_ts_frozen) {
    int all_fresh = 1;
    for (int u = 0; u < NUM_UE; u++)
      if (!meas_fresh_since(u, manual_ts_anchor[u])) all_fresh = 0;
    if (!all_fresh) {
      printf("[MANUAL-TS] waiting for a fresh measurement scan (round %d)\n",
             round);
      manual_ts_state = MTS_WAIT;
      return MTS_WAIT;
    }
    for (int u = 0; u < NUM_UE; u++) {
      manual_ts_target[u] = assignment[u];
      printf("[MANUAL-TS] target frozen: UE %d -> %s (serving=%s)\n", u,
             (manual_ts_target[u] >= 0) ? ORU_NAMES[manual_ts_target[u]] : "none",
             (serving_cell[u] >= 0) ? ORU_NAMES[serving_cell[u]] : "n/a");
    }
    manual_ts_frozen = 1;
    manual_ts_freeze_round = round;
    manual_ts_state = MTS_PENDING;
  }
  if (manual_ts_state == MTS_CONVERGED || manual_ts_state == MTS_TIMEOUT)
    return manual_ts_state;

  int all_conv = 1;
  for (int u = 0; u < NUM_UE; u++) {
    int tgt = manual_ts_target[u];
    if (tgt < 0) continue;
    if (manual_ts_ho_count[u] == 0) {
      /* "already there" only counts with a FRESH row, not a stale repeat */
      if (meas_fresh_since(u, manual_ts_anchor[u]) && serving_cell[u] == tgt) {
        manual_ts_confirmed[u] = 1;
        continue;
      }
      manual_ts_send_ts[u] = send_rc_ho_tagged(nodes, u, tgt, "MANUAL-TS");
      manual_ts_ho_count[u] = 1;
      manual_ts_sent_round[u] = round;
      all_conv = 0;
      continue;
    }
    if (manual_ts_confirmed[u]) continue;
    if (ho_confirmed_fresh(u, tgt, manual_ts_send_ts[u])) {
      manual_ts_confirmed[u] = 1;
      printf("[MANUAL-TS] HO confirmed IMSI=%d serving=%s (fresh measurement)\n",
             u + 1, ORU_NAMES[tgt]);
      continue;
    }
    all_conv = 0;
    if (manual_ts_ho_count[u] == 1 &&
        round >= manual_ts_freeze_round + MANUAL_TS_RETRY_AFTER &&
        round > manual_ts_sent_round[u]) {
      printf("[MANUAL-TS] retry: UE %d not confirmed, resending\n", u);
      manual_ts_send_ts[u] = send_rc_ho_tagged(nodes, u, tgt, "MANUAL-TS");
      manual_ts_ho_count[u] = 2;
      manual_ts_sent_round[u] = round;
    }
  }
  if (all_conv) {
    manual_ts_state = MTS_CONVERGED;
    printf("[MANUAL-TS] converged at round %d (all UEs serving==target, "
           "fresh)\n", round);
  } else if (round >= manual_ts_freeze_round + MANUAL_TS_DEADLINE) {
    manual_ts_state = MTS_TIMEOUT;
    printf("[MANUAL-TS] TIMEOUT — not confirmed by deadline (fail-closed):");
    for (int u = 0; u < NUM_UE; u++)
      if (manual_ts_target[u] >= 0 && !manual_ts_confirmed[u])
        printf(" UE%d", u);
    printf("\n");
  } else {
    manual_ts_state = MTS_PENDING;
  }
  return manual_ts_state;
}

/* NES evacuation confirmation state */
static uint64_t nes_send_ts[NUM_UE] = {0};
static int nes_evac_failed = 0;

static int ho_sent[NUM_UE] = {0};
static int sleep_sent = 0;
static int qos_sent = 0;
static int nes_round_count = 0; /* count NES rounds for sleep delay */
static int last_slept_cells[NUM_CELL]; /* cells actually slept this cycle */
static int n_last_slept = 0;
static int recovery_ho_sent[NUM_UE] = {0}; /* post-wake recovery HO one-shot */

/* Stage 3: Output Interpreter (Fig. 2) */
static void output_interpreter(const char *mode,
                               int assignment[NUM_UE],
                               int prev_assignment[NUM_UE],
                               int sleep_cells[],
                               int n_sleep,
                               e2_node_arr_xapp_t *nodes)
{
  printf("[Q-xApp] --- Stage 3: Output Interpreter ---\n");

  /* ho_sent moved to file scope */
  /* sleep_sent moved to file scope */
  /* qos_sent moved to file scope */
  /* This gate is for NES *evacuation* HO only. In ts mode the actual RC-HO is
   * driven separately by the INIT-TS (auto) / MANUAL-TS (manual) state
   * machines before this stage, so "no evacuation HO here" does NOT mean the
   * xApp is only previewing (R2.1). */
  if (strcmp(mode, "nes") != 0) {
    printf("[Q-xApp] No NES evacuation HO this stage (mode=%s; ts HO is "
           "handled by the INIT-TS/MANUAL-TS state machine)\n", mode);
    for (int i=0;i<NUM_UE;i++) ho_sent[i]=0;
    sleep_sent = 0;
    nes_round_count = 0;
    /* qos_sent reset moved to mode transition handler */
    goto skip_ho;
  }
  for (int u = 0; u < NUM_UE; u++) {
    int new_cell_idx = assignment[u];
    /* NES: only send HO for UEs actually serving on a sleeping cell (measured) */
    int ue_on_sleep = 0;
    for (int s = 0; s < n_sleep; s++) {
      if (serving_cell[u] >= 0 && CELL_IDS[serving_cell[u]] == sleep_cells[s]) {
        ue_on_sleep = 1; break;
      }
    }
    if (!ue_on_sleep) {
      printf("[Q-xApp] UE %d not on sleeping cell, skip HO\n", u);
      continue;
    }
    if (ho_sent[u]) { printf("[Q-xApp] UE %d HO already sent, skip\n", u); continue; }
    if (new_cell_idx == serving_cell[u]) {
      printf("[Q-xApp] UE %d already serving on %s, skip handover.\n", u, ORU_NAMES[new_cell_idx]);
      continue;
    }

    uint64_t imsi = (uint64_t)(u + 1);
    char target_cell_char = '0' + CELL_IDS[new_cell_idx];

    ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);

    rc_ctrl_req_data_t rc_ctrl = {0};
    rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, 3, HANDOVER_CONTROL_7_6_4_1);
    rc_ctrl.msg = gen_rc_ctrl_msg(FORMAT_1_E2SM_RC_CTRL_MSG, target_cell_char);

    int64_t st = time_now_us();
    printf("[Q-xApp] HO: UE %d (IMSI %lu) -> %s (char '%c')\n",
           u, imsi, ORU_NAMES[new_cell_idx], target_cell_char);

    /* Send HO only to LTE anchor (smallest nb_id = Cell 1) */
    {
      size_t lte_idx = 0;
      uint32_t min_id = UINT32_MAX;
      for (size_t ii = 0; ii < nodes->len; ii++) {
        if (nodes->n[ii].id.nb_id.nb_id < min_id) {
          min_id = nodes->n[ii].id.nb_id.nb_id;
          lte_idx = ii;
        }
      }
      printf("[Q-xApp]   Sending HO to LTE node[%zu] nb_id=%u\n", lte_idx, min_id);
      control_sm_xapp_api(&nodes->n[lte_idx].id, SM_RC_ID, &rc_ctrl);
      usleep(100000);
    }

    printf("[Q-xApp] HO latency for UE %d: %ld us\n", u, time_now_us() - st);
    free_rc_ctrl_req_data(&rc_ctrl);
    ho_sent[u] = 1;
    nes_send_ts[u] = meas_ts[u]; /* freshness anchor for evacuation confirm */
    usleep(500000);
  }
skip_ho:

  /* Evacuation confirmation before sleep (Codex INIT-TS review §4):
   * "ho_sent" is command-dispatched, NOT completed. Sleep only when a FRESH
   * measurement shows zero UEs actually serving on every sleep-target cell. */
  if (strcmp(mode, "nes") == 0 && !sleep_sent && !nes_evac_failed) {
    int evac_ok = 1;
    for (int u = 0; u < NUM_UE; u++) {
      if (!meas_valid[u]) { evac_ok = 0; break; }
      /* UEs we HO'd away need a row NEWER than the send */
      if (ho_sent[u] && meas_ts[u] <= nes_send_ts[u]) { evac_ok = 0; break; }
      for (int s = 0; s < n_sleep; s++) {
        if (serving_cell[u] >= 0 && CELL_IDS[serving_cell[u]] == sleep_cells[s]) {
          evac_ok = 0;
          break;
        }
      }
      if (!evac_ok) break;
    }
    if (!evac_ok) {
      if (nes_round_count + 1 >= AUTO_NES_ROUNDS) {
        printf("[NES] TIMEOUT — evacuation not confirmed by round %d, sleep withheld\n",
               nes_round_count + 1);
        nes_evac_failed = 1;
      } else {
        printf("[Q-xApp NES] evacuation not confirmed yet, defer sleep (nes_round=%d)\n",
               nes_round_count + 1);
      }
      nes_round_count++;
      goto skip_sleep;
    }
    printf("[NES] evacuation converged: sleep targets have 0 serving UEs (fresh)\n");
  }
  if (strcmp(mode, "nes") == 0 && nes_evac_failed) {
    goto skip_sleep; /* fail-closed: never sleep on unconfirmed evacuation */
  }

  /* QoS-RA mode: Send Radio_Bearer_Control (RC style=1) for each UE — once per cycle */
  if (strcmp(mode, "qos") == 0 && qos_sent) {
    printf("[Q-xApp QoS-RA] DRB weights already sent, skip\n");
  } else if (strcmp(mode, "qos") == 0 && !qos_sent) {
    printf("[Q-xApp QoS-RA] Sending Radio_Bearer_Control for %d UEs\n", NUM_UE);
    for (int u = 0; u < NUM_UE; u++) {
      uint64_t imsi = (uint64_t)(u + 1);
      int d = ue_drb_assignment[u];
      /* ctrl_act_id = DRB index + 1 (1-4) */
      uint16_t drb_act_id = (d >= 0) ? (uint16_t)(d + 1) : 1;
      char target_cell_char = '0' + CELL_IDS[assignment[u]];

      ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);
      rc_ctrl_req_data_t rc_ctrl = {0};
      rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, 1, drb_act_id);
      rc_ctrl.msg = gen_rc_ctrl_msg_drb(FORMAT_1_E2SM_RC_CTRL_MSG, target_cell_char);

      if (d >= 0) {
        printf("[Q-xApp QoS-RA] DRB: UE %d (IMSI %lu) -> DRB %d (5QI=%d, w=%.1f, act_id=%d)\n",
               u, imsi, drb_pool[d].drb_id, drb_pool[d].fiveqi,
               drb_pool[d].weight, drb_act_id);
      } else {
        printf("[Q-xApp QoS-RA] DRB: UE %d (IMSI %lu) -> unassigned (act_id=%d)\n",
               u, imsi, drb_act_id);
      }

      for (size_t i = 0; i < nodes->len; i++) {
        control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &rc_ctrl);
        usleep(100000);
      }

      free_rc_ctrl_req_data(&rc_ctrl);
      usleep(200000);
    }
    qos_sent = 1;
    printf("[Q-xApp QoS-RA] DRB weights sent (will not repeat)\n");
  }

  /* NES mode: Wake non-sleep cells, then sleep selected cells (RC style=300) */
  if (strcmp(mode, "nes") == 0 && sleep_sent) {
    printf("[Q-xApp NES] Sleep already sent this cycle, skip\n");

  } else if (strcmp(mode, "nes") == 0 && !sleep_sent) {
    /* evacuation already confirmed by the fresh-measurement gate above */
    /* Wake ALL cells that are NOT sleep targets */
    for (int c = 0; c < NUM_CELL; c++) {
      char wake_char = '0' + CELL_IDS[c];
      int is_sleep_target = 0;
      for (int s = 0; s < n_sleep; s++)
        if (sleep_cells[s] == CELL_IDS[c]) { is_sleep_target = 1; break; }
      if (is_sleep_target) continue;
      ue_id_e2sm_t wid = gen_rc_ue_id(GNB_UE_ID_E2SM, 1);
      rc_ctrl_req_data_t wctrl = {0};
      wctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, wid, 300, 2);
      wctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, wake_char);
      for (size_t i = 0; i < nodes->len; i++) {
        control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &wctrl);
        usleep(100000);
      }
      free_rc_ctrl_req_data(&wctrl);
    }
    /* Sleep selected cells */
    for (int s = 0; s < n_sleep; s++) {
      int sleep_cell_id = sleep_cells[s];
      char target_cell_char = '0' + sleep_cell_id;

      printf("[Q-xApp NES] Sending Energy_state SLEEP for cell ID=%d (char '%c')\n",
             sleep_cell_id, target_cell_char);

      ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, 0);

      rc_ctrl_req_data_t rc_ctrl = {0};
      rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, 300, 1);
      rc_ctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, target_cell_char);

      for (size_t i = 0; i < nodes->len; i++) {
        printf("[Q-xApp NES]   Sending Energy_state to node[%zu] nb_id=%u\n", i, nodes->n[i].id.nb_id.nb_id);
        control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &rc_ctrl);
        usleep(100000);
      }

      free_rc_ctrl_req_data(&rc_ctrl);
      usleep(200000);
    }
    sleep_sent = 1;
    n_last_slept = (n_sleep < NUM_CELL) ? n_sleep : NUM_CELL;
    for (int s = 0; s < n_last_slept; s++) last_slept_cells[s] = sleep_cells[s];
    printf("[Q-xApp NES] Sleep commands sent (will not repeat)\n");
  }
skip_sleep:

  /*
   * In NES, keep retrying changed handovers until the next mode transition.
   * The xApp only knows intended assignment here, not measured serving cell.
   */
  if (strcmp(mode, "nes") != 0) {
    for (int u = 0; u < NUM_UE; u++)
      prev_assignment[u] = assignment[u];
  }
}

/* =========================================================================
 * main
 * ========================================================================= */
int main(int argc, char *argv[])
{
  /* Validate an injected QXAPP_DATA_DIR before connecting to the RIC or
   * sending any control: a bad path must fail the process immediately. */
  qx_data_dir();

  fr_args_t args = init_fr_args(argc, argv);

  init_xapp_api(&args);
  sleep(1);

  struct sigaction sa;
  memset(&sa, 0, sizeof(sa));
  sa.sa_handler = sig_handler;
  sa.sa_flags   = SA_RESETHAND;
  sigaction(SIGINT,  &sa, NULL);
  sigaction(SIGTERM, &sa, NULL);

  e2_node_arr_xapp_t nodes;
  for (int retry = 0; retry < 30; retry++) {
    nodes = e2_nodes_xapp_api();
    if (nodes.len > 0) break;
    printf("[Q-xApp] Waiting for E2 nodes... (%d/30)\n", retry+1);
    sleep(3);
  }
  if (nodes.len == 0) { printf("[Q-xApp] No E2 nodes.\n"); return 1; }
  printf("[Q-xApp] Connected E2 nodes = %d\n", nodes.len);

  for (size_t i = 0; i < nodes.len; i++) {
    printf("[Q-xApp]   node[%zu] id.type=%d nb_id=%u\n",
           i, nodes.n[i].id.type, nodes.n[i].id.nb_id.nb_id);
  }

  /* Find LTE anchor node (smallest nb_id = Cell 1) for HO commands */
  size_t lte_node_idx = 0;
  uint32_t min_nb_id = UINT32_MAX;
  for (size_t ii = 0; ii < nodes.len; ii++) {
    if (nodes.n[ii].id.nb_id.nb_id < min_nb_id) {
      min_nb_id = nodes.n[ii].id.nb_id.nb_id;
      lte_node_idx = ii;
    }
  }
  printf("[Q-xApp] LTE anchor: node[%zu] nb_id=%u\n", lte_node_idx, min_nb_id);


  int prev_assignment[NUM_UE];
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
  
  char prev_mode[32] = "";

  /* Initialize DRB assignments */
  for (int u = 0; u < NUM_UE; u++) ue_drb_assignment[u] = -1;

  int round = 0;
  while (1) {
    round++;
    g_current_round = round;

    /* Read current mode */
    char mode[32];
    read_mode(mode, sizeof(mode));

    /* Auto mode: resolve effective mode from round number */
    int is_auto = (strcmp(mode, "auto") == 0);
    if (is_auto) {
      auto_resolve_mode(round, mode, sizeof(mode));
      {
        const char *segment = "TS";
        if (round > AUTO_TS_ROUNDS && round <= AUTO_TS_ROUNDS + AUTO_QOS_ROUNDS)
          segment = "TS + QoS-RA";
        else if (round > AUTO_TS_ROUNDS + AUTO_QOS_ROUNDS && round <= AUTO_TOTAL_ROUNDS)
          segment = "NES";
        else if (round > AUTO_TOTAL_ROUNDS)
          segment = "Idle";
        printf("[Q-xApp AUTO] Round %d -> %s (mode=%s)\n", round, segment, mode);
      }
      /* INIT-TS hard deadline: end of the fixed TS window. Past it without
       * convergence -> fail-closed (no QoS/NES control for the rest of the
       * run; the batch validator fails this attempt). */
      if (!init_ts_failed && !init_ts_converged && round > AUTO_TS_ROUNDS) {
        printf("[INIT-TS] TIMEOUT — mismatched:");
        for (int u = 0; u < NUM_UE; u++) {
          if (init_ts_target[u] >= 0 &&
              !(meas_valid[u] && serving_cell[u] == init_ts_target[u]))
            printf(" UE%d", u);
          if (!init_ts_frozen) { printf(" (never frozen)"); break; }
        }
        printf("\n");
        init_ts_failed = 1;
      }
      if (init_ts_failed) {
        printf("[INIT-TS] fail-closed: control suspended (round %d)\n", round);
        sleep(10);
        continue;
      }
      /* After single cycle completes, wake and restart */
      static int wake_sent = 0;
      if (round > AUTO_TOTAL_ROUNDS && !wake_sent) {
        wake_sent = 1;
        printf("[Q-xApp AUTO] Cycle complete. Sending wake for all cells...\n");
        for (int c = 0; c < NUM_CELL; c++) {
          char wake_char = '0' + CELL_IDS[c];
          ue_id_e2sm_t wid = gen_rc_ue_id(GNB_UE_ID_E2SM, 1);
          rc_ctrl_req_data_t wctrl = {0};
          wctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, wid, 300, 2);
          wctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, wake_char);
          for (size_t ii = 0; ii < nodes.len; ii++) {
            control_sm_xapp_api(&nodes.n[ii].id, SM_RC_ID, &wctrl);
            usleep(100000);
          }
          free_rc_ctrl_req_data(&wctrl);
        }
        printf("[Q-xApp AUTO] Wake commands sent.\n");
        /* Update energy for GUI */
        sleep(3);
        read_cell_energy();
        {
          int idle_assignment[NUM_UE];
          for (int u = 0; u < NUM_UE; u++) idle_assignment[u] = prev_assignment[u];
          int no_sleep[1] = {0};
          write_result_json_unified(idle_assignment, 0.0, "ts", NUM_CELL, no_sleep, 0);
        }
        /* Cycle complete — stay idle, don't repeat */
        printf("[Q-xApp AUTO] Cycle complete. Charts frozen.\n");
        sleep(10);
        continue;
      }
      /* Post-wake recovery TS (Codex-approved): one-shot re-assignment after wake.
       * Waits for awakened-cell measurements to become usable, recomputes TS,
       * and sends recovery RC-HO only for serving!=assignment UEs. */
      static int post_wake_ts_done = 0;
      static int recovery_wait_rounds = 0;
      static int post_wake_ho_done = 0;          /* HO send phase finished */
      static int rec_confirm_rounds = 0;
      static int rec_target[NUM_UE] = {-1, -1, -1, -1};
      static uint64_t rec_send_ts[NUM_UE] = {0};
      static int rec_confirmed[NUM_UE] = {0};
      static int rec_final_assign[NUM_UE] = {-1, -1, -1, -1};
      if (round > AUTO_TOTAL_ROUNDS && wake_sent && !post_wake_ts_done) {
        recovery_wait_rounds++;
        read_sinr_from_csv();
        read_cell_energy();
        /* Readiness: every slept cell must report a usable (>0 dB) link again */
        int ready = 1;
        for (int s = 0; s < n_last_slept; s++) {
          int cidx = -1;
          for (int c = 0; c < NUM_CELL; c++)
            if (CELL_IDS[c] == last_slept_cells[s]) cidx = c;
          double best = -1e9;
          if (cidx >= 0)
            for (int u = 0; u < NUM_UE; u++)
              if (sinr_matrix[u][cidx] > best) best = sinr_matrix[u][cidx];
          if (cidx < 0 || best <= 0.0) ready = 0;
        }
        printf("[POST-WAKE] wait round=%d, awakened-cell ready=%d\n",
               recovery_wait_rounds, ready);
        if (!ready && recovery_wait_rounds < 5) { sleep(10); continue; }
        printf("[POST-WAKE] rate matrix (SINR dB):\n");
        for (int u = 0; u < NUM_UE; u++) {
          printf("[POST-WAKE]   UE %d:", u);
          for (int c = 0; c < NUM_CELL; c++)
            printf(" %s=%.1f", ORU_NAMES[c], sinr_matrix[u][c]);
          printf(" serving=%d\n", serving_cell[u]);
        }
        if (!ready) {
          printf("[POST-WAKE] readiness timeout after %d rounds — abort recovery\n",
                 recovery_wait_rounds);
          g_cycle_finished = 1; g_cycle_status = "timeout";
          { int ns_fin[1] = {0}; write_result_json_unified(prev_assignment, 0.0, "ts", NUM_CELL, ns_fin, 0); }
          post_wake_ts_done = 1;
          sleep(10);
          continue;
        }
        if (!post_wake_ho_done) {
          /* TS recompute on fresh measurements (classical recovery, R2.3
           * choice B). The cap was validated at the TS-window gate, so this
           * is expected to succeed; on infeasibility abort the recovery
           * instead of publishing an over-cap assignment. */
          int rec_assign[NUM_UE];
          if (greedy_match(rec_assign) != 0) {
            printf("[POST-WAKE] recompute infeasible under cap %d — abort recovery\n",
                   a1_max_ue_per_cell);
            g_cycle_finished = 1; g_cycle_status = "infeasible";
            { int ns_fin[1] = {0}; write_result_json_unified(prev_assignment, 0.0, "ts", NUM_CELL, ns_fin, 0); }
            post_wake_ts_done = 1;
            sleep(10);
            continue;
          }
          for (int u = 0; u < NUM_UE; u++) rec_final_assign[u] = rec_assign[u]; /* preserve for final publish */
          for (int u = 0; u < NUM_UE; u++)
            printf("[POST-WAKE] UE %d measured serving=%s computed assignment=%s\n", u,
                   (serving_cell[u] >= 0) ? ORU_NAMES[serving_cell[u]] : "none",
                   (rec_assign[u] >= 0) ? ORU_NAMES[rec_assign[u]] : "none");
          /* Recovery RC-HO for mismatched UEs only (one-shot each, staggered) */
          for (int u = 0; u < NUM_UE; u++) {
            if (recovery_ho_sent[u]) continue;
            if (rec_assign[u] < 0 || serving_cell[u] < 0) continue;
            if (rec_assign[u] == serving_cell[u]) continue;
            rec_target[u] = rec_assign[u];
            rec_send_ts[u] = send_rc_ho_tagged(&nodes, u, rec_assign[u], "POST-WAKE");
            recovery_ho_sent[u] = 1;
          }
          /* refresh GUI with recovered assignment */
          {
            int no_sleep2[1] = {0};
            write_result_json_unified(rec_assign, 0.0, "ts", NUM_CELL, no_sleep2, 0);
          }
          post_wake_ho_done = 1;
          /* no-HO still goes to confirmation so stabilized power gets published */
          sleep(10);
          continue;
        }
        /* confirmation phase: complete only on FRESH serving==target rows
         * (Codex INIT-TS review §5 — "recovery complete" was send-complete) */
        rec_confirm_rounds++;
        int all_ok = 1;
        for (int u = 0; u < NUM_UE; u++) {
          if (!recovery_ho_sent[u] || rec_confirmed[u]) continue;
          if (ho_confirmed_fresh(u, rec_target[u], rec_send_ts[u])) {
            rec_confirmed[u] = 1;
            printf("[POST-WAKE] HO confirmed IMSI=%d serving=%s (fresh measurement)\n",
                   u + 1, ORU_NAMES[rec_target[u]]);
          } else {
            all_ok = 0;
          }
        }
        if (all_ok) {
          g_cycle_finished = 1; g_cycle_status = "complete";
          { int ns_fin[1] = {0}; write_result_json_unified(rec_final_assign, 0.0, "ts", NUM_CELL, ns_fin, 0); }
          post_wake_ts_done = 1;
          printf("[POST-WAKE] recovery complete, final power published\n");
        } else if (rec_confirm_rounds >= 5) {
          printf("[POST-WAKE] TIMEOUT — unconfirmed:");
          for (int u = 0; u < NUM_UE; u++)
            if (recovery_ho_sent[u] && !rec_confirmed[u]) printf(" IMSI=%d", u + 1);
          printf("\n");
          g_cycle_finished = 1; g_cycle_status = "timeout";
          { int ns_fin[1] = {0}; write_result_json_unified(rec_final_assign, 0.0, "ts", NUM_CELL, ns_fin, 0); }
          post_wake_ts_done = 1;
        }
        sleep(10);
        continue;
      }
      /* Recovery done — just idle */
      if (round > AUTO_TOTAL_ROUNDS && wake_sent && post_wake_ts_done) {
        printf("[Q-xApp AUTO] Round %d -> Idle (mode=ts)\n", round);
        printf("[Q-xApp AUTO] Cycle complete.\n");
        sleep(10);
        continue;
      }
    }

    /* Mode-entry side effects are now handled by the SINGLE effective-entry
     * detector below (Codex §16), so there is no separate mode-string
     * transition block here (which used to miss first-process entry and the
     * auto->manual boundary). prev_mode is still tracked for other uses. */
    strncpy(prev_mode, mode, sizeof(prev_mode));

    printf("===== Q-xApp Round %d [mode=%s] =====\n", round, mode);

    /* Fig. 2: Q-xApp Pipeline */
    int assignment[NUM_UE];
    int active_cells = NUM_CELL;
    int sleep_cells[NUM_CELL];
    int n_sleep = 0;

    use_case_encoder(mode);                                                        /* Stage 1 */
    read_quantum_config(); /* after Stage 1 so provenance sees this round's A1 policy */

    /* EARLY infeasible-policy gate (Codex §17/§18): runs BEFORE any entry side
     * effect, so an infeasible policy sends ZERO CONTROL-REQUEST — including
     * entry scheduler-reset / wake. For NES the forced-sleep cells reduce the
     * awake capacity, so the check uses awake_cells = NUM_CELL - n_forced_sleep
     * (use_case_encoder already read the sleep config this round). TS/QoS keep
     * all NUM_CELL awake. cap=1 (any mode) and cap=2 with 2 forced-sleep cells
     * both fail closed. Result JSON is all-unassigned, cycle_status=infeasible. */
    static int was_infeasible = 0;
    int awake_cells = (strcmp(mode, "nes") == 0)
                      ? (NUM_CELL - n_forced_sleep) : NUM_CELL;
    if (!assignment_feasible_awake(awake_cells)) {
      fprintf(stderr, "[Q-xApp] infeasible policy: %d UEs cannot fit %d awake "
                      "cells x cap %d — control suspended before any RC "
                      "(mode=%s)\n",
              NUM_UE, awake_cells, a1_max_ue_per_cell, mode);
      g_cycle_status = "infeasible";
      was_infeasible = 1;
      int no_sleep[1] = {0};
      int err_assign[NUM_UE];
      for (int u = 0; u < NUM_UE; u++) err_assign[u] = -1;
      write_result_json_unified(err_assign, 0.0, mode, NUM_CELL, no_sleep, 0);
      printf("[Q-xApp] Round %d complete (infeasible). [mode=%s]\n", round, mode);
      sleep(10);
      continue;
    }

    /* SINGLE effective-mode entry handler (Codex §16), runs ONLY for a feasible
     * policy so no entry RC precedes an infeasible fail-closed. An "entry" is
     * any change of (effective mode, is_auto) — including the FIRST process
     * round and the auto->manual boundary where the mode string does not
     * change. Every entry runs its side effects EXACTLY ONCE (no duplicate RC).
     *   ts  entry: manual target reset (manual only) + scheduler reset + wake all
     *   qos entry: freshness anchor (manual only) + qos_sent=0 + wake all
     *   nes entry: scheduler reset (prev_assignment kept for evacuation) */
    {
      static char prev_eff_mode[32] = "";
      static int prev_eff_is_auto = -1;
      int is_entry = (strcmp(prev_eff_mode, mode) != 0 ||
                      prev_eff_is_auto != is_auto);
      if (is_entry) {
        if (strcmp(mode, "ts") == 0) {
          printf("[Q-xApp] Mode entry: Traffic Steering (%s)\n",
                 is_auto ? "auto" : "manual");
          if (!is_auto) manual_ts_reset("manual-ts entry (effective mode)");
          entry_reset_scheduler_weights(&nodes);
          entry_wake_all_cells(&nodes);
          for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
        } else if (strcmp(mode, "qos") == 0) {
          printf("[Q-xApp] Mode entry: QoS-RA (%s)\n",
                 is_auto ? "auto" : "manual");
          qos_sent = 0;   /* DRB control gate: allow a real send this entry */
          if (!is_auto) {
            for (int u = 0; u < NUM_UE; u++) qos_meas_anchor[u] = meas_ts[u];
            printf("[Q-xApp QoS-RA] manual-qos entry: freshness anchor set\n");
          }
          entry_wake_all_cells(&nodes);
          for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
        } else if (strcmp(mode, "nes") == 0) {
          printf("[Q-xApp] Mode entry: NES\n");
          entry_reset_scheduler_weights(&nodes);
          /* keep prev_assignment: NES evacuation needs the serving cells */
        }
      }
      strncpy(prev_eff_mode, mode, sizeof(prev_eff_mode) - 1);
      prev_eff_mode[sizeof(prev_eff_mode) - 1] = 0;
      prev_eff_is_auto = is_auto;
    }
    if (was_infeasible) {
      /* cap recovered to a feasible value: clear the error state and force a
       * target recompute so we don't reuse a stale/frozen target (R2.6). */
      was_infeasible = 0;
      g_cycle_status = "running";
      manual_ts_reset("cap recovered to feasible");
      printf("[Q-xApp] policy feasible again (cap=%d) — status running, "
             "recomputing targets\n", a1_max_ue_per_cell);
    }

    /* Publish the live status from the FIRST successful decision's JSON, not a
     * round late (Codex §15-2). assignment_algorithm writes the result JSON
     * for qos/nes/auto-ts, so g_cycle_status must already be "running" when it
     * does. Set it optimistically here and overwrite it below if Stage 2
     * withholds control. (manual ts suppresses that JSON and sets its own
     * honest status after the state machine runs.) */
    if (!(!is_auto && strcmp(mode, "ts") == 0))
      g_cycle_status = "running";

    int stage2_rc = assignment_algorithm(mode, is_auto, assignment,
                                         &active_cells, sleep_cells,
                                         &n_sleep);                  /* Stage 2 */
    if (stage2_rc != STAGE2_OK) {
      /* Distinguish transient not-ready (pending) from math infeasibility
       * (error). Either way send no RC control and publish an explicit,
       * non-success status so the GUI never shows a stale success (blocker 4). */
      const char *st = (stage2_rc == STAGE2_NOT_READY) ? "pending" : "infeasible";
      if (stage2_rc == STAGE2_INFEASIBLE) was_infeasible = 1;
      fprintf(stderr, "[Q-xApp] Stage 2 withheld control (mode=%s, status=%s)\n",
              mode, st);
      g_cycle_status = st;
      int no_sleep[1] = {0};
      int err_assign[NUM_UE];
      for (int u = 0; u < NUM_UE; u++) err_assign[u] = -1;
      write_result_json_unified(err_assign, 0.0, mode, NUM_CELL, no_sleep, 0);
      printf("[Q-xApp] Round %d complete (%s). [mode=%s]\n", round, st, mode);
      sleep(10);
      continue;
    }

    if (is_auto && strcmp(mode, "ts") == 0 && round <= AUTO_TS_ROUNDS && !init_ts_failed) {
      /* INIT-TS enforcement (auto TS window only). */
      init_ts_enforce(assignment, &nodes, round);
      output_interpreter(mode, assignment, prev_assignment, sleep_cells, n_sleep, &nodes);
    } else if (!is_auto && strcmp(mode, "ts") == 0) {
      /* MANUAL-TS (R2.1/2.4): the intended target from Stage 2 was NOT
       * published (assignment_algorithm suppresses the ts JSON in manual
       * mode); we publish the ACHIEVED serving state with an honest status
       * and only run the output interpreter once converged. */
      int mts = manual_ts_enforce(assignment, &nodes, round);
      int serving_assign[NUM_UE];
      double serving_rate = 0.0;
      for (int u = 0; u < NUM_UE; u++) {
        serving_assign[u] = (meas_valid[u] ? serving_cell[u] : -1);
        if (serving_assign[u] >= 0)
          serving_rate += sinr_matrix[u][serving_assign[u]];
      }
      int no_sleep[1] = {0};
      if (mts == MTS_CONVERGED) {
        g_cycle_status = "running";
        output_interpreter(mode, assignment, prev_assignment, sleep_cells, n_sleep, &nodes);
        /* publish the ACHIEVED serving state with the running status (the
         * intended-target JSON was suppressed; output_interpreter itself does
         * not write the result JSON in ts mode) */
        write_result_json_unified(serving_assign, serving_rate, mode, NUM_CELL, no_sleep, 0);
      } else if (mts == MTS_TIMEOUT) {
        g_cycle_status = "error";
        fprintf(stderr, "[MANUAL-TS] fail-closed: publishing serving state, "
                        "no further RC control this event\n");
        write_result_json_unified(serving_assign, serving_rate, mode, NUM_CELL, no_sleep, 0);
      } else {
        /* WAIT/PENDING: do not present the intended target as achieved */
        g_cycle_status = "pending";
        write_result_json_unified(serving_assign, serving_rate, mode, NUM_CELL, no_sleep, 0);
      }
    } else {
      output_interpreter(mode, assignment, prev_assignment, sleep_cells, n_sleep, &nodes); /* Stage 3 */
    }

    printf("[Q-xApp] Round %d complete. [mode=%s]\n", round, mode);
    sleep(10);
  } /* end while loop */

  return 0;
}
