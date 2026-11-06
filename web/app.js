import { html, render, useEffect, useMemo, useRef, useState } from "/vendor/htm-preact.js";

const SAMPLE_CASE = "sample";
const PHASE_LABELS = {
  intake: "Intake",
  forward: "Forward pass: does the record match the claim?",
  reverse: "Reverse pass: what happened that the vendor did not say?",
  verdict: "Outcome",
};
const OUTCOME_COPY = {
  READY: ["Ready for signature", "Every claim is supported by public records and nothing adverse turned up. A person still signs."],
  CLARIFY: ["Needs clarification", "There are open questions the vendor can answer. A draft letter is ready for review."],
  HOLD: ["On hold", "At least one blocker needs a person's attention before this vendor goes further."],
};

const money = (value) => (value ? "$" + Math.round(value).toLocaleString("en-US") : "$0");
const shortHash = (hash) => (hash || "").slice(0, 12);

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

const post = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

function useRoute() {
  const [route, setRoute] = useState(location.hash.slice(1) || "/");
  useEffect(() => {
    const update = () => {
      setRoute(location.hash.slice(1) || "/");
      window.scrollTo(0, 0);
    };
    addEventListener("hashchange", update);
    return () => removeEventListener("hashchange", update);
  }, []);
  return route;
}

function Chip({ value, label }) {
  return html`<span class=${"chip " + (value || "idle")}>${label || value || "Not reviewed"}</span>`;
}

function Topbar({ route }) {
  const link = (href, label) =>
    html`<a href=${"#" + href} class=${route === href || (href === "/" && route.startsWith("/case")) ? "on" : ""}>${label}</a>`;
  return html`<header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="#/">Cross<span>exam</span></a>
      <nav class="nav">
        ${link("/", "Case desk")} ${link("/lookup", "Public record lookup")} ${link("/benchmark", "Benchmark")}
        ${link("/how", "How it works")}
      </nav>
      <span class="demo-note">Sample documents are invented. Public records are real.</span>
    </div>
  </header>`;
}

function CaseView({ caseId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let timer;
    const load = async () => {
      try {
        const next = await api(`/api/cases/${caseId}`);
        setData(next);
        if (next.vendors.some((vendor) => ["running", "new"].includes(vendor.stage)) && !next.is_demo) {
          timer = setTimeout(load, 1200);
        }
      } catch (problem) {
        setError(problem.message);
      }
    };
    load();
    return () => clearTimeout(timer);
  }, [caseId]);

  const runCopy = async () => {
    setBusy(true);
    setError("");
    try {
      const copy = await post(`/api/cases/${caseId}/clone`);
      location.hash = `/case/${copy.id}`;
    } catch (problem) {
      setError(problem.message);
    }
    setBusy(false);
  };

  if (error && !data) return html`<p class="error">${error}</p>`;
  if (!data) return html`<p class="muted">Loading the case file.</p>`;

  const claimed = data.vendors.reduce((sum, vendor) => sum + vendor.claimed_value, 0);
  const verified = data.vendors.reduce((sum, vendor) => sum + vendor.verified_value, 0);
  const holds = data.vendors.filter((vendor) => vendor.outcome === "HOLD").length;
  const cost = data.vendors.reduce((sum, vendor) => sum + vendor.cost_usd, 0);

  return html`
    <section class="hero">
      <div class="eyebrow">${data.solicitation} / ${data.buyer}</div>
      <h1>${data.title}</h1>
      <p>
        Each vendor's past-performance claims are checked against public records, then the record is searched for what the
        vendor left out. Rules decide the outcome. A person signs the decision.
      </p>
    </section>

    <div class="tally">
      <div><b>${data.vendors.length}</b><small>vendors in this case</small></div>
      <div><b>${money(claimed)}</b><small>past work claimed</small></div>
      <div><b>${money(verified)}</b><small>supported by the record</small></div>
      <div><b>${holds}</b><small>on hold for a person</small></div>
    </div>

    <div class="section-head">
      <h2>Vendors</h2>
      <div class="actions">
        ${data.is_demo
          ? html`<button class="btn" disabled=${busy} onClick=${runCopy}>${busy ? "Starting" : "Run a live copy of this case"}</button>`
          : html`<a class="btn ghost" href=${`#/case/${caseId}/add`}>Add a vendor</a>`}
        <a class="btn ghost" href=${`/api/cases/${caseId}/memo`} target="_blank" rel="noopener">Review memo</a>
      </div>
    </div>
    ${error && html`<p class="error">${error}</p>`}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Vendor</th>
            <th>Rules outcome</th>
            <th>Claims verified</th>
            <th>Value supported</th>
            <th>Top open item</th>
          </tr>
        </thead>
        <tbody>
          ${data.vendors.map(
            (vendor) => html`<tr class="link" onClick=${() => (location.hash = `/vendor/${vendor.id}`)}>
              <td><a href=${`#/vendor/${vendor.id}`}><b>${vendor.name}</b></a></td>
              <td>
                ${vendor.stage === "running"
                  ? html`<span class="chip idle">Reviewing</span>`
                  : html`<${Chip} value=${vendor.outcome} />`}
              </td>
              <td>${vendor.verified} of ${vendor.claims}</td>
              <td>
                ${money(vendor.verified_value)} of ${money(vendor.claimed_value)}
                <div class="bar">
                  <i style=${{ width: `${vendor.claimed_value ? (100 * vendor.verified_value) / vendor.claimed_value : 0}%` }}></i>
                </div>
              </td>
              <td class="small">${vendor.blockers[0] || vendor.warnings[0] || html`<span class="muted">None</span>`}</td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>
    ${cost > 0 && html`<p class="muted small">Model cost for this case: $${cost.toFixed(4)} on Vultr Serverless Inference.</p>`}
  `;
}

function Finding({ item }) {
  return html`<div class=${"finding " + item.severity}>
    <b>${item.title}</b>
    <span class="small muted">${item.detail}</span>
    ${item.url && html` <a class="small" href=${item.url} target="_blank" rel="noopener">Record</a>`}
  </div>`;
}

const FORWARD_RULES = new Set(["CX-01", "CX-02", "CX-02A", "CX-03", "CX-04", "CX-05"]);

function ClaimRows({ claims }) {
  return html`<div class="passes">
    <div class="h">What the vendor said</div>
    <div class="h">Forward pass: the record</div>
    <div class="h">Reverse pass: what came after</div>
    ${claims.map((claim, index) => {
      const findings = claim.result?.findings || [];
      const forward = findings.filter((item) => FORWARD_RULES.has(item.rule));
      const reverse = findings.filter((item) => !FORWARD_RULES.has(item.rule));
      const record = claim.record;
      return html`
        <div class="c">
          <div class="actions" style=${{ alignItems: "center" }}>
            <${Chip} value=${claim.result?.state} />
            <span class="mono small">${claim.contract_number || "No number given"}</span>
          </div>
          <p class="quote">${claim.quote}</p>
        </div>
        <div class="c">
          ${record
            ? html`<div class="small muted">
                ${record.sub_agency || record.agency}<br />
                ${record.start} to ${record.end}<br />
                Peak ${money(record.peak_obligation)}, now ${money(record.total_obligation)}
                <br /><a href=${record.url} target="_blank" rel="noopener">Open on USAspending.gov</a>
              </div>`
            : ""}
          ${forward.length
            ? forward.map((item) => html`<${Finding} item=${item} />`)
            : record && html`<div class="finding"><${Chip} value="pass" label="Matches the claim" /></div>`}
        </div>
        <div class="c">
          ${reverse.length
            ? reverse.map((item) => html`<${Finding} item=${item} />`)
            : record
              ? html`<${Chip} value="pass" label=${`${record.actions.length} modifications, none adverse`} />`
              : html`<span class="small muted">No record to examine.</span>`}
        </div>
      `;
    })}
  </div>`;
}

function Timeline({ events, live }) {
  const box = useRef(null);
  useEffect(() => {
    if (live && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [events.length, live]);

  const merged = useMemo(() => {
    const byStep = new Map();
    for (const event of events) byStep.set(event.step, event);
    return [...byStep.values()].filter((event) => event.step !== "END");
  }, [events]);

  let phase = null;
  return html`<div class="timeline" ref=${box}>
    ${merged.map((event) => {
      const header = event.phase !== phase ? html`<div class="phase-label">${PHASE_LABELS[event.phase]}</div>` : null;
      phase = event.phase;
      return html`${header}
        <div class="tl-item">
          <span class=${"dot " + event.status}></span>
          <div>
            ${event.title}
            ${event.detail && html`<div class="small muted">${event.detail}</div>`}
            ${event.url && html`<a class="small" href=${event.url} target="_blank" rel="noopener">Source</a>`}
          </div>
        </div>`;
    })}
    ${merged.length === 0 && html`<div class="tl-item"><span class="dot"></span><div>Waiting to start.</div></div>`}
  </div>`;
}

function LetterPanel({ vendor, onSaved }) {
  const [text, setText] = useState(vendor.letter || "");
  const [reviewer, setReviewer] = useState("");
  const [message, setMessage] = useState("");

  const save = async (status) => {
    setMessage("");
    try {
      const result = await post(`/api/vendors/${vendor.id}/letter`, { text, status, reviewer });
      setMessage(result.saved ? "Saved." : "The sample case is read-only. Run a live copy to save changes.");
      onSaved();
    } catch (problem) {
      setMessage(problem.message);
    }
  };

  return html`<div class="card">
    <p class="muted small" style=${{ marginTop: 0 }}>
      Drafted by the model from the findings above. Status: ${vendor.letter_status}. Nothing leaves this system until a named
      person approves it.
    </p>
    <textarea value=${text} onInput=${(event) => setText(event.target.value)}></textarea>
    <label>Approved by</label>
    <input type="text" value=${reviewer} onInput=${(event) => setReviewer(event.target.value)} placeholder="Your name" />
    <div class="actions" style=${{ marginTop: 12 }}>
      <button class="btn" onClick=${() => save("approved")}>Approve letter</button>
      <button class="btn ghost" onClick=${() => save("draft")}>Save draft</button>
    </div>
    ${message && html`<p class="small muted">${message}</p>`}
  </div>`;
}

function SignOff({ vendor, onSaved }) {
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");

  const decide = async (decision) => {
    setMessage("");
    try {
      const result = await post(`/api/vendors/${vendor.id}/decision`, { decision, reviewer, note });
      setMessage(result.saved ? "Decision recorded." : "The sample case is read-only. Run a live copy to sign.");
      onSaved();
    } catch (problem) {
      setMessage(problem.message);
    }
  };

  if (vendor.decision) {
    return html`<div class="card">
      <b>${vendor.decision.decision === "approve" ? "Approved" : "Rejected"}</b> by ${vendor.decision.reviewer}
      ${vendor.decision.note && html`<p class="small muted">${vendor.decision.note}</p>`}
    </div>`;
  }
  return html`<div class="card">
    <p class="muted small" style=${{ marginTop: 0 }}>
      The rules give an outcome. The decision belongs to a person.
      ${vendor.outcome === "HOLD" ? " Approving a vendor on hold needs a written reason." : ""}
    </p>
    <div class="grid-2">
      <div>
        <label>Reviewer</label>
        <input type="text" value=${reviewer} onInput=${(event) => setReviewer(event.target.value)} placeholder="Your name" />
      </div>
      <div>
        <label>Reason</label>
        <input type="text" value=${note} onInput=${(event) => setNote(event.target.value)} placeholder="Why this decision" />
      </div>
    </div>
    <div class="actions" style=${{ marginTop: 12 }}>
      <button class="btn" onClick=${() => decide("approve")}>Approve vendor</button>
      <button class="btn danger" onClick=${() => decide("reject")}>Reject vendor</button>
    </div>
    ${message && html`<p class="small muted">${message}</p>`}
  </div>`;
}

function VendorView({ vendorId }) {
  const [vendor, setVendor] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const data = await api(`/api/vendors/${vendorId}`);
      setVendor(data);
      setEvents(data.events);
      return data;
    } catch (problem) {
      setError(problem.message);
    }
  };

  useEffect(() => {
    let source;
    load().then((data) => {
      if (!data || !["running", "new"].includes(data.stage)) return;
      const last = data.events.length ? data.events[data.events.length - 1].seq : 0;
      source = new EventSource(`/api/vendors/${vendorId}/events?after=${last}`);
      source.addEventListener("step", (message) => setEvents((list) => [...list, JSON.parse(message.data)]));
      source.addEventListener("end", () => {
        source.close();
        load();
      });
    });
    return () => source && source.close();
  }, [vendorId]);

  if (error) return html`<p class="error">${error}</p>`;
  if (!vendor) return html`<p class="muted">Loading the dossier.</p>`;

  const result = vendor.result;
  const running = ["running", "new"].includes(vendor.stage);
  const [outcomeTitle, outcomeNote] = OUTCOME_COPY[vendor.outcome] || ["", ""];
  const alertClass = { READY: "alert ok", CLARIFY: "alert mid", HOLD: "alert" }[vendor.outcome];
  const hidden = result?.document?.hidden || [];
  const sweep = (result?.findings || []).filter((item) => item.rule !== "VX-05" && item.rule !== "VX-06");
  const integrity = (result?.findings || []).filter((item) => item.rule === "VX-05" || item.rule === "VX-06" || item.rule === "VX-07");

  return html`
    <div class="crumbs"><a href=${`#/case/${vendor.case.id}`}>${vendor.case.solicitation}</a> / ${vendor.name}</div>
    <div class="dossier-head">
      <div>
        <h1>${vendor.name}</h1>
        <div class="muted small">${vendor.doc_name} <span class="mono">sha256 ${shortHash(vendor.doc_sha256)}</span></div>
      </div>
      <div class="meta">
        ${running ? html`<span class="chip idle">Reviewing</span>` : html`<${Chip} value=${vendor.outcome} />`}
        ${result &&
        html`<div style=${{ marginTop: 6 }}>
          ${result.elapsed_s}s, $${(result.usage?.cost_usd || 0).toFixed(4)} in model cost<br />
          ${result.evidence?.length || 0} public record responses
        </div>`}
      </div>
    </div>

    ${vendor.outcome && html`<div class=${alertClass}><b>${outcomeTitle}</b>${outcomeNote}</div>`}

    ${integrity.length > 0 &&
    html`<div class="section-head"><h2>Document integrity</h2></div>
      <div class="card">
        ${integrity.map((item) => html`<${Finding} item=${item} />`)}
        ${hidden.length > 0 &&
        html`<p class="small muted" style=${{ marginBottom: 0 }}>
            Hidden from a human reader on page ${hidden[0].page}. Found in the sandbox and kept away from the model:
          </p>
          <div class="hidden-text">${hidden.map((item) => item.text).join("
")}</div>`}
      </div>`}

    ${result &&
    html`<div class="section-head">
        <h2>Claims</h2>
        <p>${money(result.verified_value)} of ${money(result.claimed_value)} supported</p>
      </div>
      ${result.claims.length ? html`<${ClaimRows} claims=${result.claims} />` : html`<p class="muted">No claims could be read.</p>`}`}

    <div class=${result ? "columns" : ""}>
      ${result &&
      html`<div>
        <div class="section-head"><h2>What the statement left out</h2></div>
        <div class="card">
          ${sweep.length
            ? sweep.map((item) => html`<${Finding} item=${item} />`)
            : html`<span class="muted">The portfolio sweep, exclusions list, sanctions list and SEC search found nothing adverse.</span>`}
          ${result.coverage_gaps?.length > 0 &&
          html`<p class="small error">Sources that did not answer: ${result.coverage_gaps.join(", ")}.</p>`}
        </div>
      </div>`}
      <div>
        <div class="section-head"><h2>Review log</h2></div>
        <${Timeline} events=${events} live=${running} />
      </div>
    </div>

    ${vendor.letter &&
    html`<div class="section-head"><h2>Clarification letter</h2></div>
      <${LetterPanel} vendor=${vendor} onSaved=${load} />`}
    ${vendor.outcome &&
    html`<div class="section-head"><h2>Sign-off</h2></div>
      <${SignOff} vendor=${vendor} onSaved=${load} />`}
    ${result?.evidence?.length > 0 &&
    html`<details style=${{ marginTop: 22 }}>
      <summary>Evidence ledger: ${result.evidence.length} responses, hashed as received</summary>
      <div class="table-wrap ledger">
        <table>
          <thead><tr><th>Source</th><th>SHA-256</th><th>Bytes</th></tr></thead>
          <tbody>
            ${result.evidence.map(
              (item) => html`<tr><td>${item.source}</td><td class="mono">${shortHash(item.sha256)}</td><td>${item.bytes.toLocaleString("en-US")}</td></tr>`,
            )}
          </tbody>
        </table>
      </div>
    </details>`}
  `;
}

function AddVendor({ caseId }) {
  const [name, setName] = useState("");
  const [uei, setUei] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("legal_name", name);
      form.append("uei", uei);
      form.append("document", file);
      const vendor = await api(`/api/cases/${caseId}/vendors`, { method: "POST", body: form });
      await post(`/api/vendors/${vendor.id}/run`);
      location.hash = `/vendor/${vendor.id}`;
    } catch (problem) {
      setError(problem.message);
      setBusy(false);
    }
  };

  return html`<div class="prose">
    <div class="crumbs"><a href=${`#/case/${caseId}`}>Back to the case</a></div>
    <h2>Add a vendor</h2>
    <p class="muted">Upload a capability statement as a PDF or text file, up to 5 MB. It is opened in a sandbox with no network.</p>
    <form onSubmit=${submit}>
      <label>Legal company name</label>
      <input type="text" required value=${name} onInput=${(event) => setName(event.target.value)} />
      <label>UEI, if known</label>
      <input type="text" value=${uei} onInput=${(event) => setUei(event.target.value)} />
      <label>Capability statement</label>
      <input type="file" required accept=".pdf,.txt" onChange=${(event) => setFile(event.target.files[0])} />
      <div class="actions" style=${{ marginTop: 14 }}>
        <button class="btn" disabled=${busy || !file}>${busy ? "Starting" : "Start the review"}</button>
      </div>
      ${error && html`<p class="error">${error}</p>`}
    </form>
  </div>`;
}

function CaseDesk() {
  const [cases, setCases] = useState([]);
  const [form, setForm] = useState({ solicitation: "", title: "", buyer: "" });
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/cases").then(setCases).catch((problem) => setError(problem.message));
  }, []);

  const create = async (event) => {
    event.preventDefault();
    try {
      const created = await post("/api/cases", form);
      location.hash = `/case/${created.id}/add`;
    } catch (problem) {
      setError(problem.message);
    }
  };
  const field = (key, label) => html`<div>
    <label>${label}</label>
    <input type="text" required value=${form[key]} onInput=${(event) => setForm({ ...form, [key]: event.target.value })} />
  </div>`;
  const others = cases.filter((item) => !item.is_demo).slice(0, 8);

  return html`
    <${CaseView} caseId=${SAMPLE_CASE} />
    <div class="section-head"><h2>Start your own case</h2></div>
    <form class="card" onSubmit=${create}>
      <div class="grid-2">${field("solicitation", "Solicitation number")} ${field("buyer", "Buyer")}</div>
      ${field("title", "Title")}
      <div class="actions" style=${{ marginTop: 14 }}><button class="btn">Create case</button></div>
      ${error && html`<p class="error">${error}</p>`}
    </form>
    ${others.length > 0 &&
    html`<div class="section-head"><h2>Recent cases</h2></div>
      <div class="table-wrap">
        <table>
          <tbody>
            ${others.map(
              (item) => html`<tr class="link" onClick=${() => (location.hash = `/case/${item.id}`)}>
                <td><a href=${`#/case/${item.id}`}>${item.solicitation}</a></td>
                <td>${item.title}</td>
                <td class="muted small">${item.vendor_count} vendors</td>
              </tr>`,
            )}
          </tbody>
        </table>
      </div>`}
  `;
}

function Lookup() {
  const [query, setQuery] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const search = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setData(null);
    try {
      setData(await post("/api/lookup", { query }));
    } catch (problem) {
      setError(problem.message);
    }
    setBusy(false);
  };

  return html`<section class="hero" style=${{ paddingBottom: 12 }}>
      <div class="eyebrow">Public record lookup</div>
      <h1>Look up any federal contract or company</h1>
      <p>This shows what the public record says and draws no conclusion about anyone.</p>
    </section>
    <form class="card" onSubmit=${search}>
      <label style=${{ marginTop: 0 }}>Contract number or company name</label>
      <input type="text" required value=${query} onInput=${(event) => setQuery(event.target.value)} placeholder="75H70125C00023" />
      <div class="actions" style=${{ marginTop: 12 }}><button class="btn" disabled=${busy}>${busy ? "Searching" : "Search the record"}</button></div>
      ${error && html`<p class="error">${error}</p>`}
    </form>
    ${data?.kind === "contract" &&
    (data.found
      ? html`<div class="section-head"><h2>${data.record.piid}</h2><p>${data.record.sub_agency || data.record.agency}</p></div>
          <div class="card">
            <p style=${{ marginTop: 0 }}>${data.record.description}</p>
            <p class="small muted">
              ${data.record.recipient_name}, ${data.record.start} to ${data.record.end}. Peak ${money(data.record.peak_obligation)}, now
              ${money(data.record.total_obligation)}. <a href=${data.record.url} target="_blank" rel="noopener">Open on USAspending.gov</a>
            </p>
          </div>
          <div class="section-head"><h2>Modifications</h2></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Mod</th><th>Action</th><th>Obligation</th></tr></thead>
              <tbody>
                ${data.record.actions.map(
                  (action) => html`<tr>
                    <td class="mono small">${action.date}</td>
                    <td class="mono small">${action.mod}</td>
                    <td class="small">${action.type_label || "Base award"}</td>
                    <td class="mono small">${money(action.amount)}</td>
                  </tr>`,
                )}
              </tbody>
            </table>
          </div>`
      : html`<p class="muted" style=${{ marginTop: 16 }}>No federal contract found for ${data.query}.</p>`)}
    ${data?.kind === "company" &&
    html`<div class="section-head"><h2>${data.awards.length} recent federal contracts</h2></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Contract</th><th>Recipient</th><th>Agency</th><th>Value</th></tr></thead>
          <tbody>
            ${data.awards.map(
              (award) => html`<tr>
                <td class="mono small"><a href=${award.url} target="_blank" rel="noopener">${award.piid}</a></td>
                <td class="small">${award.recipient}</td>
                <td class="small">${award.agency}</td>
                <td class="mono small">${money(award.amount)}</td>
              </tr>`,
            )}
          </tbody>
        </table>
      </div>
      <p class="small muted">
        Terminations on record: ${data.terminations.length}. Exclusions list matches: ${data.exclusions?.length ?? "not checked"}.
        OFAC matches: ${data.sanctions?.length ?? "not checked"}. A name match is a lead for a person to confirm.
      </p>`}`;
}

function Benchmark() {
  const [data, setData] = useState(null);
  useEffect(() => {
    api("/api/benchmark").then(setData);
  }, []);
  if (!data) return html`<p class="muted">Loading.</p>`;
  if (!data.available) return html`<section class="hero"><h1>Benchmark</h1><p>Results are published after the next run.</p></section>`;
  return html`<section class="hero" style=${{ paddingBottom: 12 }}>
      <div class="eyebrow">Benchmark, ${data.generated}</div>
      <h1>${data.headline}</h1>
      <p>${data.method}</p>
    </section>
    <div class="stat-grid">
      ${data.stats.map((stat) => html`<div class="card"><b>${stat.value}</b><span class="small muted">${stat.label}</span></div>`)}
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Forgery type</th><th>Cases</th><th>Caught by forward only</th><th>Caught with reverse pass</th></tr></thead>
        <tbody>
          ${data.rows.map(
            (row) => html`<tr><td>${row.label}</td><td>${row.cases}</td><td>${row.forward}</td><td>${row.full}</td></tr>`,
          )}
        </tbody>
      </table>
    </div>
    ${data.note && html`<p class="small muted">${data.note}</p>`}`;
}

function How() {
  return html`<section class="hero" style=${{ paddingBottom: 12 }}>
      <div class="eyebrow">How it works</div>
      <h1>Confirming a claim is the easy half</h1>
      <p>
        A vendor's list of past contracts is a list of the ones that went well. Crossexam checks each claim, then runs a fixed
        second pass aimed at what the vendor had no reason to mention.
      </p>
    </section>
    <div class="flow">
      <div><b>1. Open</b>The file is parsed in a gVisor sandbox with no network or secrets. Hidden text is separated out.</div>
      <div><b>2. Read</b>A model on Vultr Serverless Inference lists the claims. Each must quote the document exactly.</div>
      <div><b>3. Forward</b>Each claim is compared with its federal record: recipient, value, dates, customer.</div>
      <div><b>4. Reverse</b>Every modification, the vendor's full contract history, exclusions, sanctions and SEC filings.</div>
      <div><b>5. Decide</b>Fixed rules set the outcome. A model drafts the letter. A named person signs.</div>
    </div>
    <div class="prose">
      <h2>Why the model does not decide</h2>
      <p>
        Vendor documents are written by the party being judged, so anything the model reads can be aimed at it. Verdicts come
        from rules applied to public records, and the model has no path to them. Sample Vendor D shows this: its statement hides an
        instruction to mark everything verified, and the outcome does not move.
      </p>
      <h2>What runs on Vultr</h2>
      <p>
        One Cloud Compute VM hosts the application, the review pipeline and the document sandboxes. All model calls go to Vultr
        Serverless Inference. Source code is on <a href="https://github.com/muchensu77-create/crossexam">GitHub</a> under the MIT license.
      </p>
      <h2>What it does not do</h2>
      <p>
        Coverage is federal public data, so state, local and private contracts come back as unverifiable and need a customer
        reference. A flag is a lead for a person to review, and it says nothing about anyone's intent. Name matches on exclusions and
        sanctions lists can collide, which is why they place a hold and stop there.
      </p>
    </div>`;
}

function App() {
  const route = useRoute();
  const parts = route.split("/").filter(Boolean);
  let page;
  if (parts[0] === "case" && parts[2] === "add") page = html`<${AddVendor} caseId=${parts[1]} />`;
  else if (parts[0] === "case") page = html`<${CaseView} key=${parts[1]} caseId=${parts[1]} />`;
  else if (parts[0] === "vendor") page = html`<${VendorView} key=${parts[1]} vendorId=${parts[1]} />`;
  else if (parts[0] === "lookup") page = html`<${Lookup} />`;
  else if (parts[0] === "benchmark") page = html`<${Benchmark} />`;
  else if (parts[0] === "how") page = html`<${How} />`;
  else page = html`<${CaseDesk} />`;
  return html`<${Topbar} route=${route} /><main class="shell">${page}</main>`;
}

render(html`<${App} />`, document.getElementById("root"));
