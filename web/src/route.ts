// Screen ↔ address (#/run/<id>) so reload, back/forward and shared links land on the same screen.
// Hash routing: the server keeps serving one index.html, no fallback route needed.
import type { View } from "./types";

const PLAIN = ["dashboard", "notices", "approvals", "decisions", "criteria", "reviews"] as const;

export function viewToHash(view: View): string {
  if (view.view === "run") return `#/run/${encodeURIComponent(view.runId)}`;
  if (view.view === "job") return `#/job/${encodeURIComponent(view.jobId)}`;
  if (view.view === "new") return view.folderId ? `#/new?folder=${encodeURIComponent(view.folderId)}` : "#/new";
  return `#/${view.view}`;
}

export function hashToView(hash: string): View {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const [name, id] = path.split("/").map(decodeURIComponent);
  if (name === "run" && id) return { view: "run", runId: id };
  if (name === "job" && id) return { view: "job", jobId: id };
  if (name === "new") {
    const folderId = new URLSearchParams(query).get("folder");
    return folderId ? { view: "new", folderId } : { view: "new" };
  }
  return (PLAIN as readonly string[]).includes(name) ? { view: name as (typeof PLAIN)[number] } : { view: "dashboard" };
}
