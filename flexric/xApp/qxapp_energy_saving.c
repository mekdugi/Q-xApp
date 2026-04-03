/*
 * Q-xApp Energy Saving Controller
 * Energy-aware UE-Cell assignment: minimize active cells
 * Uses RC Control with ric_style_type=300 for Energy_state
 */

#include "qxapp_common.h"

/* ── energy-aware matching: pack UEs into minimum cells ───────── */
static void energy_aware_match(int assignment[NUM_UE],
                               int *out_active, int sleep_cells[], int *out_n_sleep)
{
  /* Step 1: Calculate total rate capacity per cell (sum of rates for all UEs) */
  typedef struct { double capacity; int cell_idx; } cell_cap_t;
  cell_cap_t caps[NUM_CELL];
  for (int c = 0; c < NUM_CELL; c++) {
    caps[c].cell_idx = c;
    caps[c].capacity = 0.0;
    for (int u = 0; u < NUM_UE; u++)
      caps[c].capacity += sinr_matrix[u][c];
  }

  /* Step 2: Sort cells by capacity descending (best cell first) */
  for (int i = 0; i < NUM_CELL - 1; i++)
    for (int j = i + 1; j < NUM_CELL; j++)
      if (caps[j].capacity > caps[i].capacity) {
        cell_cap_t tmp = caps[i]; caps[i] = caps[j]; caps[j] = tmp;
      }

  printf("[Q-xApp NES] Cell capacity ranking:\n");
  for (int i = 0; i < NUM_CELL; i++)
    printf("  %s (cell_idx=%d): total_capacity=%.4f\n",
           ORU_NAMES[caps[i].cell_idx], caps[i].cell_idx, caps[i].capacity);

  /* Step 3: Assign UEs to cells in capacity order, respecting MAX_UE_PER_CELL */
  int cell_load[NUM_CELL] = {0};
  int assigned[NUM_UE];
  memset(assigned, 0, sizeof(assigned));
  for (int u = 0; u < NUM_UE; u++)
    assignment[u] = -1;

  for (int ci = 0; ci < NUM_CELL; ci++) {
    int c = caps[ci].cell_idx;

    /* For this cell, pick UEs with best rate (greedy within cell) */
    typedef struct { double rate; int ue; } ue_rate_t;
    ue_rate_t candidates[NUM_UE];
    int nc = 0;
    for (int u = 0; u < NUM_UE; u++) {
      if (assigned[u]) continue;
      candidates[nc].rate = sinr_matrix[u][c];
      candidates[nc].ue = u;
      nc++;
    }

    /* Sort candidates by rate descending */
    for (int i = 0; i < nc - 1; i++)
      for (int j = i + 1; j < nc; j++)
        if (candidates[j].rate > candidates[i].rate) {
          ue_rate_t tmp = candidates[i];
          candidates[i] = candidates[j];
          candidates[j] = tmp;
        }

    /* Assign up to MAX_UE_PER_CELL */
    for (int i = 0; i < nc && cell_load[c] < MAX_UE_PER_CELL; i++) {
      int u = candidates[i].ue;
      assignment[u] = c;
      assigned[u] = 1;
      cell_load[c]++;
    }
  }

  /* Fallback: any unassigned UEs go to least-loaded cell */
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

  /* Step 4: Identify active and sleep cells */
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

/* ══════════════════════════════════════════════════════════════════ */
/*  main                                                             */
/* ══════════════════════════════════════════════════════════════════ */
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
    printf("[Q-xApp NES] Waiting for E2 nodes... (%d/30)\n", retry+1);
    sleep(2);
  }
  if (nodes.len == 0) { printf("[Q-xApp NES] No E2 nodes.\n"); return 1; }
  printf("[Q-xApp NES] Connected E2 nodes = %d\n", nodes.len);

  for (size_t i = 0; i < nodes.len; i++) {
    printf("[Q-xApp NES]   node[%zu] id.type=%d nb_id=%u\n",
           i, nodes.n[i].id.type, nodes.n[i].id.nb_id.nb_id);
  }

  /* ─── Main control loop ──────────────────────────────────── */
  int prev_assignment[NUM_UE];
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
  int round = 0;

  while (1) {
  round++;
  printf("===== Q-xApp NES Round %d =====\n", round);

  /* ─── Step 1: Read SINR from CSV ──────────────────────────── */
  printf("[Q-xApp NES] Reading SINR data from CSV...\n");
  read_sinr_from_csv();

  /* Fill UE 3 (IMSI 4) with average if no data */
  {
    int has = 0;
    for (int c = 0; c < NUM_CELL; c++) if (sinr_matrix[3][c] > 0.01) has = 1;
    if (!has) {
      for (int c = 0; c < NUM_CELL; c++) {
        double sum = 0; int cnt = 0;
        for (int u = 0; u < 3; u++) if (sinr_matrix[u][c] > 0.01) { sum += sinr_matrix[u][c]; cnt++; }
        if (cnt > 0) sinr_matrix[3][c] = sum / cnt;
      }
    }
  }

  /* Print rate matrix */
  printf("[Q-xApp NES] Rate matrix (bps/Hz):\n");
  printf("         ");
  for (int c = 0; c < NUM_CELL; c++) printf("%-10s", ORU_NAMES[c]);
  printf("\n");
  for (int u = 0; u < NUM_UE; u++) {
    printf("  UE %d:  ", u);
    for (int c = 0; c < NUM_CELL; c++)
      printf("%-10.4f", sinr_matrix[u][c]);
    printf("  (serving: %s)\n", ORU_NAMES[serving_cell[u]]);
  }

  /* ─── Step 2: Run energy-aware matching ───────────────────── */
  int assignment[NUM_UE];
  int active_cells = 0;
  int sleep_cells[NUM_CELL];
  int n_sleep = 0;
  energy_aware_match(assignment, &active_cells, sleep_cells, &n_sleep);

  double total_rate = 0.0;
  for (int u = 0; u < NUM_UE; u++)
    total_rate += sinr_matrix[u][assignment[u]];

  printf("[Q-xApp NES] Energy-aware assignment (total rate=%.4f bps/Hz, active=%d, sleep=%d):\n",
         total_rate, active_cells, n_sleep);
  for (int u = 0; u < NUM_UE; u++) {
    printf("  UE %d -> %s (rate=%.4f bps/Hz)\n",
           u, ORU_NAMES[assignment[u]], sinr_matrix[u][assignment[u]]);
  }

  /* ─── Step 3: Write result JSON with energy info ──────────── */
  write_result_json_energy(assignment, total_rate, active_cells, sleep_cells, n_sleep);

  /* ─── Step 4: Send handover commands (style 3) ────────────── */
  for (int u = 0; u < NUM_UE; u++) {
    int new_cell_idx = assignment[u];
    if (new_cell_idx == prev_assignment[u]) {
      printf("[Q-xApp NES] UE %d already on %s, skip handover.\n", u, ORU_NAMES[new_cell_idx]);
      continue;
    }

    uint64_t imsi = (uint64_t)(u + 1);
    char target_cell_char = '0' + CELL_IDS[new_cell_idx];

    ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);

    rc_ctrl_req_data_t rc_ctrl = {0};
    rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, 3, HANDOVER_CONTROL_7_6_4_1);
    rc_ctrl.msg = gen_rc_ctrl_msg(FORMAT_1_E2SM_RC_CTRL_MSG, target_cell_char);

    int64_t st = time_now_us();
    printf("[Q-xApp NES] HO: UE %d (IMSI %lu) -> %s (char '%c')\n",
           u, imsi, ORU_NAMES[new_cell_idx], target_cell_char);

    for (size_t i = 1; i < nodes.len; i++) {
      printf("[Q-xApp NES]   Sending HO to node[%zu] nb_id=%u\n", i, nodes.n[i].id.nb_id.nb_id);
      control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rc_ctrl);
    }

    printf("[Q-xApp NES] HO latency for UE %d: %ld us\n", u, time_now_us() - st);
    free_rc_ctrl_req_data(&rc_ctrl);
    usleep(500000);
  }

  /* ─── Step 5: Send Energy_state control for sleep cells (style 300) ── */
  for (int s = 0; s < n_sleep; s++) {
    int sleep_cell_id = sleep_cells[s];
    char target_cell_char = '0' + sleep_cell_id;

    printf("[Q-xApp NES] Sending Energy_state SLEEP for cell ID=%d (char '%c')\n",
           sleep_cell_id, target_cell_char);

    /* Use IMSI=0 as dummy UE ID for energy control (cell-level, not UE-level) */
    ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, 0);

    rc_ctrl_req_data_t rc_ctrl = {0};
    rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, 300, 1);
    rc_ctrl.msg = gen_rc_ctrl_msg_energy(FORMAT_1_E2SM_RC_CTRL_MSG, target_cell_char);

    for (size_t i = 1; i < nodes.len; i++) {
      printf("[Q-xApp NES]   Sending Energy_state to node[%zu] nb_id=%u\n", i, nodes.n[i].id.nb_id.nb_id);
      control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rc_ctrl);
    }

    free_rc_ctrl_req_data(&rc_ctrl);
    usleep(200000);
  }

  printf("[Q-xApp NES] Round %d complete. (active=%d, sleep=%d)\n", round, active_cells, n_sleep);
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = assignment[u];
  sleep(5);
  } /* end while loop */

  return 0;
}
