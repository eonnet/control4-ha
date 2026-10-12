const CARD_TAG = "control4-media-group-card";
const EDITOR_TAG = "control4-media-group-card-editor";
const PLAYER_CARD_TAG = "control4-media-player-card";
const DIALOG_TAG = "control4-media-group-dialog";
const CONTROL4_PLATFORM = "control4_advanced";
const FEATURE_VOLUME_SET = 4;
const FEATURE_GROUPING = 524288;

const supportsFeature = (state, feature) =>
  Boolean((Number(state?.attributes?.supported_features) || 0) & feature);

const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      })[character]
  );

const stateName = (state) =>
  state?.attributes?.friendly_name || state?.entity_id || "Unknown player";

const control4Players = (hass) =>
  Object.values(hass?.states || {})
    .filter(
      (state) =>
        state.entity_id.startsWith("media_player.") &&
        (hass.entities?.[state.entity_id]?.platform === CONTROL4_PLATFORM ||
          Number.isInteger(state.attributes.control4_room_id)) &&
        supportsFeature(state, FEATURE_GROUPING)
    )
    .sort((left, right) => stateName(left).localeCompare(stateName(right)));

class Control4MediaGroupDialog extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._entityId = undefined;
    this._originalMembers = [];
    this._selected = new Set();
    this._submitting = false;
    this._error = "";
    this._onKeyDown = (event) => {
      if (event.key === "Escape" && !this._submitting) {
        this.close();
      }
    };
  }

  show(hass, entityId) {
    this._hass = hass;
    this._entityId = entityId;
    const state = hass.states[entityId];
    this._originalMembers = (state?.attributes?.group_members || []).filter(
      (member) => member !== entityId
    );
    this._selected = new Set(this._originalMembers);
    this._render();
    document.addEventListener("keydown", this._onKeyDown);
    requestAnimationFrame(() =>
      this.shadowRoot.querySelector(".close-button")?.focus()
    );
  }

  updateHass(hass) {
    this._hass = hass;
    if (!this.isConnected || !this._entityId) {
      return;
    }
    this._updateLiveValues();
  }

  close() {
    document.removeEventListener("keydown", this._onKeyDown);
    this.dispatchEvent(new CustomEvent("control4-dialog-closed"));
    this.remove();
  }

  disconnectedCallback() {
    document.removeEventListener("keydown", this._onKeyDown);
  }

  _players() {
    const players = control4Players(this._hass);
    const leader = players.find((state) => state.entity_id === this._entityId);
    if (!leader) {
      return players;
    }
    return [leader, ...players.filter((state) => state !== leader)];
  }

  _renderPlayer(state) {
    const isLeader = state.entity_id === this._entityId;
    const selected = isLeader || this._selected.has(state.entity_id);
    const rawVolume = state.attributes.volume_level;
    const hasVolume = typeof rawVolume === "number" && Number.isFinite(rawVolume);
    const volume = hasVolume
      ? Math.max(0, Math.min(100, Math.round(rawVolume * 100)))
      : 0;
    const canSetVolume =
      hasVolume &&
      supportsFeature(state, FEATURE_VOLUME_SET) &&
      !["unavailable", "unknown", "off"].includes(state.state);
    const icon =
      state.state === "playing"
        ? "mdi:speaker-play"
        : state.state === "paused"
          ? "mdi:speaker-pause"
          : "mdi:speaker";
    const secondary = state.state === "unavailable" ? "Unavailable" : state.state;

    return `
      <article class="player" data-player-id="${escapeHtml(state.entity_id)}">
        <div class="player-heading">
          <ha-icon icon="${icon}"></ha-icon>
          <div class="player-info">
            <div class="player-name">${escapeHtml(stateName(state))}</div>
            <div class="player-state">${escapeHtml(secondary)}</div>
          </div>
          <label class="switch" aria-label="Group ${escapeHtml(stateName(state))}">
            <input
              type="checkbox"
              data-member-id="${escapeHtml(state.entity_id)}"
              ${selected ? "checked" : ""}
              ${isLeader || this._submitting ? "disabled" : ""}
            />
            <span></span>
          </label>
        </div>
        <div class="volume-row ${canSetVolume ? "" : "disabled"}">
          <ha-icon icon="mdi:volume-medium"></ha-icon>
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            value="${volume}"
            data-volume-id="${escapeHtml(state.entity_id)}"
            aria-label="${escapeHtml(stateName(state))} volume"
            ${canSetVolume && !this._submitting ? "" : "disabled"}
          />
          <output data-volume-output="${escapeHtml(state.entity_id)}">
            ${hasVolume ? `${volume}%` : "—"}
          </output>
        </div>
      </article>`;
  }

  _render() {
    if (!this._hass || !this._entityId) {
      return;
    }
    const localize = (key, fallback) => this._hass.localize(key) || fallback;
    const players = this._players();
    this.shadowRoot.innerHTML = `
      <style>${Control4MediaGroupDialog.styles}</style>
      <div class="backdrop" role="presentation">
        <section
          class="dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="dialog-title"
        >
          <header>
            <button class="icon-button close-button" aria-label="Close">×</button>
            <h2 id="dialog-title">${escapeHtml(
              localize("ui.card.media_player.media_players", "Media players")
            )}</h2>
            <button class="text-button select-all" ${this._submitting ? "disabled" : ""}>
              ${escapeHtml(
                localize("ui.card.media_player.select_all", "Select all")
              )}
            </button>
          </header>
          <div class="error" ${this._error ? "" : "hidden"}>${escapeHtml(
            this._error
          )}</div>
          <main>${players.map((state) => this._renderPlayer(state)).join("")}</main>
          <footer>
            <button class="text-button cancel" ${this._submitting ? "disabled" : ""}>
              ${escapeHtml(localize("ui.common.cancel", "Cancel"))}
            </button>
            <button class="apply" ${this._submitting ? "disabled" : ""}>
              ${escapeHtml(localize("ui.common.apply", "Apply"))}
            </button>
          </footer>
        </section>
      </div>`;

    this.shadowRoot.querySelector(".backdrop").addEventListener("click", (event) => {
      if (event.target.classList.contains("backdrop") && !this._submitting) {
        this.close();
      }
    });
    this.shadowRoot.querySelector(".close-button").addEventListener("click", () =>
      this.close()
    );
    this.shadowRoot.querySelector(".cancel").addEventListener("click", () =>
      this.close()
    );
    this.shadowRoot.querySelector(".select-all").addEventListener("click", () => {
      this._selected = new Set(
        this._players()
          .map((state) => state.entity_id)
          .filter((entityId) => entityId !== this._entityId)
      );
      this._render();
    });
    for (const checkbox of this.shadowRoot.querySelectorAll("[data-member-id]")) {
      checkbox.addEventListener("change", (event) => {
        const entityId = event.currentTarget.dataset.memberId;
        if (event.currentTarget.checked) {
          this._selected.add(entityId);
        } else {
          this._selected.delete(entityId);
        }
      });
    }
    for (const slider of this.shadowRoot.querySelectorAll("[data-volume-id]")) {
      slider.addEventListener("input", (event) => {
        const entityId = event.currentTarget.dataset.volumeId;
        const output = this.shadowRoot.querySelector(
          `[data-volume-output="${CSS.escape(entityId)}"]`
        );
        if (output) {
          output.value = `${event.currentTarget.value}%`;
          output.textContent = `${event.currentTarget.value}%`;
        }
      });
      slider.addEventListener("change", (event) => this._setVolume(event));
    }
    this.shadowRoot.querySelector(".apply").addEventListener("click", () =>
      this._applyGroup()
    );
  }

  _updateLiveValues() {
    const active = this.shadowRoot.activeElement;
    for (const slider of this.shadowRoot.querySelectorAll("[data-volume-id]")) {
      const state = this._hass.states[slider.dataset.volumeId];
      if (!state) {
        continue;
      }
      const rawVolume = state.attributes.volume_level;
      const hasVolume = typeof rawVolume === "number" && Number.isFinite(rawVolume);
      const canSetVolume =
        hasVolume &&
        supportsFeature(state, FEATURE_VOLUME_SET) &&
        !["unavailable", "unknown", "off"].includes(state.state);
      slider.disabled = !canSetVolume || this._submitting;
      slider.closest(".volume-row")?.classList.toggle("disabled", !canSetVolume);
      if (slider !== active) {
        const value = hasVolume
          ? Math.max(0, Math.min(100, Math.round(rawVolume * 100)))
          : 0;
        slider.value = String(value);
        const output = this.shadowRoot.querySelector(
          `[data-volume-output="${CSS.escape(slider.dataset.volumeId)}"]`
        );
        if (output) {
          output.value = hasVolume ? `${value}%` : "—";
          output.textContent = hasVolume ? `${value}%` : "—";
        }
      }
    }
  }

  async _setVolume(event) {
    const slider = event.currentTarget;
    const entityId = slider.dataset.volumeId;
    const volumeLevel = Number(slider.value) / 100;
    this._error = "";
    try {
      await this._hass.callService(
        "media_player",
        "volume_set",
        { volume_level: volumeLevel },
        { entity_id: entityId }
      );
    } catch (error) {
      this._error = error?.message || String(error);
      this._render();
    }
  }

  async _applyGroup() {
    if (this._submitting) {
      return;
    }
    this._submitting = true;
    this._error = "";
    this._render();
    try {
      const selected = [...this._selected];
      await this._hass.callService(
        "media_player",
        "join",
        { group_members: selected },
        { entity_id: this._entityId }
      );
      await Promise.all(
        this._originalMembers
          .filter((entityId) => !this._selected.has(entityId))
          .map((entityId) =>
            this._hass.callService(
              "media_player",
              "unjoin",
              {},
              { entity_id: entityId }
            )
          )
      );
      this.close();
    } catch (error) {
      this._submitting = false;
      this._error = error?.message || String(error);
      this._render();
    }
  }

  static styles = `
    :host {
      color: var(--primary-text-color, #fff);
      font-family: var(--paper-font-body1_-_font-family, sans-serif);
    }
    * { box-sizing: border-box; }
    .backdrop {
      position: fixed;
      inset: 0;
      z-index: 9999;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.55);
    }
    .dialog {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr) auto;
      width: min(560px, calc(100vw - 32px));
      height: min(800px, calc(100vh - 32px));
      height: min(800px, calc(100dvh - 32px));
      max-height: min(800px, calc(100vh - 32px));
      overflow: hidden;
      border-radius: var(--ha-card-border-radius, 16px);
      background: var(--card-background-color, #1c1c1c);
      box-shadow: 0 12px 36px rgba(0, 0, 0, 0.45);
    }
    header {
      display: grid;
      grid-template-columns: 48px 1fr auto;
      align-items: center;
      min-height: 72px;
      padding: 0 12px;
      border-bottom: 1px solid var(--divider-color, #444);
    }
    h2 { margin: 0; font-size: 24px; font-weight: 500; }
    button { font: inherit; cursor: pointer; }
    button:disabled { cursor: default; opacity: 0.5; }
    .icon-button, .text-button {
      border: 0;
      color: var(--primary-text-color, #fff);
      background: transparent;
    }
    .icon-button { width: 44px; height: 44px; font-size: 36px; line-height: 1; }
    .text-button { padding: 12px; color: var(--primary-color, #03a9f4); }
    .error {
      margin: 12px 16px 0;
      padding: 12px;
      border-radius: 8px;
      color: var(--error-color, #db4437);
      background: color-mix(in srgb, var(--error-color, #db4437) 15%, transparent);
    }
    .error[hidden] { display: none; }
    main { min-height: 0; overflow-y: auto; padding: 12px 20px 20px; }
    .player { padding: 12px 0 14px; border-bottom: 1px solid var(--divider-color, #444); }
    .player:last-child { border-bottom: 0; }
    .player-heading {
      display: grid;
      grid-template-columns: 40px 1fr auto;
      gap: 12px;
      align-items: center;
    }
    .player-heading > ha-icon { width: 28px; color: var(--primary-text-color, #fff); }
    .player-info { min-width: 0; }
    .player-name { font-size: 18px; overflow: hidden; text-overflow: ellipsis; }
    .player-state {
      margin-top: 2px;
      color: var(--secondary-text-color, #aaa);
      text-transform: capitalize;
    }
    .volume-row {
      display: grid;
      grid-template-columns: 40px minmax(0, 1fr) 48px;
      gap: 12px;
      align-items: center;
      margin-top: 12px;
    }
    .volume-row ha-icon { width: 24px; color: var(--secondary-text-color, #aaa); }
    .volume-row input[type="range"] { width: 100%; accent-color: var(--primary-color, #03a9f4); }
    .volume-row output { text-align: end; color: var(--secondary-text-color, #aaa); }
    .volume-row.disabled { opacity: 0.45; }
    .switch { position: relative; display: inline-flex; width: 52px; height: 30px; }
    .switch input { width: 0; height: 0; opacity: 0; }
    .switch span {
      position: absolute;
      inset: 0;
      border: 1px solid var(--secondary-text-color, #888);
      border-radius: 18px;
      background: transparent;
      transition: 0.15s;
    }
    .switch span::before {
      content: "";
      position: absolute;
      width: 22px;
      height: 22px;
      left: 3px;
      top: 3px;
      border-radius: 50%;
      background: var(--secondary-text-color, #aaa);
      transition: 0.15s;
    }
    .switch input:checked + span {
      border-color: var(--primary-color, #03a9f4);
      background: color-mix(in srgb, var(--primary-color, #03a9f4) 28%, transparent);
    }
    .switch input:checked + span::before {
      transform: translateX(22px);
      background: var(--primary-color, #03a9f4);
    }
    .switch input:disabled + span { opacity: 0.55; }
    footer {
      display: flex;
      flex: 0 0 auto;
      justify-content: flex-end;
      gap: 10px;
      padding: 14px 20px;
      border-top: 1px solid var(--divider-color, #444);
      background: var(--card-background-color, #1c1c1c);
    }
    .apply {
      min-width: 92px;
      padding: 11px 20px;
      border: 0;
      border-radius: 22px;
      color: var(--text-primary-color, #fff);
      background: var(--primary-color, #03a9f4);
    }
    @media (max-width: 600px) {
      .backdrop { align-items: stretch; }
      .dialog {
        width: 100%;
        height: 100vh;
        height: 100dvh;
        max-height: 100dvh;
        border-radius: 0;
      }
      header { grid-template-columns: 44px minmax(0, 1fr) auto; min-height: 64px; }
      h2 { font-size: 22px; white-space: nowrap; }
      main { padding-inline: 20px; }
      footer {
        padding-bottom: max(14px, env(safe-area-inset-bottom));
      }
    }
  `;
}

class Control4MediaGroupCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._dialog = undefined;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig(hass) {
    return { entity: control4Players(hass)[0]?.entity_id || "" };
  }

  setConfig(config) {
    if (!config?.entity || !String(config.entity).startsWith("media_player.")) {
      throw new Error("Select a Control4 media player entity");
    }
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._dialog?.isConnected) {
      this._dialog.updateHass(hass);
    }
    this._render();
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { rows: 2, columns: 6, min_rows: 2, min_columns: 3 };
  }

  _render() {
    if (!this._config || !this._hass) {
      return;
    }
    const state = this._hass.states[this._config.entity];
    if (!state) {
      this.shadowRoot.innerHTML = `
        <ha-card><div class="missing">Entity ${escapeHtml(
          this._config.entity
        )} was not found</div></ha-card>`;
      return;
    }
    const members = new Set([
      state.entity_id,
      ...(state.attributes.group_members || []),
    ]);
    const title = this._config.name || stateName(state);
    const media = state.attributes.media_title || state.attributes.source;
    const summary = media || `${members.size} player${members.size === 1 ? "" : "s"}`;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        button {
          display: grid;
          grid-template-columns: 48px 1fr auto;
          gap: 12px;
          align-items: center;
          width: 100%;
          padding: 18px 20px;
          border: 0;
          color: var(--primary-text-color);
          text-align: start;
          background: transparent;
          cursor: pointer;
        }
        ha-icon { color: var(--primary-color); }
        .title { font-size: 18px; }
        .summary { margin-top: 3px; color: var(--secondary-text-color); }
        .action { color: var(--primary-color); font-weight: 500; }
        .missing { padding: 20px; color: var(--error-color); }
      </style>
      <ha-card>
        <button aria-label="Manage Control4 media group">
          <ha-icon icon="mdi:speaker-multiple"></ha-icon>
          <span>
            <div class="title">${escapeHtml(title)}</div>
            <div class="summary">${escapeHtml(summary)}</div>
          </span>
          <span class="action">Players &amp; volume</span>
        </button>
      </ha-card>`;
    this.shadowRoot.querySelector("button").addEventListener("click", () =>
      this._openDialog()
    );
  }

  _openDialog() {
    this._dialog?.remove();
    this._dialog = document.createElement(DIALOG_TAG);
    this._dialog.addEventListener("control4-dialog-closed", () => {
      this._dialog = undefined;
    });
    document.body.appendChild(this._dialog);
    this._dialog.show(this._hass, this._config.entity);
  }
}

class Control4MediaPlayerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._nativeCard = undefined;
    this._dialog = undefined;
    this._loadSequence = 0;
    this._handleNativeClick = (event) => {
      const joinButton = event
        .composedPath()
        .find((element) => element?.classList?.contains("join-media"));
      if (!joinButton) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      this._openDialog();
    };
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig(hass) {
    return { entity: control4Players(hass)[0]?.entity_id || "" };
  }

  setConfig(config) {
    if (!config?.entity || !String(config.entity).startsWith("media_player.")) {
      throw new Error("Select a Control4 media player entity");
    }
    this._config = { ...config };
    if (this._nativeCard) {
      this._nativeCard.setConfig(this._nativeConfig());
    } else {
      this._showLoading();
      void this._ensureNativeCard();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (this._nativeCard) {
      this._nativeCard.hass = hass;
    }
    if (this._dialog?.isConnected) {
      this._dialog.updateHass(hass);
    }
    if (this._config && !this._nativeCard) {
      void this._ensureNativeCard();
    }
  }

  connectedCallback() {
    if (this._config && !this._nativeCard) {
      void this._ensureNativeCard();
    }
  }

  disconnectedCallback() {
    this._loadSequence += 1;
  }

  getCardSize() {
    return this._nativeCard?.getCardSize?.() || 3;
  }

  getGridOptions() {
    return (
      this._nativeCard?.getGridOptions?.() || {
        rows: 3,
        columns: 6,
        min_rows: 2,
        min_columns: 3,
      }
    );
  }

  _nativeConfig() {
    const nativeConfig = {
      type: "media-control",
      entity: this._config.entity,
    };
    for (const key of ["name", "theme"]) {
      if (this._config[key] !== undefined) {
        nativeConfig[key] = this._config[key];
      }
    }
    return nativeConfig;
  }

  _showLoading() {
    this.shadowRoot.innerHTML = `
      <style>
        .loading, .error {
          padding: 20px;
          border-radius: var(--ha-card-border-radius, 12px);
          color: var(--secondary-text-color);
          background: var(--ha-card-background, var(--card-background-color));
        }
        .error { color: var(--error-color); }
      </style>
      <div class="loading">Loading Control4 media player…</div>`;
  }

  async _ensureNativeCard() {
    if (!this._config || this._nativeCard) {
      return;
    }
    const sequence = ++this._loadSequence;
    try {
      const helpers = await window.loadCardHelpers();
      const nativeCard = await helpers.createCardElement(this._nativeConfig());
      if (sequence !== this._loadSequence || !this.isConnected) {
        return;
      }
      nativeCard.addEventListener("click", this._handleNativeClick, true);
      if (this._hass) {
        nativeCard.hass = this._hass;
      }
      this._nativeCard = nativeCard;
      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; }
          hui-media-control-card { display: block; }
        </style>`;
      this.shadowRoot.appendChild(nativeCard);
    } catch (error) {
      if (sequence !== this._loadSequence) {
        return;
      }
      this.shadowRoot.innerHTML = `<div class="error">${escapeHtml(
        error?.message || "Could not load Home Assistant's media control card"
      )}</div>`;
    }
  }

  _openDialog() {
    if (!this._hass || !this._config) {
      return;
    }
    this._dialog?.remove();
    this._dialog = document.createElement(DIALOG_TAG);
    this._dialog.addEventListener("control4-dialog-closed", () => {
      this._dialog = undefined;
    });
    document.body.appendChild(this._dialog);
    this._dialog.show(this._hass, this._config.entity);
  }
}

class Control4MediaGroupCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._config = {};
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  _changed(updates) {
    const config = { ...this._config, ...updates };
    if (!config.name) {
      delete config.name;
    }
    this._config = config;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }

  _render() {
    if (!this._hass) {
      return;
    }
    const players = control4Players(this._hass);
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        label { display: block; margin: 16px 0; color: var(--primary-text-color); }
        select, input {
          width: 100%;
          margin-top: 8px;
          padding: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          color: var(--primary-text-color);
          background: var(--card-background-color);
          font: inherit;
        }
      </style>
      <label>
        Control4 media player
        <select>
          ${players
            .map(
              (state) => `<option value="${escapeHtml(state.entity_id)}" ${
                state.entity_id === this._config.entity ? "selected" : ""
              }>${escapeHtml(stateName(state))}</option>`
            )
            .join("")}
        </select>
      </label>
      <label>
        Card title (optional)
        <input type="text" value="${escapeHtml(this._config.name || "")}" />
      </label>`;
    this.shadowRoot.querySelector("select").addEventListener("change", (event) =>
      this._changed({ entity: event.currentTarget.value })
    );
    this.shadowRoot.querySelector("input").addEventListener("change", (event) =>
      this._changed({ name: event.currentTarget.value.trim() })
    );
  }
}

const registerCard = () => {
  if (!customElements.get(DIALOG_TAG)) {
    customElements.define(DIALOG_TAG, Control4MediaGroupDialog);
  }
  if (!customElements.get(EDITOR_TAG)) {
    customElements.define(EDITOR_TAG, Control4MediaGroupCardEditor);
  }
  if (!customElements.get(CARD_TAG)) {
    customElements.define(CARD_TAG, Control4MediaGroupCard);
  }
  if (!customElements.get(PLAYER_CARD_TAG)) {
    customElements.define(PLAYER_CARD_TAG, Control4MediaPlayerCard);
  }
  window.customCards = window.customCards || [];
  if (!window.customCards.some((card) => card.type === CARD_TAG)) {
    window.customCards.push({
      type: CARD_TAG,
      name: "Control4 Media Group",
      description: "Control4 group membership with independent room volume controls",
      preview: true,
      documentationURL: "https://github.com/eonnet/control4-ha#control4-media-group-card",
    });
  }
  if (!window.customCards.some((card) => card.type === PLAYER_CARD_TAG)) {
    window.customCards.push({
      type: PLAYER_CARD_TAG,
      name: "Control4 Media Player",
      description: "Native media controls with Control4 room grouping and independent volume",
      preview: true,
      documentationURL: "https://github.com/eonnet/control4-ha#control4-media-player-card",
    });
  }
};

// Register immediately for normal Lovelace resource loading. Extra modules can
// execute before HA replaces the native custom-element registry with its scoped
// registry, so check the live registry again after the application is defined.
registerCard();
(async () => {
  for (let attempts = 0; attempts < 600; attempts += 1) {
    if (window.customElements?.get("home-assistant")) {
      registerCard();
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 100));
  }
})();
