/*
 * qxapp_ts_classify.h - Pure, self-contained TS solver fallback classifier and
 * result validator (assessment Priority 0/3/5).
 *
 * Factored out of qxapp_unified.c so the classification logic can be exercised
 * offline by a compiled C test harness (tests/test_ts_classify.c) that covers
 * every fallback-reason branch WITHOUT a live FlexRIC deployment. No global
 * state, no I/O: the caller extracts the subprocess exit code and reads the
 * stdout/stderr text, then calls these functions.
 *
 * The 4 UE x 3 cell shape is fixed for this controller.
 */
#ifndef QXAPP_TS_CLASSIFY_H
#define QXAPP_TS_CLASSIFY_H

#include <string.h>
#include <stdio.h>
#include <stdlib.h>   /* strtol (portable integer parse; no scanf %n) */
#include <errno.h>    /* ERANGE (strtol overflow detection) */
#include <limits.h>   /* INT_MIN / INT_MAX (reject before long->int cast) */

#ifndef QXAPP_TS_N_UE
#define QXAPP_TS_N_UE 4
#endif
#ifndef QXAPP_TS_N_CELL
#define QXAPP_TS_N_CELL 3
#endif

/* Fallback-reason taxonomy: every quantum-path failure is classified into
 * exactly one of these, never a single generic bucket. */
typedef enum {
  TS_FB_NONE = 0,
  TS_FB_INVALID_CLI,   /* solver rejected the arguments (contract violation) */
  TS_FB_TIMEOUT,       /* exceeded the configured deadline */
  TS_FB_NONZERO_EXIT,  /* other nonzero exit (unclassified solver error) */
  TS_FB_NO_CANDIDATE,  /* solver ran but returned no accepted candidate */
  TS_FB_PARSE,         /* exit 0 but stdout JSON was unparseable */
  TS_FB_METHOD,        /* method string is not the required v5 contract */
  TS_FB_CAPABILITY,    /* capability fields missing or unsupported */
  TS_FB_FEASIBILITY,   /* returned assignment fails the C-side cap check */
  TS_FB_MAX
} ts_fallback_reason_t;

static inline const char *ts_fb_name(ts_fallback_reason_t r)
{
  switch (r) {
    case TS_FB_NONE:        return "none";
    case TS_FB_INVALID_CLI: return "invalid-cli";
    case TS_FB_TIMEOUT:     return "timeout";
    case TS_FB_NONZERO_EXIT:return "nonzero-exit";
    case TS_FB_NO_CANDIDATE:return "no-candidate";
    case TS_FB_PARSE:       return "parse-failure";
    case TS_FB_METHOD:      return "method-mismatch";
    case TS_FB_CAPABILITY:  return "capability-unsupported";
    case TS_FB_FEASIBILITY: return "feasibility-reject";
    default:                return "unknown";
  }
}

static inline int ts_json_ws(char c)
{
  return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

/* End of a value in this FLAT result object: the next non-whitespace character
 * after a scalar/array field must be a comma (more fields) or the object's
 * closing brace. Deliberately NOT '\0' or ']' -- a truncated fragment ending
 * right after "[0,1,2,0]" (NUL) or a stray ']' is not a valid flat-object
 * field terminator and is rejected. Whitespace is skipped by callers first.
 * (Defined before ts_json_str, which uses it, so strict C11 has no implicit
 * declaration.) */
static inline int ts_json_obj_delim(char c)
{
  return c == ',' || c == '}';
}

/* Minimal JSON field extractors (flat object, string/bool scalars). Tolerant of
 * key ordering and whitespace; never allocate. Return 1 on success.
 * ts_json_str REQUIRES the value to be a proper JSON string: after the ':' the
 * next non-whitespace character must be the opening quote. Otherwise (e.g. the
 * field holds a bool/number) it returns 0 rather than scanning forward and
 * grabbing a LATER field's string value. */
static inline int ts_json_str(const char *buf, const char *key, char *out,
                              size_t cap)
{
  char pat[64];
  snprintf(pat, sizeof(pat), "\"%s\"", key);
  const char *p = strstr(buf, pat);
  if (!p) return 0;
  p = strchr(p + strlen(pat), ':');
  if (!p) return 0;
  p++;
  while (ts_json_ws(*p)) p++;      /* value must START with a quote right here */
  if (*p != '"') return 0;         /* not a string value -> do NOT scan ahead */
  p++;
  size_t i = 0;
  while (*p && *p != '"' && i + 1 < cap) out[i++] = *p++;
  out[i] = '\0';
  if (*p != '"') return 0;         /* no closing quote (or value too long) */
  p++;                             /* past the closing quote */
  while (ts_json_ws(*p)) p++;      /* then require an object value delimiter, so */
  if (!ts_json_obj_delim(*p)) return 0;  /* "weighted-aa"junk is rejected */
  return 1;
}

static inline int ts_json_bool(const char *buf, const char *key, int *out)
{
  char pat[64];
  snprintf(pat, sizeof(pat), "\"%s\"", key);
  const char *p = strstr(buf, pat);
  if (!p) return 0;
  p = strchr(p + strlen(pat), ':');
  if (!p) return 0;
  p++;
  while (ts_json_ws(*p)) p++;
  const char *after = NULL;
  int val = -1;
  if (strncmp(p, "true", 4) == 0)  { after = p + 4; val = 1; }
  else if (strncmp(p, "false", 5) == 0) { after = p + 5; val = 0; }
  if (!after) return 0;
  while (ts_json_ws(*after)) after++;      /* skip whitespace, THEN require a */
  if (!ts_json_obj_delim(*after)) return 0;  /* comma or closing object brace */
  *out = val;
  return 1;
}

/* Portable parse of a 4-int JSON array "[ i, i, i, i ]" starting at `p` (which
 * must point at '['). Uses strtol only (no scanf / no %n), so it is portable
 * across libc/MSVC and tolerant of whitespace around ints and commas. On
 * success fills out[4], sets *endp to the first character AFTER ']', returns 1;
 * on any malformation returns 0. */
static inline int ts_parse_int_array4(const char *p, int out[4],
                                      const char **endp)
{
  if (!p || *p != '[') return 0;
  p++;
  for (int i = 0; i < 4; i++) {
    while (ts_json_ws(*p)) p++;
    char *e = NULL;
    errno = 0;
    long v = strtol(p, &e, 10);
    if (e == p) return 0;                    /* no digits consumed */
    /* reject overflow BEFORE the long->int cast: on a 64-bit long, e.g.
     * 4294967296 fits `long` and would wrap to a valid small cell as int. */
    if (errno == ERANGE || v < (long) INT_MIN || v > (long) INT_MAX)
      return 0;
    out[i] = (int) v;
    p = e;
    while (ts_json_ws(*p)) p++;
    if (i < 3) {
      if (*p != ',') return 0;       /* separator */
      p++;
    } else {
      if (*p != ']') return 0;       /* closing bracket */
      p++;
    }
  }
  *endp = p;
  return 1;
}

/* Classify a NONZERO solver run. `exit_code` is the extracted process exit
 * status (124 == `timeout` deadline); `err` is the captured stderr text
 * (may be empty). Returns the specific fallback reason. */
static inline ts_fallback_reason_t
ts_classify_run_failure(int exit_code, const char *err)
{
  if (!err) err = "";
  if (exit_code == 124)
    return TS_FB_TIMEOUT;
  if (strstr(err, "no accepted candidate") ||
      strstr(err, "no feasible assignment"))
    return TS_FB_NO_CANDIDATE;
  /* Narrow, SPECIFIC dqna_ts argument/config rejection phrases only -- a bare
   * "must be" would misclassify unrelated solver/import errors. */
  if (strstr(err, "is a legacy-two-stage argument") ||
      strstr(err, "cannot be mixed") ||
      strstr(err, "requires --aa-mode") ||
      strstr(err, "--aa-iter requires") ||
      strstr(err, "fixed requires --aa-iter") ||
      strstr(err, "deprecated alias") ||
      strstr(err, "--max-per-cell must be") ||
      strstr(err, "--qual-lambda must be") ||
      strstr(err, "--seed must be") ||
      strstr(err, "max_per_cell must be") ||
      strstr(err, "utility_threshold") ||
      strstr(err, "utility_fractional_bits") ||
      strstr(err, "must be in [") ||
      strstr(err, "must be >= ") ||
      strstr(err, "structurally infeasible"))
    return TS_FB_INVALID_CLI;
  return TS_FB_NONZERO_EXIT;
}

/* Validate a solver stdout JSON (exit 0 case). On success returns 1 and fills
 * assignment_out; on failure returns 0 and sets *reason to the specific cause
 * (parse / method / capability / feasibility). Fail-closed: absent capability
 * fields are treated as unsupported. */
static inline int
ts_validate_result(const char *buf, const char *req_method,
                   const char *req_family, const char *req_cmode, int cap,
                   int assignment_out[QXAPP_TS_N_UE],
                   ts_fallback_reason_t *reason)
{
  int tmp[QXAPP_TS_N_UE];
  const char *after = NULL;
  /* Require the "assignment" field's OWN value to be the array: find the colon
   * after the key, skip whitespace, and the immediate value must be '['. Do NOT
   * strchr forward to any later '[' (which could belong to a different field). */
  const char *p = strstr(buf, "\"assignment\"");
  if (p) p = strchr(p + strlen("\"assignment\""), ':');
  if (p) {
    p++;
    while (ts_json_ws(*p)) p++;
  }
  /* Portable strtol parse (no scanf/%n); rejects a truncated "[0,1,2,3" and an
   * out-of-int-range value, then requires a flat-object value delimiter after
   * the array so "[0,1,2,0]junk", a stray ']' or a NUL-truncated fragment are
   * rejected. */
  if (!p || *p != '[' || !ts_parse_int_array4(p, tmp, &after)) {
    *reason = TS_FB_PARSE;
    return 0;
  }
  while (ts_json_ws(*after)) after++;
  if (!ts_json_obj_delim(*after)) {
    *reason = TS_FB_PARSE;
    return 0;
  }
  char method[96] = "?";
  ts_json_str(buf, "method", method, sizeof(method));
  if (strcmp(method, req_method) != 0) {
    *reason = TS_FB_METHOD;
    return 0;
  }
  char fam[32] = "", cmode[32] = "";
  int formal = 0;
  int have_fam = ts_json_str(buf, "solver_family", fam, sizeof(fam));
  int have_cmode = ts_json_str(buf, "constraint_mode", cmode, sizeof(cmode));
  int have_formal = ts_json_bool(buf, "formal_aa", &formal);
  if (!have_fam || !have_cmode || !have_formal ||
      strcmp(fam, req_family) != 0 || strcmp(cmode, req_cmode) != 0 ||
      !formal) {
    *reason = TS_FB_CAPABILITY;
    return 0;
  }
  int load[QXAPP_TS_N_CELL] = {0};
  for (int u = 0; u < QXAPP_TS_N_UE; u++) {
    if (tmp[u] < 0 || tmp[u] >= QXAPP_TS_N_CELL) {
      *reason = TS_FB_FEASIBILITY;
      return 0;
    }
    load[tmp[u]]++;
  }
  for (int c = 0; c < QXAPP_TS_N_CELL; c++) {
    if (load[c] > cap) {
      *reason = TS_FB_FEASIBILITY;
      return 0;
    }
  }
  for (int u = 0; u < QXAPP_TS_N_UE; u++) assignment_out[u] = tmp[u];
  *reason = TS_FB_NONE;
  return 1;
}

#endif /* QXAPP_TS_CLASSIFY_H */
