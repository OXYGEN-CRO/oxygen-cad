/* SPDX-License-Identifier: GPL-2.0-only
 * Exercise real exporter transaction/resource branches without a large solve.
 * This is a standalone verification executable, never linked to the MIT app.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
typedef int ITG;
#define ITGFORMAT "d"
#include "oxygen_contact_telemetry.h"
#include "oxygen_native_tangent.h"
int main(int argc, char **argv) {
    ITG mi[3] = {4, 3, 1}, map[8] = {0,2,-2,-4,0,1,-6,-8};
    ITG small_jq[3] = {1,2,2}, row[1] = {2};
    ITG n = 2, mpcs = 0, *jq = small_jq;
    double ad[2] = {4,3}, au[2] = {2,1}, u[8] = {0};
    double rhs[2] = {.7,.1}, delta[2] = {.2,-.1};
    if (argc != 2) return 2;
    setenv("O3D_CCX_NATIVE_TANGENT", "1", 1);
    if (!strcmp(argv[1], "mpc")) mpcs = 1;
    if (!strcmp(argv[1], "budget")) {
        n = 30001; jq = calloc(n + 1, sizeof(ITG));
        if (!jq) return 3;
        jq[0] = jq[n] = 1;
    }
    oxygen_native_tangent_stage("analysis", 1, 1, 2, 1., 2, mi, map,
        n, 1, 1, 1, jq, row, ad, au, u, rhs, mpcs, 1);
    if (strcmp(argv[1], "missing-solve"))
        oxygen_native_tangent_solved("analysis", n, delta);
    if (!strcmp(argv[1], "total-budget")) oxygen_tangent_bytes = 536870912ULL;
    oxygen_native_tangent_commit("analysis", 1, 1,
        !strcmp(argv[1], "time") ? 2. : 1., 2, mi, u);
    if (jq != small_jq) free(jq);
    return 0;
}
