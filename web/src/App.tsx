import { useCallback, useEffect, useRef, useState } from "react";
import { getDashboard, getStatus, listApprovals, setApiRole } from "./api";
import Sidebar, { type Badges } from "./components/Sidebar";
import TopBar from "./components/TopBar";
import { loadRole, saveRole, type Role } from "./role";
import Approvals from "./screens/Approvals";
import Criteria from "./screens/Criteria";
import Dashboard from "./screens/Dashboard";
import Decisions from "./screens/Decisions";
import JobProgress from "./screens/JobProgress";
import NewReview, { emptyDraft, type Draft } from "./screens/NewReview";
import Notices from "./screens/Notices";
import ReviewHome from "./screens/ReviewHome";
import RoleSelect from "./screens/RoleSelect";
import RunResult from "./screens/RunResult";
import { hashToView, viewToHash } from "./route";
import { menuOf, type Status, type View } from "./types";

const initialRole = loadRole();
if (initialRole) setApiRole(initialRole);

export default function App() {
  const [role, setRole] = useState<Role | null>(initialRole);
  const [view, setView] = useState<View>(() => hashToView(location.hash));
  const [status, setStatus] = useState<Status | null>(null);
  const [badges, setBadges] = useState<Badges>({ passed: 0, running: false, pending: 0, supplement: 0 });
  const [draft, setDraft] = useState<Draft>(emptyDraft);

  const refresh = useCallback(async () => {
    const [nextStatus, board, approvals] = await Promise.all([getStatus(), getDashboard(), listApprovals()]);
    setStatus(nextStatus);
    setBadges({ passed: board.cards.awaiting_start, running: !!nextStatus.running_job_id,
      pending: approvals.pending.length, supplement: approvals.supplement.length });
    return nextStatus;
  }, []);

  useEffect(() => {
    // Opening the app with no address: resume a running job's progress screen.
    const opened = location.hash;
    refresh().then((s) => {
      if (s.running_job_id && !opened) {
        const job: View = { view: "job", jobId: s.running_job_id };
        history.replaceState(null, "", viewToHash(job));
        setView(job);
      }
    });
    // Back/forward: the address is the source of truth (screen-only extras like notes are dropped)
    const onPop = () => setView(hashToView(location.hash));
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, [refresh]);

  const lastNew = useRef<View>({ view: "new" });
  const navigate = useCallback((next: View) => {
    if (next.view === "new") lastNew.current = next;  // "다시 시도" returns here, keeping the notice folder
    const hash = viewToHash(next);
    if (location.hash !== hash) history.pushState(null, "", hash);
    setView(next);
    refresh();
  }, [refresh]);
  const chooseRole = (next: Role | null) => {
    setRole(next);
    saveRole(next);
    if (next) {
      setApiRole(next);
      refresh();
    }
  };
  const onStarted = useCallback((jobId: string) => navigate({ view: "job", jobId }), [navigate]);
  const onDone = useCallback((runId: string) => {
    setDraft(emptyDraft);
    navigate({ view: "run", runId });
  }, [navigate]);
  const onRetry = useCallback(() => navigate(lastNew.current), [navigate]);

  if (!role) return <RoleSelect onSelect={chooseRole} />;
  return (
    <div className="app">
      <TopBar role={role} onSwitchRole={() => chooseRole(null)} roleNote={status?.role_note} />
      <div className="body">
        <Sidebar active={menuOf(view)} badges={badges} role={role} onNavigate={navigate} />
        <main className="main" key={`${menuOf(view)}-${role}`}>
          {view.view === "dashboard" && <Dashboard role={role} onNavigate={navigate} />}
          {view.view === "notices" && <Notices role={role} status={status} onNavigate={navigate} onChanged={refresh} />}
          {view.view === "approvals" && <Approvals role={role} onNavigate={navigate} />}
          {view.view === "decisions" && <Decisions onNavigate={navigate} />}
          {view.view === "criteria" && <Criteria role={role} />}
          {view.view === "reviews" && <ReviewHome role={role} runningJobId={status?.running_job_id ?? null} onNavigate={navigate} />}
          {view.view === "new" && <NewReview status={status} draft={draft} setDraft={setDraft} onStarted={onStarted}
            folderId={view.folderId} noticePdf={view.noticePdf} noticeName={view.noticeName} notes={view.notes} />}
          {view.view === "job" && <JobProgress jobId={view.jobId} notes={view.notes} onDone={onDone} onRetry={onRetry} onOpenReviews={() => navigate({ view: "reviews" })} />}
          {view.view === "run" && <RunResult key={view.runId} runId={view.runId} role={role} status={status} onChanged={refresh} onNavigate={navigate} />}
        </main>
      </div>
    </div>
  );
}
