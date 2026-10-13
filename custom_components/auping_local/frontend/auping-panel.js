// Bedienfeld "Bett" für die Integration auping_local.
// Reines Web-Component ohne externe Bibliotheken; Farben kommen aus dem HA-Theme.

const DOMAIN = "auping_local";
const TABS = [
  ["remote", "Fernbedienung"],
  ["calib", "Kalibrieren"],
  ["settings", "Einstellungen"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const pct = (v) => `${Math.round((v ?? 0) * 100)} %`;
const sec = (ms) => (ms == null ? "–" : (ms / 1000).toFixed(2).replace(".", ","));

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(`auping.${key}`);
    localStorage.setItem(`auping.${key}`, value);
  } catch (e) {
    return null;
  }
  return null;
}

const ICON = {
  up: '<svg viewBox="0 0 24 24"><path d="M7.4 15.4 12 10.8l4.6 4.6L18 14l-6-6-6 6z"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M7.4 8.6 12 13.2l4.6-4.6L18 10l-6 6-6-6z"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="M12 17.3 18.2 21l-1.6-7L22 9.2l-7.2-.6L12 2 9.2 8.6 2 9.2 7.4 14l-1.6 7z"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.8 9.94l-3.75-3.75zM20.7 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75z"/></svg>',
  save: '<svg viewBox="0 0 24 24"><path d="M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7zm-5 16a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm3-10H5V5h10z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z"/></svg>',
  left: '<svg viewBox="0 0 24 24"><path d="M15.4 7.4 14 6l-6 6 6 6 1.4-1.4L10.8 12z"/></svg>',
  right: '<svg viewBox="0 0 24 24"><path d="M8.6 16.6 10 18l6-6-6-6-1.4 1.4 4.6 4.6z"/></svg>',
};

class AupingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = store("tab") || "remote";
    this._side = store("side") || "both";
    this._calibMotor = null;
    this._size = "big";
    this._data = null;
    this._entries = [];
    this._entryId = null;
    this._structure = null;
    this._unsub = null;
    this._toast = null;
    this._favEdit = false;
    this._lastLimit = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (!this._started) {
      this._started = true;
      this._init();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  set panel(_p) {}

  disconnectedCallback() {
    this._unsubscribe();
  }

  connectedCallback() {
    if (this._started && !this._unsub && this._entryId) this._subscribe();
  }

  async _init() {
    this._renderShell();
    try {
      this._entries = await this._hass.callWS({ type: `${DOMAIN}/entries` });
    } catch (e) {
      this._entries = [];
    }
    if (!this._entries.length) {
      this._content.innerHTML = `<div class="card empty">Kein Auping-Bett eingerichtet. Unter <b>Einstellungen → Geräte & Dienste</b> die Integration „Auping Bett (lokal)“ hinzufügen.</div>`;
      return;
    }
    const saved = store("entry");
    this._entryId = this._entries.some((e) => e.entry_id === saved) ? saved : this._entries[0].entry_id;
    this._subscribe();
  }

  async _subscribe() {
    this._unsubscribe();
    try {
      this._unsub = await this._hass.connection.subscribeMessage((msg) => this._onData(msg), {
        type: `${DOMAIN}/subscribe`,
        entry_id: this._entryId,
      });
    } catch (e) {
      this._content.innerHTML = `<div class="card empty">Verbindung zur Integration fehlgeschlagen: ${esc(e.message || e)}</div>`;
    }
  }

  _unsubscribe() {
    if (this._unsub) {
      const u = this._unsub;
      this._unsub = null;
      Promise.resolve(u).then((fn) => fn && fn()).catch(() => {});
    }
  }

  async _cmd(action, data = {}) {
    try {
      return await this._hass.callWS({ type: `${DOMAIN}/command`, entry_id: this._entryId, action, data });
    } catch (e) {
      this._showToast(`Fehler: ${e.message || e.code || e}`);
      return null;
    }
  }

  _showToast(text) {
    const t = this.shadowRoot.querySelector(".toast");
    if (!t) return;
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(this._toast);
    this._toast = setTimeout(() => t.classList.remove("show"), 3500);
  }

  // ------------------------------------------------------------------ Grundgerüst

  _renderShell() {
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <div class="toolbar">
        <span class="menu"></span>
        <div class="title">Bett</div>
        <div class="status" id="status"></div>
      </div>
      <nav class="tabs">${TABS.map(
        ([k, label]) => `<button class="tab" data-tab="${k}">${label}</button>`
      ).join("")}</nav>
      <main id="content"></main>
      <div class="toast" role="status"></div>`;
    this._content = this.shadowRoot.getElementById("content");
    this._status = this.shadowRoot.getElementById("status");
    try {
      this._menu = document.createElement("ha-menu-button");
      this._menu.hass = this._hass;
      this._menu.narrow = this._narrow;
      this.shadowRoot.querySelector(".menu").appendChild(this._menu);
    } catch (e) {
      /* außerhalb von HA */
    }
    this.shadowRoot.querySelectorAll(".tab").forEach((b) =>
      b.addEventListener("click", () => {
        this._tab = b.dataset.tab;
        store("tab", this._tab);
        this._structure = null;
        this._render();
      })
    );
    this._markTab();
  }

  _markTab() {
    this.shadowRoot.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === this._tab));
  }

  _onData(data) {
    this._data = data;
    if (!this._calibMotor || !data.motors.includes(this._calibMotor)) {
      this._calibMotor = data.motors.includes("back") ? "back" : data.motors[0];
    }
    if (this._side !== "both" && !data.sides.includes(this._side)) this._side = "both";
    this._render();
  }

  _structureKey() {
    const d = this._data;
    const m = d.measure;
    const o = d.offset;
    return JSON.stringify([
      this._tab,
      this._side,
      this._calibMotor,
      this._size,
      d.sides,
      d.motors,
      d.names,
      d.mode,
      m && [m.motor, m.direction, m.phase, Object.keys(m.marks)],
      o && [o.motor, o.direction, o.phase, o.rounds],
      this._tab === "settings" ? [d.box, d.version, d.host, d.mattress, d.max_diff] : null,
      this._tab === "remote" ? [d.favorites.map((f) => [f.id, f.name]), this._favEdit, d.mattress, d.max_diff] : null,
    ]);
  }

  _render() {
    this._markTab();
    if (!this._data) return;
    const d = this._data;
    this._status.innerHTML = d.available && d.box_online
      ? `<span class="dot ok"></span>verbunden`
      : `<span class="dot bad"></span>${d.box_online ? "Befehl fehlgeschlagen" : "Box nicht erreichbar"}`;
    const key = this._structureKey();
    if (key !== this._structure) {
      this._structure = key;
      const html = { remote: () => this._remoteHtml(), calib: () => this._calibHtml(), settings: () => this._settingsHtml() }[
        this._tab
      ]();
      this._content.innerHTML = html;
      this._bind();
    }
    this._update();
  }

  _sideName(s) {
    return s === "both" ? "Beide" : this._data.names[s] || s;
  }

  _sideOrder() {
    return this._data.sides.length === 2 ? ["a", "both", "b"] : ["a"];
  }

  // ------------------------------------------------------------------ Fernbedienung

  _remoteHtml() {
    const d = this._data;
    const sides = this._sideOrder();
    const seg =
      d.sides.length === 2
        ? `<div class="segmented" role="tablist">${sides
            .map(
              (s) =>
                `<button class="seg ${s === this._side ? "active" : ""}" data-side="${s}">${esc(this._sideName(s))}${
                  s === "both" ? '<small>synchron</small>' : ""
                }</button>`
            )
            .join("")}</div>`
        : "";
    const motors = d.motors
      .map(
        (m) => `
      <section class="card motor">
        <div class="motor-head">
          <h2>${esc(d.motor_names[m])}</h2>
          <span class="moving" data-moving="${m}"></span>
        </div>
        <div class="motor-body">
          <div class="bars">${d.sides
            .map(
              (s) => `<div class="bar-row ${this._side !== "both" && this._side !== s ? "dim" : ""}">
                <span class="bar-label">${esc(d.names[s])}</span>
                <div class="bar"><div class="fill" data-fill="${s}:${m}"></div></div>
                <span class="bar-val" data-val="${s}:${m}"></span></div>`
            )
            .join("")}
          </div>
          <div class="hold">
            <button class="holdbtn" data-hold="up" data-motor="${m}" aria-label="${esc(d.motor_names[m])} hoch">${ICON.up}</button>
            <button class="holdbtn" data-hold="down" data-motor="${m}" aria-label="${esc(d.motor_names[m])} runter">${ICON.down}</button>
          </div>
        </div>
      </section>`
      )
      .join("");
    const mattressHint =
      d.sides.length === 2 && d.mattress === "single"
        ? ` Eine Matratze: Die Seiten dürfen höchstens ${Math.round(d.max_diff * 100)} % auseinander.`
        : "";
    return `
      <div class="notice warn" id="unknown" hidden>Position noch unbekannt – einmal <b>Referenzfahrt</b> machen, dann stimmen Prozentwerte und Sync.</div>
      ${seg}
      <div class="hint">Taste gedrückt halten zum Fahren. ${
        this._side === "both" ? "Beide Seiten fahren mit Ausgleich im Gleichschritt." : `Nur ${esc(this._sideName(this._side))}.`
      }${mattressHint}</div>
      <div class="grid">${motors}</div>
      <section class="card actions">
        <button class="btn danger big" data-act="stop_all">Stopp</button>
        <button class="btn" data-act="flat">Alles flach</button>
        <button class="btn" data-act="home">Referenzfahrt</button>
      </section>
      ${this._favoritesHtml()}`;
  }

  _favoritesHtml() {
    const d = this._data;
    const favs = d.favorites;
    let body;
    if (!favs.length) {
      body = `<p class="small">Noch keine Favoriten. Bring das Bett in die gewünschte Lage und tippe auf <b>Speichern</b>.</p>`;
    } else if (this._favEdit) {
      body = `<div class="fav-edit">${favs
        .map(
          (f, i) => `<div class="fav-line">
            <span class="fav-name">${ICON.star}${esc(f.name)}</span>
            <button class="icon-btn" data-favmove="${f.id}:-1" ${i === 0 ? "disabled" : ""} title="Nach vorne" aria-label="Nach vorne">${ICON.left}</button>
            <button class="icon-btn" data-favmove="${f.id}:1" ${i === favs.length - 1 ? "disabled" : ""} title="Nach hinten" aria-label="Nach hinten">${ICON.right}</button>
            <button class="icon-btn" data-favrename="${f.id}" title="Umbenennen" aria-label="Umbenennen">${ICON.edit}</button>
            <button class="icon-btn" data-favover="${f.id}" title="Mit aktueller Lage überschreiben" aria-label="Überschreiben">${ICON.save}</button>
            <button class="icon-btn danger" data-favdel="${f.id}" title="Löschen" aria-label="Löschen">${ICON.trash}</button>
          </div>`
        )
        .join("")}</div>`;
    } else {
      body = `<div class="fav-row">${favs
        .map((f) => `<button class="btn fav-go" data-fav="${f.id}">${ICON.star}<span>${esc(f.name)}</span></button>`)
        .join("")}</div>`;
    }
    const sideNote =
      this._side !== "both" && favs.length && !this._favEdit
        ? `<p class="small">Fährt nur ${esc(this._sideName(this._side))} in die gespeicherte Lage.</p>`
        : "";
    return `<section class="card favs">
        <div class="card-head"><h2>Favoriten</h2>
          <div class="head-actions">
            ${favs.length ? `<button class="link" data-favedit>${this._favEdit ? "Fertig" : "Bearbeiten"}</button>` : ""}
            <button class="btn small-btn" data-favnew>${ICON.plus}Speichern</button>
          </div>
        </div>
        ${body}${sideNote}
      </section>`;
  }

  // ------------------------------------------------------------------ Kalibrieren

  _calibHtml() {
    const d = this._data;
    const m = this._calibMotor;
    const chips = d.motors
      .map((x) => `<button class="chip ${x === m ? "active" : ""}" data-cmotor="${x}">${esc(d.motor_names[x])}</button>`)
      .join("");
    const two = d.sides.length === 2;
    let session = "";
    if (d.offset) session = this._offsetHtml(d.offset);
    else if (d.measure) session = this._measureHtml(d.measure);
    const busy = !!(d.offset || d.measure);
    return `
      <section class="card">
        <h2>So funktioniert der Ausgleich</h2>
        <p>Beide Motoren sind gleich schnell, aber eine Seite kommt später in Bewegung (Totweg). Die Integration startet deshalb die
        früher anlaufende Seite um genau diesen <b>Startversatz</b> später – beide Lattenroste fahren dann im Gleichschritt.
        Den Versatz findest du hier ohne Stoppuhr: Testfahrt ab der Endlage, hinschauen, sagen welche Seite höher steht.</p>
        <div class="chips">${chips}</div>
      </section>
      ${session}
      ${
        two
          ? `<section class="card ${busy ? "disabled" : ""}">
        <h2>1 · Startversatz abgleichen</h2>
        <p>Je Richtung etwa 4–8 Runden à 15–30 Sekunden. Erst hoch, dann runter.</p>
        <div class="row">
          <button class="btn primary" data-offset="up" ${busy ? "disabled" : ""}>Hoch abgleichen</button>
          <button class="btn primary" data-offset="down" ${busy ? "disabled" : ""}>Runter abgleichen</button>
        </div>
      </section>`
          : ""
      }
      <section class="card ${busy ? "disabled" : ""}">
        <h2>${two ? "2 · " : ""}Laufzeit messen <span class="opt">für die Prozentanzeige</span></h2>
        <p>Das Bett fährt an die Gegen-Endlage und dann einmal durch. Tippe je Seite, sobald der Motor am Endschalter stehen bleibt.</p>
        <div class="row">
          <button class="btn" data-measure="up" ${busy ? "disabled" : ""}>Hochfahrt messen</button>
          <button class="btn" data-measure="down" ${busy ? "disabled" : ""}>Runterfahrt messen</button>
        </div>
      </section>
      <section class="card">
        <h2>Werte ${esc(d.motor_names[m])}</h2>
        <table class="values">
          <thead><tr><th></th>${d.sides.map((s) => `<th>${esc(d.names[s])}</th>`).join("")}</tr></thead>
          <tbody>${[
            ["lag_up", "Anlauf hoch"],
            ["lag_down", "Anlauf runter"],
            ["up", "Laufzeit hoch"],
            ["down", "Laufzeit runter"],
          ]
            .map(
              ([k, label]) =>
                `<tr><td>${label}</td>${d.sides
                  .map(
                    (s) =>
                      `<td><input type="number" step="0.01" min="0" inputmode="decimal" data-cal="${s}:${m}:${k}"> s</td>`
                  )
                  .join("")}</tr>`
            )
            .join("")}</tbody>
        </table>
        <p class="small">Werte lassen sich auch direkt ändern. Für den Sync zählt beim Anlauf nur die Differenz zwischen den Seiten.</p>
      </section>`;
  }

  _offsetHtml(o) {
    const d = this._data;
    const dirTxt = o.direction > 0 ? "hoch" : "runter";
    const endTxt = o.direction > 0 ? "unten" : "oben";
    let body;
    if (o.phase === "feedback") {
      const first = o.rounds <= 1 && o.step == null;
      body = `
        <p class="question">Welche Seite steht jetzt <b>höher</b>?</p>
        ${
          first
            ? `<div class="row size"><span>Unterschied:</span>
              <button class="chip ${this._size === "big" ? "active" : ""}" data-size="big">deutlich</button>
              <button class="chip ${this._size === "small" ? "active" : ""}" data-size="small">nur etwas</button></div>`
            : ""
        }
        <div class="row three">
          <button class="btn primary" data-answer="a_higher">${esc(d.names.a)} höher</button>
          <button class="btn ok" data-answer="equal">Gleich hoch</button>
          <button class="btn primary" data-answer="b_higher">${esc(d.names.b)} höher</button>
        </div>`;
    } else {
      body = `<p class="progress"><span class="spinner"></span>${
        o.phase === "relevel" ? `Beide Seiten fahren an die Endlage ${endTxt} …` : "Testfahrt läuft – gleich hinschauen …"
      }</p>`;
    }
    return `<section class="card session">
      <h2>Startversatz ${esc(d.motor_names[o.motor])} ${dirTxt} · Runde ${o.rounds + (o.phase === "feedback" ? 0 : 1)}</h2>
      ${body}
      <div class="lagline" id="lagline"></div>
      <div class="row"><button class="link" data-cancel>Abbrechen</button></div>
    </section>`;
  }

  _measureHtml(m) {
    const d = this._data;
    const dirTxt = m.direction > 0 ? "oben" : "unten";
    if (m.phase === "relevel") {
      return `<section class="card session"><h2>Laufzeit ${esc(d.motor_names[m.motor])}</h2>
        <p class="progress"><span class="spinner"></span>Fahre an die Gegen-Endlage …</p>
        <div class="row"><button class="link" data-cancel>Abbrechen</button></div></section>`;
    }
    const marks = d.sides
      .map(
        (s) => `<div class="stopwatch">
          <div class="sw-name">${esc(d.names[s])}</div>
          <div class="sw-time" data-sw="${s}">0,00</div>
          <button class="btn primary" data-mark="${s}" ${m.marks[s] != null ? "disabled" : ""}>${dirTxt} angekommen</button>
        </div>`
      )
      .join("");
    const done = d.sides.every((s) => m.marks[s] != null);
    return `<section class="card session"><h2>Laufzeit ${esc(d.motor_names[m.motor])} messen</h2>
      <div class="sw-row">${marks}</div>
      ${d.sides.length === 2 ? `<div class="row"><button class="btn" data-mark="both" ${done ? "disabled" : ""}>Beide gleichzeitig angekommen</button></div>` : ""}
      <div class="row">
        <button class="btn ok" data-finish="1" ${done ? "" : "disabled"}>Übernehmen</button>
        <button class="link" data-finish="0">Verwerfen</button>
      </div></section>`;
  }

  // ------------------------------------------------------------------ Einstellungen

  _settingsHtml() {
    const d = this._data;
    const b = d.box || {};
    const v = d.version || {};
    const two = d.sides.length === 2;
    return `
      ${
        two
          ? `<section class="card">
        <h2>Ansteuerung beider Seiten</h2>
        <p>Ob die Box beide Steuereinheiten wirklich gleichzeitig bedienen kann, ist aus der App nicht ersichtlich. Test: Beide Seiten
        bekommen kurz nacheinander je einen eigenen Befehl (2,5 s hoch). Laufen <b>beide</b> los, passt „Gleichzeitig“.
        Bleibt eine Seite stehen oder stoppt die erste, wenn die zweite startet, „Nacheinander“ wählen.</p>
        <div class="row"><button class="btn" data-test>Test starten (${esc(d.motor_names[this._calibMotor])})</button></div>
        <div class="row radio">
          <label><input type="radio" name="mode" value="parallel" ${d.mode === "parallel" ? "checked" : ""}> Gleichzeitig</label>
          <label><input type="radio" name="mode" value="sequential" ${d.mode === "sequential" ? "checked" : ""}> Nacheinander</label>
        </div>
      </section>`
          : ""
      }
      ${
        two
          ? `<section class="card">
        <h2>Matratze</h2>
        <div class="row radio col">
          <label><input type="radio" name="mattress" value="single" ${d.mattress === "single" ? "checked" : ""}>
            Eine durchgehende Matratze – eine Seite allein darf nur begrenzt abweichen</label>
          <label><input type="radio" name="mattress" value="dual" ${d.mattress === "dual" ? "checked" : ""}>
            Zwei Einzelmatratzen – Seiten frei verstellbar</label>
        </div>
        <div class="row slider ${d.mattress === "single" ? "" : "hidden"}">
          <span>Max. Unterschied</span>
          <input type="range" min="1" max="50" step="1" value="${Math.round(d.max_diff * 100)}" data-maxdiff>
          <b data-maxdiffval>${Math.round(d.max_diff * 100)} %</b>
        </div>
        <p class="small">In Prozent des Verstellwegs. „Beide“ fährt immer synchron; die Grenze greift, wenn nur eine Seite fährt
        oder ein Favorit die Seiten unterschiedlich hoch stellen würde.</p>
      </section>`
          : ""
      }
      <section class="card">
        <h2>Positionen</h2>
        <p>Nach Benutzung der Funk-Fernbedienung weiß die Integration nicht, wo das Bett steht. Dann einmal <b>Referenzfahrt</b> machen –
        oder, wenn das Bett gerade sicher ganz flach ist:</p>
        <div class="row"><button class="btn" data-markflat>Alles als „ganz unten“ markieren</button></div>
      </section>
      <section class="card">
        <h2>Box</h2>
        <dl class="info">
          <dt>Adresse</dt><dd>${esc(d.host)}</dd>
          <dt>Firmware</dt><dd>${esc(b.firmware ?? "–")}</dd>
          <dt>Name</dt><dd>${esc(b.name ?? "–")}</dd>
          <dt>MAC</dt><dd>${esc(b.mac_address ?? "–")}</dd>
          <dt>Antwortzeit</dt><dd id="rtt">–</dd>
        </dl>
      </section>
      <section class="card">
        <h2>Version</h2>
        <dl class="info">
          <dt>Integration</dt><dd>${esc(v.version ?? "–")}</dd>
          ${v.commit ? `<dt>Commit</dt><dd>${esc(v.commit)}</dd>` : ""}
          ${v.built ? `<dt>Gebaut</dt><dd>${esc(v.built)}</dd>` : ""}
        </dl>
      </section>`;
  }

  // ------------------------------------------------------------------ Ereignisse

  _bind() {
    const root = this._content;
    const d = this._data;
    root.querySelectorAll("[data-side]").forEach((b) =>
      b.addEventListener("click", () => {
        this._side = b.dataset.side;
        store("side", this._side);
        this._render();
      })
    );
    root.querySelectorAll(".holdbtn").forEach((btn) => this._bindHold(btn));
    root.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => this._cmd(b.dataset.act)));
    const favName = (id) => (d.favorites.find((f) => f.id === id) || {}).name || "";
    root.querySelectorAll("[data-fav]").forEach((b) =>
      b.addEventListener("click", () => this._cmd("goto_favorite", { id: b.dataset.fav, side: this._side }))
    );
    root.querySelectorAll("[data-favnew]").forEach((b) =>
      b.addEventListener("click", async () => {
        const name = await this._dialog({
          title: "Aktuelle Lage speichern",
          text: "Gespeichert wird die Lage beider Seiten.",
          input: `Favorit ${d.favorites.length + 1}`,
          confirm: "Speichern",
        });
        if (name == null) return;
        if ((await this._cmd("save_favorite", { name })) != null) this._showToast(`„${name.trim()}“ gespeichert`);
      })
    );
    root.querySelectorAll("[data-favedit]").forEach((b) =>
      b.addEventListener("click", () => {
        this._favEdit = !this._favEdit;
        this._render();
      })
    );
    root.querySelectorAll("[data-favmove]").forEach((b) =>
      b.addEventListener("click", () => {
        const [id, offset] = b.dataset.favmove.split(":");
        this._cmd("move_favorite", { id, offset: Number(offset) });
      })
    );
    root.querySelectorAll("[data-favrename]").forEach((b) =>
      b.addEventListener("click", async () => {
        const id = b.dataset.favrename;
        const name = await this._dialog({ title: "Umbenennen", input: favName(id), confirm: "Übernehmen" });
        if (name != null) this._cmd("rename_favorite", { id, name });
      })
    );
    root.querySelectorAll("[data-favover]").forEach((b) =>
      b.addEventListener("click", async () => {
        const id = b.dataset.favover;
        const ok = await this._dialog({
          title: `„${favName(id)}“ überschreiben?`,
          text: "Der Favorit bekommt die aktuelle Lage beider Seiten.",
          confirm: "Überschreiben",
        });
        if (ok && (await this._cmd("save_favorite", { id })) != null) this._showToast("Überschrieben");
      })
    );
    root.querySelectorAll("[data-favdel]").forEach((b) =>
      b.addEventListener("click", async () => {
        const id = b.dataset.favdel;
        const ok = await this._dialog({ title: `„${favName(id)}“ löschen?`, confirm: "Löschen", danger: true });
        if (ok) await this._cmd("delete_favorite", { id });
      })
    );
    root.querySelectorAll('input[name="mattress"]').forEach((r) =>
      r.addEventListener("change", () => this._cmd("set_mattress", { mode: r.value }))
    );
    root.querySelectorAll("[data-maxdiff]").forEach((r) => {
      const label = root.querySelector("[data-maxdiffval]");
      r.addEventListener("input", () => (label.textContent = `${r.value} %`));
      r.addEventListener("change", () => this._cmd("set_mattress", { max_diff: Number(r.value) }));
    });
    root.querySelectorAll("[data-cmotor]").forEach((b) =>
      b.addEventListener("click", () => {
        this._calibMotor = b.dataset.cmotor;
        this._render();
      })
    );
    root.querySelectorAll("[data-offset]").forEach((b) =>
      b.addEventListener("click", () => {
        this._size = "big";
        this._cmd("offset_start", { motor: this._calibMotor, direction: b.dataset.offset });
      })
    );
    root.querySelectorAll("[data-size]").forEach((b) =>
      b.addEventListener("click", () => {
        this._size = b.dataset.size;
        this._render();
      })
    );
    root.querySelectorAll("[data-answer]").forEach((b) =>
      b.addEventListener("click", async () => {
        const res = await this._cmd("offset_feedback", { answer: b.dataset.answer, size: this._size });
        if (res && res.done) this._showToast("Startversatz gespeichert");
      })
    );
    root.querySelectorAll("[data-measure]").forEach((b) =>
      b.addEventListener("click", () => this._cmd("measure_start", { motor: this._calibMotor, direction: b.dataset.measure }))
    );
    root.querySelectorAll("[data-mark]").forEach((b) =>
      b.addEventListener("click", () => this._cmd("measure_mark", { side: b.dataset.mark }))
    );
    root.querySelectorAll("[data-finish]").forEach((b) =>
      b.addEventListener("click", async () => {
        const res = await this._cmd("measure_finish", { apply: b.dataset.finish === "1" });
        if (b.dataset.finish === "1" && res) this._showToast("Laufzeit gespeichert");
      })
    );
    root.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", () => this._cmd("calib_cancel")));
    root.querySelectorAll("[data-cal]").forEach((inp) =>
      inp.addEventListener("change", () => {
        const [side, motor, key] = inp.dataset.cal.split(":");
        const value = parseFloat(String(inp.value).replace(",", "."));
        if (Number.isFinite(value)) this._cmd("set_calibration", { side, motor, key, value: value * 1000 });
      })
    );
    root.querySelectorAll("[data-test]").forEach((b) =>
      b.addEventListener("click", () => this._cmd("test_parallel", { motor: this._calibMotor }))
    );
    root.querySelectorAll('input[name="mode"]').forEach((r) =>
      r.addEventListener("change", () => this._cmd("set_mode", { mode: r.value }))
    );
    root.querySelectorAll("[data-markflat]").forEach((b) =>
      b.addEventListener("click", async () => {
        const ok = await this._dialog({
          title: "Alles als ganz unten markieren?",
          text: "Nur wenn das Bett wirklich auf beiden Seiten ganz flach steht.",
          confirm: "Markieren",
        });
        if (!ok) return;
        for (const s of d.sides) for (const m of d.motors) await this._cmd("set_position", { side: s, motor: m, position: 0 });
        this._showToast("Positionen auf 0 gesetzt");
      })
    );
  }

  _bindHold(btn) {
    let active = false;
    const motor = btn.dataset.motor;
    const start = (e) => {
      e.preventDefault();
      if (active) return;
      active = true;
      try {
        btn.setPointerCapture(e.pointerId);
      } catch (err) {
        /* egal */
      }
      btn.classList.add("pressed");
      if (navigator.vibrate) navigator.vibrate(15);
      this._cmd("start_move", { side: this._side, motor, direction: btn.dataset.hold });
    };
    const end = () => {
      if (!active) return;
      active = false;
      btn.classList.remove("pressed");
      this._cmd("stop_motor", { side: this._side, motor });
    };
    btn.addEventListener("pointerdown", start);
    btn.addEventListener("pointerup", end);
    btn.addEventListener("pointercancel", end);
    btn.addEventListener("lostpointercapture", end);
    btn.addEventListener("contextmenu", (e) => e.preventDefault());
    btn.addEventListener("keydown", (e) => {
      if ((e.key === " " || e.key === "Enter") && !e.repeat) start(e);
    });
    btn.addEventListener("keyup", (e) => {
      if (e.key === " " || e.key === "Enter") end();
    });
  }

  // ------------------------------------------------------------------ Dialog

  /** Eigener Dialog (statt confirm/prompt, die in der HA-App nicht zuverlässig gehen).
   *  Mit ``input`` liefert er den Text, sonst true – bei Abbruch null. */
  _dialog({ title, text = "", input = null, confirm = "OK", danger = false }) {
    if (this.shadowRoot.querySelector(".dialog-wrap")) return Promise.resolve(null);  // nie zwei auf einmal
    return new Promise((resolve) => {
      const wrap = document.createElement("div");
      wrap.className = "dialog-wrap";
      wrap.innerHTML = `<div class="dialog" role="dialog" aria-modal="true">
          <h2>${esc(title)}</h2>
          ${text ? `<p>${esc(text)}</p>` : ""}
          ${input != null ? `<input class="dialog-input" type="text" maxlength="40" value="${esc(input)}">` : ""}
          <div class="dialog-actions">
            <button class="link" data-x="cancel">Abbrechen</button>
            <button class="btn ${danger ? "danger" : "primary"}" data-x="ok">${esc(confirm)}</button>
          </div></div>`;
      this.shadowRoot.appendChild(wrap);
      const field = wrap.querySelector(".dialog-input");
      const done = (value) => {
        wrap.remove();
        resolve(value);
      };
      const ok = () => {
        if (field) {
          if (!field.value.trim()) return field.focus();
          done(field.value.trim());
        } else done(true);
      };
      wrap.querySelector('[data-x="cancel"]').addEventListener("click", () => done(null));
      wrap.querySelector('[data-x="ok"]').addEventListener("click", ok);
      wrap.addEventListener("click", (e) => e.target === wrap && done(null));
      wrap.addEventListener("keydown", (e) => {
        if (e.key === "Escape") done(null);
        if (e.key === "Enter") ok();
      });
      if (field) {
        field.focus();
        field.select();
      } else wrap.querySelector('[data-x="ok"]').focus();
    });
  }

  // ------------------------------------------------------------------ Laufende Werte

  _update() {
    const d = this._data;
    const root = this._content;
    let unknown = false;
    for (const s of d.sides) {
      for (const m of d.motors) {
        const p = d.positions[s][m];
        if (!p.known) unknown = true;
        const fill = root.querySelector(`[data-fill="${s}:${m}"]`);
        if (fill) {
          fill.style.width = `${Math.round(p.pos * 1000) / 10}%`;
          fill.classList.toggle("unknown", !p.known);
        }
        const val = root.querySelector(`[data-val="${s}:${m}"]`);
        if (val) val.textContent = p.known ? pct(p.pos) : `~${pct(p.pos)}`;
      }
    }
    for (const m of d.motors) {
      const el = root.querySelector(`[data-moving="${m}"]`);
      if (!el) continue;
      const dirs = new Set(d.sides.map((s) => d.positions[s][m].moving).filter((x) => x));
      el.textContent = dirs.has(1) ? "fährt hoch" : dirs.has(-1) ? "fährt runter" : "";
    }
    const warn = root.querySelector("#unknown");
    if (warn) warn.hidden = !unknown;
    root.querySelectorAll("[data-cal]").forEach((inp) => {
      if (inp === this.shadowRoot.activeElement) return;
      const [s, m, k] = inp.dataset.cal.split(":");
      inp.value = ((d.calibration[s][m][k] ?? 0) / 1000).toFixed(2);
    });
    const lagline = root.querySelector("#lagline");
    if (lagline && d.offset) {
      const o = d.offset;
      const key = o.direction > 0 ? "lag_up" : "lag_down";
      const parts = d.sides.map((s) => `${esc(d.names[s])} ${sec(d.calibration[s][o.motor][key])} s`);
      lagline.innerHTML = `Anlauf jetzt: ${parts.join(" · ")}${o.step ? ` · Schrittweite ${Math.round(o.step)} ms` : ""}`;
    }
    if (d.measure) {
      for (const s of d.sides) {
        const el = root.querySelector(`[data-sw="${s}"]`);
        if (!el) continue;
        const mark = d.measure.marks[s];
        el.textContent = sec(mark ?? d.measure.elapsed[s]);
        el.classList.toggle("done", mark != null);
      }
    }
    root.querySelectorAll("[data-fav]").forEach((b) => b.classList.toggle("active", b.dataset.fav === d.active_favorite));
    if (d.limit_hit && d.limit_hit !== this._lastLimit) this._showToast(d.limit_hit);
    this._lastLimit = d.limit_hit;
    const rtt = root.querySelector("#rtt");
    if (rtt) rtt.textContent = d.rtt_ms == null ? "–" : `${d.rtt_ms} ms`;
  }
}

const STYLE = `
:host {
  display: block;
  min-height: 100vh;
  background: var(--primary-background-color, #f5f5f5);
  color: var(--primary-text-color, #212121);
  font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
  --accent: var(--primary-color, #03a9f4);
  --card: var(--card-background-color, #fff);
  --muted: var(--secondary-text-color, #727272);
  --line: var(--divider-color, rgba(0,0,0,.12));
  --danger: var(--error-color, #db4437);
  --good: var(--success-color, #43a047);
  --warn: var(--warning-color, #ffa600);
  --radius: var(--ha-card-border-radius, 12px);
}
* { box-sizing: border-box; }
.toolbar {
  display: flex; align-items: center; gap: 8px;
  height: var(--header-height, 56px); padding: 0 12px;
  background: var(--app-header-background-color, var(--primary-color));
  color: var(--app-header-text-color, #fff);
  position: sticky; top: 0; z-index: 2;
}
.toolbar .title { font-size: 20px; flex: 1; }
.status { font-size: 13px; display: flex; align-items: center; gap: 6px; opacity: .95; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dot.ok { background: #7CFC9A; }
.dot.bad { background: #ff8a80; }
.tabs { display: flex; gap: 4px; padding: 8px 16px 0; max-width: 1100px; margin: 0 auto; }
.tab {
  flex: 1; max-width: 220px; border: none; background: none; color: var(--muted);
  padding: 10px 8px; font: inherit; font-weight: 500; cursor: pointer;
  border-bottom: 3px solid transparent;
}
.tab.active { color: var(--accent); border-bottom-color: var(--accent); }
main { padding: 12px 16px 48px; max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }
.card {
  background: var(--card); border-radius: var(--radius); padding: 14px 16px;
  box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12));
  border: var(--ha-card-border-width, 0) solid var(--ha-card-border-color, var(--line));
}
.card.disabled { opacity: .55; }
.card.empty { text-align: center; color: var(--muted); padding: 32px 16px; }
h2 { font-size: 16px; margin: 0 0 8px; font-weight: 500; }
p { margin: 6px 0 10px; line-height: 1.45; }
.small, .hint { font-size: 13px; color: var(--muted); }
.opt { font-size: 12px; color: var(--muted); font-weight: 400; margin-left: 6px; }
.notice { border-radius: var(--radius); padding: 10px 14px; font-size: 14px; }
.notice.warn { background: color-mix(in srgb, var(--warn) 18%, transparent); }
.segmented { display: flex; background: var(--card); border-radius: 999px; padding: 4px; gap: 4px; box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12)); }
.seg {
  flex: 1; border: none; border-radius: 999px; padding: 10px 6px; font: inherit; font-weight: 500;
  background: none; color: var(--primary-text-color); cursor: pointer; display: flex; flex-direction: column; align-items: center; line-height: 1.15;
}
.seg small { font-size: 11px; font-weight: 400; opacity: .75; }
.seg.active { background: var(--accent); color: var(--text-primary-color, #fff); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.motor-head { display: flex; justify-content: space-between; align-items: baseline; }
.moving { font-size: 12px; color: var(--accent); }
.motor-body { display: flex; gap: 14px; align-items: center; }
.bars { flex: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.bar-row { display: grid; grid-template-columns: 64px 1fr 48px; align-items: center; gap: 8px; font-size: 13px; }
.bar-row.dim { opacity: .4; }
.bar-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.bar { height: 10px; border-radius: 5px; background: var(--line); overflow: hidden; }
.fill { height: 100%; width: 0; background: var(--accent); border-radius: 5px; transition: width .2s linear; }
.fill.unknown { background: repeating-linear-gradient(45deg, var(--accent), var(--accent) 6px, transparent 6px, transparent 10px); }
.bar-val { text-align: right; font-variant-numeric: tabular-nums; }
.hold { display: flex; flex-direction: column; gap: 8px; }
.holdbtn {
  width: 72px; height: 60px; border-radius: 14px; border: none; cursor: pointer;
  background: color-mix(in srgb, var(--accent) 14%, var(--card)); color: var(--accent);
  touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
  display: grid; place-items: center; transition: transform .08s, background .08s;
}
.holdbtn svg { width: 38px; height: 38px; fill: currentColor; }
.holdbtn.pressed { background: var(--accent); color: var(--text-primary-color, #fff); transform: scale(.96); }
.actions { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 10px; }
.btn {
  border: none; border-radius: 10px; padding: 12px 14px; font: inherit; font-weight: 500; cursor: pointer;
  background: color-mix(in srgb, var(--primary-text-color) 8%, var(--card)); color: var(--primary-text-color);
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 44px;
}
.btn:disabled { opacity: .45; cursor: default; }
.btn.primary { background: var(--accent); color: var(--text-primary-color, #fff); }
.btn.ok { background: var(--good); color: #fff; }
.btn.danger { background: var(--danger); color: #fff; }
.btn.big { font-size: 17px; }
.btn svg { width: 18px; height: 18px; fill: currentColor; }
.link { border: none; background: none; color: var(--accent); font: inherit; cursor: pointer; padding: 6px; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.card-head h2 { margin: 0; }
.head-actions { display: flex; align-items: center; gap: 4px; }
.small-btn { min-height: 36px; padding: 6px 12px; font-size: 14px; }
.fav-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.fav-go { justify-content: flex-start; text-align: left; }
.fav-go span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fav-go.active { background: color-mix(in srgb, var(--accent) 22%, var(--card)); box-shadow: inset 0 0 0 2px var(--accent); }
.fav-go.active svg { fill: var(--accent); }
.fav-edit { display: flex; flex-direction: column; gap: 4px; }
.fav-line { display: flex; align-items: center; gap: 2px; padding: 4px 0; border-bottom: 1px solid var(--line); }
.fav-line:last-child { border-bottom: none; }
.fav-name { flex: 1; display: flex; align-items: center; gap: 8px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fav-name svg { width: 18px; height: 18px; fill: var(--muted); flex: none; }
.icon-btn { border: none; background: none; color: var(--primary-text-color); width: 40px; height: 40px; border-radius: 50%; cursor: pointer; display: grid; place-items: center; }
.icon-btn:hover { background: color-mix(in srgb, var(--primary-text-color) 8%, transparent); }
.icon-btn:disabled { opacity: .3; cursor: default; }
.icon-btn svg { width: 20px; height: 20px; fill: currentColor; }
.icon-btn.danger { color: var(--danger); }
.row.col { flex-direction: column; align-items: flex-start; gap: 10px; }
.row.radio.col label { margin: 0; align-items: flex-start; line-height: 1.35; }
.row.radio.col input { margin-top: 3px; }
.row.slider { gap: 12px; }
.row.slider input { flex: 1; min-width: 120px; accent-color: var(--accent); }
.row.slider b { min-width: 48px; text-align: right; font-variant-numeric: tabular-nums; }
.hidden { display: none !important; }
.dialog-wrap { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: grid; place-items: center; z-index: 10; padding: 16px; }
.dialog { background: var(--card); color: var(--primary-text-color); border-radius: 16px; padding: 18px 20px; width: min(420px, 100%); box-shadow: 0 10px 40px rgba(0,0,0,.35); }
.dialog h2 { font-size: 18px; margin-bottom: 6px; }
.dialog-input { width: 100%; padding: 10px 12px; font: inherit; border-radius: 8px; border: 1px solid var(--line); background: var(--primary-background-color); color: var(--primary-text-color); margin: 8px 0 4px; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.chips, .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.row { margin-top: 8px; }
.row.three { display: grid; grid-template-columns: 1fr 1fr 1fr; }
.row.radio label { display: flex; gap: 6px; align-items: center; margin-right: 16px; }
.chip {
  border: 1px solid var(--line); border-radius: 999px; padding: 7px 14px; background: none;
  color: var(--primary-text-color); font: inherit; cursor: pointer;
}
.chip.active { background: var(--accent); border-color: var(--accent); color: var(--text-primary-color, #fff); }
.session { border: 2px solid var(--accent); }
.question { font-size: 17px; }
.progress { display: flex; gap: 10px; align-items: center; }
.spinner { width: 18px; height: 18px; border-radius: 50%; border: 3px solid var(--line); border-top-color: var(--accent); animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.lagline { font-size: 13px; color: var(--muted); margin-top: 8px; }
.sw-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.stopwatch { display: flex; flex-direction: column; gap: 6px; align-items: stretch; text-align: center; }
.sw-name { color: var(--muted); }
.sw-time { font-size: 34px; font-variant-numeric: tabular-nums; font-weight: 300; }
.sw-time.done { color: var(--good); font-weight: 500; }
table.values { border-collapse: collapse; width: 100%; font-size: 14px; }
.values th { text-align: left; font-weight: 500; color: var(--muted); padding: 4px; }
.values td { padding: 4px; white-space: nowrap; }
.values input {
  width: 76px; padding: 6px; border-radius: 6px; border: 1px solid var(--line);
  background: var(--card); color: var(--primary-text-color); font: inherit; text-align: right;
}
dl.info { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0; font-size: 14px; }
dl.info dt { color: var(--muted); }
dl.info dd { margin: 0; word-break: break-all; }
.toast {
  position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 40px); opacity: 0;
  background: var(--primary-text-color); color: var(--card); padding: 10px 18px; border-radius: 10px;
  transition: all .2s; pointer-events: none; max-width: calc(100% - 32px); z-index: 5;
}
.toast.show { opacity: 1; transform: translate(-50%, 0); }
@media (max-width: 600px) {
  main { padding: 10px 12px 40px; }
  .tabs { padding: 6px 8px 0; }
  .grid { grid-template-columns: 1fr; }
  .actions { grid-template-columns: 1fr 1fr; }
  .actions .big { grid-column: 1 / -1; }
  .holdbtn { width: 80px; height: 64px; }
}
`;

if (!customElements.get("auping-panel")) customElements.define("auping-panel", AupingPanel);
