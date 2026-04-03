/*
 * Q-xApp Greedy Handover Controller
 * Quantum-inspired greedy matching for UE-Cell assignment
 * Based on orange_handover.c RC control message format
 */

#include "qxapp_common.h"

/* ── greedy matching: assign UEs to cells maximising total rate ──── */
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
    printf("[Q-xApp] Waiting for E2 nodes... (%d/30)", retry+1); printf("%c",10);
    sleep(2);
  }
  if (nodes.len == 0) { printf("[Q-xApp] No E2 nodes."); printf("%c",10); return 1; }
  printf("[Q-xApp] Connected E2 nodes = %d\n", nodes.len);

  for (size_t i = 0; i < nodes.len; i++) {
    printf("[Q-xApp]   node[%zu] id.type=%d nb_id=%u\n",
           i, nodes.n[i].id.type, nodes.n[i].id.nb_id.nb_id);
  }

  int prev_assignment[NUM_UE];
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
  int round = 0;

  while (1) {
  round++;
  printf("===== Q-xApp Round %d =====", round); printf("%c",10);

  printf("[Q-xApp] Reading SINR data from CSV...\n");
  read_sinr_from_csv();

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

  int assignment[NUM_UE];
  greedy_match(assignment);

  double total_rate = 0.0;
  for (int u = 0; u < NUM_UE; u++)
    total_rate += sinr_matrix[u][assignment[u]];

  printf("[Q-xApp] Greedy assignment (total rate=%.4f bps/Hz):\n", total_rate);
  for (int u = 0; u < NUM_UE; u++) {
    printf("  UE %d -> %s (rate=%.4f bps/Hz)\n",
           u, ORU_NAMES[assignment[u]],
           sinr_matrix[u][assignment[u]]);
  }

  write_result_json(assignment, total_rate);

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

    for (size_t i = 1; i < nodes.len; i++) {
      printf("[Q-xApp]   Sending to node[%zu] nb_id=%u\n", i, nodes.n[i].id.nb_id.nb_id);
      control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rc_ctrl);
    }

    printf("[Q-xApp] HO latency for UE %d: %ld us\n", u, time_now_us() - st);
    free_rc_ctrl_req_data(&rc_ctrl);
    usleep(500000);
  }

  printf("[Q-xApp] Round complete."); printf("%c",10);
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = assignment[u];
  sleep(5);
  }

  return 0;
}
