// Bounded browser waits with the actual asynchronous job states in failures.
const fs = require("node:fs");
const path = require("node:path");

async function beginOperation(page, request, label, timeout, evidenceDir) {
  const initial = await request.get("/api/state", { timeout: 10000 });
  if (!initial.ok())
    throw Error(`${label}: initial state HTTP ${initial.status()}`);
  const before = new Set((await initial.json()).jobs.map((job) => job.id));
  const deadline = Date.now() + timeout;
  const writes = [];
  const stateReadErrors = [];
  let state;
  const responseFailure = async (response) => {
    if (response.request().method() === "GET" || response.ok()) return;
    if (!new URL(response.url()).pathname.startsWith("/api/")) return;
    writes.push({ url: response.url(), status: response.status() });
  };
  page.on("response", responseFailure);
  return async (ready) => {
    try {
      while (Date.now() < deadline) {
        if (writes.length) throw Error("Application write failed");
        let response;
        try {
          response = await request.get("/api/state", {
            timeout: Math.max(1, Math.min(10000, deadline - Date.now())),
          });
        } catch (error) {
          // A synchronous native command can keep state reads busy on two CPUs.
          // Its overall deadline still applies; server errors and failed jobs do not retry.
          if (error.name !== "TimeoutError") throw error;
          stateReadErrors.push(error.message.split("\n")[0]);
          if (stateReadErrors.length > 3) stateReadErrors.shift();
          continue;
        }
        if (!response.ok()) throw Error(`State HTTP ${response.status()}`);
        state = await response.json();
        const failed = state.jobs.filter(
          (job) =>
            !before.has(job.id) && ["failed", "cancelled"].includes(job.state),
        );
        if (failed.length)
          throw Error(
            `Job ${failed[0].id} ${failed[0].state}: ${failed[0].message}`,
          );
        if (writes.length) throw Error("Application write failed");
        if (await ready(state)) return state;
        await new Promise((resolve) =>
          setTimeout(
            resolve,
            Math.min(1000, Math.max(0, deadline - Date.now())),
          ),
        );
      }
      throw Error(`Operation budget expired after ${timeout}ms`);
    } catch (error) {
      const diagnostics = {
        label,
        timeout_ms: timeout,
        error: String(error),
        writes,
        state_read_errors: stateReadErrors,
        revision: state?.project?.revision,
        geometry_state: state?.geometry_state,
        jobs: state?.jobs?.map(
          ({
            id,
            kind,
            state,
            revision,
            study_id,
            message,
            created_at,
            started_at,
            finished_at,
          }) => ({
            id,
            kind,
            state,
            revision,
            study_id,
            message,
            created_at,
            started_at,
            finished_at,
            new_in_operation: !before.has(id),
          }),
        ),
      };
      try {
        diagnostics.alerts = await page.getByRole("alert").allTextContents();
        diagnostics.status = await page.getByRole("status").allTextContents();
      } catch {
        /* The original failure remains useful if the page has closed. */
      }
      if (evidenceDir) {
        fs.mkdirSync(evidenceDir, { recursive: true });
        const filename = path.join(
          evidenceDir,
          `operation-${label.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`,
        );
        fs.writeFileSync(
          filename + ".json",
          JSON.stringify(diagnostics, null, 2),
        );
        try {
          await page.screenshot({
            path: filename + ".png",
            fullPage: true,
            timeout: 5000,
          });
        } catch {}
      }
      throw Error(
        `${label}: ${error.message}\n${JSON.stringify(diagnostics, null, 2)}`,
        { cause: error },
      );
    } finally {
      page.off("response", responseFailure);
    }
  };
}

module.exports = { beginOperation };
