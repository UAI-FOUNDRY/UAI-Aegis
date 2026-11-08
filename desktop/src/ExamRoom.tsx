import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage, isApiError, Receipt, RunOut, SessionView, Status } from "./api";
import CodeEditor from "./CodeEditor";
import DataFiles from "./DataFiles";
import { renderMarkdown } from "./markdown";
import { Kind, questionKind, SampleView, TestResultView } from "./testdata";

const HEARTBEAT_MS = 10_000; // CLIENT_PROTOCOL.md section 4
const AUTOSAVE_MS = 5_000;

type Answer = {
  language: string;
  code: string;
  /** Last revision the server confirmed; null = unknown (after Run/Submit, which also save). */
  revision: number | null;
  dirty: boolean;
  savedAt: Date | null;
};

type Result = { kind: "run" | "submit"; out: RunOut } | { kind: "error"; message: string };

function answersFrom(view: SessionView, existing: Record<string, Answer> = {}): Record<string, Answer> {
  const out: Record<string, Answer> = {};
  for (const q of view.questions) {
    if (existing[q.id]) {
      out[q.id] = existing[q.id]; // keep what is on screen (it is the newest)
      continue;
    }
    const language = q.draft?.language ?? q.languages[0]?.key ?? "";
    out[q.id] = {
      language,
      code: q.draft?.code ?? q.starter_code[language] ?? "",
      revision: q.draft?.revision ?? null,
      dirty: Boolean(q.draft?.unsynced),
      savedAt: q.draft && !q.draft.unsynced ? new Date(q.draft.updated_at) : null,
    };
  }
  return out;
}

function statusFrom(v: SessionView): Status {
  return {
    status: v.status,
    lock_reason: v.lock_reason,
    strikes: v.strikes,
    strikes_left: Math.max(0, v.max_strikes - v.strikes),
    server_now: v.server_now,
    deadline_at: v.deadline_at,
    seconds_left: v.seconds_left,
    device_change_pending: v.device_change_pending,
  };
}

function formatTime(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

const VERDICTS: Record<string, string> = {
  AC: "Passed",
  PA: "Partly correct",
  WA: "Wrong answer",
  TLE: "Time limit exceeded",
  MLE: "Memory limit exceeded",
  RE: "Runtime error",
  CE: "Compile error",
  IE: "Server error",
};

export default function ExamRoom({
  initial,
  onLeave,
  onSignedOut,
}: {
  initial: SessionView;
  onLeave: () => void;
  onSignedOut: () => void;
}) {
  const sid = initial.session_id;
  const [view, setView] = useState<SessionView>(initial);
  const [status, setStatus] = useState<Status>(() => statusFrom(initial));
  const statusRef = useRef(status.status);
  statusRef.current = status.status;

  const [answers, setAnswers] = useState<Record<string, Answer>>(() => answersFrom(initial));
  const answersRef = useRef(answers);
  const updateAnswer = useCallback((qid: string, patch: Partial<Answer>) => {
    answersRef.current = { ...answersRef.current, [qid]: { ...answersRef.current[qid], ...patch } };
    setAnswers(answersRef.current);
  }, []);

  const [current, setCurrent] = useState(0);
  const [results, setResults] = useState<Record<string, Result>>({});
  const [busy, setBusy] = useState<Record<string, "run" | "submit" | undefined>>({});
  const [warning, setWarning] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmFinish, setConfirmFinish] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [finished, setFinished] = useState<Receipt[] | null>(
    initial.status === "submitted" || initial.status === "terminated" ? initial.receipts : null,
  );
  const [lastBeat, setLastBeat] = useState<{ at: Date; ok: boolean; note?: string } | null>(null);
  // The professor moved this exam to the student's other laptop (or kept it there).
  const [moved, setMoved] = useState(false);
  const deadlineRef = useRef(initial.deadline_at);

  // Server clock minus laptop clock: the countdown follows the server (section 4).
  const offset = useRef(Date.parse(initial.server_now) - Date.now());
  const [secondsLeft, setSecondsLeft] = useState(initial.seconds_left);

  // ------------------------------------------------------------------ ending

  const endExam = useCallback(async () => {
    setStatus((s) => ({ ...s, status: "submitted" }));
    try {
      const v = await api.getSession(sid);
      setFinished(v.receipts);
    } catch {
      setFinished([]);
    }
  }, [sid]);

  /** Shared handling for save/run/submit/heartbeat errors (section 6). Returns true if handled. */
  const handleError = useCallback(
    (err: unknown, qid?: string): boolean => {
      if (!isApiError(err)) {
        setWarning(errorMessage(err));
        return false;
      }
      switch (err.code) {
        case "session_expired":
          onSignedOut();
          return true;
        case "network_error":
          setOffline(true);
          return true;
        case "stale_revision":
          // Only this laptop can write this session, so our code is the newest:
          // adopt the server's revision and the next autosave sends it again.
          if (qid) updateAnswer(qid, { revision: (err.details?.revision as number | undefined) ?? null });
          return true;
        case "session_locked":
          setStatus((s) => ({ ...s, status: "locked" }));
          return true;
        case "time_over":
        case "session_finished":
          endExam();
          return true;
        case "stale_signature":
          setWarning("Your laptop's date or time is wrong. Turn on \"Set time automatically\" in system settings.");
          return true;
        case "device_mismatch":
          setWarning("This exam is running on another laptop.");
          return true;
        default:
          setWarning(err.message);
          return false;
      }
    },
    [endExam, onSignedOut, updateAnswer],
  );

  // ------------------------------------------------------------------ autosave (section 4)

  const saving = useRef(new Set<string>());
  const saveOne = useCallback(
    async (qid: string): Promise<boolean> => {
      const a = answersRef.current[qid];
      if (!a || !a.dirty) return true;
      if (saving.current.has(qid) || statusRef.current !== "active") return false;
      saving.current.add(qid);
      const sent = { code: a.code, language: a.language };
      try {
        const r = await api.saveAnswer(sid, qid, sent.language, sent.code, a.revision);
        const now = answersRef.current[qid];
        updateAnswer(qid, {
          revision: r.revision,
          dirty: now.code !== sent.code || now.language !== sent.language,
          savedAt: new Date(),
        });
        setOffline(false);
        return true;
      } catch (err) {
        handleError(err, qid);
        return false;
      } finally {
        saving.current.delete(qid);
      }
    },
    [sid, handleError, updateAnswer],
  );

  useEffect(() => {
    if (finished || moved) return;
    const id = window.setInterval(() => {
      for (const qid of Object.keys(answersRef.current)) saveOne(qid);
    }, AUTOSAVE_MS);
    return () => window.clearInterval(id);
  }, [finished, moved, saveOne]);

  // ------------------------------------------------------------------ heartbeat (section 4)

  // One heartbeat at a time. The ref survives React's dev-mode double mount, which is
  // what sent two at once before (and logged the same heartbeat_gap twice).
  const beating = useRef(false);
  const beat = useCallback(async () => {
    if (beating.current) return;
    beating.current = true;
    const before = statusRef.current;
    try {
      const s = await api.heartbeat(sid);
      offset.current = Date.parse(s.server_now) - Date.now();
      const added = Date.parse(s.deadline_at) - Date.parse(deadlineRef.current);
      deadlineRef.current = s.deadline_at;
      if (added >= 30_000 && before !== "locked") {
        const minutes = Math.round(added / 60_000);
        setNotice(`Your professor gave you ${minutes} more minute${minutes === 1 ? "" : "s"}.`);
        window.setTimeout(() => setNotice(null), 10_000);
      }
      setStatus(s);
      setOffline(false);
      setLastBeat({ at: new Date(), ok: true });
      if (s.status === "submitted" || s.status === "terminated") {
        await endExam();
      } else if (before === "locked" && s.status === "active") {
        // Professor resumed (or approved this laptop): reload questions, keep the code on screen.
        const v = await api.getSession(sid);
        setView(v);
        answersRef.current = answersFrom(v, answersRef.current);
        setAnswers(answersRef.current);
      }
    } catch (err) {
      if (isApiError(err) && err.code === "device_mismatch") {
        // Final: heartbeats are accepted from a laptop still waiting for approval, so
        // this laptop no longer holds the exam. Rust has already ended the lockdown.
        setMoved(true);
        return;
      }
      handleError(err);
      setLastBeat({ at: new Date(), ok: false, note: isApiError(err) ? err.code : "error" });
    } finally {
      beating.current = false;
    }
  }, [sid, endExam, handleError]);

  useEffect(() => {
    if (finished || moved) return;
    beat();
    const id = window.setInterval(beat, HEARTBEAT_MS);
    // Coming back to the window: refresh at once so a new strike shows immediately.
    const onFocus = () => window.setTimeout(beat, 2500);
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [finished, moved, beat]);

  // Countdown, recomputed every second from the server deadline.
  useEffect(() => {
    const tick = () => setSecondsLeft((Date.parse(status.deadline_at) - (Date.now() + offset.current)) / 1000);
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [status.deadline_at]);

  // ------------------------------------------------------------------ run / submit / finish

  const execute = async (qid: string, kind: "run" | "submit") => {
    const a = answersRef.current[qid];
    setBusy((b) => ({ ...b, [qid]: kind }));
    try {
      const out =
        kind === "run"
          ? await api.runCode(sid, qid, a.language, a.code)
          : await api.submitCode(sid, qid, a.language, a.code);
      setResults((r) => ({ ...r, [qid]: { kind, out } }));
      const now = answersRef.current[qid];
      updateAnswer(qid, {
        revision: null, // Run/Submit saved on the server; the next autosave skips the revision check
        dirty: now.code !== a.code || now.language !== a.language,
        savedAt: new Date(),
      });
      setOffline(false);
    } catch (err) {
      if (!handleError(err, qid) || (isApiError(err) && err.code === "network_error")) {
        setResults((r) => ({ ...r, [qid]: { kind: "error", message: errorMessage(err) } }));
      }
    } finally {
      setBusy((b) => ({ ...b, [qid]: undefined }));
    }
  };

  const finish = async () => {
    setFinishing(true);
    setWarning(null);
    // Final marks use the latest SAVED code, so save everything first.
    const saved = await Promise.all(Object.keys(answersRef.current).map((qid) => saveOne(qid)));
    if (saved.includes(false)) {
      setFinishing(false);
      setConfirmFinish(false);
      setWarning("Some code could not be saved. Check your Wi-Fi, then press Finish again.");
      return;
    }
    try {
      const out = await api.finishExam(sid);
      setStatus((s) => ({ ...s, status: out.status }));
      setFinished(out.receipts);
    } catch (err) {
      handleError(err);
    } finally {
      setFinishing(false);
      setConfirmFinish(false);
    }
  };

  const blockedPaste = (chars: number) => {
    setNotice("Pasting from outside the editor is not allowed. This was recorded.");
    window.setTimeout(() => setNotice(null), 4000);
    api.reportBlockedPaste(sid, chars).then(setStatus).catch(() => undefined);
  };

  // ------------------------------------------------------------------ render

  const questions = view.questions;
  const q = questions[Math.min(current, questions.length - 1)];
  const unsaved = Object.values(answers).filter((a) => a.dirty).length;
  const saveText = offline
    ? "Offline: your code is kept on this laptop and will be sent when you're back online"
    : unsaved > 0
      ? "Saving…"
      : "All changes saved";
  const statementHtml = useMemo(() => (q ? renderMarkdown(q.statement_md) : ""), [q]);
  const instructionsHtml = useMemo(() => renderMarkdown(view.instructions_md), [view.instructions_md]);

  if (moved) {
    return (
      <section className="card narrow">
        <h1>This exam is on another laptop</h1>
        <p className="muted">{view.exam_title}</p>
        <p>
          Your professor moved this exam to your other laptop (or kept it there). Continue on that laptop: your saved
          code is on the server and opens there.
        </p>
        <button onClick={onLeave}>Back to exams</button>
      </section>
    );
  }

  if (finished) {
    const titles = new Map(questions.map((x) => [x.id, x.title]));
    return (
      <section className="card narrow">
        <h1>Exam submitted</h1>
        <p className="muted">{view.exam_title}</p>
        {finished.length > 0 ? (
          <>
            <p>Your receipts. Each one is proof of exactly what you handed in:</p>
            <ul className="receipts">
              {finished.map((r) => (
                <li key={r.question_id}>
                  {titles.get(r.question_id) && <div className="muted">{titles.get(r.question_id)}</div>}
                  <code>{r.receipt}</code>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="muted">Your answers were handed in.</p>
        )}
        <button onClick={onLeave}>Back to exams</button>
      </section>
    );
  }

  const locked = status.status === "locked";
  const answer = q ? answers[q.id] : undefined;
  const language = q?.languages.find((l) => l.key === answer?.language) ?? q?.languages[0];
  const result = q ? results[q.id] : undefined;
  const kind: Kind = q ? questionKind(q) : "stdio";

  return (
    <section className="exam-room">
      <div className="exam-bar">
        <div className="exam-title">
          <strong>{view.exam_title}</strong>
          <div className="muted">
            Strikes: {status.strikes} · {status.strikes_left} left · {saveText}
          </div>
        </div>
        {!locked && questions.length > 1 && (
          <nav className="qnav">
            {questions.map((x, i) => (
              <button
                key={x.id}
                className={`qtab ${i === current ? "active" : ""}`}
                onClick={() => setCurrent(i)}
                title={x.title}
              >
                Q{x.ordinal}
                {answers[x.id]?.dirty && <span className="dot" title="Not saved yet" />}
              </button>
            ))}
          </nav>
        )}
        <div className="row">
          <div className={`timer ${secondsLeft < 300 ? "low" : ""}`}>{formatTime(secondsLeft)}</div>
          <button className="danger" onClick={() => setConfirmFinish(true)} disabled={locked}>
            Finish exam
          </button>
        </div>
      </div>

      {warning && <p className="error">{warning}</p>}
      {notice && <p className="notice">{notice}</p>}

      {locked ? (
        <div className="card narrow locked">
          <h1>Waiting for your professor</h1>
          {status.device_change_pending ? (
            <p>You opened this exam on a new laptop. Ask your professor to approve this laptop.</p>
          ) : (
            <p>Your exam is paused ({status.lock_reason ?? "locked"}). Your code is saved. The timer keeps running.</p>
          )}
          <p className="muted">This screen updates by itself.</p>
        </div>
      ) : (
        q &&
        answer &&
        language && (
          <Split>
            {/* Left: the question */}
            <div className="qside">
              {view.instructions_md && (
                <details className="instructions" open={current === 0}>
                  <summary>Exam instructions</summary>
                  <div className="md" dangerouslySetInnerHTML={{ __html: instructionsHtml }} />
                </details>
              )}
              <div className="row between">
                <h2>
                  Q{q.ordinal}. {q.title}
                </h2>
                <span className="pill">{q.marks} marks</span>
              </div>
              {/* Sanitised: raw HTML off + DOMPurify (section 8) */}
              <div className="md" dangerouslySetInnerHTML={{ __html: statementHtml }} />
              {q.files?.length > 0 && <DataFiles key={q.id} sessionId={sid} questionId={q.id} files={q.files} />}
              {q.samples.map((s) => (
                <section key={s.ordinal} className="sample-block">
                  <h3>{kind === "checks" ? `Checks ${s.ordinal}` : `Example ${s.ordinal}`}</h3>
                  <SampleView kind={kind} input={s.input} expected={s.expected_output} statement={q.statement_md} />
                </section>
              ))}
              {q.hidden_test_count > 0 && (
                <p className="muted">
                  {q.hidden_test_count} hidden test{q.hidden_test_count === 1 ? " is" : "s are"} checked when you Submit
                  and after the exam.
                </p>
              )}
            </div>

            {/* Right: editor, Run / Submit, results */}
            <div className="codeside">
              <div className="row between toolbar">
                {q.languages.length > 1 ? (
                  <select
                    value={answer.language}
                    aria-label="Language"
                    onChange={(e) => {
                      const next = e.target.value;
                      const starterOld = q.starter_code[answer.language] ?? "";
                      const keepCode = answer.code.trim() !== "" && answer.code !== starterOld;
                      updateAnswer(q.id, {
                        language: next,
                        code: keepCode ? answer.code : (q.starter_code[next] ?? ""),
                        dirty: true,
                      });
                    }}
                  >
                    {q.languages.map((l) => (
                      <option key={l.key} value={l.key}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="pill lang">{language.name}</span>
                )}
                <div className="row">
                  <button className="secondary" disabled={!!busy[q.id]} onClick={() => execute(q.id, "run")}>
                    {busy[q.id] === "run" ? "Running…" : "Run samples"}
                  </button>
                  <button disabled={!!busy[q.id]} onClick={() => execute(q.id, "submit")}>
                    {busy[q.id] === "submit" ? "Checking…" : "Submit"}
                  </button>
                </div>
              </div>
              <div className="editor-wrap">
                <CodeEditor
                  key={q.id}
                  value={answer.code}
                  monacoLanguage={language.monaco_id}
                  readOnly={locked}
                  onChange={(code) => {
                    if (code !== answersRef.current[q.id]?.code) updateAnswer(q.id, { code, dirty: true });
                  }}
                  onBlockedPaste={blockedPaste}
                />
              </div>
              <ResultPanel
                key={q.id}
                result={result}
                running={busy[q.id]}
                kind={kind}
                statement={q.statement_md}
              />
            </div>
          </Split>
        )
      )}

      <footer className="beat muted">
        {lastBeat
          ? `Heartbeat ${lastBeat.ok ? "OK" : `failed (${lastBeat.note})`} at ${lastBeat.at.toLocaleTimeString()}`
          : "Connecting…"}
      </footer>

      {confirmFinish && (
        <div className="modal-backdrop">
          <div className="card narrow modal">
            <h1>Finish the exam?</h1>
            <p>Your latest code for every question is handed in. You can't change it afterwards.</p>
            {unsaved > 0 && <p className="muted">Unsaved changes are saved first.</p>}
            <div className="row end">
              <button className="secondary" disabled={finishing} onClick={() => setConfirmFinish(false)}>
                Keep working
              </button>
              <button className="danger" disabled={finishing} onClick={finish}>
                {finishing ? "Handing in…" : "Finish exam"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

/** Two panes with a divider the student can drag (question | editor). */
function Split({ children }: { children: [React.ReactNode, React.ReactNode] }) {
  const [left, setLeft] = useState(40); // percent of the width for the question
  const box = useRef<HTMLDivElement>(null);
  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = box.current;
    if (!el) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const r = el.getBoundingClientRect();
      setLeft(Math.min(65, Math.max(25, ((ev.clientX - r.left) / r.width) * 100)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("dragging");
    };
    document.body.classList.add("dragging");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div className="split" ref={box} style={{ gridTemplateColumns: `${left}% 8px 1fr` }}>
      {children[0]}
      <div className="divider" onPointerDown={drag} title="Drag to resize" />
      {children[1]}
    </div>
  );
}

/** Spot the classic mistake: input("Enter n:") prints its prompt into the output. */
function promptHint(stdout?: string | null, expected?: string | null): boolean {
  if (!stdout || expected == null) return false;
  const out = stdout.split("\n")[0];
  const exp = expected.split("\n")[0];
  return out !== exp && exp.length > 0 && out.endsWith(exp);
}

function ResultPanel({
  result,
  running,
  kind,
  statement,
}: {
  result: Result | undefined;
  running: "run" | "submit" | undefined;
  kind: Kind;
  statement: string;
}) {
  // Hidden only until something new happens: a new run or a new result always shows.
  const current: unknown = running ?? result ?? null;
  const [hiddenFor, setHiddenFor] = useState<unknown>(undefined);
  const open = hiddenFor !== current;
  const setOpen = (o: boolean) => setHiddenFor(o ? undefined : current);

  let title = "Results";
  let pill: React.ReactNode = null;
  let body: React.ReactNode;
  if (running) {
    body = <p className="muted">{running === "run" ? "Running your code on the samples…" : "Checking your code against all tests…"}</p>;
  } else if (!result) {
    body = <p className="muted">Press Run samples to test your code on the examples, or Submit to check it against all tests.</p>;
  } else if (result.kind === "error") {
    body = <p className="error">{result.message}</p>;
  } else {
    const { out } = result;
    const allPassed = out.total > 0 && out.passed === out.total;
    title = result.kind === "run" ? "Sample tests" : "Submission";
    pill = (
      <span className={`pill ${allPassed ? "ok" : "bad"}`}>
        {out.passed}/{out.total} passed
      </span>
    );
    body = (
      <>
        {out.hidden_summary && <p className="notice">{out.hidden_summary}</p>}
        {out.compile_output && (
          <>
            <div className="muted">Compiler output</div>
            <pre className="out bad">{out.compile_output}</pre>
          </>
        )}
        {kind === "stdio" && out.tests.some((t) => t.verdict === "WA" && promptHint(t.stdout, t.expected_output)) && (
          <p className="notice">
            Tip: your output starts with extra text. If you wrote something like <code>input("Enter n:")</code>, the
            prompt is printed too. Use <code>input()</code> with no text: the output must match exactly.
          </p>
        )}
        {out.tests.map((t) => (
          <details key={`${t.is_sample}-${t.ordinal}`} className="test" open={t.is_sample && (t.verdict !== "AC" || out.tests.length === 1)}>
            <summary>
              <span className={`pill ${t.verdict === "AC" ? "ok" : "bad"}`}>{VERDICTS[t.verdict] ?? t.verdict}</span>{" "}
              {t.is_sample ? `${kind === "checks" ? "Checks" : "Example"} ${t.ordinal}` : `Hidden test ${t.ordinal}`}
              {t.time_ms != null && <span className="muted"> · {t.time_ms} ms</span>}
            </summary>
            {t.message && <p className={kind === "ml" ? "score" : "muted"}>{t.message}</p>}
            <TestResultView kind={kind} test={t} statement={statement} />
          </details>
        ))}
      </>
    );
  }

  return (
    <div className={`results ${open ? "open" : ""} ${result || running ? "filled" : ""}`}>
      <button className="results-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>
          <strong>{title}</strong> {pill}
        </span>
        <span className="muted">{open ? "Hide ▾" : "Show ▴"}</span>
      </button>
      {open && <div className="results-body">{body}</div>}
    </div>
  );
}
