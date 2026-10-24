import { useEffect, useState } from "react";
import { Button, Input, SelectControl } from "../../components/ui";
import type { DomainPanelProps, JsonObject } from "../contracts";
import "./platform.css";

type CatalogEntry = { directory: string; name?: string; revision?: number; archived?: boolean; error?: string };
type Review = { id: string; text: string; revision: number; state?: string; decision?: string };
type Status = { directory: string; revision: number; history_count: number; mode: string; project: {id: string}; review: { reviews: Review[]; checkpoints: Review[]; comments: Review[] }; limits: string[] };
const scope = () => window.location.pathname.match(/^\/p\/[^/]+/)?.[0] || "";
async function request(path: string, input?: unknown, method = "POST"): Promise<JsonObject> {
  const reply = await fetch(`${scope()}/api/platform/${path}`, input === undefined ? {} : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) });
  const body = await reply.json();
  if (!reply.ok) throw new Error(body.detail || `Request failed (${reply.status})`);
  return body;
}

export default function PlatformPanel({ project, selection, refresh, setError }: DomainPanelProps) {
  const [status, setStatus] = useState<Status | null>(null);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [name, setName] = useState("");
  const [title, setTitle] = useState(project.name);
  const [showArchived, setShowArchived] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [source, setSource] = useState("");
  const [baseRevision, setBaseRevision] = useState(project.revision);
  const [conflict, setConflict] = useState<JsonObject | null>(null);
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [role, setRole] = useState("reader");
  const [kind, setKind] = useState("human");
  const [accounts, setAccounts] = useState<Array<{id: string; name: string; role: string; revoked: boolean}>>([]);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const clientId = "platform-editor";
  const storageKey = status ? `oxygen-draft:${status.project.id}` : null;
  const dirty = source.trim().length > 0;
  async function reload() {
    const next = await request("status") as unknown as Status;
    setStatus(next);
    if (next.mode === "trusted-local") {
      const list = await request(`projects?archived=${showArchived}`);
      setCatalog(list.projects as CatalogEntry[]);
    }
  }
  useEffect(() => { reload().catch(error => setError(String(error))); }, [project.revision, showArchived]);
  useEffect(() => {
    if (!storageKey) return;
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      try { const draft = JSON.parse(saved); setSource(draft.source); setBaseRevision(draft.baseRevision); setMessage("Recovered this browser's draft. Saved source is unchanged."); }
      catch { setMessage("The browser draft could not be read. Saved source is unchanged."); }
    }
  }, [storageKey]);
  useEffect(() => {
    if (!storageKey || !source) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ source, baseRevision })); }
    catch { setMessage("Browser draft storage is unavailable. Use Save recovery draft before closing."); }
  }, [source, baseRevision, storageKey]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function act(operation: () => Promise<unknown>, success = "Saved") {
    setBusy(true); setMessage("");
    try { await operation(); await reload(); await refresh(); setMessage(success); }
    catch (error) { setError(String(error)); setMessage(String(error)); }
    finally { setBusy(false); }
  }
  const run = (operation: string, input: JsonObject = {}) => request(`operation/${operation}`, input);
  function clearDraft() {
    setSource(""); setConflict(null);
    if (storageKey) localStorage.removeItem(storageKey);
  }
  async function applyDraft() {
    const proposed = JSON.parse(source);
    const result = await run("reconcile", {base_revision: baseRevision, project: proposed});
    if ((result.conflicts as unknown[]).length) { setConflict(result); setMessage("Concurrent edits conflict. Compare the paths below and revise your draft before applying."); return; }
    const requestId = crypto.randomUUID();
    await request("commands", {request_id: requestId, command: {expected_revision: result.expected_revision, actor: "human", project: result.project, summary: "Apply recovered source draft"}});
    await run("draft_discard", {id: clientId}); clearDraft();
  }
  return <div className="platform-panel">
    <h3>Projects and recovery</h3>
    <p>Saved revision {project.revision}{status ? ` · ${status.history_count} retained revisions · ${status.mode}` : ""}</p>
    <div className="platform-actions">
      <a href={`${scope()}/api/platform/portable`} download>Download complete project</a>
      <Button disabled={busy} onClick={() => act(async () => { const r = await run("recover"); setMessage(JSON.stringify(r)); }, "Recovery inspected; saved project reopened")}>Inspect recovery</Button>
    </div>
    <p className="platform-note">Local source, assets and saved history work without a cloud account. An installed CAD and solver runtime is required. Archives exclude credentials, unsaved drafts and jobs.</p>
    <details><summary>Local projects</summary>
      <label>Current project title<Input value={title} onChange={event => setTitle(event.target.value)} /></label>
      <Button disabled={busy || !title || !status} onClick={() => act(() => request(`projects/${encodeURIComponent(status?.directory || "")}/rename`, {name: title, expected_revision: project.revision}), "Project renamed")}>Rename current project</Button>
      <label>Directory name<Input value={name} onChange={event => setName(event.target.value)} placeholder="new-project" /></label>
      <div className="platform-actions">
        <Button disabled={busy || !name} onClick={() => act(() => request(`projects/${encodeURIComponent(name)}/create`, {name}), "Project created")}>Create</Button>
        <Button disabled={busy || !name || !status} onClick={() => act(() => request(`projects/${encodeURIComponent(name)}/duplicate`, {source: status?.directory}), "Complete project duplicated")}>Duplicate current</Button>
      </div>
      <label>Restore a portable project or release<input type="file" accept=".zip" onChange={event => setRestoreFile(event.target.files?.[0] || null)} /></label>
      <Button disabled={busy || !restoreFile || !name} onClick={() => act(async () => {
        const response = await fetch(`${scope()}/api/platform/restore?name=${encodeURIComponent(name)}`, {method: "POST", body: restoreFile});
        if (!response.ok) throw new Error((await response.json()).detail);
      }, "Project restored with dependencies and history")}>Restore into new directory</Button>
      <Button disabled={busy} onClick={() => setShowArchived(value => !value)}>{showArchived ? "Show active projects" : "Show archived projects"}</Button>
      {catalog.map(item => <div key={item.directory} className="platform-project"><a href={`/p/${encodeURIComponent(item.directory)}/`} target="_blank" rel="noreferrer">{item.name || item.directory}</a><span>{item.error || `r${item.revision}`}</span><Button disabled={busy} onClick={() => act(() => request(`projects/${encodeURIComponent(item.directory)}/archive`, {archived: !item.archived}), item.archived ? "Project reopened" : "Project archived")}>{item.archived ? "Reopen" : "Archive"}</Button></div>)}
      {status?.mode !== "trusted-local" && <p>Catalog changes require trusted local mode. Each authenticated project has separate credentials.</p>}
    </details>
    <details><summary>Recoverable source draft</summary>
      <p>Your draft is kept in this browser as you type. Save a recovery draft to also retain it in the project directory.</p>
      <div className="platform-actions">
        <Button disabled={busy} onClick={() => { setSource(JSON.stringify(project, null, 2)); setBaseRevision(project.revision); }}>Start from saved source</Button>
        <Button disabled={busy} onClick={() => act(async () => { const draft = await run("draft_load", {id: clientId}); if (draft.project) { setSource(JSON.stringify(draft.project, null, 2)); setBaseRevision(draft.base_revision as number); } }, "Recovery draft loaded")}>Load recovery draft</Button>
      </div>
      <textarea aria-label="Recoverable project source" value={source} onChange={event => setSource(event.target.value)} spellCheck={false} rows={12} />
      <p>{dirty ? `Unsaved draft based on revision ${baseRevision}` : "No unsaved source draft"}</p>
      <div className="platform-actions">
        <Button disabled={busy || !dirty} onClick={() => act(() => run("draft_save", {id: clientId, base_revision: baseRevision, project: JSON.parse(source)}), "Recovery draft saved; live project unchanged")}>Save recovery draft</Button>
        <Button disabled={busy || !dirty} onClick={() => act(applyDraft, "Draft reconciliation completed")}>Reconcile and apply</Button>
        <Button disabled={busy || !dirty} onClick={() => act(async () => { await run("draft_discard", {id: clientId}); clearDraft(); }, "Draft discarded")}>Discard draft</Button>
      </div>
      {conflict && <pre>{JSON.stringify(conflict.conflicts, null, 2)}</pre>}
    </details>
    <details><summary>Checkpoints and exact revision review</summary>
      <label>Review note<Input value={note} onChange={event => setNote(event.target.value)} placeholder="What was verified or needs changes?" /></label>
      <div className="platform-actions">
        <Button disabled={busy || !note} onClick={() => act(() => run("checkpoint", {revision: project.revision, text: note}), "Named checkpoint saved")}>Checkpoint</Button>
        <Button disabled={busy || !note} onClick={() => act(() => request("comments", {revision: project.revision, text: note, anchor: selection ? {selection} : undefined}), "Revision comment saved")}>Comment</Button>
        <Button disabled={busy || !note} onClick={() => act(() => request("reviews", {revision: project.revision, text: note, decision: "approved"}), "Exact revision approved")}>Approve revision</Button>
      </div>
      {status?.review.reviews.map(review => <p key={review.id}>r{review.revision} · {review.decision} · {review.state}: {review.text}</p>)}
      {status?.review.checkpoints.map(checkpoint => <p key={checkpoint.id}>Checkpoint r{checkpoint.revision}: {checkpoint.text}</p>)}
    </details>
    <details><summary>Project access</summary>
      <p>Credentials protect this project's HTTP API. Local filesystem owners and CLI users remain administrators. Keep credentials outside project archives.</p>
      <label>Project credential<Input type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} /></label>
      <Button disabled={busy || !token} onClick={() => act(async () => { await request("session", {token}); setToken(""); }, "Signed in to this project")}>Sign in</Button>
      {status?.mode === "trusted-local" && <Button disabled={busy} onClick={() => act(async () => { const result = await request("identity/enable", {name: name || "Project owner"}); setSecret(result.token as string); }, "Project credentials enabled; save the owner credential")}>Enable project credentials</Button>}
      {status?.mode === "authenticated-project" && <>
        <label>Member name<Input value={name} onChange={event => setName(event.target.value)} /></label>
        <SelectControl aria-label="Member role" value={role} onChange={event => setRole(event.target.value)}>{["viewer", "reader", "editor", "manager", "administrator"].map(value => <option key={value}>{value}</option>)}</SelectControl>
        <SelectControl aria-label="Credential kind" value={kind} onChange={event => setKind(event.target.value)}><option>human</option><option>agent</option></SelectControl>
        <Button disabled={busy || !name} onClick={() => act(async () => { const result = await request("identity/issue", {name, role, kind}); setSecret(result.token as string); }, "Credential issued; copy it once")}>Issue credential</Button>
        <Button disabled={busy} onClick={() => act(async () => { const result = await request("identity"); setAccounts(result.accounts as typeof accounts); }, "Access list loaded")}>List access</Button>
        {accounts.map(account => <div className="platform-project" key={account.id}><span>{account.name} · {account.role}{account.revoked ? " · revoked" : ""}</span><Button disabled={busy || account.revoked} onClick={() => act(() => request("identity/revoke", {id: account.id}), "Credential revoked")}>Revoke</Button></div>)}
      </>}
      {secret && <label>Copy this credential now<Input readOnly value={secret} /><Button onClick={() => setSecret("")}>Hide credential</Button></label>}
    </details>
    {message && <p role="status">{message}</p>}
  </div>;
}
