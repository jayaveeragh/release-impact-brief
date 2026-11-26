import { useState } from "react";
import { api, sourceUrl, type ChatAnswer, type ChatContext, type NeedsConfirmation } from "./api";

// Ask about the code at any level. Every statement shown carries citations to real lines;
// statements the model couldn't cite were removed by the server and are listed as such.
export function ChatPanel({ name, repo, commit, context }: {
  name: string; repo: string; commit: string; context: ChatContext;
}) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<ChatAnswer[]>([]);
  const [pending, setPending] = useState<NeedsConfirmation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (confirm: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.chat(name, question, context, confirm);
      if ("needs_confirmation" in result) {
        setPending(result);
      } else {
        setPending(null);
        setHistory((h) => [result, ...h]);
        setQuestion("");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const where = context.kind === "repo" ? "this repository" : `this ${context.kind}`;
  return (
    <section className="chat">
      <h2>Ask about {where}</h2>
      <form onSubmit={(e) => { e.preventDefault(); if (question.trim()) send(false); }}>
        <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={2}
          placeholder="e.g. What happens if the last name is empty?" aria-label="Question" />
        <button className="primary" disabled={busy || !question.trim()}>
          {busy ? "Reading the code… (a local model takes ~30 s)" : "Ask"}</button>
      </form>
      <p className="muted small">Answers only from this commit's code, and every statement links to the lines
        it comes from. It never runs or changes anything.</p>
      {error && <p className="error">{error}</p>}
      {pending && (
        <div className="confirm">
          <p>This model costs money: up to ${pending.estimate.max_usd.toFixed(3)}.</p>
          <button disabled={busy} onClick={() => send(true)}>Spend it</button>
        </div>
      )}
      {history.map((answer, i) => (
        <article key={i} className="answer">
          <h3>{answer.question}</h3>
          {answer.segments.length === 0
            ? <p className="muted">No statement could be backed by the code it was shown.</p>
            : <ul>{answer.segments.map((s, j) => (
              <li key={j}>{s.text}{" "}{s.citations.map((c) => (
                <a key={c.id} className="cite" href={sourceUrl(repo, commit, c.file, c.start)}
                  target="_blank" rel="noreferrer" title={c.label}>
                  {c.file.split("/").pop()}:{c.start}{c.end > c.start ? `–${c.end}` : ""}</a>))}</li>))}</ul>}
          {answer.not_settled.length > 0 && (
            <div className="not-settled"><strong>Not settled by the code:</strong>
              <ul>{answer.not_settled.map((t, j) => <li key={j}>{t}</li>)}</ul></div>)}
          {answer.removed.length > 0 && (
            <details className="muted small"><summary>{answer.removed.length} uncited statement(s) removed</summary>
              <ul>{answer.removed.map((r, j) => <li key={j}>{r.text} ({r.reason})</li>)}</ul></details>)}
        </article>
      ))}
    </section>
  );
}
