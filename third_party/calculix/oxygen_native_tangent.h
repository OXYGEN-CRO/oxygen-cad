/* SPDX-License-Identifier: GPL-2.0-only
 * Read-only Newton linearization output for the pinned CalculiX 2.23 source.
 * The assembly state is deliberately distinct from the accepted output state.
 */
#ifndef OXYGEN_NATIVE_TANGENT_H
#define OXYGEN_NATIVE_TANGENT_H
#include <stdint.h>

static ITG oxygen_tangent_step = -1, oxygen_tangent_increment = -1;
static ITG oxygen_tangent_iteration = -1, oxygen_tangent_nodes = 0;
static ITG oxygen_tangent_equations = 0;
static double oxygen_tangent_time = 0.;
static unsigned long long oxygen_tangent_serial = 0, oxygen_tangent_bytes = 0;
static unsigned long long oxygen_tangent_solved_serial = 0;
static const char *oxygen_tangent_unavailable = NULL;

static int oxygen_tangent_enabled(void) {
    const char *value = getenv("O3D_CCX_NATIVE_TANGENT");
    return value && !strcmp(value, "1");
}

static void oxygen_tangent_name(char *target, size_t capacity,
                                const char *jobname, const char *suffix) {
    if (strnlen(jobname, 132) > 120 ||
        snprintf(target, capacity, "%s%s", jobname, suffix) >= (int)capacity)
        oxygen_contact_output_error("native tangent filename exceeds budget");
}

static void oxygen_tangent_write(FILE *stream, const void *values,
                                 size_t width, size_t count) {
    if (fwrite(values, width, count, stream) != count)
        oxygen_contact_output_error("cannot write native tangent");
}

static void oxygen_native_tangent_stage(
    const char *jobname, ITG step, ITG increment, ITG iteration, double time,
    ITG nodes, const ITG *mi, const ITG *nactdof, ITG equations,
    ITG capacity, ITG upper_offset, ITG asymmetric, const ITG *jq,
    const ITG *irow, const double *ad, const double *au,
    const double *assembly_displacements, const double *rhs,
    ITG mpcs, ITG penalty_scale) {
    FILE *stream;
    char filename[256];
    ITG entries, stride = mi[1] + 1;
    uint16_t byte_order = 1;
    if (!oxygen_tangent_enabled()) return;
    oxygen_tangent_step = step; oxygen_tangent_increment = increment;
    oxygen_tangent_iteration = iteration; oxygen_tangent_time = time;
    oxygen_tangent_nodes = nodes; oxygen_tangent_unavailable = NULL;
    oxygen_tangent_equations = equations;
    ++oxygen_tangent_serial;
    if (equations < 1 || nodes < 1 || stride < 4 || stride > 8) {
        oxygen_tangent_unavailable = "unsupported_equation_or_node_layout";
        return;
    }
    if (mpcs) {
        oxygen_tangent_unavailable = "multipoint_constraint_expansion_not_exported";
        return;
    }
    entries = jq[equations] - 1;
    if (jq[0] != 1 || entries < 0 || entries > capacity ||
        (asymmetric != 0 && asymmetric != 1) ||
        (asymmetric && upper_offset < entries))
        oxygen_contact_output_error("invalid native tangent sparse layout");
    if (equations > 30000 || entries > 8000000 || nodes > 100000) {
        oxygen_tangent_unavailable = "native_tangent_resource_budget";
        return;
    }
    oxygen_tangent_name(filename, sizeof(filename), jobname, ".native-tangent.pending");
    stream = fopen(filename, "wb");
    if (!stream) oxygen_contact_output_error("cannot create native tangent staging file");
    fprintf(stream, "{\"schema\":1,\"layout\":\"newton-rhs-correction-accepted-v1\",\"step\":%" ITGFORMAT
        ",\"increment\":%" ITGFORMAT ",\"iteration\":%" ITGFORMAT
        ",\"nodes\":%" ITGFORMAT ",\"stride\":%" ITGFORMAT
        ",\"equations\":%" ITGFORMAT ",\"off_diagonal\":%" ITGFORMAT
        ",\"asymmetric\":%" ITGFORMAT ",\"penalty_scale\":%" ITGFORMAT
        ",\"integer_bytes\":%zu,\"real_bytes\":%zu,\"byte_order\":\"%s\",\"assembly_time\":",
        step, increment, iteration, nodes, stride, equations, entries,
        asymmetric, penalty_scale, sizeof(ITG), sizeof(double),
        *(unsigned char *)&byte_order ? "little" : "big");
    oxygen_contact_real(stream, time); fputs("}\n", stream);
    oxygen_tangent_write(stream, ad, sizeof(double), equations);
    oxygen_tangent_write(stream, au, sizeof(double), entries);
    if (asymmetric) oxygen_tangent_write(stream, au + upper_offset, sizeof(double), entries);
    oxygen_tangent_write(stream, jq, sizeof(ITG), equations + 1);
    oxygen_tangent_write(stream, irow, sizeof(ITG), entries);
    oxygen_tangent_write(stream, nactdof, sizeof(ITG), (size_t)stride * nodes);
    oxygen_tangent_write(stream, assembly_displacements, sizeof(double), (size_t)stride * nodes);
    oxygen_tangent_write(stream, rhs, sizeof(double), equations);
    if (fclose(stream)) oxygen_contact_output_error("cannot close native tangent staging file");
}

static void oxygen_native_tangent_solved(const char *jobname, ITG equations,
                                         const double *correction) {
    char filename[256];
    FILE *stream;
    if (!oxygen_tangent_enabled() || oxygen_tangent_unavailable) return;
    if (!oxygen_tangent_serial || oxygen_tangent_equations != equations ||
        oxygen_tangent_solved_serial == oxygen_tangent_serial)
        oxygen_contact_output_error("unmatched native tangent solve");
    oxygen_tangent_name(filename, sizeof(filename), jobname, ".native-tangent.pending");
    stream = fopen(filename, "ab");
    if (!stream) oxygen_contact_output_error("cannot append native Newton correction");
    oxygen_tangent_write(stream, correction, sizeof(double), equations);
    if (fclose(stream)) oxygen_contact_output_error("cannot close native Newton correction");
    oxygen_tangent_solved_serial = oxygen_tangent_serial;
}

static void oxygen_native_tangent_commit(const char *jobname, ITG step,
    ITG increment, double time, ITG nodes, const ITG *mi,
    const double *accepted_displacements) {
    static FILE *manifest = NULL;
    static unsigned long long accepted = 0;
    FILE *source, *target;
    char filename[256], suffix[80], pending[256], buffer[65536];
    size_t count;
    long bytes = 0;
    ITG node;
    if (!oxygen_tangent_enabled()) return;
    if (!manifest) {
        oxygen_tangent_name(filename, sizeof(filename), jobname, ".native-tangent.jsonl");
        manifest = fopen(filename, "wb");
        if (!manifest) oxygen_contact_output_error("cannot create native tangent manifest");
        fputs("{\"type\":\"header\",\"schema\":1,\"upstream\":\"2.23\","
              "\"instrumentation\":\"native-newton-linearization-v1\","
              "\"source_sha256\":\"9c88385c10fb04f5dc6c4e98027a51bebdd8aee3920e05190d6c1dd08357d6e7\"}\n", manifest);
    }
    ++accepted;
    if (step != oxygen_tangent_step || increment != oxygen_tangent_increment ||
        nodes != oxygen_tangent_nodes || !oxygen_tangent_serial ||
        fabs(time - oxygen_tangent_time) > 1e-10 * fmax(fabs(time), 1.))
        oxygen_tangent_unavailable = "no_matching_newton_linearization";
    if (!oxygen_tangent_unavailable &&
        oxygen_tangent_solved_serial != oxygen_tangent_serial)
        oxygen_tangent_unavailable = "no_matching_newton_linearization";
    source = NULL;
    if (!oxygen_tangent_unavailable) {
        oxygen_tangent_name(pending, sizeof(pending), jobname, ".native-tangent.pending");
        source = fopen(pending, "rb");
        if (!source || fseek(source, 0, SEEK_END))
            oxygen_contact_output_error("missing native tangent staging file");
        bytes = ftell(source);
        if (bytes < 0) oxygen_contact_output_error("native tangent size unavailable");
        bytes += (long)((size_t)nodes * 3 * sizeof(double));
        rewind(source);
        if ((unsigned long long)bytes + oxygen_tangent_bytes > 536870912ULL)
            oxygen_tangent_unavailable = "native_tangent_total_output_budget";
    }
    fprintf(manifest, "{\"type\":\"frame\",\"step\":%" ITGFORMAT
        ",\"increment\":%" ITGFORMAT ",\"iteration\":%" ITGFORMAT
        ",\"assembly_serial\":%llu,\"accepted_time\":", step, increment,
        oxygen_tangent_iteration, oxygen_tangent_serial);
    oxygen_contact_real(manifest, time);
    if (oxygen_tangent_unavailable) {
        fprintf(manifest, ",\"available\":false,\"reason\":\"%s\"}\n", oxygen_tangent_unavailable);
        if (source) fclose(source);
    } else {
        snprintf(suffix, sizeof(suffix), ".native-tangent-%08llu.bin", accepted);
        oxygen_tangent_name(filename, sizeof(filename), jobname, suffix);
        target = fopen(filename, "wb");
        if (!target) oxygen_contact_output_error("cannot create accepted native tangent file");
        while ((count = fread(buffer, 1, sizeof(buffer), source)))
            oxygen_tangent_write(target, buffer, 1, count);
        if (ferror(source) || fclose(target))
            oxygen_contact_output_error("native tangent copy failed");
        fclose(source);
        target = fopen(filename, "ab");
        if (!target) oxygen_contact_output_error("cannot append accepted native displacement");
        for (node = 0; node < nodes; ++node) {
            oxygen_tangent_write(target, accepted_displacements + (mi[1] + 1) * node + 1, sizeof(double), 3);
        }
        if (fclose(target)) oxygen_contact_output_error("cannot close accepted native displacement");
        oxygen_tangent_bytes += (unsigned long long)bytes;
        fprintf(manifest, ",\"available\":true,\"file\":\"%s\",\"bytes\":%ld}\n", filename, bytes);
    }
    if (fflush(manifest)) oxygen_contact_output_error("cannot flush native tangent manifest");
}
#endif
