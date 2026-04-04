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

static double qos_weights[NUM_UE] = {2.0, 2.0, 1.0, 1.0};

/* == DRB pool definition for QoS-based Resource Allocation ================ */
#define NUM_DRB 4
#define MAX_GBR_PRB_RATIO 0.6

typedef struct {
    int drb_id;
    int fiveqi;
    int is_gbr;
    double prb_reserve;
    double priority;
    double gbr_kbps;
} drb_profile_t;

static drb_profile_t drb_pool[NUM_DRB] = {
    {1, 2, 1, 0.4, 4.0, 50000},
    {2, 4, 1, 0.2, 3.0, 30000},
    {3, 7, 0, 0.0, 2.0, 0},
    {4, 9, 0, 0.0, 1.0, 0},
};

static int ue_drb_assignment[NUM_UE]; /* each UE assigned DRB index (0-3), -1=unassigned */

/* read QoS weight config from file */
static void read_qos_config(void)
{
  FILE *fp = fopen(QOS_CONFIG_FILE, "r");
  if (!fp) {
    printf("[Q-xApp QoS-RA] Config file not found, using defaults\n");
    qos_weights[0] = 2.0; qos_weights[1] = 2.0;
    qos_weights[2] = 1.0; qos_weights[3] = 1.0;
    return;
  }
  char buf[256];
  if (fgets(buf, sizeof(buf), fp)) {
    int idx = 0;
    char *p = buf;
    while (*p && idx < NUM_UE) {
      qos_weights[idx++] = atof(p);
      char *comma = strchr(p, ',');
      if (!comma) break;
      p = comma + 1;
    }
  }
  fclose(fp);
  printf("[Q-xApp QoS-RA] Loaded weights:");
  for (int u = 0; u < NUM_UE; u++) printf(" %.1f", qos_weights[u]);
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
    if (cell_load[c] >= MAX_UE_PER_CELL) continue;
    assignment[u] = c;
    assigned[u] = 1;
    cell_load[c]++;
  }

  for (int u = 0; u < NUM_UE; u++) {
    if (assignment[u] >= 0) continue;
    int best = -1;
    for (int c = 0; c < NUM_CELL; c++) {
      if (cell_load[c] >= MAX_UE_PER_CELL) continue;
      if (best < 0 || cell_load[c] < cell_load[best]) best = c;
    }
    if (best < 0) best = 0;
    assignment[u] = best;
    cell_load[best]++;
  }
}

/* QoS-based Resource Allocation: DRB matching per cell
 * Step 1: greedy_match() for UE-Cell assignment
 * Step 2: intra-cell DRB matching with 2x4 utility matrix */
static void qos_drb_match(int assignment[NUM_UE])
{
  /* Step 1: UE-Cell assignment using greedy */
  greedy_match(assignment);

  /* Initialize DRB assignments to unassigned */
  for (int u = 0; u < NUM_UE; u++)
    ue_drb_assignment[u] = -1;

  /* Step 2: For each cell, do intra-cell DRB matching */
  for (int c = 0; c < NUM_CELL; c++) {
    /* Find UEs assigned to this cell */
    int cell_ues[NUM_UE];
    int n_cell_ues = 0;
    for (int u = 0; u < NUM_UE; u++) {
      if (assignment[u] == c)
        cell_ues[n_cell_ues++] = u;
    }
    if (n_cell_ues == 0) continue;

    /* Compute utility matrix: n_cell_ues x NUM_DRB */
    double utility[NUM_UE][NUM_DRB];
    memset(utility, 0, sizeof(utility));

    double sinr_rate_arr[NUM_UE];
    for (int i = 0; i < n_cell_ues; i++) {
      int u = cell_ues[i];
      sinr_rate_arr[i] = sinr_matrix[u][c];
    }

    printf("[Q-xApp QoS-RA] Cell %s: %d UEs, utility matrix:\n", ORU_NAMES[c], n_cell_ues);
    printf("         ");
    for (int d = 0; d < NUM_DRB; d++) printf("DRB%-6d", drb_pool[d].drb_id);
    printf("\n");

    for (int i = 0; i < n_cell_ues; i++) {
      int u = cell_ues[i];
      double sr = sinr_rate_arr[i];
      printf("  UE %d:  ", u);
      for (int d = 0; d < NUM_DRB; d++) {
        if (drb_pool[d].is_gbr) {
          /* GBR DRB: utility = gbr_kbps * min(1.0, sinr_rate / 15.0) */
          double sinr_eff = sr / 15.0;
          if (sinr_eff > 1.0) sinr_eff = 1.0;
          utility[i][d] = drb_pool[d].gbr_kbps * sinr_eff;
        } else {
          /* NGBR DRB: utility = priority * sinr_rate */
          utility[i][d] = drb_pool[d].priority * sr;
        }
        printf("%-9.1f", utility[i][d]);
      }
      printf("\n");
    }

    /* Greedy matching: sort all (UE, DRB) pairs by utility descending */
    typedef struct { double util; int ue_local; int drb; } pair_t;
    pair_t pairs[NUM_UE * NUM_DRB];
    int np = 0;
    for (int i = 0; i < n_cell_ues; i++)
      for (int d = 0; d < NUM_DRB; d++) {
        pairs[np].util = utility[i][d];
        pairs[np].ue_local = i;
        pairs[np].drb = d;
        np++;
      }

    for (int i = 0; i < np - 1; i++)
      for (int j = i + 1; j < np; j++)
        if (pairs[j].util > pairs[i].util) {
          pair_t tmp = pairs[i]; pairs[i] = pairs[j]; pairs[j] = tmp;
        }

    /* Assign: each DRB to at most 1 UE, GBR PRB sum <= MAX_GBR_PRB_RATIO */
    int ue_assigned[NUM_UE];
    memset(ue_assigned, 0, sizeof(ue_assigned));
    int drb_assigned[NUM_DRB];
    memset(drb_assigned, 0, sizeof(drb_assigned));
    double gbr_prb_sum = 0.0;

    for (int k = 0; k < np; k++) {
      int i = pairs[k].ue_local;
      int d = pairs[k].drb;
      if (ue_assigned[i] || drb_assigned[d]) continue;
      /* Check GBR PRB constraint */
      if (drb_pool[d].is_gbr) {
        if (gbr_prb_sum + drb_pool[d].prb_reserve > MAX_GBR_PRB_RATIO)
          continue;
        gbr_prb_sum += drb_pool[d].prb_reserve;
      }
      int u = cell_ues[i];
      ue_drb_assignment[u] = d;
      ue_assigned[i] = 1;
      drb_assigned[d] = 1;
      printf("[Q-xApp QoS-RA]   UE %d -> DRB %d (5QI=%d, %s, util=%.1f)\n",
             u, drb_pool[d].drb_id, drb_pool[d].fiveqi,
             drb_pool[d].is_gbr ? "GBR" : "NGBR", pairs[k].util);
    }

    /* Fallback: any unassigned UE gets first available DRB */
    for (int i = 0; i < n_cell_ues; i++) {
      if (ue_assigned[i]) continue;
      for (int d = 0; d < NUM_DRB; d++) {
        if (drb_assigned[d]) continue;
        if (drb_pool[d].is_gbr && gbr_prb_sum + drb_pool[d].prb_reserve > MAX_GBR_PRB_RATIO)
          continue;
        int u = cell_ues[i];
        ue_drb_assignment[u] = d;
        ue_assigned[i] = 1;
        drb_assigned[d] = 1;
        if (drb_pool[d].is_gbr) gbr_prb_sum += drb_pool[d].prb_reserve;
        printf("[Q-xApp QoS-RA]   UE %d -> DRB %d (fallback)\n", u, drb_pool[d].drb_id);
        break;
      }
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
    for (int i = 0; i < nc && cell_load[c] < MAX_UE_PER_CELL; i++) {
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
      if (cell_load[c] >= MAX_UE_PER_CELL) continue;
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
static double cell_energy[NUM_CELL]; /* delta joules per round */
static double prev_net_energy[NUM_CELL] = {0};
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

static double read_net_energy_from_csv(int cell_id)
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
  return atof(comma1 + 1); /* NetEnergy */
}

static void read_cell_energy(void)
{
  for (int c = 0; c < NUM_CELL; c++) {
    double net = read_net_energy_from_csv(CELL_IDS[c]);
    if (!energy_initialized) {
      cell_energy[c] = 0.0;
      prev_net_energy[c] = net;
    } else {
      cell_energy[c] = net - prev_net_energy[c];
      if (cell_energy[c] < 0) cell_energy[c] = 0;
      prev_net_energy[c] = net;
    }
  }
  energy_initialized = 1;
}

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
  fprintf(fp, "  \"total_rate_bps_hz\": %.4f,\n", total_rate);
  if (strcmp(mode, "nes") == 0) {
    fprintf(fp, "  \"active_cells\": %d,\n", active_cells);
    fprintf(fp, "  \"sleep_cells\": [");
    for (int i = 0; i < n_sleep; i++)
      fprintf(fp, "%d%s", sleep_cells[i], i < n_sleep - 1 ? ", " : "");
    fprintf(fp, "],\n");
  }
  if (strcmp(mode, "qos") == 0) {
    fprintf(fp, "  \"qos_weights\": [");
    for (int u = 0; u < NUM_UE; u++)
      fprintf(fp, "%.1f%s", qos_weights[u], u < NUM_UE - 1 ? ", " : "");
    fprintf(fp, "],\n");
    fprintf(fp, "  \"drb_assignment\": [\n");
    for (int u = 0; u < NUM_UE; u++) {
      int d = ue_drb_assignment[u];
      if (d >= 0 && d < NUM_DRB) {
        fprintf(fp, "    {\"ue\": %d, \"drb\": %d, \"fiveqi\": %d, \"type\": \"%s\", \"gbr_kbps\": %.0f}%s\n",
                u, drb_pool[d].drb_id, drb_pool[d].fiveqi,
                drb_pool[d].is_gbr ? "GBR" : "NGBR",
                drb_pool[d].gbr_kbps,
                u < NUM_UE - 1 ? "," : "");
      } else {
        fprintf(fp, "    {\"ue\": %d, \"drb\": -1, \"fiveqi\": 0, \"type\": \"NONE\", \"gbr_kbps\": 0}%s\n",
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
  fprintf(fp, "}\n}\n");
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
  if (strcmp(mode_buf, "ts") != 0 && strcmp(mode_buf, "nes") != 0 && strcmp(mode_buf, "qos") != 0) {
    strncpy(mode_buf, "ts", buf_sz);
  }
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
    printf("  (serving: %s)\n", ORU_NAMES[serving_cell[u]]);
  }

  /* Mode-specific configuration */
  if (strcmp(mode, "ts") == 0) {
    read_a1_policy();
    printf("[Q-xApp] A1 policy: max_ue_per_cell=%d\n", a1_max_ue_per_cell);
  } else if (strcmp(mode, "qos") == 0) {
    read_qos_config();
    read_a1_policy();
    printf("[Q-xApp QoS-RA] Weights:");
    for (int u = 0; u < NUM_UE; u++) printf(" UE%d=%.1f", u, qos_weights[u]);
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
    qos_drb_match(assignment);
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
      printf("  UE %d -> %s (rate=%.4f bps/Hz, DRB=%d, 5QI=%d, %s)\n",
             u, ORU_NAMES[assignment[u]], sinr_matrix[u][assignment[u]],
             drb_pool[d].drb_id, drb_pool[d].fiveqi,
             drb_pool[d].is_gbr ? "GBR" : "NGBR");
    } else {
      printf("  UE %d -> %s (rate=%.4f bps/Hz)\n",
             u, ORU_NAMES[assignment[u]], sinr_matrix[u][assignment[u]]);
    }
  }

  /* Write result JSON */
  write_result_json_unified(assignment, total_rate, mode, *active_cells, sleep_cells, *n_sleep);
}

/* Stage 3: Output Interpreter (Fig. 2) */
static void output_interpreter(const char *mode,
                               int assignment[NUM_UE],
                               int prev_assignment[NUM_UE],
                               int sleep_cells[],
                               int n_sleep,
                               e2_node_arr_xapp_t *nodes)
{
  printf("[Q-xApp] --- Stage 3: Output Interpreter ---\n");

  /* Send handover commands (RC style=3) for changed UEs */
  for (int u = 0; u < NUM_UE; u++) {
    int new_cell_idx = assignment[u];
    if (new_cell_idx == prev_assignment[u]) {
      printf("[Q-xApp] UE %d already on %s, skip handover.\n", u, ORU_NAMES[new_cell_idx]);
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

    for (size_t i = 1; i < nodes->len; i++) {
      printf("[Q-xApp]   Sending HO to node[%zu] nb_id=%u\n", i, nodes->n[i].id.nb_id.nb_id);
      control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &rc_ctrl);
    }

    printf("[Q-xApp] HO latency for UE %d: %ld us\n", u, time_now_us() - st);
    free_rc_ctrl_req_data(&rc_ctrl);
    usleep(500000);
  }

  /* QoS-RA mode: Send Radio_Bearer_Control (RC style=1) for each UE */
  if (strcmp(mode, "qos") == 0) {
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
        printf("[Q-xApp QoS-RA] DRB: UE %d (IMSI %lu) -> DRB %d (5QI=%d, %s, act_id=%d)\n",
               u, imsi, drb_pool[d].drb_id, drb_pool[d].fiveqi,
               drb_pool[d].is_gbr ? "GBR" : "NGBR", drb_act_id);
      } else {
        printf("[Q-xApp QoS-RA] DRB: UE %d (IMSI %lu) -> unassigned (act_id=%d)\n",
               u, imsi, drb_act_id);
      }

      for (size_t i = 1; i < nodes->len; i++) {
        control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &rc_ctrl);
      }

      free_rc_ctrl_req_data(&rc_ctrl);
      usleep(200000);
    }
  }

  /* NES mode: Wake non-sleep cells, then sleep selected cells (RC style=300) */
  if (strcmp(mode, "nes") == 0) {
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
      for (size_t i = 1; i < nodes->len; i++)
        control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &wctrl);
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

      for (size_t i = 1; i < nodes->len; i++) {
        printf("[Q-xApp NES]   Sending Energy_state to node[%zu] nb_id=%u\n", i, nodes->n[i].id.nb_id.nb_id);
        control_sm_xapp_api(&nodes->n[i].id, SM_RC_ID, &rc_ctrl);
      }

      free_rc_ctrl_req_data(&rc_ctrl);
      usleep(200000);
    }
  }

  /* Update prev_assignment */
  for (int u = 0; u < NUM_UE; u++)
    prev_assignment[u] = assignment[u];
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
    sleep(2);
  }
  if (nodes.len == 0) { printf("[Q-xApp] No E2 nodes.\n"); return 1; }
  printf("[Q-xApp] Connected E2 nodes = %d\n", nodes.len);

  for (size_t i = 0; i < nodes.len; i++) {
    printf("[Q-xApp]   node[%zu] id.type=%d nb_id=%u\n",
           i, nodes.n[i].id.type, nodes.n[i].id.nb_id.nb_id);
  }

  int prev_assignment[NUM_UE];
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
  int round = 0;
  char prev_mode[32] = "";

  /* Initialize DRB assignments */
  for (int u = 0; u < NUM_UE; u++) ue_drb_assignment[u] = -1;

  while (1) {
    round++;

    /* Read current mode */
    char mode[32];
    read_mode(mode, sizeof(mode));

    /* Handle mode transitions */
    if (strcmp(mode, prev_mode) != 0) {
      if (strcmp(mode, "nes") == 0) {
        printf("[Q-xApp] Mode switched to: NES\n");
      /* Reset all UE scheduling weights to default */
      for (int u = 0; u < NUM_UE; u++) {
        uint64_t imsi = (uint64_t)(u + 1);
        ue_id_e2sm_t rst_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);
        rc_ctrl_req_data_t rst_ctrl = {0};
        rst_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, rst_ue_id, 1, 4);
        rst_ctrl.msg = gen_rc_ctrl_msg_drb(FORMAT_1_E2SM_RC_CTRL_MSG, '2');
        for (size_t i = 1; i < nodes.len; i++)
          control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rst_ctrl);
        free_rc_ctrl_req_data(&rst_ctrl);
      }
      } else if (strcmp(mode, "qos") == 0) {
        printf("[Q-xApp] Mode switched to: QoS-based Resource Allocation\n");
        /* Wake up ALL cells when switching to QoS-RA */
        printf("[Q-xApp] Waking up all cells...\n");
        for (int c = 0; c < NUM_CELL; c++) {
          char wake_cell = '0' + CELL_IDS[c];
          ue_id_e2sm_t wake_ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, 1);
          rc_ctrl_req_data_t wake_ctrl = {0};
          wake_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, wake_ue_id, 300, 2);
          wake_ctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, wake_cell);
          for (size_t i = 1; i < nodes.len; i++) {
            control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &wake_ctrl);
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
          for (size_t i = 1; i < nodes.len; i++)
            control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rst_ctrl);
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
          for (size_t i = 1; i < nodes.len; i++) {
            control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &wake_ctrl);
          }
          free_rc_ctrl_req_data(&wake_ctrl);
          printf("[Q-xApp] Wake up cell ID=%d\n", CELL_IDS[c]);
        }
      }
      strncpy(prev_mode, mode, sizeof(prev_mode));
      /* Reset prev_assignment on mode change to force re-evaluation */
      for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
    }

    printf("===== Q-xApp Round %d [mode=%s] =====\n", round, mode);

    /* Fig. 2: Q-xApp Pipeline */
    int assignment[NUM_UE];
    int active_cells = NUM_CELL;
    int sleep_cells[NUM_CELL];
    int n_sleep = 0;

    use_case_encoder(mode);                                                        /* Stage 1 */
    assignment_algorithm(mode, assignment, &active_cells, sleep_cells, &n_sleep);  /* Stage 2 */
    output_interpreter(mode, assignment, prev_assignment, sleep_cells, n_sleep, &nodes); /* Stage 3 */

    printf("[Q-xApp] Round %d complete. [mode=%s]\n", round, mode);
    sleep(5);
  } /* end while loop */

  return 0;
}
