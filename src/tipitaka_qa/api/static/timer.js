"use strict";
// Meditation timer: a plan of walking (จงกรม) and sitting segments, a bowl bell at each change,
// and a lotus that opens over the whole session. Time comes from the clock (end timestamps), so a
// throttled background tab catches up instead of drifting.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const view = $("#view-timer");
  if (!view) return;

  const KIND = { walk: "เดินจงกรม", sit: "นั่งสมาธิ" };
  const SHORT = { walk: "เดิน", sit: "นั่ง" };
  const PRESETS = [
    [["walk", 20], ["sit", 20], ["walk", 20], ["sit", 20]],
    [["walk", 40], ["sit", 20]],
    [["sit", 30]],
  ];
  const PRESET_NAME = ["เดิน-นั่ง 20 · 2 รอบ", "เดิน 40 · นั่ง 20", "นั่งอย่างเดียว"];
  const STEP = 5, MIN = 5, MAX = 120, MAX_SEGS = 8;
  const MS = 60000;

  const toPlan = (p) => p.map(([k, m]) => ({ k, m }));
  let plan = toPlan(PRESETS[0]);
  try {
    const saved = JSON.parse(localStorage.getItem("timerPlan"));
    if (Array.isArray(saved) && saved.length && saved.every((s) => KIND[s.k] && s.m >= MIN && s.m <= MAX)) plan = saved;
  } catch { /* storage unavailable or bad data */ }
  const savePlan = () => { try { localStorage.setItem("timerPlan", JSON.stringify(plan)); } catch { /* ignore */ } };

  // run: { idx, end (epoch ms, while running), left (ms, while paused), paused }
  let run = null, finished = false, ticker = 0, practiced = 0;   // practiced: ms actually sat/walked, shown at the end

  // ---------- the lotus ----------
  // petals fan out from the base; [closed°, open°] per petal, back layer first
  const LAYERS = [
    { cls: "back", L: 104, W: 34, squash: 0, angles: [[-16, -84], [-9, -52], [-3, -18], [3, 18], [9, 52], [16, 84]] },
    { cls: "mid", L: 108, W: 30, squash: .1, angles: [[-7, -38], [7, 38]] },
    { cls: "front", L: 92, W: 33, squash: .28, angles: [[-12, -64], [-5, -30], [0, 0], [5, 30], [12, 64]] },
  ];
  const petal = (L, W) => `M0 0C${-W} ${-L * .26} ${-W * .82} ${-L * .8} 0 ${-L}C${W * .82} ${-L * .8} ${W} ${-L * .26} 0 0Z`;
  const BX = 180, BY = 206, CY = 132, R = 124, RING = 2 * Math.PI * R, WATER = 214;
  // a lily pad: a disc with a notch, seen in perspective, with veins from the notch
  const pad = (x, y, r, rot) => `<g class="pad" transform="translate(${x} ${y}) scale(${r} ${r * .28}) rotate(${rot})">` +
    `<path d="M0 0L.97-.24A1 1 0 1 0 .97 .24Z"/>` +
    `<path class="vein" vector-effect="non-scaling-stroke" d="${[-150, -100, -50, 0, 50, 100, 150, 180].map((g) =>
      `M0 0L${(Math.cos((g + 180) * Math.PI / 180) * .9).toFixed(2)} ${(Math.sin((g + 180) * Math.PI / 180) * .9).toFixed(2)}`).join("")}"/></g>`;
  // pollen drifting up off the water: [x, y, r, seconds, delay]
  const MOTES = [[118, 200, 1.6, 11, 0], [150, 226, 1.2, 13, 4], [214, 196, 2, 12, 7], [246, 222, 1.4, 10, 2], [96, 232, 1.2, 14, 9], [266, 204, 1.6, 12, 5], [190, 236, 1.1, 15, 11]];
  view.querySelector(".lotus").innerHTML = `
    <defs>
      <radialGradient id="t-sky" cx=".5" cy=".55" r=".5"><stop class="sky0" offset="0"/><stop class="sky1" offset=".62"/><stop class="sky2" offset="1"/></radialGradient>
      <radialGradient id="t-water" cx=".5" cy="0" r=".7" gradientTransform="translate(.15 0) scale(.7 1)"><stop class="w0" offset="0"/><stop class="w1" offset="1"/></radialGradient>
      <linearGradient id="pg-back" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#f3d9d3"/><stop offset="1" stop-color="#c25f73"/></linearGradient>
      <linearGradient id="pg-front" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#fff4ee"/><stop offset=".55" stop-color="#f4c6c6"/><stop offset="1" stop-color="#d9798a"/></linearGradient>
      <linearGradient id="t-pad" x1="0" y1="-1" x2="0" y2="1"><stop offset="0" stop-color="#a9bf98"/><stop offset="1" stop-color="#6f8f62"/></linearGradient>
      <clipPath id="t-below"><rect x="0" y="${WATER}" width="360" height="80"/></clipPath>
    </defs>
    <circle class="sky" cx="${BX}" cy="${CY}" r="${R}"/>
    <circle class="ring-track" cx="${BX}" cy="${CY}" r="${R}"/>
    <circle class="ring" cx="${BX}" cy="${CY}" r="${R}" stroke-dasharray="${RING}" stroke-dashoffset="${RING}" transform="rotate(-90 ${BX} ${CY})"/>
    <path class="water" d="M0 ${WATER}H360V${WATER + 76}H0Z"/>
    <g clip-path="url(#t-below)"><use class="reflection" href="#t-flower" transform="translate(0 ${2 * WATER + 2}) scale(1 -.55) translate(0 ${-WATER * .82})"/></g>
    <g class="ripples">${[0, 1, 2].map((i) => `<ellipse cx="${BX}" cy="${BY + 10}" rx="34" ry="7" style="animation-delay:${-i * 3}s"/>`).join("")}</g>
    <path class="shimmer" d="M44 ${WATER + 22}h70M250 ${WATER + 26}h64M84 ${WATER + 44}h52M226 ${WATER + 50}h46M150 ${WATER + 64}h60"/>
    ${pad(84, 230, 64, 18)}${pad(286, 238, 50, 160)}${pad(238, 262, 26, 70)}
    <g id="t-flower" class="flower">
      <path class="stem" d="M${BX} ${BY}c-1 6 1 10 0 14"/>
      ${LAYERS.map((ly, li) => ly.angles.map((g) =>
        `<path class="petal ${ly.cls}" d="${petal(ly.L, ly.W)}M0 -6V${-ly.L * .82}" data-l="${li}" data-c="${g[0]}" data-o="${g[1]}"/>`).join("") +
        (ly.cls === "mid" ? `<g class="pod"><circle cx="${BX}" cy="${BY - 34}" r="13"/>${[-8, -3, 2, 7, -5.5, .5, 5.5].map((dx, k) =>
          `<circle class="seed" cx="${BX + dx}" cy="${BY - 37 + (k > 3 ? 6 : 0)}" r="1.6"/>`).join("")}</g>` : "")).join("")}
    </g>
    <g class="motes">${MOTES.map(([x, y, r, d, dl]) => `<circle cx="${x}" cy="${y}" r="${r}" style="animation-duration:${d}s;animation-delay:${-dl}s"/>`).join("")}</g>`;
  const petals = [...view.querySelectorAll(".petal")];
  const ring = $(".ring", view);

  function drawLotus(open, segFrac) {
    const e = open * open * (3 - 2 * open);   // smoothstep: the bud loosens slowly, then opens
    for (const p of petals) {
      const ly = LAYERS[+p.dataset.l], c = +p.dataset.c, o = +p.dataset.o;
      p.style.transform = `translate(${BX}px, ${BY}px) rotate(${c + (o - c) * e}deg) scaleY(${1 - ly.squash * e})`;
    }
    ring.setAttribute("stroke-dashoffset", String(RING * (1 - segFrac)));
  }

  // ---------- the bell: a struck bowl from a few decaying partials ----------
  let ac = null;
  function bell(times = 1, at = 0) {
    try {
      ac ||= new (window.AudioContext || window.webkitAudioContext)();
      if (ac.state === "suspended") ac.resume();
      for (let i = 0; i < times; i++) {
        const t = ac.currentTime + 0.05 + at + i * 3.5;
        for (const [r, g, d] of [[1, .42, 8], [2.02, .16, 5], [2.76, .1, 3.5], [5.4, .04, 1.8]]) {
          const o = ac.createOscillator(), v = ac.createGain();
          o.frequency.value = 294 * r;
          v.gain.setValueAtTime(0.0001, t);
          v.gain.exponentialRampToValueAtTime(g, t + 0.01);
          v.gain.exponentialRampToValueAtTime(0.0001, t + d);
          o.connect(v).connect(ac.destination);
          o.start(t); o.stop(t + d + 0.1);
        }
      }
    } catch { /* no audio: the timer still runs */ }
  }

  // ---------- spoken cues after the bell: which posture comes next, or that the session is over ----------
  const voices = {};
  async function loadVoices() {
    for (const k of ["walk", "sit", "end"]) {
      if (voices[k] || !ac) continue;
      try { voices[k] = await ac.decodeAudioData(await (await fetch(`/static/sounds/${k}.mp3`)).arrayBuffer()); }
      catch { /* offline or no decoder: the bell alone still marks the change */ }
    }
  }
  function say(k, at) {
    if (!ac || !voices[k]) return;
    const src = ac.createBufferSource();
    src.buffer = voices[k];
    src.connect(ac.destination);
    src.start(ac.currentTime + at);
  }
  const VOICE_AT = 2.2;   // seconds after the bell, once its strike has softened

  // ---------- keep the screen on while a session runs ----------
  let lock = null;
  async function keepAwake(on) {
    try {
      if (on && !lock && navigator.wakeLock) {
        lock = await navigator.wakeLock.request("screen");
        lock.addEventListener("release", () => { lock = null; });
      } else if (!on && lock) { await lock.release(); lock = null; }
    } catch { /* not allowed or not supported */ }
  }

  // ---------- time ----------
  const segMs = (i) => plan[i].m * MS;
  const totalMs = () => plan.reduce((s, x) => s + x.m * MS, 0);
  const leftMs = () => !run ? 0 : run.paused ? run.left : Math.max(0, run.end - Date.now());
  function clock(ms) {
    const s = Math.ceil(ms / 1000), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, ss = s % 60;
    const two = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${two(m)}:${two(ss)}` : `${m}:${two(ss)}`;
  }

  function tick() {
    if (!run || run.paused) return;
    const now = Date.now();
    if (now >= run.end) {
      // past one or more changes (a background tab may wake late): ring once, land in the right segment
      while (run && now >= run.end) {
        run.done += segMs(run.idx);
        if (run.idx + 1 >= plan.length) { finish(); return; }
        run.idx += 1; run.end += segMs(run.idx);
      }
      bell(1); say(plan[run.idx].k, VOICE_AT);
    }
    render();
  }

  function start() {
    if (finished) { finished = false; }
    if (!run) { run = { idx: 0, end: Date.now() + segMs(0), paused: false, done: 0 }; bell(1); loadVoices(); }
    else if (run.paused) { run.end = Date.now() + run.left; run.paused = false; }
    clearInterval(ticker); ticker = setInterval(tick, 500);
    keepAwake(true);
    render();
  }
  function pause() {
    if (!run || run.paused) return;
    run.left = Math.max(0, run.end - Date.now()); run.paused = true;
    clearInterval(ticker); keepAwake(false);
    render();
  }
  function stop() {
    run = null; finished = false;
    clearInterval(ticker); keepAwake(false);
    render();
  }
  // end the current segment early: on to the next one, or the session's end after the last
  function skip() {
    if (!run) return;
    run.done += segMs(run.idx) - leftMs();
    if (run.idx + 1 >= plan.length) { finish(); return; }
    run.idx += 1; run.end = Date.now() + segMs(run.idx); run.paused = false;
    clearInterval(ticker); ticker = setInterval(tick, 500);
    keepAwake(true);
    bell(1); say(plan[run.idx].k, VOICE_AT);
    render();
  }
  function finish() {
    practiced = run.done;
    run = null; finished = true;
    clearInterval(ticker); keepAwake(false);
    bell(1); say("end", VOICE_AT); bell(2, VOICE_AT + 4);
    render();
  }

  // ---------- drawing ----------
  const els = {
    phase: $("#t-phase"), clock: $("#t-clock"), next: $("#t-next"), plan: $("#t-plan"),
    presets: $("#t-presets"), edit: $("#t-edit"), go: $("#t-go"), stop: $("#t-stop"), skip: $("#t-skip"), tab: $('[data-view="timer"]'),
    setup: $("#t-setup"), open: $("#t-open"), begin: $("#t-begin"), total: $("#t-total"),
  };
  els.presets.innerHTML = PRESETS.map((p, i) =>
    `<button type="button" class="preset" data-preset="${i}"><span>${PRESET_NAME[i]}</span>` +
    `<small>${p.reduce((s, x) => s + x[1], 0)} นาที</small></button>`).join("");

  function render() {
    const editing = !run;
    els.stop.hidden = els.skip.hidden = !run;
    els.open.hidden = !!run;
    els.go.textContent = !run ? (finished ? "เริ่มอีกครั้ง" : "เริ่ม") : run.paused ? "ทำต่อ" : "พัก";
    els.tab?.classList.toggle("live", !!run && !run.paused);
    view.classList.toggle("running", !!run && !run.paused);

    let done = 0;          // ms of the session already behind us
    if (run) { for (let i = 0; i < run.idx; i++) done += segMs(i); done += segMs(run.idx) - leftMs(); }
    if (finished) done = totalMs();

    if (run) {
      els.phase.textContent = KIND[plan[run.idx].k] + (run.paused ? " · พักอยู่" : "");
      els.clock.textContent = clock(leftMs());
      const n = plan[run.idx + 1];
      els.next.textContent = n ? `ต่อไป ${KIND[n.k]} ${n.m} นาที` : "ช่วงสุดท้าย";
    } else if (finished) {
      els.phase.textContent = "ครบเวลาแล้ว";
      els.clock.textContent = clock(0);
      els.next.textContent = `ภาวนารวม ${Math.round(practiced / MS)} นาที`;
    } else {
      els.phase.textContent = "พร้อมเริ่ม";
      els.clock.textContent = clock(totalMs());
      els.next.textContent = `เริ่มด้วย${KIND[plan[0].k]} ${plan[0].m} นาที`;
    }

    // the plan as a strip: each segment as wide as its minutes, filling as it passes
    els.plan.innerHTML = plan.map((s, i) => {
      const f = finished || (run && i < run.idx) ? 1 : run && i === run.idx ? 1 - leftMs() / segMs(i) : 0;
      return `<li class="seg ${s.k}${run && i === run.idx ? " now" : ""}" style="flex-grow:${s.m}">` +
        `<span class="seg-bar"><i style="transform:scaleX(${f.toFixed(4)})"></i></span>` +
        `<span class="seg-label">${SHORT[s.k]} ${s.m}</span></li>`;
    }).join("");

    const segFrac = run ? 1 - leftMs() / segMs(run.idx) : finished ? 1 : 0;
    drawLotus(done / totalMs(), segFrac);

    if (editing) renderEditor();
  }

  function renderEditor() {
    const match = PRESETS.findIndex((p) => p.length === plan.length && p.every(([k, m], i) => plan[i].k === k && plan[i].m === m));
    els.presets.querySelectorAll(".preset").forEach((b, i) => b.setAttribute("aria-pressed", String(i === match)));
    els.edit.querySelector("ol").innerHTML = plan.map((s, i) => `
      <li class="row">
        <button type="button" class="kind ${s.k}" data-flip="${i}" aria-label="เปลี่ยนเป็น${KIND[s.k === "walk" ? "sit" : "walk"]}">${KIND[s.k]}</button>
        <span class="mins">
          <button type="button" class="step" data-dec="${i}" aria-label="ลด ${STEP} นาที" ${s.m <= MIN ? "disabled" : ""}>−</button>
          <b>${s.m}</b> นาที
          <button type="button" class="step" data-inc="${i}" aria-label="เพิ่ม ${STEP} นาที" ${s.m >= MAX ? "disabled" : ""}>+</button>
        </span>
        <button type="button" class="drop" data-drop="${i}" aria-label="ลบช่วงนี้" ${plan.length < 2 ? "hidden" : ""}><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
      </li>`).join("");
    els.edit.querySelector("[data-add]").hidden = plan.length >= MAX_SEGS;
    els.total.textContent = `รวม ${Math.round(totalMs() / MS)} นาที`;
  }

  // the plan is set in a dialog that greets every visit to the tab while no session runs
  const openSetup = () => { if (!run && !els.setup.open) { renderEditor(); els.setup.showModal(); } };
  els.setup.addEventListener("click", (e) => { if (e.target === els.setup) els.setup.close(); });   // backdrop
  new MutationObserver(() => { if (!view.hidden) openSetup(); }).observe(view, { attributes: true, attributeFilter: ["hidden"] });

  view.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t === els.go) { if (run && !run.paused) pause(); else start(); return; }
    if (t === els.stop) { stop(); return; }
    if (t === els.skip) { skip(); return; }
    if (t === els.open) { openSetup(); return; }
    if (t === els.begin) { els.setup.close(); start(); return; }
    if (t.dataset.close !== undefined) { els.setup.close(); return; }
    if (run) return;   // the plan is fixed while a session runs
    const d = t.dataset;
    if (d.preset !== undefined) plan = toPlan(PRESETS[+d.preset]);
    else if (d.flip !== undefined) plan[+d.flip].k = plan[+d.flip].k === "walk" ? "sit" : "walk";
    else if (d.dec !== undefined) plan[+d.dec].m = Math.max(MIN, plan[+d.dec].m - STEP);
    else if (d.inc !== undefined) plan[+d.inc].m = Math.min(MAX, plan[+d.inc].m + STEP);
    else if (d.drop !== undefined) plan.splice(+d.drop, 1);
    else if (d.add !== undefined) plan.push({ k: plan[plan.length - 1].k === "walk" ? "sit" : "walk", m: 20 });
    else return;
    finished = false;
    savePlan();
    render();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && run && !run.paused) { keepAwake(true); tick(); }
  });

  render();
  if (!view.hidden) openSetup();   // the page opened straight on this tab
})();
