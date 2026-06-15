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

#define MODE_FILE "/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/xapp_mode.txt"
#define SLEEP_CONFIG_FILE "/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/xapp_sleep_config.txt"
#define A1_POLICY_FILE "/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/xapp_a1_policy.txt"
#define QOS_CONFIG_FILE "/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/xapp_qos_config.txt"

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

/* Cell-specific DRB availability: each cell offers 3 of 4 DRBs */
static int cell_drb_avail[NUM_CELL][NUM_DRB] = {
    {1, 0, 1, 1},  /* O-RU 1: DRB 1(5QI=2), DRB 3(5QI=7), DRB 4(5QI=9) */
    {0, 1, 1, 1},  /* O-RU 2: DRB 2(5QI=4), DRB 3(5QI=7), DRB 4(5QI=9) */
    {1, 1, 1, 0},  /* O-RU 3: DRB 1(5QI=2), DRB 2(5QI=4), DRB 3(5QI=7) */
};

static int ue_drb_assignment[NUM_UE]; /* each UE assigned DRB index (0-3), -1=unassigned */
static int ue_fiveqi[NUM_UE] = {2, 4, 7, 9}; /* per-UE 5QI requirement (from GUI) */

/* read QoS config: per-UE 5QI values from file */
static void read_qos_config(void)
{
  FILE *fp = fopen(QOS_CONFIG_FILE, "r");
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

/* greedy matching: assign UEs to cells maximising total rate */
static void greedy_match(int assignment[NUM_UE])
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
    if (best < 0) best = 0;
    assignment[u] = best;
    cell_load[best]++;
  }
}

/* QoS-based Resource Allocation: UE-DRB matching (independent from UE-Cell)
 * Runs as a separate xApp alongside TS (greedy_match).
 * Each UE has a 5QI requirement; each cell offers a subset of DRBs.
 * Utility = match quality between UE 5QI and DRB 5QI × SINR. */
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

/* energy-aware matching: pack UEs into minimum cells */
static void energy_aware_match(int assignment[NUM_UE],
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

  for (int ci = 0; ci < NUM_CELL; ci++) {
    int c = caps[ci].cell_idx;
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
      if (cell_load[c] >= a1_max_ue_per_cell) continue;
      if (best < 0 || cell_load[c] < cell_load[best]) best = c;
    }
    if (best < 0) best = 0;
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
}



/* read sleep config from file: comma-separated cell IDs */

static void read_sleep_config(void)
{
  n_forced_sleep = 0;
  FILE *fp = fopen(SLEEP_CONFIG_FILE, "r");
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
  FILE *fp = fopen(A1_POLICY_FILE, "r");
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
  sprintf(path, "%s/energyfilecell%d.csv", CSV_DIR, cell_id);
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
  FILE *fp = fopen(RESULT_JSON, "w");
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
  fclose(fp);
  printf("[Q-xApp] Result written to %s\n", RESULT_JSON);
}

/* read mode from file */
static void read_mode(char *mode_buf, size_t buf_sz)
{
  FILE *fp = fopen(MODE_FILE, "r");
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

/* Stage 2: Quantum Assignment Algorithm (Fig. 2) */
static void assignment_algorithm(const char *mode,
                                 int assignment[NUM_UE],
                                 int *active_cells,
                                 int sleep_cells[],
                                 int *n_sleep)
{
  printf("[Q-xApp] --- Stage 2: Quantum Assignment Algorithm ---\n");

  if (strcmp(mode, "nes") == 0) {
    energy_aware_match(assignment, active_cells, sleep_cells, n_sleep);
  } else if (strcmp(mode, "qos") == 0) {
    /* QoS must not re-steer: use the converged frozen INIT-TS assignment.
     * Re-running greedy_match here could diverge from actual serving state
     * mid-cycle (Codex INIT-TS review §3). Manual qos mode (no INIT-TS)
     * falls back to greedy. */
    if (init_ts_converged) {
      for (int u = 0; u < NUM_UE; u++) assignment[u] = init_ts_target[u];
      printf("[Q-xApp QoS-RA] Using frozen INIT-TS assignment\n");
    } else {
      greedy_match(assignment);
    }
    /* QoS-RA xApp: UE-DRB assignment (runs in parallel with TS) */
    drb_match(assignment);
    *active_cells = NUM_CELL;
    *n_sleep = 0;
  } else {
    greedy_match(assignment);
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
    printf("[Q-xApp TS] Greedy assignment (total rate=%.4f bps/Hz):\n", total_rate);
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

  /* Write result JSON */
  write_result_json_unified(assignment, total_rate, mode, *active_cells, sleep_cells, *n_sleep);
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
  /* RC HO only in NES mode, only for UEs on sleeping cells */
  if (strcmp(mode, "nes") != 0) {
    printf("[Q-xApp] Skipping RC HO (not NES mode)\n");
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
          /* TS recompute on fresh measurements */
          int rec_assign[NUM_UE];
          greedy_match(rec_assign);
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

    /* Handle mode transitions — skip on initial startup (prev_mode empty) */
    if (strcmp(mode, prev_mode) != 0 && strlen(prev_mode) > 0) {
      if (strcmp(mode, "nes") == 0) {
        printf("[Q-xApp] Mode switched to: NES\n");
      /* Reset all UE scheduling weights to default */
      for (int u = 0; u < NUM_UE; u++) {
        uint64_t imsi = (uint64_t)(u + 1);
        ue_id_e2sm_t rst_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);
        rc_ctrl_req_data_t rst_ctrl = {0};
        rst_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, rst_ue_id, 1, 4);
        rst_ctrl.msg = gen_rc_ctrl_msg_drb(FORMAT_1_E2SM_RC_CTRL_MSG, '2');
        for (size_t i = 0; i < nodes.len; i++) {
          control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rst_ctrl);
          usleep(100000);
        }
        free_rc_ctrl_req_data(&rst_ctrl);
      }
      } else if (strcmp(mode, "qos") == 0) {
        printf("[Q-xApp] Mode switched to: QoS-based Resource Allocation\n");
        qos_sent = 0; /* reset gate on QoS entry */
        /* Wake up ALL cells when switching to QoS-RA */
        printf("[Q-xApp] Waking up all cells...\n");
        for (int c = 0; c < NUM_CELL; c++) {
          char wake_cell = '0' + CELL_IDS[c];
          ue_id_e2sm_t wake_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, 1);
          rc_ctrl_req_data_t wake_ctrl = {0};
          wake_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, wake_ue_id, 300, 2);
          wake_ctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, wake_cell);
          for (size_t i = 0; i < nodes.len; i++) {
            control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &wake_ctrl);
            usleep(100000);
          }
          free_rc_ctrl_req_data(&wake_ctrl);
          printf("[Q-xApp] Wake up cell ID=%d\n", CELL_IDS[c]);
        }
      } else {
        printf("[Q-xApp] Mode switched to: Traffic Steering\n");
        /* Reset all UE scheduling weights to default */
        printf("[Q-xApp] Resetting all UE scheduling weights...\n");
        for (int u = 0; u < NUM_UE; u++) {
          uint64_t imsi = (uint64_t)(u + 1);
          ue_id_e2sm_t rst_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);
          rc_ctrl_req_data_t rst_ctrl = {0};
          rst_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, rst_ue_id, 1, 4);
          rst_ctrl.msg = gen_rc_ctrl_msg_drb(FORMAT_1_E2SM_RC_CTRL_MSG, '2');
          for (size_t i = 0; i < nodes.len; i++) {
            control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rst_ctrl);
            usleep(100000);
          }
          free_rc_ctrl_req_data(&rst_ctrl);
        }
        /* Wake up ALL cells when switching to TS */
        printf("[Q-xApp] Waking up all cells...\n");
        for (int c = 0; c < NUM_CELL; c++) {
          char wake_cell = '0' + CELL_IDS[c];
          ue_id_e2sm_t wake_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, 1);
          rc_ctrl_req_data_t wake_ctrl = {0};
          wake_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, wake_ue_id, 300, 2);
          wake_ctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, wake_cell);
          for (size_t i = 0; i < nodes.len; i++) {
            control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &wake_ctrl);
            usleep(100000);
          }
          free_rc_ctrl_req_data(&wake_ctrl);
          printf("[Q-xApp] Wake up cell ID=%d\n", CELL_IDS[c]);
        }
      }
      /*
       * Keep the previous TS/QoS serving assignment when entering NES.
       * NES HO uses prev_assignment to identify UEs that are currently on
       * the cell that will sleep; resetting it here makes every NES HO skip.
       */
      if (strcmp(mode, "nes") != 0) {
        for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
      }
    }
    /* Always update prev_mode (outside transition block so it works on first round too) */
    strncpy(prev_mode, mode, sizeof(prev_mode));

    printf("===== Q-xApp Round %d [mode=%s] =====\n", round, mode);

    /* Fig. 2: Q-xApp Pipeline */
    int assignment[NUM_UE];
    int active_cells = NUM_CELL;
    int sleep_cells[NUM_CELL];
    int n_sleep = 0;

    use_case_encoder(mode);                                                        /* Stage 1 */
    assignment_algorithm(mode, assignment, &active_cells, sleep_cells, &n_sleep);  /* Stage 2 */
    /* INIT-TS enforcement (auto TS window only): freeze the computed
     * assignment once and drive ACTUAL serving state to it before QoS. */
    if (is_auto && strcmp(mode, "ts") == 0 && round <= AUTO_TS_ROUNDS && !init_ts_failed)
      init_ts_enforce(assignment, &nodes, round);
    output_interpreter(mode, assignment, prev_assignment, sleep_cells, n_sleep, &nodes); /* Stage 3 */

    printf("[Q-xApp] Round %d complete. [mode=%s]\n", round, mode);
    sleep(10);
  } /* end while loop */

  return 0;
}
