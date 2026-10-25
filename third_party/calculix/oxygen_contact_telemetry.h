/* SPDX-License-Identifier: GPL-2.0-only
 * Copyright (C) 2026 Oxygen CAD contributors
 *
 * Read-only instrumentation for CalculiX 2.23 nonlingeo.c. The upstream
 * numerical routines and their state are unchanged. This header is compiled
 * into the separately invoked GPL solver, not the Python application.
 */
#ifndef OXYGEN_CONTACT_TELEMETRY_H
#define OXYGEN_CONTACT_TELEMETRY_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define OXYGEN_CONTACT_SCHEMA 1

static void oxygen_contact_output_error(const char *message) {
    fprintf(stderr, "Oxygen contact telemetry: %s\n", message);
    exit(201);
}

static void oxygen_contact_real(FILE *stream, double value) {
    if (!isfinite(value)) oxygen_contact_output_error("nonfinite solver state");
    fprintf(stream, "%.17g", value);
}

static void oxygen_contact_reals(FILE *stream, const double *values, ITG count) {
    ITG i;
    fputc('[', stream);
    for (i = 0; i < count; ++i) {
        if (i) fputc(',', stream);
        oxygen_contact_real(stream, values[i]);
    }
    fputc(']', stream);
}

static void oxygen_contact_nodes(FILE *stream, const ITG *nodes, ITG count) {
    ITG i;
    fputc('[', stream);
    for (i = 0; i < count; ++i) {
        if (i) fputc(',', stream);
        fprintf(stream, "%" ITGFORMAT, nodes[i]);
    }
    fputc(']', stream);
}

static void oxygen_contact_snapshot(
    const char *jobname, ITG step, ITG increment, double total_time,
    double duration, ITG bulk_elements, ITG elements, ITG integration_points,
    ITG node_labels, const double *coordinates, const double *displacements,
    const double *previous_displacements, const double *clearance_adjustments,
    const ITG *mi, const ITG *ipkon, const ITG *kon, const char *lakon,
    const ITG *islavsurf, const double *pslavsurf, const double *pmastsurf,
    const double *springarea, const double *stress, const double *energy,
    ITG state_count, const double *state, const double *initial_state) {
    static FILE *stream = NULL;
    const char *enabled = getenv("O3D_CCX_CONTACT_TELEMETRY");
    ITG e, q, index, count, masters, point, face, point_count = 0, bulk_count = 0;
    double friction_increment = 0., spring_energy = 0.;
    char filename[160];
    if (!enabled || strcmp(enabled, "1")) return;
    if (!stream) {
        if (strnlen(jobname, 132) > 120)
            oxygen_contact_output_error("job name exceeds output budget");
        snprintf(filename, sizeof(filename), "%s.contact.jsonl", jobname);
        stream = fopen(filename, "wb");
        if (!stream) oxygen_contact_output_error("cannot create output");
        fprintf(stream, "{\"type\":\"header\",\"schema\":%d,"
            "\"upstream\":\"2.23\",\"source_sha256\":\""
            "9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7\","
            "\"instrumentation\":\"accepted-static-face-contact-v1\","
            "\"number_format\":\"17 significant digits\"}\n", OXYGEN_CONTACT_SCHEMA);
    }
    fprintf(stream, "{\"type\":\"frame\",\"step\":%" ITGFORMAT
        ",\"increment\":%" ITGFORMAT ",\"solver_total_time\":", step, increment);
    oxygen_contact_real(stream, total_time);
    fputs(",\"increment_duration\":", stream);
    oxygen_contact_real(stream, duration);
    for (e = 0; e < bulk_elements; ++e) if (ipkon[e] >= 0) bulk_count++;
    fprintf(stream, ",\"bulk_elements\":%" ITGFORMAT
        ",\"bulk_label_max\":%" ITGFORMAT ",\"node_label_max\":%" ITGFORMAT
        ",\"contact_points\":%" ITGFORMAT "}\n", bulk_count, bulk_elements, node_labels, elements-bulk_elements);
    for (e = 0; e < node_labels; ++e) {
        fprintf(stream, "{\"type\":\"node\",\"id\":%" ITGFORMAT ",\"reference_mm\":", e+1);
        oxygen_contact_reals(stream, coordinates+3*e, 3);
        fputs(",\"displacement_mm\":", stream);
        oxygen_contact_reals(stream, displacements+(mi[1]+1)*e+1, 3);
        fputs(",\"previous_displacement_mm\":", stream);
        oxygen_contact_reals(stream, previous_displacements+(mi[1]+1)*e+1, 3);
        fputs("}\n", stream);
        if (e % 1024 == 0 && ftell(stream) > 536870912L)
            oxygen_contact_output_error("output exceeds 512 MiB");
    }
    for (e = 0; e < bulk_elements; ++e) {
        if (ipkon[e] < 0) continue;
        if (memcmp(lakon + 8*e, "C3D10", 5))
            oxygen_contact_output_error("bulk telemetry currently requires C3D10");
        fprintf(stream, "{\"type\":\"bulk\",\"element\":%" ITGFORMAT
            ",\"internal_energy_density_n_mm_mm3\":[", e+1);
        for (q = 0; q < 4; ++q) {
            if (q) fputc(',', stream);
            oxygen_contact_real(stream, energy[2*(mi[0]*e+q)]);
        }
        fputs("]}\n", stream);
    }
    for (e = bulk_elements; e < elements; ++e) {
        if (memcmp(lakon + 8*e, "ESPRNGC", 7))
            oxygen_contact_output_error("unexpected contact element family");
        /* Generated face-contact ipkon is one-based, unlike bulk ipkon.
         * gencontelem_f2f writes count, master nodes, secondary nodes, then
         * quadrature index, secondary-face ordinal and primary-face data. */
        index = ipkon[e]-1;
        count = kon[index];
        masters = lakon[8*e+7]-'0';
        if (masters != 6 || count != 12)
            oxygen_contact_output_error("contact telemetry requires two quadratic triangles");
        point = kon[index+count+1]-1;
        face = kon[index+count+2]-1;
        if (point < 0 || point >= integration_points || face < 0)
            oxygen_contact_output_error("invalid contact quadrature identity");
        fprintf(stream, "{\"type\":\"point\",\"generated_element\":%" ITGFORMAT
            ",\"quadrature_id\":%" ITGFORMAT ",\"secondary_face\":%" ITGFORMAT
            ",\"primary_nodes\":", e+1, point+1, islavsurf[2*face]);
        oxygen_contact_nodes(stream, kon+index+1, masters);
        fputs(",\"secondary_nodes\":", stream);
        oxygen_contact_nodes(stream, kon+index+1+masters, count-masters);
        fputs(",\"secondary_adjustments_mm\":", stream);
        oxygen_contact_reals(stream, clearance_adjustments+27*face, 3*(count-masters));
        fputs(",\"secondary_uv_weight\":", stream);
        oxygen_contact_reals(stream, pslavsurf+3*point, 3);
        fputs(",\"primary_uv_face_normal\":", stream);
        oxygen_contact_reals(stream, pmastsurf+6*point, 6);
        fputs(",\"solver_area_mm2\":", stream);
        oxygen_contact_real(stream, springarea[2*point]);
        fputs(",\"first_step_clearance_correction_mm\":", stream);
        oxygen_contact_real(stream, springarea[2*point+1]);
        fputs(",\"contact_displacement_stress\":", stream);
        oxygen_contact_reals(stream, stress+6*mi[0]*e, 6);
        index = 2*mi[0]*(bulk_elements+point);
        fputs(",\"spring_energy_n_mm\":", stream);
        oxygen_contact_real(stream, energy[index]);
        fputs(",\"friction_increment_n_mm\":", stream);
        oxygen_contact_real(stream, energy[index+1]);
        if (state_count >= 9) {
            index = state_count*mi[0]*(bulk_elements+point);
            fputs(",\"state_plastic_relative_tangential\":", stream);
            oxygen_contact_reals(stream, state+index, 9);
            fputs(",\"initial_state_plastic_relative_tangential\":", stream);
            oxygen_contact_reals(stream, initial_state+index, 9);
        }
        fputs("}\n", stream);
        index = 2*mi[0]*(bulk_elements+point);
        spring_energy += energy[index];
        friction_increment += energy[index+1];
        point_count++;
        if (ftell(stream) > 536870912L)
            oxygen_contact_output_error("output exceeds 512 MiB; partition/refine the requested evidence budget");
    }
    fprintf(stream, "{\"type\":\"end\",\"points\":%" ITGFORMAT
        ",\"spring_energy_n_mm\":", point_count);
    oxygen_contact_real(stream, spring_energy);
    fputs(",\"friction_increment_n_mm\":", stream);
    oxygen_contact_real(stream, friction_increment);
    fputs("}\n", stream);
    if (ftell(stream) > 536870912L)
        oxygen_contact_output_error("output exceeds 512 MiB");
    if (fflush(stream) || ferror(stream))
        oxygen_contact_output_error("incomplete output write");
}
#endif
