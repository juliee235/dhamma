"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const messagesEl = $("#messages");
const input = $("#q");
const sendBtn = $("#send");

const EXAMPLES = [
  "สติปัฏฐาน ๔ มีอะไรบ้าง",
  "ปัจฉิมวาจาของพระพุทธเจ้าคืออะไร ตรัสที่ไหน",
  "มงคลสูตรกล่าวถึงมงคลอะไรบ้าง",
  "ธรรมทายาทสูตรสอนอะไร",
  "ปฏิจจสมุปบาทคืออะไร",
  "เวรย่อมระงับด้วยอะไร ตามธรรมบท",
];
const STATUS_LABEL = {
  verified: "✅ อ่านตัวบทแล้ว",
  outline: "🔷 อ้างทั้งหมวด (ตรงกับโครงสร้าง)",
  seen: "🟡 เห็นเพียงบางส่วน",
  unread: "🟠 ไม่ได้เปิดอ่าน",
  invalid: "❌ ไม่มีอยู่จริง",
};
const state = { history: [], busy: false, cards: {} };
const welcomeHTML = $("#welcome").outerHTML;

// ---------- helpers ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function md(text) {
  if (window.marked && window.DOMPurify) {
    return DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }));
  }
  return esc(text).replace(/\n/g, "<br>");   // CDN unavailable: plain text
}
function keyLabel(key) {
  return "ข้อ " + key.split(":")[1].replace("-", "–");
}
function pathText(card) {
  return (card.path && card.path.length ? card.path : [card.sutta, ...(card.section_path || [])])
    .filter(Boolean).join(" › ");
}
function unitName(card) {
  // Units without a sutta name in the source are named after their วรรค ("อนนุสสุตวรรคที่ ๔"):
  // put the นิกาย / book in front so readers know where it is.
  const ctx = card.context || [];
  if (card.sutta && ctx.length && ctx[ctx.length - 1] === card.sutta) {
    return `${ctx[0].split(" ")[0]} · ${card.sutta}`;
  }
  return card.sutta;
}
function citeLabel(card) {
  const num = card.citation.replace(/^เล่ม\s*\d+\s*/, "");          // "ข้อ 274" or "พุทธวงศ์ ข้อ 5"
  const name = unitName(card);
  if (!name) return num;
  return num.startsWith("ข้อ") ? `${name} ${num}` : `${name} · ${num}`;
}
function pages(a, b) {
  if (!a) return "";
  return "หน้า " + a + (b && b !== a ? "–" + b : "");
}
function nearBottom() {
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120;
}
function scrollDown(force = false) {
  if (force || nearBottom()) messagesEl.scrollTop = messagesEl.scrollHeight;
}

function chip(key, card) {
  const status = card ? card.status : "pending";
  const title = card ? `${card.citation} · ${pathText(card)} · ${card.status_label}` : key;
  // "มหาสติปัฏฐานสูตร ข้อ 274" / "สุเมธกถา · พุทธวงศ์ ข้อ 5": always say which sutta it comes from
  const label = card ? citeLabel(card) : keyLabel(key);
  return `<button type="button" class="cite s-${status}" data-open="${esc(key)}" title="${esc(title)}">${esc(label)}</button>`;
}

function groupChips(keys, cards) {
  const groups = [];
  for (const k of [...new Set(keys)]) {
    const c = cards[k];
    const name = c ? unitName(c) || c.citation : k;
    const g = groups.find((x) => x.name === name);
    if (g) g.keys.push(k); else groups.push({ name, keys: [k] });
  }
  return groups.map((g) => {
    if (g.keys.length === 1) return chip(g.keys[0], cards[g.keys[0]]);
    // worst status of the group decides the colour; the chip opens the first passage
    const order = ["invalid", "unread", "seen", "outline", "verified"];
    const status = g.keys.map((k) => cards[k]?.status || "pending").sort((a, b) => order.indexOf(a) - order.indexOf(b))[0];
    const nums = g.keys.map((k) => k.split(":")[1].replace("-", "–")).join(", ");
    const title = g.keys.map((k) => cards[k]?.citation || k).join(" · ");
    return `<button type="button" class="cite s-${status}" data-open="${esc(g.keys[0])}" title="${esc(title)}">${esc(g.name)} ข้อ ${esc(nums)}</button>`;
  }).join("");
}

// ---------- SSE over fetch (POST) ----------
async function* readSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

// ---------- chat ----------
function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  messagesEl.append(el);
}

function addBot() {
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `
    <div class="card">
      <details class="steps" open><summary>กำลังค้นคว้า…</summary><ol></ol></details>
      <div class="answer"><span class="typing">กำลังค้นตัวบท</span></div>
      <div class="sources" hidden></div>
      <div class="warnings"></div>
    </div>`;
  messagesEl.append(el);
  return {
    steps: $(".steps", el), stepList: $(".steps ol", el), answer: $(".answer", el),
    sources: $(".sources", el), warnings: $(".warnings", el),
  };
}

let draftFrame = 0;
function showDraft(ui, draft) {
  cancelAnimationFrame(draftFrame);
  draftFrame = requestAnimationFrame(() => {
    // citations show as grey "pending" chips until the verifier has checked them
    ui.answer.innerHTML = md(draft).replace(/\[เล่ม\s*[0-9๐-๙]+\s*ข้อ[^\]]*\]/g, (m) => `<span class="cite">${m.slice(1, -1)}</span>`);
    scrollDown();
  });
}

function renderFinal(ui, d) {
  cancelAnimationFrame(draftFrame);
  ui.answer.classList.remove("checking");
  Object.assign(state.cards, d.cards);
  // adjacent citations become one chip per sutta: "มหาสติปัฏฐานสูตร ข้อ 274, 275"
  ui.answer.innerHTML = md(d.answer).replace(/(?:⟦\d+⟧\s*)+/g, (run) => {
    const keys = [...run.matchAll(/⟦(\d+)⟧/g)].flatMap((m) => d.citations[Number(m[1])] || []);
    return groupChips(keys, d.cards) + " ";
  });

  const n = ui.stepList.children.length;
  ui.steps.querySelector("summary").textContent = n ? `ดูขั้นตอนการค้น (${n} ขั้น)` : "ตอบโดยไม่ได้ค้นตัวบท";
  ui.steps.open = false;

  const keys = Object.keys(d.cards);
  if (keys.length) {
    ui.sources.hidden = false;
    ui.sources.innerHTML = `<details><summary>แหล่งอ้างอิง (${keys.length}) — กดเพื่อดู</summary><ul>` + keys.map((k) => {
      const c = d.cards[k];
      return `<li>${chip(k, c)}<span class="where">${esc(c.status_label)}<br><small>${esc(pathText(c))}</small></span></li>`;
    }).join("") + "</ul></details>";
  }
  if (d.warnings.length) {
    ui.warnings.innerHTML = "<strong>ข้อสังเกตจากระบบตรวจอ้างอิง</strong><ul>" +
      d.warnings.map((w) => `<li>${esc(w)}</li>`).join("") + "</ul>";
  }
}

function setBusy(busy) {
  state.busy = busy;
  sendBtn.disabled = busy;
  sendBtn.textContent = busy ? "กำลังค้น…" : "ถาม";
}

async function ask(question) {
  question = question.trim();
  if (!question || state.busy) return;
  setBusy(true);
  $("#welcome")?.remove();
  addUser(question);
  const ui = addBot();
  scrollDown(true);
  let draft = "";
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: state.history.slice(-8) }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : `เชื่อมต่อไม่สำเร็จ (${res.status})`);
    }
    let finished = false;
    for await (const ev of readSSE(res)) {
      if (ev.event === "step") {
        const li = document.createElement("li");
        li.textContent = ev.data.text;
        ui.stepList.append(li);
        if (ev.data.tool === "notice" && draft.trim()) {
          // the draft is being checked against the scripture: keep it visible until the checked version arrives
          ui.answer.classList.add("checking");
          ui.answer.dataset.note = ev.data.text;
        } else {
          ui.answer.innerHTML = '<span class="typing">กำลังค้นตัวบท</span>';
        }
        draft = "";
        scrollDown();
      } else if (ev.event === "token") {
        ui.answer.classList.remove("checking");
        draft += ev.data.text;
        showDraft(ui, draft);
      } else if (ev.event === "final") {
        renderFinal(ui, ev.data);
        state.history.push({ role: "user", content: question }, { role: "assistant", content: ev.data.history_text });
        finished = true;
      } else if (ev.event === "error") {
        throw new Error(ev.data.message);
      }
    }
    if (!finished) throw new Error("การเชื่อมต่อขาดก่อนได้คำตอบ กรุณาลองใหม่");
  } catch (e) {
    cancelAnimationFrame(draftFrame);
    ui.answer.innerHTML = `<p class="error">${esc(e.message || "เกิดข้อผิดพลาด")}</p>`;
    ui.steps.querySelector("summary").textContent = "ไม่สำเร็จ";
  } finally {
    setBusy(false);
    scrollDown();
    loadStats();
  }
}

// ---------- reader ----------
const reader = $("#reader");
const readerBody = $("#reader-body");

async function openReader(key) {
  reader.hidden = false;
  $("#reader-title").textContent = keyLabel(key);
  readerBody.innerHTML = '<p class="muted">กำลังโหลด…</p>';
  readerBody.scrollTop = 0;
  let res;
  try {
    res = await fetch(`/api/passage/${encodeURIComponent(key)}`);
  } catch {
    readerBody.innerHTML = '<p class="error">โหลดไม่สำเร็จ</p>';
    return;
  }
  if (!res.ok) {
    const flagged = state.cards[key]?.status === "invalid";
    readerBody.innerHTML = `<p class="error">${res.status === 404 ? "ไม่พบข้อนี้ในฐานข้อมูล" : "โหลดไม่สำเร็จ"}</p>` +
      (flagged ? '<p class="muted">อ้างอิงนี้ถูกระบบตรวจตั้งธงไว้ว่าไม่มีอยู่จริง</p>' : "");
    return;
  }
  const d = await res.json();
  if (d.kind === "range") renderRange(d); else renderPassage(d);
}

function statusBadge(key) {
  const c = state.cards[key];
  return c && c.status !== "invalid" ? `<span class="badge s-${c.status}">${esc(STATUS_LABEL[c.status])}</span>` : "";
}

function outlineHTML(d) {
  if (!d.outline || !d.outline.length) return "";
  const pre = d.key.split(":")[0];   // "10" or "33.2"
  const items = d.outline.map((o) => {
    const key = o.start_para === o.end_para ? `${pre}:${o.start_para}` : `${pre}:${o.start_para}-${o.end_para}`;
    const cur = d.para_no >= o.start_para && d.para_no <= o.end_para ? " current" : "";
    const sub = o.kind === "pabba" ? " sub" : "";
    return `<li class="${sub}${cur}"><button type="button" data-open="${key}">${esc(o.title)}</button><span>ข้อ ${o.start_para}${o.end_para !== o.start_para ? "–" + o.end_para : ""}</span></li>`;
  }).join("");
  return `<details class="outline" open><summary>โครงสร้าง${esc(d.sutta.title)}</summary><ul>${items}</ul></details>`;
}

function crumbsHTML(path, volume) {
  return `<div class="crumbs"><button type="button" class="linklike" data-toc="${volume}">เล่ม ${volume}</button> › ` +
    path.map(esc).join(" › ") + "</div>";
}

function renderPassage(d) {
  $("#reader-title").textContent = d.sutta.title;
  const meta = [pages(d.page_start, d.page_end), d.sutta.nikaya_no ? `ลำดับในนิกาย: สูตรที่ ${d.sutta.nikaya_no}` : ""]
    .filter(Boolean).join(" · ");
  const prev = d.prev ? `<button type="button" class="ghost" data-open="${d.prev.ref}">← ${esc(keyLabel(d.prev.ref))}</button>` : "";
  const next = d.next ? `<button type="button" class="ghost next" data-open="${d.next.ref}">${esc(keyLabel(d.next.ref))} →</button>` : "";
  const whole = d.unit_range && d.unit_range.split("-")[1] - d.unit_range.split(":")[1].split("-")[0] < 200
    ? `<button type="button" class="ghost" data-open="${d.unit_range}">ดูทุกข้อใน${esc(d.sutta.title)}</button>` : "";
  const trailer = d.trailer
    ? `<details class="trailer"><summary>ท้ายสูตร (อุททาน / สารบัญ)</summary><div class="scripture small">${esc(d.trailer)}</div></details>` : "";
  readerBody.innerHTML = `
    <h2 class="sutta-title">${esc(d.sutta.title)}</h2>
    <div class="cite-line">${esc(d.citation)}</div>
    ${crumbsHTML(d.path, d.volume)}
    <div class="meta">${esc(meta)}</div>
    ${statusBadge(d.key)}
    <div class="scripture">${esc(d.text)}</div>
    ${trailer}
    <nav class="pager">${prev}${next}</nav>
    ${whole ? `<p>${whole}</p>` : ""}
    ${outlineHTML(d)}`;
  readerBody.scrollTop = 0;
}

function renderRange(d) {
  const unitName = d.path.length ? d.path[d.path.length - 1] : d.citation;
  $("#reader-title").textContent = unitName;
  const items = d.items.map((i) => `
    <li><button type="button" data-open="${i.ref}">
      <b>${esc(keyLabel(i.ref))}</b>${esc(i.snippet)}…
      ${i.path.length ? `<small>${esc(i.path.join(" › "))}</small>` : ""}
    </button></li>`).join("");
  readerBody.innerHTML = `
    <h2 class="sutta-title">${esc(unitName)}</h2>
    <div class="cite-line">${esc(d.citation)}</div>
    ${crumbsHTML(d.path, d.volume)}
    <div class="meta">${d.items.length} ข้อ · กดที่ข้อเพื่ออ่านตัวบทเต็ม</div>
    ${statusBadge(d.key)}
    <ul class="range-list">${items}</ul>`;
  readerBody.scrollTop = 0;
}

// ---------- table of contents of a volume ----------
async function openToc(volume) {
  reader.hidden = false;
  $("#reader-title").textContent = `เล่ม ${volume}`;
  readerBody.innerHTML = '<p class="muted">กำลังโหลด…</p>';
  const res = await fetch(`/api/toc/${encodeURIComponent(volume)}`).catch(() => null);
  if (!res || !res.ok) { readerBody.innerHTML = '<p class="error">โหลดสารบัญไม่สำเร็จ</p>'; return; }
  const t = await res.json();
  const divs = Object.fromEntries(t.divisions.map((d) => [d.id, d]));
  const chain = (id) => { const out = []; while (id && divs[id]) { out.unshift(divs[id]); id = divs[id].parent; } return out; };
  let shown = [];
  const rows = [];
  for (const u of t.units) {
    const c = chain(u.division_id);
    let k = 0;
    while (k < shown.length && k < c.length && shown[k].id === c[k].id) k++;
    for (const d of c.slice(k)) {
      if (u.synthetic && d.id === u.division_id && d.title === u.title) continue;  // the unit itself
      rows.push(`<li class="toc-div l${d.level}">${esc(d.title)}</li>`);
    }
    shown = c;
    const n = u.last_para - u.first_para;
    const pre = u.part > 1 ? `${t.no}.${u.part}` : `${t.no}`;
    const key = n > 0 && n < 200 ? `${pre}:${u.first_para}-${u.last_para}` : `${pre}:${u.first_para}`;
    rows.push(`<li class="toc-unit${c.length ? " in" + Math.min(c.length, 2) : ""}"><button type="button" data-open="${key}">${esc(u.title)}</button>` +
      `<span>ข้อ ${u.first_para}${n > 0 ? "–" + u.last_para : ""}</span></li>`);
  }
  readerBody.innerHTML = `
    <div class="crumbs">${esc(t.pitaka || "")}</div>
    <h2>เล่ม ${t.no} · ${esc(t.title)}</h2>
    <div class="meta">${t.units.length} หน่วย (สูตร/วรรค)</div>
    <ul class="toc">${rows.join("")}</ul>`;
  readerBody.scrollTop = 0;
}

// ---------- answered count (welcome) and today's quota (above the composer) ----------
async function loadStats() {
  try {
    const { answered, remaining, limit } = await (await fetch("/api/stats")).json();
    const count = $("[data-stats]");
    if (count && typeof answered === "number") {
      count.innerHTML = `ตอบไปแล้ว <b>${answered.toLocaleString("th-TH")}</b> คำถาม`;
      count.hidden = false;
    }
    const quota = $("#quota");
    if (typeof remaining === "number") {
      quota.innerHTML = remaining > 0
        ? `วันนี้เหลือ <b>${remaining}/${limit}</b> คำถาม`
        : `วันนี้ถามครบ <b>${limit}/${limit}</b> แล้ว พรุ่งนี้ถามต่อได้`;
      quota.classList.toggle("out", remaining === 0);
      quota.hidden = false;
    }
  } catch { /* both are optional */ }
}

// ---------- welcome ----------
async function initWelcome() {
  const ex = $("#examples");
  if (ex) ex.innerHTML = EXAMPLES.map((q) => `<button type="button" class="example" data-ask="${esc(q)}">${esc(q)}</button>`).join("");
  loadStats();
  const list = $("#volume-list");
  try {
    const vols = await (await fetch("/api/volumes")).json();
    const nos = vols.map((v) => v.no);
    $("#scope").textContent = nos.length
      ? `ข้อมูล: เล่ม ${nos[0]}${nos.length > 1 ? "–" + nos[nos.length - 1] : ""}` : "ยังไม่มีข้อมูล";
    if (!list) return;
    let lastNikaya = null;
    list.innerHTML = vols.map((v) => {
      const nik = v.nikaya || v.pitaka || "";
      const head = nik !== lastNikaya ? `<h4 class="vol-group">${esc(nik)}</h4>` : "";
      lastNikaya = nik;
      const sub = v.nikaya ? v.title.replace(v.nikaya, "").trim() : v.title;
      return `${head}<button type="button" class="sutta-card" data-toc="${v.no}">
        <b>เล่ม ${v.no} · ${esc(sub)}</b><span>${v.units} หน่วย · ข้อ ${v.first_para}–${v.last_para}</span></button>`;
    }).join("");
  } catch {
    if (list) list.innerHTML = '<p class="muted">โหลดรายชื่อเล่มไม่สำเร็จ</p>';
  }
}

// ---------- events ----------
document.addEventListener("click", (e) => {
  const open = e.target.closest("[data-open]");
  if (open) { openReader(open.dataset.open); return; }
  const toc = e.target.closest("[data-toc]");
  if (toc) { openToc(toc.dataset.toc); return; }
  const ex = e.target.closest("[data-ask]");
  if (ex) { ask(ex.dataset.ask); }
});
$("#reader-close").addEventListener("click", () => { reader.hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") reader.hidden = true; });

$("#ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value;
  input.value = "";
  input.style.height = "";
  ask(q);
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("#ask").requestSubmit();
  }
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
});
$("#new-chat").addEventListener("click", () => {
  if (state.busy) return;
  state.history = [];
  state.cards = {};
  messagesEl.innerHTML = welcomeHTML;
  reader.hidden = true;
  initWelcome();
  input.focus();
});

// ---------- left tabs: chat / short dramas ----------
const VIEWS = ["chat", "drama"];
function showView(name) {
  if (!VIEWS.includes(name)) name = "chat";
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== name;
  document.querySelectorAll("[data-view]").forEach((b) =>
    b.setAttribute("aria-current", b.dataset.view === name ? "page" : "false"));
  $("#new-chat").hidden = name !== "chat";
  if (name === "drama") { loadDramas(); if (!$("#drama-story").hidden) observeReels(); } else pauseReels();
  try { localStorage.setItem("view", name); } catch { /* storage unavailable */ }
}

// the drama tab opens on a shelf of stories; choosing one opens its episodes as the vertical feed
let dramas = null;
async function loadDramas() {
  if (dramas) return;
  const shelf = $("#drama-list");
  try {
    dramas = await (await fetch("/static/dramas.json")).json();
    shelf.innerHTML = dramas.length ? dramas.map(storyHTML).join("") : '<p class="drama-note">ยังไม่มีละคร</p>';
  } catch {
    dramas = null;
    shelf.innerHTML = '<p class="drama-note">โหลดละครไม่สำเร็จ ลองกดแท็บอีกครั้ง</p>';
  }
}

const thaiNum = (n) => String(n).replace(/\d/g, (d) => "๐๑๒๓๔๕๖๗๘๙"[d]);
function storyHTML(s, i) {
  const n = s.episodes.length;
  const cover = s.poster
    ? `<img src="${esc(s.poster)}" alt="" loading="lazy">`
    : `<span class="story-blank" aria-hidden="true">${esc(s.title.slice(0, 1))}</span>`;
  return `<button type="button" class="story" data-story="${i}">${cover}
    <span class="story-text"><strong>${esc(s.title)}</strong>
    <span class="story-meta">${n ? `${thaiNum(n)} ตอน` : "ยังไม่มีวิดีโอ"} · ${esc(s.source)}</span></span></button>`;
}

function openStory(i) {
  const s = dramas[i];
  // a story without video yet still opens: one card with its summary and the passage link
  const eps = s.episodes.length ? s.episodes : [{ title: s.title, source: s.source, ref: s.ref, summary: s.summary }];
  const feed = $("#drama-feed");
  feed.innerHTML = eps.map(reelHTML).join("");
  $("#drama-list").hidden = true;
  $("#drama-story").hidden = false;
  feed.scrollTop = 0;
  observeReels();
  $("#drama-back").focus();
}
function closeStory() {
  pauseReels();
  reelObserver?.disconnect();
  $("#drama-feed").innerHTML = "";
  $("#drama-story").hidden = true;
  $("#drama-list").hidden = false;
}

function reelHTML(d) {
  const media = d.video
    ? `<video class="reel-video" src="${esc(d.video)}"${d.poster ? ` poster="${esc(d.poster)}"` : ""} playsinline loop muted preload="metadata"></video>
       <button type="button" class="reel-sound" aria-label="เปิดเสียง">🔇</button>`
    : `<div class="reel-placeholder"><span class="soon">ยังไม่มีวิดีโอ</span><p>${esc(d.summary)}</p></div>`;
  return `<article class="reel" aria-label="${esc(d.title)}"><div class="reel-frame">${media}
    <div class="reel-info">
      <h3>${esc(d.title)}</h3>${d.video ? `<p>${esc(d.summary)}</p>` : ""}
      <div class="reel-actions">
        <button type="button" class="reel-btn" data-open="${esc(d.ref)}">📖 อ่านตัวบท · ${esc(d.source)}</button>
      </div>
    </div></div></article>`;
}

let reelObserver = null;
function observeReels() {
  // play the clip that is on screen, pause the rest
  reelObserver?.disconnect();
  reelObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const v = e.target.querySelector("video");
      if (!v) continue;
      if (e.isIntersecting) v.play().catch(() => { /* autoplay refused: tap to play */ });
      else v.pause();
    }
  }, { threshold: 0.6 });
  document.querySelectorAll(".reel").forEach((r) => reelObserver.observe(r));
}
function pauseReels() { document.querySelectorAll(".reel-video").forEach((v) => v.pause()); }

document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-view]");
  if (tab) { showView(tab.dataset.view); return; }
  const story = e.target.closest("[data-story]");
  if (story) { openStory(+story.dataset.story); return; }
  if (e.target.closest("#drama-back")) { closeStory(); return; }
  const sound = e.target.closest(".reel-sound");
  if (sound) {
    const v = sound.parentElement.querySelector("video");
    v.muted = !v.muted;
    sound.textContent = v.muted ? "🔇" : "🔊";
    sound.setAttribute("aria-label", v.muted ? "เปิดเสียง" : "ปิดเสียง");
    return;
  }
  const video = e.target.closest(".reel-video");
  if (video) { if (video.paused) video.play().catch(() => {}); else video.pause(); }
});

// ---------- text size (for older readers) ----------
const SIZES = [17, 19, 21, 24];
function applySize(i) {
  i = Math.max(0, Math.min(SIZES.length - 1, i));
  document.documentElement.style.setProperty("--fs", SIZES[i] + "px");
  try { localStorage.setItem("fontStep", String(i)); } catch { /* storage unavailable */ }
  return i;
}
let sizeStep = 2;
try { const v = localStorage.getItem("fontStep"); if (v !== null) sizeStep = Number(v); } catch { /* ignore */ }
sizeStep = applySize(sizeStep);
$("#font-down").addEventListener("click", () => { sizeStep = applySize(sizeStep - 1); });
$("#font-up").addEventListener("click", () => { sizeStep = applySize(sizeStep + 1); });

initWelcome();
let startView = "chat";
try { startView = localStorage.getItem("view") || "chat"; } catch { /* ignore */ }
showView(startView);
