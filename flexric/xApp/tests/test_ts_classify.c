/*
 * test_ts_classify.c - Offline unit test for the TS fallback classifier and
 * result validator (qxapp_ts_classify.h), assessment Priority 0/3/5.
 *
 * Compiles the actual C classification logic standalone (no FlexRIC, no RIC,
 * no Python) and exercises EVERY taxonomy branch:
 *   run-failure:  timeout, no-candidate, invalid-cli, nonzero-exit
 *   result:       success, parse, method-mismatch, capability, feasibility
 *
 * Build + run (from repo root):
 *   cc -std=c11 -Wall -Wextra -o /tmp/test_ts_classify \
 *      flexric/xApp/tests/test_ts_classify.c && /tmp/test_ts_classify
 * Exit 0 on all-pass, 1 otherwise.
 */
#include <stdio.h>
#include <string.h>
#include "../qxapp_ts_classify.h"

static int failures = 0;
static int checks = 0;

static void expect_reason(const char *name, ts_fallback_reason_t got,
                          ts_fallback_reason_t want)
{
  checks++;
  if (got != want) {
    failures++;
    printf("  FAIL %-28s got=%s want=%s\n", name, ts_fb_name(got),
           ts_fb_name(want));
  } else {
    printf("  ok   %-28s -> %s\n", name, ts_fb_name(got));
  }
}

static void expect_int(const char *name, int got, int want)
{
  checks++;
  if (got != want) {
    failures++;
    printf("  FAIL %-28s got=%d want=%d\n", name, got, want);
  } else {
    printf("  ok   %-28s -> %d\n", name, got);
  }
}

/* A well-formed v5 result the controller accepts. */
static const char *GOOD =
  "{\"assignment\": [0, 1, 2, 0], \"score\": 3.0, \"feasible\": true, "
  "\"feasibility_prob\": 0.5, \"method\": "
  "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
  "\"solver_family\": \"weighted-aa\", \"oracle_type\": \"soft-cost\", "
  "\"formal_aa\": true, \"constraint_mode\": \"cap-only\", "
  "\"selection_mode\": \"classical-best-of-candidates\", "
  "\"backend\": \"reference\", \"elapsed_ms\": 100}";

#define REQ_METHOD "quantum-fullA-17q-valid3-caponly-weightedAA-v5"
#define REQ_FAMILY "weighted-aa"
#define REQ_CMODE  "cap-only"

int main(void)
{
  printf("run-failure classification:\n");
  expect_reason("timeout(124)", ts_classify_run_failure(124, ""),
                TS_FB_TIMEOUT);
  expect_reason("no-candidate",
                ts_classify_run_failure(1, "[dqna_ts] no accepted candidate "
                                           "within budgets (runs=1)"),
                TS_FB_NO_CANDIDATE);
  expect_reason("legacy no feasible",
                ts_classify_run_failure(1, "[dqna_ts] no feasible assignment"),
                TS_FB_NO_CANDIDATE);
  expect_reason("invalid-cli(feas-iter)",
                ts_classify_run_failure(1, "[dqna_ts] --feas-iter is a "
                                           "legacy-two-stage argument"),
                TS_FB_INVALID_CLI);
  expect_reason("invalid-cli(mixed)",
                ts_classify_run_failure(1, "arguments cannot be mixed"),
                TS_FB_INVALID_CLI);
  expect_reason("nonzero-exit(generic)",
                ts_classify_run_failure(1, "Traceback: ImportError qiskit"),
                TS_FB_NONZERO_EXIT);
  /* narrowed classifier: an unrelated error that merely contains "must be"
   * must NOT be misclassified as invalid-CLI */
  expect_reason("nonzero-exit(unrelated must-be)",
                ts_classify_run_failure(1, "RuntimeError: array must be "
                                           "contiguous for BLAS"),
                TS_FB_NONZERO_EXIT);

  printf("result validation:\n");
  int a[QXAPP_TS_N_UE];
  ts_fallback_reason_t r;

  int ok = ts_validate_result(GOOD, REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_int("success-returns-1", ok, 1);
  expect_reason("success-reason", r, TS_FB_NONE);
  expect_int("success-assignment0", a[0], 0);
  expect_int("success-assignment2", a[2], 2);

  ok = ts_validate_result("{\"score\": 1.0}", REQ_METHOD, REQ_FAMILY,
                          REQ_CMODE, 2, a, &r);
  expect_int("parse-returns-0", ok, 0);
  expect_reason("parse-reason", r, TS_FB_PARSE);

  /* malformed: assignment array missing the closing bracket -> parse reject */
  ok = ts_validate_result("{\"assignment\": [0,1,2,3 , \"method\": \"x\"}",
                          REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_int("parse-no-closing-bracket-rc", ok, 0);
  expect_reason("parse-no-closing-bracket", r, TS_FB_PARSE);

  /* malformed: assignment array followed by junk (no value delimiter) */
  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0]junk, \"method\": \"x\"}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("parse-array-junk-suffix", r, TS_FB_PARSE);

  /* malformed: truncated right after the array (NUL, no comma/brace) */
  ok = ts_validate_result("{\"assignment\": [0,1,2,0]",
                          REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("parse-array-truncated-nul", r, TS_FB_PARSE);

  /* malformed: overflow value that fits `long` but wraps as int (4294967296)
   * must be rejected before the cast, not accepted as a small cell */
  ok = ts_validate_result(
      "{\"assignment\": [4294967296, 1, 2, 0], \"method\": \"x\"}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("parse-int-overflow", r, TS_FB_PARSE);

  /* malformed: assignment value is NOT an array (a number), and a later field
   * has a '[' -- must not scan forward and grab it */
  ok = ts_validate_result(
      "{\"assignment\": 5, \"other\": [0,1,2,0], \"method\": \"x\"}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("parse-assignment-not-array", r, TS_FB_PARSE);

  /* malformed: string capability value with a junk suffix ("weighted-aa"junk)
   * -> the trailing delimiter check rejects it, so solver_family is missing */
  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"weighted-aa\"junk, \"constraint_mode\": "
      "\"cap-only\", \"formal_aa\": true}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("str-junk-suffix-rejected", r, TS_FB_CAPABILITY);

  /* malformed: array followed by ']' (stray bracket, not an object delim) */
  ok = ts_validate_result("{\"assignment\": [0,1,2,0]] , \"method\": \"x\"}",
                          REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("parse-array-stray-bracket", r, TS_FB_PARSE);

  /* valid: realistic json.dump spacing ("[0, 1, 2, 0]") plus trailing
   * whitespace before the object delimiters is tolerated (%d skips the leading
   * whitespace after each comma; the array is then followed by ws then ','). */
  ok = ts_validate_result(
      "{\"assignment\": [0, 1, 2, 0]  , \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"weighted-aa\", \"constraint_mode\": \"cap-only\", "
      "\"formal_aa\":  true  , \"x\": 1}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_int("valid-with-whitespace-rc", ok, 1);
  expect_reason("valid-with-whitespace", r, TS_FB_NONE);

  /* malformed: capability bool literal with a junk suffix -> not accepted,
   * so formal_aa is treated as missing -> capability reject */
  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"weighted-aa\", "
      "\"constraint_mode\": \"cap-only\", \"formal_aa\": truejunk}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("bool-suffix-rejected", r, TS_FB_CAPABILITY);

  /* malformed: "true garbage" (whitespace then non-delimiter) also rejected */
  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"weighted-aa\", "
      "\"constraint_mode\": \"cap-only\", \"formal_aa\": true garbage}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("bool-ws-then-junk-rejected", r, TS_FB_CAPABILITY);

  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": \"quantum-2stage-15q-v41\", "
      "\"solver_family\": \"weighted-aa\", \"constraint_mode\": \"cap-only\", "
      "\"formal_aa\": true}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("method-mismatch", r, TS_FB_METHOD);

  /* ts_json_str must NOT scan forward and grab a LATER field's string when the
   * target field is a non-string; here method is a number, so it is treated as
   * missing (method="?") -> method mismatch, NOT "weighted-aa" from below. */
  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": 12345, "
      "\"solver_family\": \"quantum-fullA-17q-valid3-caponly-weightedAA-v5\"}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("str-nonstring-not-grabbed", r, TS_FB_METHOD);

  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\"}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("capability-missing", r, TS_FB_CAPABILITY);

  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"gated-heuristic\", "
      "\"constraint_mode\": \"cap-only\", \"formal_aa\": true}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("capability-mismatch-family", r, TS_FB_CAPABILITY);

  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,0], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"weighted-aa\", "
      "\"constraint_mode\": \"cap-only\", \"formal_aa\": false}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("capability-formal-false", r, TS_FB_CAPABILITY);

  /* feasibility: cell 0 overloaded (3 > cap 2) */
  ok = ts_validate_result(
      "{\"assignment\": [0,0,0,1], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"weighted-aa\", "
      "\"constraint_mode\": \"cap-only\", \"formal_aa\": true}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("feasibility-overcap", r, TS_FB_FEASIBILITY);

  /* feasibility: invalid cell index 3 (>= N_CELL) */
  ok = ts_validate_result(
      "{\"assignment\": [0,1,2,3], \"method\": "
      "\"quantum-fullA-17q-valid3-caponly-weightedAA-v5\", "
      "\"solver_family\": \"weighted-aa\", "
      "\"constraint_mode\": \"cap-only\", \"formal_aa\": true}",
      REQ_METHOD, REQ_FAMILY, REQ_CMODE, 2, a, &r);
  expect_reason("feasibility-badindex", r, TS_FB_FEASIBILITY);
  (void)ok;

  printf("\n%d checks, %d failures\n", checks, failures);
  printf("TS_CLASSIFY_TEST=%s\n", failures ? "FAIL" : "PASS");
  return failures ? 1 : 0;
}
