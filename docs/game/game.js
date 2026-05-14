// The Star-Builders Mystery — vanilla JS, no build step.
//
// Each analyst maps 1:1 to an Open Pulse data layer:
//   STARGAZER     → surface popularity (stars, citations)
//   CARTOGRAPHER  → Neo4j community graph
//   ARCHIVIST     → SPARQL store / semantic metadata
//   CHRONICLER    → GrimoireLab / CHAOSS time-series
//   SCOUT         → the Open Pulse crawler

(function () {
  "use strict";

  // ----- Data ------------------------------------------------------------

  const ANALYSTS = [
    {
      id: "stargazer",
      codename: "STARGAZER",
      role: "Surface popularity",
      icon: "★",
      bias: "Sees what shines. Cannot tell you if it is real.",
      script: [
        { who: "STARGAZER", text: "Case file 00731. Yeah I've been watching eldritch-engine since Tuesday. 50,000 stars in 7 days." },
        { who: "STARGAZER", text: "Three trending boards picked it up. Two industry blogs cited it. The aggregate reputation system is amplifying it across the federation feed." },
        { who: "YOU", text: "Any red flags from where you sit?" },
        { who: "STARGAZER", text: "Star-to-contributor ratio: 6,250-to-1. Industry median is 18-to-1. That is anomalous in a way I cannot explain from my console alone." },
        { who: "STARGAZER", text: "I can tell you what is popular. I cannot tell you if it is real. Talk to the others." },
        { who: "SYSTEM", text: "Evidence logged: 'A'." },
      ],
      evidence: {
        id: "A",
        kind: "popularity",
        label: "50,000 stars; star-to-contributor ratio 6,250:1 (industry median is 18:1).",
      },
    },
    {
      id: "cartographer",
      codename: "CARTOGRAPHER",
      role: "Collaboration graph",
      icon: "◇",
      bias: "Sees who connects to whom. Cannot tell you what they did.",
      script: [
        { who: "CARTOGRAPHER", text: "I have a network read on the eldritch-engine contributor set. Eight visible accounts." },
        { who: "YOU", text: "How do they relate to each other?" },
        { who: "CARTOGRAPHER", text: "Six of the eight only ever committed to eldritch-engine. No history elsewhere. They appeared the week the repo went public." },
        { who: "CARTOGRAPHER", text: "The remaining two share a three-year history with a private organization, eldritch-collective. They co-contributed to an internal repo there." },
        { who: "CARTOGRAPHER", text: "I see who knows whom. I do not see what they did, when, or why. Pair me with the others." },
        { who: "SYSTEM", text: "Evidence logged: 'B'." },
      ],
      evidence: {
        id: "B",
        kind: "structure",
        label: "8 contributors; 6 have only eldritch-engine; 2 share a 3-year history inside org eldritch-collective.",
      },
    },
    {
      id: "archivist",
      codename: "ARCHIVIST",
      role: "Semantic metadata",
      icon: "❖",
      bias: "Knows what each thing is. Does not observe time.",
      script: [
        { who: "ARCHIVIST", text: "Cataloging eldritch-engine. Repo created seven days ago. Rust. MIT-licensed. Self-tagged as post-quantum cryptography." },
        { who: "ARCHIVIST", text: "The README cites ORCID 0000-0002-1234-9999. I cross-checked against the federation researcher registry." },
        { who: "YOU", text: "And?" },
        { who: "ARCHIVIST", text: "That ORCID is real. Issued in 2003. Belongs to Dr. Hadrien Voss at New Geneva Federal Institute. Specialization: post-quantum cryptography. Twenty years on the topic." },
        { who: "ARCHIVIST", text: "I catalog. I do not observe time. I cannot tell you what Dr. Voss did between 2003 and last Tuesday." },
        { who: "SYSTEM", text: "Evidence logged: 'C'." },
      ],
      evidence: {
        id: "C",
        kind: "provenance",
        label: "ORCID 0000-0002-1234-9999 — real researcher, Dr. H. Voss, post-quantum crypto since 2003.",
      },
    },
    {
      id: "chronicler",
      codename: "CHRONICLER",
      role: "Time-series activity",
      icon: "◫",
      bias: "Reads the rhythm. Does not read what is being played.",
      script: [
        { who: "CHRONICLER", text: "I've run the CHAOSS metrics on eldritch-engine. Eight contributors, all active in the last seven days. Healthy at a glance." },
        { who: "CHRONICLER", text: "But the pattern is off. Every contributor commits at exactly 09:00, 09:47, 10:34 UTC. Same intervals. Every weekday." },
        { who: "YOU", text: "Could that be coincidence?" },
        { who: "CHRONICLER", text: "No. Real humans drift. Timezones shift. Weekends dip. Coffee meetings push commits late. These commits have zero variance. It is a scheduler." },
        { who: "CHRONICLER", text: "I read rhythm. I do not read intent. Talk to the SCOUT — the threads have to lead somewhere." },
        { who: "SYSTEM", text: "Evidence logged: 'D'." },
      ],
      evidence: {
        id: "D",
        kind: "popularity",
        label: "Commits clustered at exact 47-minute intervals, weekdays only, zero variance — bot-pool signature.",
      },
    },
    {
      id: "scout",
      codename: "SCOUT",
      role: "Crawl & discovery",
      icon: "◎",
      bias: "Brings raw threads back. Does not interpret them.",
      script: [
        { who: "SCOUT", text: "Went looking under the public surface. The org eldritch-collective owns a private repo — engine-core. Federation crawl picked up its existence." },
        { who: "YOU", text: "Anything inside?" },
        { who: "SCOUT", text: "Private, so no contents. But the metadata: created 2009. Last commit, three days ago. Continuous activity for fifteen years." },
        { who: "SCOUT", text: "And — this is the thread to pull — the public eldritch-engine's git tree shares SHAs with the private engine-core, starting 2014. The public repo is a curated mirror of the private one, force-pushed as a flat squash last week." },
        { who: "SCOUT", text: "I find the threads. I do not pull them. That call is yours." },
        { who: "SYSTEM", text: "Evidence logged: 'E'." },
      ],
      evidence: {
        id: "E",
        kind: "provenance",
        label: "Private mirror eldritch-collective/engine-core created 2009; identical SHAs with public repo since 2014.",
      },
    },
  ];

  // Win condition: at least one popularity-signal AND at least one
  // provenance-signal must be in the submitted set. The strongest answer
  // (full marks) combines provenance × 2 + popularity × 1 — that is the
  // C+E+(A or D) combination.
  const POPULARITY_IDS = new Set(["A", "D"]);
  const PROVENANCE_IDS = new Set(["C", "E"]);

  const VERDICTS = {
    // Full marks — the canonical right answers.
    "A+C+E": {
      grade: "Verdict accepted · Full marks",
      gradeKind: "good",
      body:
        "You showed that <strong>real work</strong> exists behind eldritch-engine — a real researcher (ORCID since 2003) and a private mirror going back to 2009 — and that the popularity is <strong>manufactured</strong> (anomalous star-to-contributor ratio). The Bureau rules that the underlying code is genuine; the trending placement is the result of a stars campaign and is suppressed pending review.",
      takeaway:
        "This is what Open Pulse is for. Stars alone would have said 'success.' The semantic store (Archivist) plus the crawler (Scout) plus a popularity sanity-check (Stargazer) together produce a verdict no single layer could reach.",
    },
    "C+D+E": {
      grade: "Verdict accepted · Full marks",
      gradeKind: "good",
      body:
        "You showed that <strong>real work</strong> exists — a real researcher (ORCID since 2003) and a private mirror since 2009 — and that the public surface is <strong>automated</strong> (bot-clustered commits). The Bureau rules the code is genuine; the public-facing release is being driven by a scheduler, likely a stars-amplification pipeline.",
      takeaway:
        "The Chronicler caught the rhythm anomaly while the Archivist and Scout established the long private history. CHAOSS time-series plus the SPARQL store plus the crawler — exactly the combination Open Pulse makes possible.",
    },

    // Two-of-three — partial credit (one popularity + one provenance + a wildcard).
    "A+C+B": {
      grade: "Verdict accepted · Partial marks",
      gradeKind: "partial",
      body:
        "You have a real researcher (ORCID) and an anomalous popularity signal. You also have a contributor-network observation, but you did not pull the private-mirror thread that proves the work is OLD. A reviewer could still object: 'Maybe the researcher started the project last week, the stars are manufactured.'",
      takeaway:
        "Always pair the Archivist's identity evidence with the Scout's history evidence. Provenance is two-sided: who, and since when.",
    },
    "A+E+B": {
      grade: "Verdict accepted · Partial marks",
      gradeKind: "partial",
      body:
        "Solid provenance (private mirror since 2009) and a popularity anomaly. The contributor-network observation supports it. Missing: the actual person. Without the ORCID, the verdict is 'someone has been working on this in private for years' — true but anonymous.",
      takeaway:
        "Graph evidence is structural. The semantic store (Archivist) is what gives the structure a name.",
    },
    "C+D+B": {
      grade: "Verdict accepted · Partial marks",
      gradeKind: "partial",
      body:
        "Real researcher, bot-clustered commits, and a graph anomaly. Convincing — but you did not establish that the work is OLD. A skeptical reviewer could say the researcher created the project last week and ran the stars campaign themselves.",
      takeaway:
        "The Scout's private-mirror thread is the unique piece of evidence that proves history. No other analyst sees outside the public surface.",
    },
    "B+D+E": {
      grade: "Verdict accepted · Partial marks",
      gradeKind: "partial",
      body:
        "You caught the bot pattern and the private-mirror history, with a contributor-network observation in support. But you never asked who the researcher actually is. A clean Bureau filing names the principal.",
      takeaway:
        "Provenance has two halves. The Scout shows the work is real. The Archivist names who did it. Without both, the verdict is anonymous.",
    },

    // Anti-patterns — submissions that miss the lesson.
    "A+B+D": {
      grade: "Verdict rejected · Surface only",
      gradeKind: "poor",
      body:
        "You proved the public face is suspicious — anomalous stars, an unusual contributor graph, bot-patterned commits — but you have no evidence about the actual code, the actual people, or the project's history. The Bureau cannot rule on a project based only on what its surface looks like.",
      takeaway:
        "This is the trap GitHub stars and trending boards alone create. The data you collected is real and useful, but it is only the surface layer. You also needed the Archivist (Who is behind this?) and the Scout (Has it existed before?).",
    },
    "A+B+C": {
      grade: "Verdict rejected · Missing history",
      gradeKind: "poor",
      body:
        "You named a real researcher and flagged the popularity anomaly, plus you have a graph observation. But you have no evidence that the code itself has any history. The work could still be a fresh project by a real person riding a stars campaign — or a copy-paste of someone else's work attributed to that ORCID.",
      takeaway:
        "When the popularity story is suspicious, always pull the Scout's thread. Adjacent repos and force-push patterns are the only way to confirm provenance over time.",
    },
    "B+C+D": {
      grade: "Verdict rejected · Missing history",
      gradeKind: "poor",
      body:
        "You established a real researcher, a contributor anomaly, and a bot-commit pattern. But — same as above — nothing in your submission proves the project has any prior history. Without the Scout's discovery, every submission like this stays one-sided.",
      takeaway:
        "Five tools exist for a reason. Cross-layer evidence is the point.",
    },
    "C+D+B": {
      // duplicate handled above
    },

    // The fully-historical, no-popularity-check combination.
    "B+C+E": {
      grade: "Verdict rejected · Missed the trigger",
      gradeKind: "poor",
      body:
        "Strong provenance — a real researcher, a graph footprint, and the private mirror since 2009. But you submitted nothing that addresses why this investigation opened in the first place: the suspicious popularity. A Bureau filing must address the trigger.",
      takeaway:
        "Provenance is necessary but not sufficient. Always include at least one popularity-signal piece of evidence — that's what tells you whether the surface deserves the trust the trending boards give it.",
    },
  };

  // ----- State -----------------------------------------------------------

  const state = {
    talkedTo: new Set(),
    collected: [],          // ordered array of evidence ids
    selected: new Set(),    // chosen for submission
    currentAnalyst: null,
    dialogStep: 0,
    typing: false,
  };

  // ----- Rendering -------------------------------------------------------

  function renderAnalysts() {
    const list = document.getElementById("analysts-list");
    list.innerHTML = ANALYSTS.map((a) => `
      <li>
        <button class="analyst ${state.talkedTo.has(a.id) ? "talked-to" : ""}"
                type="button"
                data-id="${a.id}"
                aria-label="Open transcript with ${a.codename}">
          <div class="analyst-head">
            <span class="analyst-icon" aria-hidden="true">${a.icon}</span>
            <div>
              <p class="analyst-codename">${a.codename}</p>
              <p class="analyst-role">${a.role}</p>
            </div>
          </div>
          <p class="analyst-bias">${a.bias}</p>
        </button>
      </li>
    `).join("");
  }

  function renderDossier() {
    const list = document.getElementById("dossier-list");
    const hint = document.getElementById("dossier-hint");
    if (state.collected.length === 0) {
      hint.classList.remove("is-hidden");
      list.innerHTML = "";
    } else {
      hint.classList.add("is-hidden");
      list.innerHTML = state.collected.map((id) => {
        const e = findEvidence(id);
        return `<li class="dossier-item"><span class="ev-tag">[${e.id}]</span>${escape(e.label)}</li>`;
      }).join("");
    }
    const btn = document.getElementById("submit-btn");
    const helper = document.getElementById("submit-help");
    if (state.talkedTo.size >= 3) {
      btn.disabled = false;
      helper.textContent = "Ready when you are.";
    } else {
      btn.disabled = true;
      const remaining = 3 - state.talkedTo.size;
      helper.textContent = `Talk to ${remaining} more analyst${remaining === 1 ? "" : "s"} to enable submission.`;
    }
  }

  function findEvidence(id) {
    for (const a of ANALYSTS) {
      if (a.evidence.id === id) return a.evidence;
    }
    return null;
  }

  function escape(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ----- Dialog ----------------------------------------------------------

  function openDialog(analystId) {
    const a = ANALYSTS.find((x) => x.id === analystId);
    if (!a) return;
    state.currentAnalyst = a;
    state.dialogStep = 0;
    document.getElementById("dialog-icon").textContent = a.icon;
    document.getElementById("dialog-title").textContent = a.codename;
    document.getElementById("dialog-role").textContent = a.role;
    document.getElementById("dialog-text").innerHTML = "";
    document.getElementById("dialog-modal").hidden = false;
    document.getElementById("dialog-advance").textContent = "Begin transcript ▸";
    state.typing = false;
  }

  function closeDialog() {
    document.getElementById("dialog-modal").hidden = true;
    if (state.currentAnalyst) {
      state.talkedTo.add(state.currentAnalyst.id);
      const ev = state.currentAnalyst.evidence;
      if (!state.collected.includes(ev.id)) state.collected.push(ev.id);
    }
    state.currentAnalyst = null;
    renderAnalysts();
    renderDossier();
  }

  function advanceDialog() {
    if (!state.currentAnalyst) return;
    if (state.typing) {
      // skip typing to end
      finishTyping();
      return;
    }
    const script = state.currentAnalyst.script;
    if (state.dialogStep >= script.length) {
      closeDialog();
      return;
    }
    const line = script[state.dialogStep++];
    typeLine(line);
    // update button label based on remaining steps
    const btn = document.getElementById("dialog-advance");
    btn.textContent = state.dialogStep >= script.length ? "Log evidence & exit ✓" : "Next ▸";
  }

  let typingTimer = null;
  let typingTarget = null;
  let typingFull = null;

  function typeLine(line) {
    const text = document.getElementById("dialog-text");
    const speakerKind =
      line.who === "YOU" ? "you" :
      line.who === "SYSTEM" ? "system" : "";

    // Build the speaker label + a typed span via DOM, not innerHTML, so
    // appending characters does not collide with HTML entity escaping.
    if (text.childNodes.length > 0) {
      text.appendChild(document.createElement("br"));
      text.appendChild(document.createElement("br"));
    }
    const speakerEl = document.createElement("span");
    speakerEl.className = "speaker" + (speakerKind ? " " + speakerKind : "");
    speakerEl.textContent = line.who + ":";
    text.appendChild(speakerEl);
    text.appendChild(document.createTextNode(" "));

    const typedEl = document.createElement("span");
    typedEl.className = "typed";
    text.appendChild(typedEl);

    typingFull = line.text;
    typingTarget = typedEl;
    state.typing = true;
    let i = 0;
    function step() {
      if (i >= typingFull.length) {
        state.typing = false;
        typingTimer = null;
        return;
      }
      typingTarget.textContent += typingFull.charAt(i++);
      text.scrollTop = text.scrollHeight;
      typingTimer = setTimeout(step, 7);
    }
    step();
  }

  function finishTyping() {
    if (typingTimer) { clearTimeout(typingTimer); typingTimer = null; }
    if (typingTarget && typingFull != null) {
      typingTarget.textContent = typingFull;
    }
    state.typing = false;
  }

  // ----- Submission flow (drag-and-drop + tap fallback) ------------------

  // Slot state: 3 positions, each holds an evidence id or null.
  let slotState = [null, null, null];
  // For tap-to-select on touch devices: which deck card is currently primed.
  let tapPrimed = null;

  function shortLabel(text) {
    // Coverage cards show a 3-line snippet; the source labels are short
    // enough that no truncation is needed but we strip the leading metric
    // numbers for breathing room.
    return text;
  }

  function openSubmission() {
    slotState = [null, null, null];
    tapPrimed = null;
    renderSlots();
    renderDeck();
    updateCoverage();
    updateSubmitFooter();
    // populate slot numbers (CSS attr)
    const labels = ["EVIDENCE 01", "EVIDENCE 02", "EVIDENCE 03"];
    document.querySelectorAll("#slots-row .slot").forEach((el, i) => {
      el.dataset.num = labels[i];
    });
    document.getElementById("submit-modal").hidden = false;
  }

  function closeSubmission() {
    document.getElementById("submit-modal").hidden = true;
  }

  function renderSlots() {
    const slots = document.querySelectorAll("#slots-row .slot");
    slots.forEach((el, i) => {
      const id = slotState[i];
      el.classList.toggle("empty", !id);
      el.classList.toggle("filled", !!id);
      el.classList.remove("drag-over");
      el.innerHTML = "";
      if (id) {
        const ev = findEvidence(id);
        el.innerHTML = `
          <div class="placed-card" draggable="true" data-id="${ev.id}" data-from="slot:${i}">
            <span class="ev-tag">[${ev.id}]</span>
            <span class="ev-snippet">${escape(shortLabel(ev.label))}</span>
          </div>`;
      }
    });
    // re-wire drag handlers on the new card nodes
    wireSlotCardDrag();
  }

  function renderDeck() {
    const deck = document.getElementById("deck-list");
    deck.innerHTML = state.collected.map((id) => {
      const ev = findEvidence(id);
      const placed = slotState.includes(id);
      const primed = tapPrimed === id;
      const classes = ["ev-card"];
      if (placed) classes.push("placed");
      if (primed) classes.push("tap-selected");
      return `
        <li>
          <div class="${classes.join(" ")}"
               draggable="${placed ? "false" : "true"}"
               data-id="${ev.id}"
               data-from="deck"
               role="button"
               tabindex="0">
            <span class="ev-tag">[${ev.id}]</span>
            <span class="ev-snippet">${escape(shortLabel(ev.label))}</span>
          </div>
        </li>`;
    }).join("");
  }

  function updateCoverage() {
    const counts = { popularity: 0, provenance: 0, structure: 0 };
    slotState.forEach((id) => {
      if (!id) return;
      const ev = findEvidence(id);
      if (ev && counts[ev.kind] != null) counts[ev.kind]++;
    });
    document.querySelectorAll(".coverage .cov-col").forEach((col) => {
      const kind = col.dataset.kind;
      col.dataset.pips = String(counts[kind] || 0);
      col.classList.toggle("lit", counts[kind] > 0);
    });
  }

  function updateSubmitFooter() {
    const filled = slotState.filter(Boolean).length;
    document.getElementById("picker-count").textContent = `${filled} / 3 placed`;
    document.getElementById("verdict-btn").disabled = filled !== 3;
  }

  function placeInSlot(slotIdx, evidenceId) {
    if (slotIdx < 0 || slotIdx > 2) return;
    if (!state.collected.includes(evidenceId)) return;
    // Remove evidence from whichever slot already holds it (move semantics)
    for (let i = 0; i < 3; i++) {
      if (slotState[i] === evidenceId) slotState[i] = null;
    }
    slotState[slotIdx] = evidenceId;
    tapPrimed = null;
    renderSlots();
    renderDeck();
    updateCoverage();
    updateSubmitFooter();
  }

  function removeFromSlot(slotIdx) {
    if (slotIdx < 0 || slotIdx > 2) return;
    slotState[slotIdx] = null;
    renderSlots();
    renderDeck();
    updateCoverage();
    updateSubmitFooter();
  }

  function firstEmptySlot() {
    return slotState.findIndex((s) => s === null);
  }

  // --- Drag-and-drop wiring ----------------------------------------------

  let draggingFrom = null;     // "deck" | "slot:N"
  let draggingId = null;

  function onDeckDragStart(e) {
    const card = e.target.closest(".ev-card");
    if (!card || card.classList.contains("placed")) {
      e.preventDefault();
      return;
    }
    draggingId = card.dataset.id;
    draggingFrom = "deck";
    card.classList.add("dragging");
    if (e.dataTransfer) {
      e.dataTransfer.setData("text/plain", draggingId);
      e.dataTransfer.effectAllowed = "move";
    }
  }

  function onDragEnd(e) {
    const card = e.target.closest(".ev-card, .placed-card");
    if (card) card.classList.remove("dragging");
    document.querySelectorAll("#slots-row .slot").forEach((s) => s.classList.remove("drag-over"));
    draggingFrom = null;
    draggingId = null;
  }

  function wireSlotCardDrag() {
    document.querySelectorAll("#slots-row .placed-card").forEach((card) => {
      card.addEventListener("dragstart", (e) => {
        draggingId = card.dataset.id;
        draggingFrom = card.dataset.from;
        card.classList.add("dragging");
        if (e.dataTransfer) {
          e.dataTransfer.setData("text/plain", draggingId);
          e.dataTransfer.effectAllowed = "move";
        }
      });
      card.addEventListener("dragend", onDragEnd);
    });
  }

  function onSlotDragOver(e) {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    const slot = e.target.closest(".slot");
    if (slot) slot.classList.add("drag-over");
  }

  function onSlotDragLeave(e) {
    const slot = e.target.closest(".slot");
    if (slot) slot.classList.remove("drag-over");
  }

  function onSlotDrop(e) {
    e.preventDefault();
    const slot = e.target.closest(".slot");
    if (!slot) return;
    slot.classList.remove("drag-over");
    const idx = Number(slot.dataset.slot);
    if (Number.isFinite(idx) && draggingId) {
      placeInSlot(idx, draggingId);
    }
    draggingId = null;
    draggingFrom = null;
  }

  // Drop a slot card OUT (back to deck) by dragging it outside the slot area
  function onDeckDrop(e) {
    e.preventDefault();
    if (draggingFrom && draggingFrom.startsWith("slot:")) {
      const idx = Number(draggingFrom.split(":")[1]);
      removeFromSlot(idx);
    }
    draggingId = null;
    draggingFrom = null;
  }
  function onDeckDragOver(e) { e.preventDefault(); }

  // --- Click-to-place fallback (touch + accessibility) ------------------

  function onDeckClick(e) {
    const card = e.target.closest(".ev-card");
    if (!card) return;
    if (card.classList.contains("placed")) return;
    const id = card.dataset.id;
    if (tapPrimed === id) {
      // toggle off
      tapPrimed = null;
    } else {
      // if a slot is empty, jump straight in
      const empty = firstEmptySlot();
      if (empty >= 0) {
        placeInSlot(empty, id);
        return;
      }
      tapPrimed = id;
    }
    renderDeck();
  }

  function onSlotClick(e) {
    const slot = e.target.closest(".slot");
    if (!slot) return;
    const idx = Number(slot.dataset.slot);
    if (!Number.isFinite(idx)) return;
    if (tapPrimed) {
      placeInSlot(idx, tapPrimed);
      return;
    }
    if (slotState[idx]) {
      // tap a filled slot to remove
      removeFromSlot(idx);
    }
  }

  function deliverVerdict() {
    const ids = slotState.filter(Boolean).sort();
    const key = ids.join("+");
    const v = VERDICTS[key] || synthesisedVerdict(ids);
    document.getElementById("verdict-grade").className =
      "verdict-grade " + v.gradeKind;
    document.getElementById("verdict-grade").textContent = v.grade;
    document.getElementById("verdict-body").innerHTML = v.body;
    document.getElementById("verdict-takeaway").textContent = v.takeaway;
    closeSubmission();
    document.getElementById("verdict-modal").hidden = false;
  }

  // Synthesise a verdict for unscripted combinations — must always
  // produce a useful teaching outcome.
  function synthesisedVerdict(ids) {
    const set = new Set(ids);
    const pops = ids.filter((i) => POPULARITY_IDS.has(i)).length;
    const provs = ids.filter((i) => PROVENANCE_IDS.has(i)).length;

    if (pops >= 1 && provs >= 2) {
      return {
        grade: "Verdict accepted · Full marks",
        gradeKind: "good",
        body: "You combined a popularity-anomaly signal with two pieces of provenance evidence (identity + history). That's the strongest case the Bureau can ask for.",
        takeaway: "Two-sided provenance (who + since when) plus a sanity check on the surface story is the canonical Open Pulse combination.",
      };
    }
    if (pops >= 1 && provs >= 1) {
      return {
        grade: "Verdict accepted · Partial marks",
        gradeKind: "partial",
        body: "You have one popularity signal and one provenance signal. The verdict holds but a reviewer could pick at the unaddressed half of provenance.",
        takeaway: "Strong cases combine identity (Archivist) AND history (Scout). They are different evidence shapes.",
      };
    }
    if (pops === 0 && provs >= 2) {
      return {
        grade: "Verdict rejected · Missed the trigger",
        gradeKind: "poor",
        body: "You have provenance but you did not address the popularity anomaly that opened the case. A Bureau filing must speak to what triggered the investigation.",
        takeaway: "Always include at least one popularity-signal piece of evidence.",
      };
    }
    return {
      grade: "Verdict rejected · Surface only",
      gradeKind: "poor",
      body: "Your submission contains no provenance evidence at all. You cannot rule on a project without establishing who is behind it and whether the work has any history.",
      takeaway: "Surface signals tell you that something looks suspicious. Provenance tells you what is actually there.",
    };
  }

  function closeVerdict() {
    document.getElementById("verdict-modal").hidden = true;
  }

  function resetForRetry() {
    state.selected = new Set();
    closeVerdict();
    openSubmission();
  }

  // ----- Wiring ----------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    renderAnalysts();
    renderDossier();

    document.getElementById("analysts-list").addEventListener("click", (e) => {
      const btn = e.target.closest("button.analyst");
      if (!btn) return;
      openDialog(btn.dataset.id);
    });

    document.getElementById("dialog-advance").addEventListener("click", advanceDialog);
    document.getElementById("dialog-close").addEventListener("click", () => {
      if (typingTimer) clearTimeout(typingTimer);
      // closing the modal early still counts as debriefed if any line was shown
      if (state.dialogStep > 0) closeDialog();
      else {
        document.getElementById("dialog-modal").hidden = true;
        state.currentAnalyst = null;
      }
    });

    document.getElementById("submit-btn").addEventListener("click", openSubmission);
    document.getElementById("submit-close").addEventListener("click", closeSubmission);
    document.getElementById("verdict-btn").addEventListener("click", deliverVerdict);

    // Drag-and-drop + tap fallback for the submission picker.
    const deckList = document.getElementById("deck-list");
    deckList.addEventListener("dragstart", onDeckDragStart);
    deckList.addEventListener("dragend", onDragEnd);
    deckList.addEventListener("dragover", onDeckDragOver);
    deckList.addEventListener("drop", onDeckDrop);
    deckList.addEventListener("click", onDeckClick);

    const slotsRow = document.getElementById("slots-row");
    slotsRow.addEventListener("dragover", onSlotDragOver);
    slotsRow.addEventListener("dragleave", onSlotDragLeave);
    slotsRow.addEventListener("drop", onSlotDrop);
    slotsRow.addEventListener("dragend", onDragEnd);
    slotsRow.addEventListener("click", onSlotClick);

    document.getElementById("verdict-close").addEventListener("click", closeVerdict);
    document.getElementById("verdict-retry").addEventListener("click", resetForRetry);

    // Esc closes whichever modal is open
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      ["verdict-modal", "submit-modal", "dialog-modal"].forEach((id) => {
        const el = document.getElementById(id);
        if (!el.hidden) el.hidden = true;
      });
    });
  });
})();
