/*
 * Q-xApp Greedy Handover Controller
 * Quantum-inspired greedy matching for UE-Cell assignment
 * Based on orange_handover.c RC control message format
 */

#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_struct.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_list.h"
#include "../../../../src/util/time_now_us.h"
#include "../../../../src/sm/rc_sm/rc_sm_id.h"
#include "../../../../src/sm/rc_sm/ie/rc_data_ie.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>

/* ── constants ────────────────────────────────────────────────────── */
#define NUM_UE    4
#define NUM_CELL  3
#define MAX_UE_PER_CELL  2
#define KPM_RAN_FUNCTION  2
#define CSV_DIR  "/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran"
#define RESULT_JSON "/home/wookjin/ns-O-RAN-flexric/mmwave-LENA-oran/qxapp_result.json"

static const int CELL_IDS[NUM_CELL] = {2, 3, 4};  /* all mmWave cells */
static const char *ORU_NAMES[NUM_CELL] = {"O-RU 1", "O-RU 2", "O-RU 3"};
static const char *CELL_FILES[NUM_CELL] = {
  "cu-cp-cell-2.txt",
  "cu-cp-cell-3.txt",
  "cu-cp-cell-4.txt"
};

/* ── SINR / rate data ─────────────────────────────────────────────── */
static double sinr_matrix[NUM_UE][NUM_CELL];   /* [ue][cell] in dB */
static int    serving_cell[NUM_UE];            /* cell index 0..2 */

/* ── dynamic UE ID map: amf_ue_ngap_id -> UE index 0..3 ─────────── */
#define MAX_UE_MAP 64
static struct { uint64_t ngap_id; int ue_idx; int valid; } ue_map[MAX_UE_MAP];
static int ue_map_cnt = 0;

static int get_or_add_ue(uint64_t ngap_id)
{
  for (int i = 0; i < ue_map_cnt; i++)
    if (ue_map[i].valid && ue_map[i].ngap_id == ngap_id)
      return ue_map[i].ue_idx;
  if (ue_map_cnt >= NUM_UE) return -1;
  int idx = ue_map_cnt;
  ue_map[idx].ngap_id = ngap_id;
  ue_map[idx].ue_idx  = idx;
  ue_map[idx].valid   = 1;
  ue_map_cnt++;
  return idx;
}

/* ── Shannon capacity: C = log2(1 + 10^(sinr_dB/10))  [bps/Hz] ── */
static double sinr_to_rate(double sinr_dB)
{
  double linear = pow(10.0, sinr_dB / 10.0);
  return log2(1.0 + linear);
}

/* ── read SINR from CSV files ─────────────────────────────────────── */
/* File format per line: ue_ngap_id,serving_sinr,neigh1_sinr,neigh2_sinr */
/* FIX: Track per-UE timestamps so that only the most recent data wins. */
/* Previously, cell-4.txt (read last) would overwrite fresher data from  */
/* cell-2.txt or cell-3.txt, causing stale SINR → wrong assignments.    */
static void read_sinr_from_csv(void)
{
  /* Per-UE: best (newest) timestamp seen so far */
  uint64_t best_ts[NUM_UE];
  memset(best_ts, 0, sizeof(best_ts));

  /* Temporary storage: collect candidate rows, pick freshest per UE */
  typedef struct {
    uint64_t ts;
    int      cell_idx;      /* index into CELL_IDS / CELL_FILES */
    double   srv_sinr;
    int      neigh_id[8];   /* neighbor cell IDs  */
    double   neigh_sinr[8]; /* neighbor SINRs     */
    int      n_neigh;
  } row_t;

  row_t best_row[NUM_UE];
  int   has_row[NUM_UE];
  memset(has_row, 0, sizeof(has_row));

  for (int c = 0; c < NUM_CELL; c++) {
    char path[512];
    sprintf(path, "%s/%s", CSV_DIR, CELL_FILES[c]);
    FILE *fp = fopen(path, "r");
    if (!fp) continue;

    char line[4096];
    int lineno = 0;
    while (fgets(line, sizeof(line), fp)) {
      lineno++;
      if (lineno == 1) continue;  /* skip header */
      if (strlen(line) < 20) continue;

      /* Simple field extraction by counting commas */
      char *p = line;
      char *fstart[40];
      int nf = 0;
      fstart[0] = p;
      while (*p && nf < 39) {
        if (*p == ',') {
          *p = 0;
          nf++;
          fstart[nf] = p + 1;
        }
        p++;
      }
      nf++;
      if (nf < 9) continue;

      uint64_t ts = (uint64_t)strtoull(fstart[0], NULL, 10);
      int ue_imsi = atoi(fstart[6]);
      double srv_sinr = atof(fstart[7]);
      if (ue_imsi < 1 || ue_imsi > NUM_UE) continue;
      int ue_idx = ue_imsi - 1;

      /* Only keep if this timestamp is >= the best we have seen */
      if (has_row[ue_idx] && ts < best_ts[ue_idx]) continue;

      best_ts[ue_idx] = ts;
      has_row[ue_idx] = 1;

      best_row[ue_idx].ts       = ts;
      best_row[ue_idx].cell_idx = c;
      best_row[ue_idx].srv_sinr = srv_sinr;
      best_row[ue_idx].n_neigh  = 0;

      /* Collect neighbor triplets starting at field 9 */
      for (int n = 9; n + 2 < nf; n += 3) {
        if (fstart[n][0] == 0) break;
        int ni = best_row[ue_idx].n_neigh;
        if (ni >= 8) break;
        best_row[ue_idx].neigh_id[ni]   = atoi(fstart[n]);
        best_row[ue_idx].neigh_sinr[ni] = atof(fstart[n+1]);
        best_row[ue_idx].n_neigh++;
      }
    }
    fclose(fp);
  }

  /* Now build sinr_matrix from the single best row per UE */
  memset(sinr_matrix, 0, sizeof(sinr_matrix));
  for (int u = 0; u < NUM_UE; u++) {
    if (!has_row[u]) continue;
    row_t *r = &best_row[u];

    serving_cell[u] = r->cell_idx;
    sinr_matrix[u][r->cell_idx] = sinr_to_rate(r->srv_sinr);

    for (int n = 0; n < r->n_neigh; n++) {
      int nid = r->neigh_id[n];
      double nsinr = r->neigh_sinr[n];
      if (nid < 0) nid = -nid;
      for (int cc = 0; cc < NUM_CELL; cc++) {
        if (CELL_IDS[cc] == nid) {
          sinr_matrix[u][cc] = sinr_to_rate(nsinr);
          break;
        }
      }
    }
  }
}

/* ── write result JSON ────────────────────────────────────────────── */
static void write_result_json(int assignment[NUM_UE], double total_rate)
{
  FILE *fp = fopen(RESULT_JSON, "w");
  if (!fp) return;
  fprintf(fp, "{\n");
  fprintf(fp, "  \"timestamp\": %ld,\n", (long)time(NULL));
  fprintf(fp, "  \"total_rate_bps_hz\": %.4f,\n", total_rate);
  fprintf(fp, "  \"assignment\": [\n");
  for (int u = 0; u < NUM_UE; u++) {
    fprintf(fp, "    {\"ue\": %d, \"oru\": \"%s\", \"rate\": %.4f}%s\n", u, ORU_NAMES[assignment[u]], sinr_matrix[u][assignment[u]], u < NUM_UE - 1 ? "," : "");
  }
  fprintf(fp, "  ]\n}\n");
  fclose(fp);
  printf("[Q-xApp] Result written to %s\n", RESULT_JSON);
}

/* ── greedy matching: assign UEs to cells maximising total rate ──── */
/* Capacity constraint: max MAX_UE_PER_CELL UEs per cell.             */
static void greedy_match(int assignment[NUM_UE])
{
  int cell_load[NUM_CELL] = {0};

  /* Build (rate, ue, cell) tuples and sort descending */
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

  /* Sort descending by rate */
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

  /* Fallback: unassigned UEs go to least-loaded cell */
  for (int u = 0; u < NUM_UE; u++) {
    if (assignment[u] >= 0) continue;
    int best = -1;
    for (int c = 0; c < NUM_CELL; c++) {
      if (cell_load[c] >= MAX_UE_PER_CELL) continue;
      if (best < 0 || cell_load[c] < cell_load[best]) best = c;
    }
    if (best < 0) best = 0;  /* last resort */
    assignment[u] = best;
    cell_load[best]++;
  }
}

/* ══════════════════════════════════════════════════════════════════ */
/*  RC Control Message — EXACT copy from orange_handover.c          */
/* ══════════════════════════════════════════════════════════════════ */

static
e2sm_rc_ctrl_hdr_frmt_1_t gen_rc_ctrl_hdr_frmt_1(ue_id_e2sm_t ue_id, uint32_t ric_style_type, uint16_t ctrl_act_id)
{
  e2sm_rc_ctrl_hdr_frmt_1_t dst = {0};
  dst.ue_id = cp_ue_id_e2sm(&ue_id);
  dst.ric_style_type = ric_style_type;
  dst.ctrl_act_id = ctrl_act_id;
  return dst;
}

static
e2sm_rc_ctrl_hdr_t gen_rc_ctrl_hdr(e2sm_rc_ctrl_hdr_e hdr_frmt, ue_id_e2sm_t ue_id, uint32_t ric_style_type, uint16_t ctrl_act_id)
{
  e2sm_rc_ctrl_hdr_t dst = {0};
  if (hdr_frmt == FORMAT_1_E2SM_RC_CTRL_HDR) {
    dst.format = FORMAT_1_E2SM_RC_CTRL_HDR;
    dst.frmt_1 = gen_rc_ctrl_hdr_frmt_1(ue_id, ric_style_type, ctrl_act_id);
  } else {
    assert(0!=0 && "not implemented the fill func for this ctrl hdr frmt");
  }
  return dst;
}

/* ── RAN parameter 1: Target Primary Cell ID ──────────────────── */
static
void gen_Target_Primary_Cell_ID(seq_ran_param_t* Target_Primary_Cell_ID, char TARGET_CELL)
{
  Target_Primary_Cell_ID->ran_param_id = TARGET_PRIMARY_CELL_ID_8_4_4_1;
  Target_Primary_Cell_ID->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  Target_Primary_Cell_ID->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(Target_Primary_Cell_ID->ran_param_val.strct != NULL && "Memory exhausted");
  Target_Primary_Cell_ID->ran_param_val.strct->sz_ran_param_struct = 1;
  Target_Primary_Cell_ID->ran_param_val.strct->ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(Target_Primary_Cell_ID->ran_param_val.strct->ran_param_struct != NULL && "Memory exhausted");

  seq_ran_param_t* CHOICE_Target_Cell = &Target_Primary_Cell_ID->ran_param_val.strct->ran_param_struct[0];
  CHOICE_Target_Cell->ran_param_id = CHOICE_TARGET_CELL_8_4_4_1;
  CHOICE_Target_Cell->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  CHOICE_Target_Cell->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(CHOICE_Target_Cell->ran_param_val.strct != NULL && "Memory exhausted");
  CHOICE_Target_Cell->ran_param_val.strct->sz_ran_param_struct = 2;
  CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
  assert(CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct != NULL && "Memory exhausted");

  /* NR Cell */
  seq_ran_param_t* NR_Cell = &CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct[0];
  NR_Cell->ran_param_id = NR_CELL_8_4_4_1;
  NR_Cell->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  NR_Cell->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(NR_Cell->ran_param_val.strct != NULL && "Memory exhausted");
  NR_Cell->ran_param_val.strct->sz_ran_param_struct = 1;
  NR_Cell->ran_param_val.strct->ran_param_struct = calloc(1, sizeof(seq_ran_param_t));

  seq_ran_param_t* NR_CGI = &NR_Cell->ran_param_val.strct->ran_param_struct[0];
  NR_CGI->ran_param_id = NR_CGI_8_4_4_1;
  NR_CGI->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  NR_CGI->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(NR_CGI->ran_param_val.flag_false != NULL && "Memory exhausted");
  NR_CGI->ran_param_val.flag_false->type = BIT_STRING_RAN_PARAMETER_VALUE;
  char nr_cgi_str[1];
  nr_cgi_str[0] = TARGET_CELL;
  byte_array_t nr_cgi = cp_str_to_ba(nr_cgi_str);
  NR_CGI->ran_param_val.flag_false->octet_str_ran.len = nr_cgi.len;
  NR_CGI->ran_param_val.flag_false->octet_str_ran.buf = nr_cgi.buf;

  /* E-UTRA Cell */
  seq_ran_param_t* EUTRA_Cell = &CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct[1];
  EUTRA_Cell->ran_param_id = EUTRA_CELL_8_4_4_1;
  EUTRA_Cell->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  EUTRA_Cell->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(EUTRA_Cell->ran_param_val.strct != NULL && "Memory exhausted");
  EUTRA_Cell->ran_param_val.strct->sz_ran_param_struct = 1;
  EUTRA_Cell->ran_param_val.strct->ran_param_struct = calloc(1, sizeof(seq_ran_param_t));

  seq_ran_param_t* EUTRA_CGI = &EUTRA_Cell->ran_param_val.strct->ran_param_struct[0];
  EUTRA_CGI->ran_param_id = EUTRA_CGI_8_4_4_1;
  EUTRA_CGI->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  EUTRA_CGI->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(EUTRA_CGI->ran_param_val.flag_false != NULL && "Memory exhausted");
  EUTRA_CGI->ran_param_val.flag_false->type = BIT_STRING_RAN_PARAMETER_VALUE;
  char eUTRA_cgi_str[2];
  eUTRA_cgi_str[0] = TARGET_CELL;
  eUTRA_cgi_str[1] = '\0';
  byte_array_t eUTRA_cgi = cp_str_to_ba(eUTRA_cgi_str);
  EUTRA_CGI->ran_param_val.flag_false->octet_str_ran.len = eUTRA_cgi.len;
  EUTRA_CGI->ran_param_val.flag_false->octet_str_ran.buf = eUTRA_cgi.buf;
}

/* ── RAN parameter 2: List of PDU sessions for handover ───────── */
static
void gen_List_of_PDU_sessions_for_handover(seq_ran_param_t* List_PDU_sessions_ho)
{
  int num_PDU_session = 1;
  List_PDU_sessions_ho->ran_param_id = LIST_OF_PDU_SESSIONS_FOR_HANDOVER_8_4_4_1;
  List_PDU_sessions_ho->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_PDU_sessions_ho->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_PDU_sessions_ho->ran_param_val.lst != NULL && "Memory exhausted");
  List_PDU_sessions_ho->ran_param_val.lst->sz_lst_ran_param = num_PDU_session;
  List_PDU_sessions_ho->ran_param_val.lst->lst_ran_param = calloc(num_PDU_session, sizeof(lst_ran_param_t));
  assert(List_PDU_sessions_ho->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  lst_ran_param_t* PDU_session_item = &List_PDU_sessions_ho->ran_param_val.strct->ran_param_struct[0];
  PDU_session_item->ran_param_struct.sz_ran_param_struct = 2;
  PDU_session_item->ran_param_struct.ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
  assert(PDU_session_item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  seq_ran_param_t* PDU_Session_ID = &PDU_session_item->ran_param_struct.ran_param_struct[0];
  PDU_Session_ID->ran_param_id = PDU_SESSION_ID_8_4_4_1;
  PDU_Session_ID->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  PDU_Session_ID->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(PDU_Session_ID->ran_param_val.flag_false != NULL && "Memory exhausted");
  PDU_Session_ID->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char pduid_str[] = "5";
  byte_array_t pduid = cp_str_to_ba(pduid_str);
  PDU_Session_ID->ran_param_val.flag_false->octet_str_ran.len = pduid.len;
  PDU_Session_ID->ran_param_val.flag_false->octet_str_ran.buf = pduid.buf;

  seq_ran_param_t* List_of_QoS_flows = &PDU_session_item->ran_param_struct.ran_param_struct[1];
  List_of_QoS_flows->ran_param_id = LIST_OF_QOS_FLOWS_IN_THE_PDU_SESSION_8_4_4_1;
  List_of_QoS_flows->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_of_QoS_flows->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_of_QoS_flows->ran_param_val.lst != NULL && "Memory exhausted");
  List_of_QoS_flows->ran_param_val.lst->sz_lst_ran_param = 1;
  List_of_QoS_flows->ran_param_val.lst->lst_ran_param = calloc(1, sizeof(lst_ran_param_t));
  assert(List_of_QoS_flows->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  lst_ran_param_t* QoS_flow_Item = &List_of_QoS_flows->ran_param_val.lst->lst_ran_param[0];
  QoS_flow_Item->ran_param_struct.sz_ran_param_struct = 1;
  QoS_flow_Item->ran_param_struct.ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(QoS_flow_Item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  seq_ran_param_t* QoS_Flow_Id = &QoS_flow_Item->ran_param_struct.ran_param_struct[0];
  QoS_Flow_Id->ran_param_id = QOS_FLOW_ITEM_8_4_4_1;
  QoS_Flow_Id->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  QoS_Flow_Id->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(QoS_Flow_Id->ran_param_val.flag_false != NULL && "Memory exhausted");
  QoS_Flow_Id->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char QFI_str[] = "1";
  byte_array_t QFI = cp_str_to_ba(QFI_str);
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.len = QFI.len;
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.buf = QFI.buf;
}

/* ── RAN parameter 3: List of DRBs for handover ──────────────── */
static
void gen_List_of_DRBs_for_handover(seq_ran_param_t* List_DRBs_ho)
{
  int num_DRBs = 1;
  List_DRBs_ho->ran_param_id = LIST_OF_DRBS_FOR_HANDOVER_8_4_4_1;
  List_DRBs_ho->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_DRBs_ho->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_DRBs_ho->ran_param_val.lst != NULL && "Memory exhausted");
  List_DRBs_ho->ran_param_val.lst->sz_lst_ran_param = num_DRBs;
  List_DRBs_ho->ran_param_val.lst->lst_ran_param = calloc(num_DRBs, sizeof(lst_ran_param_t));
  assert(List_DRBs_ho->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  lst_ran_param_t* DRB_item_ho = &List_DRBs_ho->ran_param_val.strct->ran_param_struct[0];
  DRB_item_ho->ran_param_struct.sz_ran_param_struct = 2;
  DRB_item_ho->ran_param_struct.ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
  assert(DRB_item_ho->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  seq_ran_param_t* DRB_ID = &DRB_item_ho->ran_param_struct.ran_param_struct[0];
  DRB_ID->ran_param_id = DRB_ID_8_4_4_1;
  DRB_ID->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  DRB_ID->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(DRB_ID->ran_param_val.flag_false != NULL && "Memory exhausted");
  DRB_ID->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char DRB_ID_str[] = "3";
  byte_array_t drpID = cp_str_to_ba(DRB_ID_str);
  DRB_ID->ran_param_val.flag_false->octet_str_ran.len = drpID.len;
  DRB_ID->ran_param_val.flag_false->octet_str_ran.buf = drpID.buf;

  seq_ran_param_t* List_of_QoS_flows = &DRB_item_ho->ran_param_struct.ran_param_struct[1];
  List_of_QoS_flows->ran_param_id = LIST_OF_QOS_FLOWS_IN_THE_DRB_8_4_4_1;
  List_of_QoS_flows->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_of_QoS_flows->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_of_QoS_flows->ran_param_val.lst != NULL && "Memory exhausted");
  List_of_QoS_flows->ran_param_val.lst->sz_lst_ran_param = 1;
  List_of_QoS_flows->ran_param_val.lst->lst_ran_param = calloc(1, sizeof(lst_ran_param_t));
  assert(List_of_QoS_flows->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  lst_ran_param_t* QoS_flow_Item = &List_of_QoS_flows->ran_param_val.lst->lst_ran_param[0];
  QoS_flow_Item->ran_param_struct.sz_ran_param_struct = 1;
  QoS_flow_Item->ran_param_struct.ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(QoS_flow_Item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  seq_ran_param_t* QoS_Flow_Id = &QoS_flow_Item->ran_param_struct.ran_param_struct[0];
  QoS_Flow_Id->ran_param_id = QOS_FLOW_ITEM_DRB_8_4_4_1;
  QoS_Flow_Id->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  QoS_Flow_Id->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(QoS_Flow_Id->ran_param_val.flag_false != NULL && "Memory exhausted");
  QoS_Flow_Id->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char QFI_str[] = "10";
  byte_array_t QFI = cp_str_to_ba(QFI_str);
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.len = QFI.len;
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.buf = QFI.buf;
}

/* ── RAN parameter 4: List of Secondary cells to be setup ─────── */
static
void gen_List_of_Secondary_cells_to_be_setup(seq_ran_param_t* List_num_2ndCells)
{
  int num_2ndCells = 1;
  List_num_2ndCells->ran_param_id = LIST_OF_SECONDARY_CELLS_TO_BE_SETUP_8_4_4_1;
  List_num_2ndCells->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_num_2ndCells->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_num_2ndCells->ran_param_val.lst != NULL && "Memory exhausted");
  List_num_2ndCells->ran_param_val.lst->sz_lst_ran_param = num_2ndCells;
  List_num_2ndCells->ran_param_val.lst->lst_ran_param = calloc(num_2ndCells, sizeof(lst_ran_param_t));
  assert(List_num_2ndCells->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  lst_ran_param_t* secCell_item = &List_num_2ndCells->ran_param_val.strct->ran_param_struct[0];
  secCell_item->ran_param_struct.sz_ran_param_struct = 1;
  secCell_item->ran_param_struct.ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(secCell_item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  seq_ran_param_t* secCell_Id = &secCell_item->ran_param_struct.ran_param_struct[0];
  secCell_Id->ran_param_id = SECONDARY_CELL_ID_8_4_4_1;
  secCell_Id->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  secCell_Id->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(secCell_Id->ran_param_val.flag_false != NULL && "Memory exhausted");
  secCell_Id->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char cellID_str[] = "0";
  byte_array_t cid = cp_str_to_ba(cellID_str);
  secCell_Id->ran_param_val.flag_false->octet_str_ran.len = cid.len;
  secCell_Id->ran_param_val.flag_false->octet_str_ran.buf = cid.buf;
}

/* ── Assemble the 4-parameter control message (format 1) ─────── */
static
e2sm_rc_ctrl_msg_frmt_1_t gen_rc_ctrl_msg_frmt_1_Handover_Control(char TARGET_CELL)
{
  e2sm_rc_ctrl_msg_frmt_1_t dst = {0};

  dst.sz_ran_param = 4;
  dst.ran_param = calloc(4, sizeof(seq_ran_param_t));
  assert(dst.ran_param != NULL && "Memory exhausted");

  gen_Target_Primary_Cell_ID(&dst.ran_param[0], TARGET_CELL);
  gen_List_of_PDU_sessions_for_handover(&dst.ran_param[1]);
  gen_List_of_DRBs_for_handover(&dst.ran_param[2]);
  gen_List_of_Secondary_cells_to_be_setup(&dst.ran_param[3]);

  return dst;
}

static
e2sm_rc_ctrl_msg_t gen_rc_ctrl_msg(e2sm_rc_ctrl_msg_e msg_frmt, char TARGET_CELL)
{
  e2sm_rc_ctrl_msg_t dst = {0};
  if (msg_frmt == FORMAT_1_E2SM_RC_CTRL_MSG) {
    dst.format = msg_frmt;
    dst.frmt_1 = gen_rc_ctrl_msg_frmt_1_Handover_Control(TARGET_CELL);
  } else {
    assert(0!=0 && "not implemented the fill func for this ctrl msg frmt");
  }
  return dst;
}

/* ── UE ID generation: use IMSI 1-4 ─────────────────────────────── */
static
ue_id_e2sm_t gen_rc_ue_id(ue_id_e2sm_e type, uint64_t IMSI)
{
  ue_id_e2sm_t ue_id = {0};
  if (type == GNB_UE_ID_E2SM) {
    ue_id.type = GNB_UE_ID_E2SM;
    ue_id.gnb.ran_ue_id = (uint64_t *)malloc(sizeof(uint64_t));
    *(ue_id.gnb.ran_ue_id) = IMSI;
  } else {
    assert(0!=0 && "not supported UE ID type");
  }
  return ue_id;
}

/* ── Signal handler: clean exit ──────────────────────────────────── */
static void sig_handler(int sig)
{
  (void)sig;
  printf("\n[Q-xApp] Caught signal %d, exiting cleanly.\n", sig);
  _exit(0);
}

/* ══════════════════════════════════════════════════════════════════ */
/*  main                                                             */
/* ══════════════════════════════════════════════════════════════════ */
int main(int argc, char *argv[])
{
  fr_args_t args = init_fr_args(argc, argv);

  /* Init the xApp */
  init_xapp_api(&args);
  sleep(1);

  /* Register signal handler AFTER FlexRIC init, with SA_RESETHAND */
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

  /* ─── Main control loop ──────────────────────────────────── */
  int prev_assignment[NUM_UE];
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = -1;
  int round = 0;

  while (1) {
  round++;
  printf("===== Q-xApp Round %d =====", round); printf("%c",10);

  /* ─── Step 1: Read SINR from CSV BEFORE greedy matching ──────── */
  printf("[Q-xApp] Reading SINR data from CSV...\n");
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

  /* ─── Step 2: Run greedy matching ────────────────────────────── */
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

  /* ─── Step 3: Write result JSON ──────────────────────────────── */
  write_result_json(assignment, total_rate);

  /* ─── Step 4: Send handover commands ─────────────────────────── */
  for (int u = 0; u < NUM_UE; u++) {
    int new_cell_idx = assignment[u];
    if (new_cell_idx == prev_assignment[u]) {
      printf("[Q-xApp] UE %d already on %s, skip handover.\n", u, ORU_NAMES[new_cell_idx]);
      continue;
    }

    /* Use IMSI 1-4 (not mapped E2 ID) */
    uint64_t imsi = (uint64_t)(u + 1);
    char target_cell_char = '0' + CELL_IDS[new_cell_idx];

    ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, imsi);

    rc_ctrl_req_data_t rc_ctrl = {0};
    rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, 3, HANDOVER_CONTROL_7_6_4_1);
    rc_ctrl.msg = gen_rc_ctrl_msg(FORMAT_1_E2SM_RC_CTRL_MSG, target_cell_char);

    int64_t st = time_now_us();
    printf("[Q-xApp] HO: UE %d (IMSI %lu) -> %s (char '%c')\n",
           u, imsi, ORU_NAMES[new_cell_idx], target_cell_char);

    /* Send to ALL E2 nodes except node 0 (LTE) */
    /* Nodes that don't have the UE will return NOT FOUND */
    for (size_t i = 1; i < nodes.len; i++) {
      printf("[Q-xApp]   Sending to node[%zu] nb_id=%u\n", i, nodes.n[i].id.nb_id.nb_id);
      control_sm_xapp_api(&nodes.n[i].id, SM_RC_ID, &rc_ctrl);
    }

    printf("[Q-xApp] HO latency for UE %d: %ld us\n", u, time_now_us() - st);
    free_rc_ctrl_req_data(&rc_ctrl);
    usleep(500000);  /* 500ms between handovers */
  }

  printf("[Q-xApp] Round complete."); printf("%c",10);
  for (int u = 0; u < NUM_UE; u++) prev_assignment[u] = assignment[u];
  sleep(5);
  } /* end while loop */

  return 0;
}
