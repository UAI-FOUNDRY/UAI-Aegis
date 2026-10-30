import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, isApiError, SessionView, Status, StudentExam, User } from "./api";

type Screen =
  | { name: "login" }
  | { name: "change-password" }
  | { name: "exams" }
  | { name: "exam"; view: SessionView };

export default function App() {
  const [screen, setScreen] = useState<Screen>({ name: "login" });
  const [user, setUser] = useState<User | null>(null);

  const signedOut = useCallback(() => {
    setUser(null);
    setScreen({ name: "login" });
  }, []);

  const signOut = async () => {
    await api.logout().catch(() => undefined);
    signedOut();
  };

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">UAI Aegis</span>
        {user && (
          <span className="who">
            {user.full_name}
            {screen.name !== "exam" && (
              <button className="link" onClick={signOut}>Sign out</button>
            )}
          </span>
        )}
      </header>
      <main>
        {screen.name === "login" && (
          <Login
            onDone={(u, needsChange) => {
              setUser(u);
              setScreen(needsChange ? { name: "change-password" } : { name: "exams" });
            }}
          />
        )}
        {screen.name === "change-password" && <ChangePassword onDone={() => setScreen({ name: "exams" })} />}
        {screen.name === "exams" && (
          <ExamList onStarted={(view) => setScreen({ name: "exam", view })} onSignedOut={signedOut} />
        )}
        {screen.name === "exam" && (
          <ExamRoom
            initial={screen.view}
            onLeave={() => setScreen({ name: "exams" })}
            onSignedOut={signedOut}
          />
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------- sign in (section 1 + 2)

function Login({ onDone }: { onDone: (u: User, needsPasswordChange: boolean) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // Rust signs in, loads/creates the device key in the keychain and registers the laptop.
      const res = await api.login(email, password);
      onDone(res.user, res.needs_password_change);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card narrow" onSubmit={submit}>
      <h1>Sign in</h1>
      <p className="muted">Use your college account.</p>
      <label>
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
      </label>
      <label>
        Password
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}

function ChangePassword({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card narrow" onSubmit={submit}>
      <h1>Set a new password</h1>
      <p className="muted">You need a new password before your first exam.</p>
      <label>
        Current password
        <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
      </label>
      <label>
        New password
        <input type="password" value={next} onChange={(e) => setNext(e.target.value)} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button disabled={busy}>{busy ? "Saving…" : "Save password"}</button>
    </form>
  );
}

// ---------------------------------------------------------------- exam list (section 4)

function ExamList({ onStarted, onSignedOut }: { onStarted: (v: SessionView) => void; onSignedOut: () => void }) {
  const [exams, setExams] = useState<StudentExam[] | null>(null);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setExams(await api.listExams());
    } catch (err) {
      if (isApiError(err) && err.code === "session_expired") return onSignedOut();
      setError(errorMessage(err));
    }
  }, [onSignedOut]);

  useEffect(() => {
    load();
  }, [load]);

  const start = async (exam: StudentExam) => {
    setBusy(exam.exam_id);
    setError(null);
    try {
      // Resuming after a crash on the same laptop needs no code.
      const code = exam.session_id ? null : codes[exam.exam_id] ?? "";
      onStarted(await api.startExam(exam.exam_id, code));
    } catch (err) {
      if (isApiError(err) && err.code === "session_expired") return onSignedOut();
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card">
      <div className="row between">
        <h1>Your exams</h1>
        <button className="secondary" onClick={load}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}
      {exams === null && <p className="muted">Loading…</p>}
      {exams?.length === 0 && <p className="muted">No open exams. Ask your professor to open the exam, then refresh.</p>}
      <ul className="exams">
        {exams?.map((exam) => {
          const finished = exam.session_status === "submitted" || exam.session_status === "terminated";
          const resumable = exam.session_id && !finished;
          return (
            <li key={exam.exam_id} className="exam">
              <div>
                <strong>{exam.title}</strong>
                <div className="muted">
                  {exam.subject_code} · {exam.subject_name} · {exam.duration_minutes} min
                </div>
              </div>
              {finished && <span className="pill">Submitted</span>}
              {resumable && (
                <button disabled={busy === exam.exam_id} onClick={() => start(exam)}>Resume</button>
              )}
              {!exam.session_id && exam.status === "live" && (
                <div className="row">
                  <input
                    className="code"
                    placeholder="Exam code"
                    value={codes[exam.exam_id] ?? ""}
                    onChange={(e) => setCodes({ ...codes, [exam.exam_id]: e.target.value.toUpperCase() })}
                    maxLength={32}
                  />
                  <button disabled={busy === exam.exam_id || !codes[exam.exam_id]} onClick={() => start(exam)}>
                    Start
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------- exam room (sections 4 + 6)

const HEARTBEAT_MS = 10_000;

function formatTime(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

function ExamRoom({
  initial,
  onLeave,
  onSignedOut,
}: {
  initial: SessionView;
  onLeave: () => void;
  onSignedOut: () => void;
}) {
  const [view, setView] = useState<SessionView>(initial);
  const [status, setStatus] = useState<Status>({
    status: initial.status,
    lock_reason: initial.lock_reason,
    strikes: initial.strikes,
    strikes_left: Math.max(0, initial.max_strikes - initial.strikes),
    server_now: initial.server_now,
    deadline_at: initial.deadline_at,
    seconds_left: initial.seconds_left,
    device_change_pending: initial.device_change_pending,
  });
  // Server clock minus laptop clock, so the countdown follows the server (section 4).
  const offset = useRef(Date.parse(initial.server_now) - Date.now());
  const [secondsLeft, setSecondsLeft] = useState(initial.seconds_left);
  const [lastBeat, setLastBeat] = useState<{ at: Date; ok: boolean; note?: string } | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const prevStatus = useRef(initial.status);

  const finished = status.status === "submitted" || status.status === "terminated";

  // Countdown: recomputed every second from the server deadline.
  useEffect(() => {
    const tick = () => {
      const serverNow = Date.now() + offset.current;
      setSecondsLeft((Date.parse(status.deadline_at) - serverNow) / 1000);
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [status.deadline_at]);

  // Heartbeat every 10 s, also while locked (that's how we learn the professor resumed).
  useEffect(() => {
    if (finished) return;
    let stopped = false;
    const beat = async () => {
      try {
        const s = await api.heartbeat(view.session_id);
        if (stopped) return;
        offset.current = Date.parse(s.server_now) - Date.now();
        setStatus(s);
        setWarning(null);
        setLastBeat({ at: new Date(), ok: true });
        // Locked -> active: the professor resumed. Reload the full view.
        if (prevStatus.current === "locked" && s.status === "active") {
          setView(await api.getSession(view.session_id));
        }
        prevStatus.current = s.status;
      } catch (err) {
        if (stopped) return;
        if (isApiError(err)) {
          switch (err.code) {
            case "session_expired":
              return onSignedOut();
            case "stale_signature":
              setWarning("Your laptop's date or time is wrong. Fix it in system settings (set time automatically).");
              break;
            case "time_over":
            case "session_finished":
              setStatus((s) => ({ ...s, status: "submitted" }));
              break;
            case "device_mismatch":
              setWarning("This exam is running on another laptop.");
              break;
            default:
              setWarning(err.message);
          }
        } else {
          setWarning(errorMessage(err));
        }
        setLastBeat({ at: new Date(), ok: false, note: isApiError(err) ? err.code : "error" });
      }
    };
    beat();
    const id = window.setInterval(beat, HEARTBEAT_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [view.session_id, finished, onSignedOut]);

  if (finished) {
    return (
      <section className="card narrow">
        <h1>Exam submitted</h1>
        <p className="muted">{view.exam_title}</p>
        {view.receipts.length > 0 && (
          <>
            <p>Your receipts (proof of exactly what you handed in):</p>
            <ul className="receipts">
              {view.receipts.map((r) => (
                <li key={r.question_id}><code>{r.receipt}</code></li>
              ))}
            </ul>
          </>
        )}
        <button onClick={onLeave}>Back to exams</button>
      </section>
    );
  }

  return (
    <section className="exam-room">
      <div className="exam-bar">
        <div>
          <strong>{view.exam_title}</strong>
          <div className="muted">
            Strikes: {status.strikes} · {status.strikes_left} left
          </div>
        </div>
        <div className={`timer ${secondsLeft < 300 ? "low" : ""}`}>{formatTime(secondsLeft)}</div>
      </div>

      {warning && <p className="error">{warning}</p>}

      {status.status === "locked" ? (
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
        <div className="questions">
          {view.questions.map((q) => (
            <article key={q.id} className="card">
              <div className="row between">
                <h2>
                  Q{q.ordinal}. {q.title}
                </h2>
                <span className="pill">{q.marks} marks</span>
              </div>
              {/* Plain text for now (React escapes it). Markdown rendering comes with
                  DOMPurify in the editor step (CLIENT_PROTOCOL.md section 8). */}
              <p className="statement">{q.statement_md}</p>
              <div className="muted">
                Languages: {q.languages.map((l) => l.name).join(", ")} · {q.samples.length} sample test(s) ·{" "}
                {q.hidden_test_count} hidden
              </div>
            </article>
          ))}
        </div>
      )}

      <footer className="beat muted">
        {lastBeat
          ? `Heartbeat ${lastBeat.ok ? "OK" : `failed (${lastBeat.note})`} at ${lastBeat.at.toLocaleTimeString()}`
          : "Connecting…"}
      </footer>
    </section>
  );
}
