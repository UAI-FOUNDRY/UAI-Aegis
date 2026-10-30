import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, errorMessage, isApiError, SessionView, StudentExam, User } from "./api";
import ExamRoom from "./ExamRoom";

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
        <span className="who">
          {user?.full_name}
          {user && screen.name !== "exam" && (
            <button className="link" onClick={signOut}>Sign out</button>
          )}
          {/* On Windows the app runs on its own desktop with no taskbar: this is the way out. */}
          {screen.name !== "exam" && (
            <button className="link" onClick={() => api.quitApp().catch(() => undefined)}>Quit</button>
          )}
        </span>
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
  const [problems, setProblems] = useState<string[]>([]);

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
    setProblems([]);
    try {
      // Check the laptop first (extra screens, VMs, remote-control or recording tools),
      // so the student can fix it before anything is reported to the professor.
      const check = await api.precheck();
      if (!check.ok) {
        setProblems(check.problems.map((p) => p.message));
        return;
      }
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
      {problems.length > 0 && (
        <div className="error">
          <strong>Fix these before starting:</strong>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          Then press Start again.
        </div>
      )}
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
