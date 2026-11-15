import { useEffect, useState } from "react";
import { api, type Job, type ViewListing } from "./api";

const STAGES: Record<Job["status"], string> = {
  queued: "Waiting for the previous analysis to finish",
  fetching: "Fetching one pinned commit from GitHub",
  extracting: "Reading code and build files",
  awaiting_confirmation: "Waiting for you: this model costs money",
  summarizing: "Writing summaries (class, module, repo)",
  viewing: "Choosing how to present it, then checking every reference",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled; nothing was spent",
};

export function Home({ onOpen }: { onOpen: (name: string) => void }) {
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [views, setViews] = useState<ViewListing[]>([]);

  useEffect(() => {
    api.views().then(setViews).catch(() => setViews([]));
  }, [job?.status]);

  useEffect(() => {
    if (!job || ["done", "failed", "cancelled"].includes(job.status)) return;
    const timer = window.setTimeout(() => {
      api.job(job.id).then(setJob).catch((e: Error) => setError(e.message));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [job]);

  useEffect(() => {
    if (job?.status === "done" && job.view) onOpen(job.view);
  }, [job, onOpen]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      setJob(await api.start(url, ref));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const busy = job !== null && !["done", "failed", "cancelled"].includes(job.status);

  return (
    <main className="home">
      <h1>Explain a GitHub repository</h1>
      <p className="lead">
        Paste a public repository. It is read at one pinned commit, nothing from it is run, and
        every box and line in the result points back to the code.
      </p>
      <form onSubmit={submit} className="analyze">
        <input
          aria-label="Repository URL"
          placeholder="https://github.com/spring-projects/spring-petclinic"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
        />
        <input aria-label="Branch, tag or commit" placeholder="branch / tag / commit (optional)"
          value={ref} onChange={(e) => setRef(e.target.value)} className="ref" />
        <button type="submit" disabled={busy}>Analyze</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      {job && <JobStatus job={job} onChange={setJob} onError={setError} />}

      <h2>Analyzed</h2>
      {views.length === 0 ? <p className="muted">Nothing yet.</p> : (
        <ul className="recent">
          {views.map((v) => (
            <li key={v.name}>
              <button onClick={() => onOpen(v.name)}>{v.repo.replace("https://github.com/", "")}</button>
              <span className="muted"> @ {v.commit.slice(0, 7)} · {v.layout} · {v.runtime} · {v.model}</span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function JobStatus({ job, onChange, onError }: {
  job: Job; onChange: (job: Job) => void; onError: (message: string) => void;
}) {
  const percent = job.total > 0 ? Math.round((100 * job.completed) / job.total) : 0;
  const act = (action: (id: string) => Promise<Job>) =>
    action(job.id).then(onChange).catch((e: Error) => onError(e.message));
  return (
    <section className={`job job-${job.status}`} aria-live="polite">
      <strong>{STAGES[job.status]}</strong>
      {job.status === "summarizing" && (
        <>
          <progress max={job.total} value={job.completed} />
          <span className="muted"> {job.completed} / {job.total} requests ({percent}%) · a local
            model takes a few seconds each</span>
        </>
      )}
      {job.status === "awaiting_confirmation" && job.estimate && (
        <div className="confirm">
          <p>Estimated cost: at least ${job.estimate.min_usd.toFixed(2)}, at most
            ${job.estimate.max_usd.toFixed(2)} (cap ${job.estimate.cap_usd.toFixed(2)}).</p>
          <button onClick={() => act(api.confirm)}>Spend it</button>
          <button className="secondary" onClick={() => act(api.cancel)}>Cancel</button>
        </div>
      )}
      {job.status === "failed" && <p className="error">{job.error}</p>}
    </section>
  );
}
