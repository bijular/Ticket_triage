import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { api, setToken } from "./api";
import Overview from "./pages/Overview";
import Plan from "./pages/Plan";
import Runs from "./pages/Runs";
import RunDetailPage from "./pages/RunDetail";
import NewRun from "./pages/NewRun";
import Report from "./pages/Report";
import Datasets from "./pages/Datasets";
import Review from "./pages/Review";
import "./styles.css";

const links: [string, string][] = [["/", "Overview"], ["/plan", "Run plan"], ["/runs", "Runs"], ["/new", "New run"], ["/review", "Blind review"], ["/report", "Report"], ["/datasets", "Datasets"]];

function TokenPrompt() {
  const [v, setV] = useState("");
  return (
    <div className="card" style={{ maxWidth: 420, margin: "80px auto" }}>
      <h2>Access token required</h2>
      <p className="muted">This console was started with TRIAGE_UI_TOKEN set.</p>
      <form onSubmit={(e) => { e.preventDefault(); setToken(v); location.reload(); }}>
        <label className="f"><span>Token</span><input type="password" value={v} onChange={(e) => setV(e.target.value)} autoFocus /></label>
        <button className="primary" type="submit">Continue</button>
      </form>
    </div>
  );
}

function App() {
  const [locked, setLocked] = useState(false);
  const [mode, setMode] = useState<"operator" | "review_only">("operator");
  useEffect(() => { api<{ mode: string }>("/health").then((h) => setMode(h.mode === "review_only" ? "review_only" : "operator")).catch(() => {}); }, []);
  useEffect(() => {
    const h = () => setLocked(true);
    window.addEventListener("triage-unauthorized", h);
    return () => window.removeEventListener("triage-unauthorized", h);
  }, []);
  if (locked) return <TokenPrompt />;
  return (
    <div className="shell">
      <nav className="side" aria-label="Main">
        <div className="brand"><i /> Triage eval</div>
        {(mode === "review_only" ? links.filter(([to]) => to === "/review") : links).map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>)}
        <div className="foot">{mode === "review_only" ? "Reviewer console · blind" : "Operator console · localhost only"}</div>
      </nav>
      <main>
        {mode === "review_only" ? (
          <Routes><Route path="*" element={<Review />} /></Routes>
        ) : (
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/plan" element={<Plan />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetailPage />} />
          <Route path="/new" element={<NewRun />} />
          <Route path="/review" element={<Review />} />
          <Route path="/report" element={<Report />} />
          <Route path="/datasets" element={<Datasets />} />
          <Route path="*" element={<div className="empty">Not found</div>} />
        </Routes>
        )}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><BrowserRouter><App /></BrowserRouter></StrictMode>);
