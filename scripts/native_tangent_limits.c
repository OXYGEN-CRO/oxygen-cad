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
#include "oxygen_accepted_bulk.h"
static int bulk_case(const char *mode) {
    ITG mi[3]={1,3,1},ipkon[1]={0},kon[4]={1,2,3,4};
    ITG mat[1]={1},orientation[1]={0},nelcon[2]={2,1},flags[2]={2,0};
    ITG nodes=4,mpcs=0;
    char family[8]={'C','3','D','4',' ',' ',' ',' '};
    double u[16]={0},forces[16]={0},tensor[27]={200000.,.3},stress[6]={0};
    setenv("O3D_CCX_ACCEPTED_BULK","1",1);
    if(!strcmp(mode,"bulk-mpc")) mpcs=1;
    if(!strcmp(mode,"bulk-budget")) nodes=100001;
    if(!strcmp(mode,"bulk-family")) family[3]='8';
    if(strcmp(mode,"bulk-missing")) oxygen_accepted_bulk_stage("analysis",1,1,2,1.,.1,
        nodes,1,mi,ipkon,kon,family,mat,orientation,0,nelcon,flags,0,mpcs,0,0,0,
        u,u,forces,tensor,stress,stress,stress,NULL,NULL);
    oxygen_accepted_bulk_commit("analysis",1,1,!strcmp(mode,"bulk-time")?2.:1.,
        nodes,mi,u,1,ipkon,family,0,NULL);
    return 0;
}
int main(int argc, char **argv) {
    ITG mi[3] = {4, 3, 1}, map[8] = {0,2,-2,-4,0,1,-6,-8};
    ITG small_jq[3] = {1,2,2}, row[1] = {2};
    ITG n = 2, mpcs = 0, *jq = small_jq;
    double ad[2] = {4,3}, au[2] = {2,1}, u[8] = {0};
    double rhs[2] = {.7,.1}, delta[2] = {.2,-.1};
    if (argc != 2) return 2;
    if(!strncmp(argv[1],"bulk-",5)) return bulk_case(argv[1]);
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
