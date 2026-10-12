const CARD_TAG = "control4-media-group-card";
const EDITOR_TAG = "control4-media-group-card-editor";
const PLAYER_CARD_TAG = "control4-media-player-card";
const PRESET_CARD_TAG = "control4-media-preset-card";
const PRESET_EDITOR_TAG = "control4-media-preset-card-editor";
const DIALOG_TAG = "control4-media-group-dialog";
const CONTROL4_PLATFORM = "control4_advanced";
const FEATURE_VOLUME_SET = 4;
const FEATURE_VOLUME_MUTE = 8;
const FEATURE_GROUPING = 524288;
const FEEDBACK_TIMEOUT = 8000;
const PRESET_LIST_COMMAND = `${CONTROL4_PLATFORM}/group_presets/list`;
const PRESET_SAVE_COMMAND = `${CONTROL4_PLATFORM}/group_presets/save`;
const PRESET_DELETE_COMMAND = `${CONTROL4_PLATFORM}/group_presets/delete`;
const PRESET_REORDER_COMMAND = `${CONTROL4_PLATFORM}/group_presets/reorder`;
const PRESET_ACTIVATE_COMMAND = `${CONTROL4_PLATFORM}/group_presets/activate`;

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

const setSliderProgress = (slider, value) => {
  const normalized = Math.max(0, Math.min(100, Number(value) || 0));
  slider.value = String(normalized);
  slider.style.setProperty("--volume-value", `${normalized}%`);
};

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
    this._pendingMutes = new Map();
    this._groupVolumeBaseline = undefined;
    this._presets = [];
    this._presetsLoaded = false;
    this._presetEditorOpen = false;
    this._presetBusy = false;
    this._presetMessage = "";
    this._presetError = "";
    this._presetDraftName = "";
    this._presetEditingId = undefined;
    this._refreshTimer = undefined;
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
    void this._loadPresets();
    document.addEventListener("keydown", this._onKeyDown);
    this._refreshTimer = window.setInterval(() => {
      const currentHass = document.querySelector("home-assistant")?.hass;
      if (currentHass && currentHass !== this._hass) {
        this.updateHass(currentHass);
      }
    }, 250);
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
    window.clearInterval(this._refreshTimer);
    this._refreshTimer = undefined;
    const modal = this.shadowRoot.querySelector(".modal-shell");
    if (modal?.open) {
      modal.close();
    }
    this.dispatchEvent(new CustomEvent("control4-dialog-closed"));
    this.remove();
  }

  disconnectedCallback() {
    document.removeEventListener("keydown", this._onKeyDown);
    window.clearInterval(this._refreshTimer);
    this._refreshTimer = undefined;
  }

  _players() {
    const players = control4Players(this._hass);
    const leader = players.find((state) => state.entity_id === this._entityId);
    if (!leader) {
      return players;
    }
    return [leader, ...players.filter((state) => state !== leader)];
  }

  _groupStates() {
    return this._players().filter(
      (state) =>
        state.entity_id === this._entityId || this._selected.has(state.entity_id)
    );
  }

  _volumeStates() {
    return this._groupStates().filter((state) => {
      const volume = state.attributes.volume_level;
      return (
        typeof volume === "number" &&
        Number.isFinite(volume) &&
        supportsFeature(state, FEATURE_VOLUME_SET) &&
        !["unavailable", "unknown", "off"].includes(state.state)
      );
    });
  }

  _muteStates() {
    return this._groupStates().filter(
      (state) =>
        typeof state.attributes.is_volume_muted === "boolean" &&
        supportsFeature(state, FEATURE_VOLUME_MUTE) &&
        !["unavailable", "unknown", "off"].includes(state.state)
    );
  }

  _displayedMuted(state) {
    return this._pendingMutes.get(state.entity_id)?.value ??
      state.attributes.is_volume_muted;
  }

  _volumeSnapshot() {
    const levels = new Map();
    for (const state of this._volumeStates()) {
      const slider = this.shadowRoot.querySelector(
        `[data-volume-id="${CSS.escape(state.entity_id)}"]`
      );
      const displayed = slider ? Number(slider.value) : Number.NaN;
      const value = Number.isFinite(displayed)
        ? displayed
        : state.attributes.volume_level * 100;
      levels.set(state.entity_id, Math.max(0, Math.min(100, value)));
    }
    return levels;
  }

  _averageVolume(levels) {
    if (!levels.size) {
      return undefined;
    }
    return [...levels.values()].reduce((sum, value) => sum + value, 0) /
      levels.size;
  }

  _groupName() {
    return stateName(this._hass.states[this._entityId]);
  }

  _groupTargetSummary(states = this._groupStates()) {
    return states.map((state) => stateName(state)).join(" · ");
  }

  _renderGroupControls() {
    const groupStates = this._groupStates();
    const volumeLevels = this._volumeStates().map(
      (state) => state.attributes.volume_level * 100
    );
    const average = volumeLevels.length
      ? Math.round(
          volumeLevels.reduce((sum, value) => sum + value, 0) /
            volumeLevels.length
        )
      : 0;
    const muteStates = this._muteStates();
    const allMuted =
      muteStates.length > 0 &&
      muteStates.every((state) => this._displayedMuted(state) === true);
    const muteAction = allMuted ? "Unmute all" : "Mute all";
    return `
      <section class="group-controls" aria-label="Group controls">
        <div class="group-controls-heading">
          <div>
            <div class="group-name">Master volume — ${escapeHtml(
              this._groupName()
            )} group</div>
            <div class="group-target">${escapeHtml(
              this._groupTargetSummary(groupStates)
            )}</div>
          </div>
          <span class="group-count">${groupStates.length} player${
            groupStates.length === 1 ? "" : "s"
          }</span>
        </div>
        <div class="group-volume-row">
          <button
            type="button"
            class="group-mute-button ${allMuted ? "muted" : ""}"
            aria-label="${muteAction} selected players"
            aria-pressed="${allMuted ? "true" : "false"}"
            title="${muteAction}"
            ${muteStates.length && !this._submitting ? "" : "disabled"}
          >
            <ha-icon icon="${allMuted ? "mdi:volume-off" : "mdi:volume-medium"}"></ha-icon>
            <span>${muteAction}</span>
          </button>
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            value="${average}"
            style="--volume-value: ${average}%"
            data-group-volume
            aria-label="Group volume"
            ${volumeLevels.length && !this._submitting ? "" : "disabled"}
          />
          <output data-group-volume-output>
            ${volumeLevels.length ? `${average}%` : "—"}
          </output>
        </div>
      </section>`;
  }

  _resolvePresetState(entityId, roomId, players = this._players()) {
    const exact = players.find(
      (state) =>
        state.entity_id === entityId &&
        Number(state.attributes.control4_room_id) === Number(roomId)
    );
    return (
      exact ||
      players.find(
        (state) =>
          Number(state.attributes.control4_room_id) === Number(roomId)
      )
    );
  }

  _resolvePreset(preset) {
    const players = this._players();
    const leader = this._resolvePresetState(
      preset.leader_entity_id,
      preset.leader_room_id,
      players
    );
    const members = [];
    const missing = [];
    const seen = new Set();
    for (const member of preset.members || []) {
      const state = this._resolvePresetState(
        member.entity_id,
        member.room_id,
        players
      );
      if (!state || seen.has(state.entity_id)) {
        missing.push(member);
        continue;
      }
      seen.add(state.entity_id);
      members.push({ ...member, state });
    }
    return { leader, members, missing };
  }

  _renderPresetSection() {
    let content;
    if (!this._presetsLoaded) {
      content = `<div class="preset-empty">Loading saved groups…</div>`;
    } else if (!this._presets.length) {
      content = `
        <div class="preset-empty">
          Save the selected rooms, their volume levels, and mute states for
          one-tap recall.
        </div>`;
    } else {
      content = `<div class="preset-list">${this._presets
        .map((preset, index) => {
          const resolved = this._resolvePreset(preset);
          const unavailable = !resolved.leader || resolved.missing.length > 0;
          const leaderName = resolved.leader
            ? stateName(resolved.leader)
            : "Leader unavailable";
          const groupDetail = unavailable
            ? `${resolved.members.length}/${preset.members.length} players available`
            : `${resolved.members.length} player${
                resolved.members.length === 1 ? "" : "s"
              } · ${leaderName} leader`;
          const detail = preset.media?.title
            ? `${groupDetail} · ${preset.media.title}`
            : groupDetail;
          return `
            <article class="preset-card ${unavailable ? "unavailable" : ""}">
              <button
                type="button"
                class="preset-activate"
                data-preset-id="${escapeHtml(preset.id)}"
                aria-label="Activate ${escapeHtml(preset.name)} group preset"
                ${unavailable || this._submitting || this._presetBusy ? "disabled" : ""}
              >
                <ha-icon icon="mdi:play-circle-outline"></ha-icon>
                <span>
                  <strong>${escapeHtml(preset.name)}</strong>
                  <small>${escapeHtml(detail)}</small>
                </span>
              </button>
              <div class="preset-actions">
                <button
                  type="button"
                  class="preset-action"
                  data-edit-preset-id="${escapeHtml(preset.id)}"
                  aria-label="Edit ${escapeHtml(preset.name)} group preset"
                  title="Rename or update preset"
                  ${this._submitting || this._presetBusy ? "disabled" : ""}
                ><ha-icon icon="mdi:pencil-outline"></ha-icon></button>
                <button
                  type="button"
                  class="preset-action"
                  data-move-preset-id="${escapeHtml(preset.id)}"
                  data-move-direction="-1"
                  aria-label="Move ${escapeHtml(preset.name)} up"
                  title="Move up"
                  ${index === 0 || this._submitting || this._presetBusy ? "disabled" : ""}
                ><ha-icon icon="mdi:arrow-up"></ha-icon></button>
                <button
                  type="button"
                  class="preset-action"
                  data-move-preset-id="${escapeHtml(preset.id)}"
                  data-move-direction="1"
                  aria-label="Move ${escapeHtml(preset.name)} down"
                  title="Move down"
                  ${index === this._presets.length - 1 || this._submitting || this._presetBusy ? "disabled" : ""}
                ><ha-icon icon="mdi:arrow-down"></ha-icon></button>
                <button
                  type="button"
                  class="preset-action preset-delete"
                  data-delete-preset-id="${escapeHtml(preset.id)}"
                  aria-label="Delete ${escapeHtml(preset.name)} group preset"
                  title="Delete preset"
                  ${this._submitting || this._presetBusy ? "disabled" : ""}
                ><ha-icon icon="mdi:delete-outline"></ha-icon></button>
              </div>
            </article>`;
        })
        .join("")}</div>`;
    }
    return `
      <section class="preset-section" data-presets>
        <div class="preset-heading">
          <div>
            <h3>Group presets</h3>
            <p>Recall source, rooms, volume, and mute settings</p>
          </div>
          <button
            type="button"
            class="save-current-preset"
            ${
              !this._presetsLoaded ||
              this._presetEditorOpen ||
              this._submitting ||
              this._presetBusy
                ? "disabled"
                : ""
            }
          ><ha-icon icon="mdi:plus"></ha-icon>Save current</button>
        </div>
        <div class="preset-notice preset-error" ${
          this._presetError ? "" : "hidden"
        }>${escapeHtml(this._presetError)}</div>
        <div class="preset-notice preset-success" ${
          this._presetMessage ? "" : "hidden"
        }>${escapeHtml(this._presetMessage)}</div>
        ${
          this._presetEditorOpen
            ? `<form class="preset-editor">
                <input
                  type="text"
                  maxlength="40"
                  data-preset-name
                  placeholder="Preset name, for example Whole House"
                  value="${escapeHtml(this._presetDraftName)}"
                  aria-label="Group preset name"
                  required
                />
                <button type="submit" class="preset-save" ${
                  this._presetBusy ? "disabled" : ""
                } data-preset-action="${
                  this._presetEditingId ? "rename" : "create"
                }">${this._presetEditingId ? "Rename" : "Save"}</button>
                ${
                  this._presetEditingId
                    ? `<button type="submit" class="preset-update" data-preset-action="update" ${
                        this._presetBusy ? "disabled" : ""
                      }>Update current</button>`
                    : ""
                }
                <button type="button" class="preset-cancel" ${
                  this._presetBusy ? "disabled" : ""
                }>Cancel</button>
              </form>`
            : ""
        }
        ${content}
      </section>`;
  }

  _renderPlayerSections(players) {
    const grouped = players.filter(
      (state) =>
        state.entity_id === this._entityId || this._selected.has(state.entity_id)
    );
    const available = players.filter(
      (state) =>
        state.entity_id !== this._entityId && !this._selected.has(state.entity_id)
    );
    return `
      <section class="player-section grouped-section">
        <div class="player-section-heading">
          <h3>Players in this group</h3>
          <span data-grouped-count>${grouped.length}</span>
        </div>
        <div data-grouped-players>
          ${grouped.map((state) => this._renderPlayer(state)).join("")}
        </div>
      </section>
      <section class="player-section available-section">
        <div class="player-section-heading">
          <h3>Available players</h3>
          <span data-available-count>${available.length}</span>
        </div>
        <div
          class="empty-players"
          data-available-empty
          ${available.length ? "hidden" : ""}
        >All players are selected</div>
        <div data-available-players>
          ${available.map((state) => this._renderPlayer(state)).join("")}
        </div>
      </section>`;
  }

  _renderPlayer(state) {
    const isLeader = state.entity_id === this._entityId;
    const selected = isLeader || this._selected.has(state.entity_id);
    const rawVolume = state.attributes.volume_level;
    const hasVolume = typeof rawVolume === "number" && Number.isFinite(rawVolume);
    const volume = hasVolume
      ? Math.max(0, Math.min(100, Math.round(rawVolume * 100)))
      : 0;
    const rawMuted = state.attributes.is_volume_muted;
    const pendingMute = this._pendingMutes.get(state.entity_id);
    const muted = pendingMute?.value ?? rawMuted;
    const hasMute = typeof muted === "boolean";
    const canSetVolume =
      hasVolume &&
      supportsFeature(state, FEATURE_VOLUME_SET) &&
      !["unavailable", "unknown", "off"].includes(state.state);
    const canMute =
      hasMute &&
      supportsFeature(state, FEATURE_VOLUME_MUTE) &&
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
          <button
            type="button"
            class="mute-button ${muted ? "muted" : ""}"
            data-mute-id="${escapeHtml(state.entity_id)}"
            aria-label="${muted ? "Unmute" : "Mute"} ${escapeHtml(stateName(state))}"
            aria-pressed="${muted ? "true" : "false"}"
            title="${muted ? "Unmute" : "Mute"}"
            ${canMute && !this._submitting ? "" : "disabled"}
          >
            <ha-icon icon="${muted ? "mdi:volume-off" : "mdi:volume-medium"}"></ha-icon>
          </button>
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            value="${volume}"
            style="--volume-value: ${volume}%"
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
      <dialog class="modal-shell" aria-labelledby="dialog-title">
        <div class="backdrop" role="presentation">
        <section
          class="dialog"
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
          ${this._renderGroupControls()}
          <main>
            ${this._renderPlayerSections(players)}
            ${this._renderPresetSection()}
          </main>
          <footer>
            <button class="text-button cancel" ${this._submitting ? "disabled" : ""}>
              ${escapeHtml(localize("ui.common.cancel", "Cancel"))}
            </button>
            <button class="apply" ${this._submitting ? "disabled" : ""}>
              ${escapeHtml(localize("ui.common.apply", "Apply"))}
            </button>
          </footer>
        </section>
        </div>
      </dialog>`;

    const modal = this.shadowRoot.querySelector(".modal-shell");
    modal.addEventListener("cancel", (event) => {
      event.preventDefault();
      if (!this._submitting) {
        this.close();
      }
    });
    if (typeof modal.showModal === "function") {
      try {
        modal.showModal();
      } catch (_error) {
        modal.setAttribute("open", "");
      }
    } else {
      modal.setAttribute("open", "");
    }

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
    this._bindPresetEvents();
    for (const checkbox of this.shadowRoot.querySelectorAll("[data-member-id]")) {
      checkbox.addEventListener("change", (event) => {
        const entityId = event.currentTarget.dataset.memberId;
        if (event.currentTarget.checked) {
          this._selected.add(entityId);
        } else {
          this._selected.delete(entityId);
        }
        this._groupVolumeBaseline = undefined;
        this._movePlayerToSection(entityId, event.currentTarget.checked);
        this._updateGroupControls();
      });
    }
    const groupSlider = this.shadowRoot.querySelector("[data-group-volume]");
    groupSlider.addEventListener("pointerdown", () =>
      this._captureGroupVolumeBaseline(true)
    );
    groupSlider.addEventListener("focus", () =>
      this._captureGroupVolumeBaseline(false)
    );
    groupSlider.addEventListener("input", (event) => {
      this._captureGroupVolumeBaseline(false);
      setSliderProgress(event.currentTarget, event.currentTarget.value);
      const output = this.shadowRoot.querySelector("[data-group-volume-output]");
      output.value = `${event.currentTarget.value}%`;
      output.textContent = `${event.currentTarget.value}%`;
    });
    groupSlider.addEventListener("change", (event) =>
      this._setGroupVolume(event)
    );
    groupSlider.addEventListener("blur", () => {
      this._groupVolumeBaseline = undefined;
      requestAnimationFrame(() => this._updateGroupControls());
    });
    this.shadowRoot
      .querySelector(".group-mute-button")
      .addEventListener("click", () => this._setGroupMute());
    for (const slider of this.shadowRoot.querySelectorAll("[data-volume-id]")) {
      slider.addEventListener("input", (event) => {
        const entityId = event.currentTarget.dataset.volumeId;
        setSliderProgress(event.currentTarget, event.currentTarget.value);
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
    for (const button of this.shadowRoot.querySelectorAll("[data-mute-id]")) {
      button.addEventListener("click", (event) => this._setMute(event));
    }
    this.shadowRoot.querySelector(".apply").addEventListener("click", () =>
      this._applyGroup()
    );
  }

  _sendPresetMessage(message) {
    const send = this._hass?.connection?.sendMessagePromise;
    if (typeof send !== "function") {
      throw new Error("Home Assistant WebSocket connection is unavailable");
    }
    return send.call(this._hass.connection, message);
  }

  _refreshPresetSection() {
    const current = this.shadowRoot.querySelector("[data-presets]");
    if (!current) {
      return;
    }
    const template = document.createElement("template");
    template.innerHTML = this._renderPresetSection().trim();
    current.replaceWith(template.content.firstElementChild);
    this._bindPresetEvents();
  }

  _bindPresetEvents() {
    this.shadowRoot
      .querySelector(".save-current-preset")
      ?.addEventListener("click", () => {
        this._presetEditorOpen = true;
        this._presetEditingId = undefined;
        this._presetDraftName = "";
        this._presetMessage = "";
        this._presetError = "";
        this._refreshPresetSection();
        requestAnimationFrame(() =>
          this.shadowRoot.querySelector("[data-preset-name]")?.focus()
        );
      });
    const nameInput = this.shadowRoot.querySelector("[data-preset-name]");
    nameInput?.addEventListener("input", (event) => {
      this._presetDraftName = event.currentTarget.value;
    });
    this.shadowRoot
      .querySelector(".preset-editor")
      ?.addEventListener("submit", (event) => this._savePreset(event));
    this.shadowRoot
      .querySelector(".preset-cancel")
      ?.addEventListener("click", () => {
        this._presetEditorOpen = false;
        this._presetEditingId = undefined;
        this._presetDraftName = "";
        this._presetError = "";
        this._refreshPresetSection();
      });
    for (const button of this.shadowRoot.querySelectorAll("[data-preset-id]")) {
      button.addEventListener("click", (event) =>
        this._activatePreset(event.currentTarget.dataset.presetId)
      );
    }
    for (const button of this.shadowRoot.querySelectorAll(
      "[data-edit-preset-id]"
    )) {
      button.addEventListener("click", (event) => {
        const preset = this._presets.find(
          (item) => item.id === event.currentTarget.dataset.editPresetId
        );
        if (!preset) {
          return;
        }
        this._presetEditorOpen = true;
        this._presetEditingId = preset.id;
        this._presetDraftName = preset.name;
        this._presetMessage = "";
        this._presetError = "";
        this._refreshPresetSection();
        requestAnimationFrame(() =>
          this.shadowRoot.querySelector("[data-preset-name]")?.focus()
        );
      });
    }
    for (const button of this.shadowRoot.querySelectorAll(
      "[data-move-preset-id]"
    )) {
      button.addEventListener("click", (event) =>
        this._movePreset(
          event.currentTarget.dataset.movePresetId,
          Number(event.currentTarget.dataset.moveDirection)
        )
      );
    }
    for (const button of this.shadowRoot.querySelectorAll(
      "[data-delete-preset-id]"
    )) {
      button.addEventListener("click", (event) =>
        this._deletePreset(event.currentTarget.dataset.deletePresetId)
      );
    }
  }

  async _loadPresets() {
    try {
      const response = await this._sendPresetMessage({
        type: PRESET_LIST_COMMAND,
      });
      this._presets = Array.isArray(response?.presets) ? response.presets : [];
      this._presetError = "";
    } catch (error) {
      this._presetError = `Could not load group presets: ${
        error?.message || String(error)
      }`;
    } finally {
      this._presetsLoaded = true;
      if (this.isConnected) {
        this._refreshPresetSection();
      }
    }
  }

  _currentPresetMembers() {
    return this._groupStates().map((state) => {
      const slider = this.shadowRoot.querySelector(
        `[data-volume-id="${CSS.escape(state.entity_id)}"]`
      );
      const rawVolume = state.attributes.volume_level;
      const hasVolume = typeof rawVolume === "number" && Number.isFinite(rawVolume);
      const canSaveVolume = hasVolume && supportsFeature(state, FEATURE_VOLUME_SET);
      const displayedVolume = canSaveVolume && slider
        ? Number(slider.value) / 100
        : Number.NaN;
      const volumeLevel = Number.isFinite(displayedVolume)
        ? Math.max(0, Math.min(1, displayedVolume))
        : canSaveVolume
          ? Math.max(0, Math.min(1, rawVolume))
          : null;
      const muted = this._displayedMuted(state);
      return {
        entity_id: state.entity_id,
        room_id: Number(state.attributes.control4_room_id),
        volume_level: volumeLevel,
        is_volume_muted:
          typeof muted === "boolean" && supportsFeature(state, FEATURE_VOLUME_MUTE)
            ? muted
            : null,
      };
    });
  }

  _currentPresetMedia(leader) {
    const contentType = leader?.attributes?.control4_media_content_type;
    const contentId = leader?.attributes?.control4_media_content_id;
    const title = leader?.attributes?.control4_media_title;
    if (
      typeof contentType !== "string" ||
      typeof contentId !== "string" ||
      typeof title !== "string"
    ) {
      return null;
    }
    return {
      content_type: contentType,
      content_id: contentId,
      title,
    };
  }

  async _savePreset(event) {
    event.preventDefault();
    if (this._presetBusy) {
      return;
    }
    const name = this._presetDraftName.trim();
    const action = event.submitter?.dataset.presetAction || "create";
    const existing = this._presetEditingId
      ? this._presets.find((preset) => preset.id === this._presetEditingId)
      : undefined;
    const useCurrent = action !== "rename";
    const currentLeader = this._hass.states[this._entityId];
    const leader = useCurrent ? currentLeader : this._resolvePreset(existing || {}).leader;
    const leaderRoomId = Number(
      useCurrent
        ? leader?.attributes?.control4_room_id
        : existing?.leader_room_id
    );
    if (!name || !Number.isInteger(leaderRoomId)) {
      this._presetError = !name
        ? "Enter a name for the preset."
        : "The Control4 group leader is unavailable.";
      this._refreshPresetSection();
      return;
    }

    this._presetBusy = true;
    this._presetError = "";
    this._presetMessage = "";
    this._refreshPresetSection();
    try {
      const members = useCurrent
        ? this._currentPresetMembers()
        : existing.members.map((member) => ({ ...member }));
      const media = useCurrent
        ? this._currentPresetMedia(leader)
        : existing.media || null;
      const response = await this._sendPresetMessage({
        type: PRESET_SAVE_COMMAND,
        ...(existing ? { preset_id: existing.id } : {}),
        name,
        leader_entity_id: useCurrent
          ? leader.entity_id
          : existing.leader_entity_id,
        leader_room_id: leaderRoomId,
        members,
        media,
      });
      const saved = response?.preset;
      if (!saved?.id) {
        throw new Error("Home Assistant returned an invalid preset");
      }
      const index = this._presets.findIndex((preset) => preset.id === saved.id);
      if (index === -1) {
        this._presets.push(saved);
      } else {
        this._presets[index] = saved;
      }
      this._presetEditorOpen = false;
      this._presetEditingId = undefined;
      this._presetDraftName = "";
      this._presetMessage = `${saved.name} ${
        action === "rename" ? "renamed" : action === "update" ? "updated" : "saved"
      }`;
    } catch (error) {
      this._presetError = `Could not save preset: ${
        error?.message || String(error)
      }`;
    } finally {
      this._presetBusy = false;
      if (this.isConnected) {
        this._refreshPresetSection();
      }
    }
  }

  async _movePreset(presetId, direction) {
    if (this._presetBusy || ![-1, 1].includes(direction)) {
      return;
    }
    const index = this._presets.findIndex((preset) => preset.id === presetId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= this._presets.length) {
      return;
    }
    const reordered = [...this._presets];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    this._presetBusy = true;
    this._presetError = "";
    this._presetMessage = "";
    this._refreshPresetSection();
    try {
      const response = await this._sendPresetMessage({
        type: PRESET_REORDER_COMMAND,
        preset_ids: reordered.map((preset) => preset.id),
      });
      this._presets = Array.isArray(response?.presets)
        ? response.presets
        : reordered;
      this._presetMessage = "Preset order saved";
    } catch (error) {
      this._presetError = `Could not reorder presets: ${
        error?.message || String(error)
      }`;
    } finally {
      this._presetBusy = false;
      if (this.isConnected) {
        this._refreshPresetSection();
      }
    }
  }

  async _deletePreset(presetId) {
    if (this._presetBusy) {
      return;
    }
    const preset = this._presets.find((item) => item.id === presetId);
    if (!preset || !window.confirm(`Delete the “${preset.name}” group preset?`)) {
      return;
    }
    this._presetBusy = true;
    this._presetError = "";
    this._presetMessage = "";
    this._refreshPresetSection();
    try {
      await this._sendPresetMessage({
        type: PRESET_DELETE_COMMAND,
        preset_id: presetId,
      });
      this._presets = this._presets.filter((item) => item.id !== presetId);
      this._presetMessage = `${preset.name} deleted`;
    } catch (error) {
      this._presetError = `Could not delete preset: ${
        error?.message || String(error)
      }`;
    } finally {
      this._presetBusy = false;
      if (this.isConnected) {
        this._refreshPresetSection();
      }
    }
  }

  async _activatePreset(presetId) {
    if (this._submitting || this._presetBusy) {
      return;
    }
    const preset = this._presets.find((item) => item.id === presetId);
    if (!preset) {
      return;
    }
    const resolved = this._resolvePreset(preset);
    if (!resolved.leader || resolved.missing.length) {
      this._presetError = "This preset contains players that are no longer available.";
      this._refreshPresetSection();
      return;
    }

    const leaderId = resolved.leader.entity_id;
    const memberIds = resolved.members.map((member) => member.state.entity_id);
    this._entityId = leaderId;
    this._selected = new Set(memberIds.filter((entityId) => entityId !== leaderId));
    this._submitting = true;
    this._presetBusy = true;
    this._error = "";
    this._presetMessage = `Activating ${preset.name}…`;
    this._render();

    try {
      await this._sendPresetMessage({
        type: PRESET_ACTIVATE_COMMAND,
        preset_id: preset.id,
      });
      this.close();
    } catch (error) {
      this._submitting = false;
      this._presetBusy = false;
      this._presetMessage = "";
      const message = error?.message || String(error);
      this._error = message.startsWith("Could not activate ")
        ? message
        : `Could not activate ${preset.name}: ${message}`;
      this._render();
    }
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
        setSliderProgress(slider, value);
        const output = this.shadowRoot.querySelector(
          `[data-volume-output="${CSS.escape(slider.dataset.volumeId)}"]`
        );
        if (output) {
          output.value = hasVolume ? `${value}%` : "—";
          output.textContent = hasVolume ? `${value}%` : "—";
        }
      }
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-mute-id]")) {
      const entityId = button.dataset.muteId;
      const state = this._hass.states[entityId];
      if (!state) {
        continue;
      }
      const actualMuted = state.attributes.is_volume_muted;
      let pending = this._pendingMutes.get(entityId);
      if (
        pending &&
        (actualMuted === pending.value || Date.now() >= pending.expiresAt)
      ) {
        this._pendingMutes.delete(entityId);
        pending = undefined;
      }
      const muted = pending?.value ?? actualMuted;
      const canMute =
        typeof actualMuted === "boolean" &&
        supportsFeature(state, FEATURE_VOLUME_MUTE) &&
        !["unavailable", "unknown", "off"].includes(state.state);
      button.disabled = !canMute || this._submitting;
      this._updateMuteButton(button, muted === true);
    }
    this._updateGroupControls(active);
  }

  _movePlayerToSection(entityId, grouped) {
    const player = this.shadowRoot.querySelector(
      `[data-player-id="${CSS.escape(entityId)}"]`
    );
    const target = this.shadowRoot.querySelector(
      grouped ? "[data-grouped-players]" : "[data-available-players]"
    );
    if (player && target) {
      target.appendChild(player);
    }
    this._updatePlayerSections();
  }

  _updatePlayerSections() {
    const grouped = this.shadowRoot.querySelectorAll(
      "[data-grouped-players] > .player"
    ).length;
    const available = this.shadowRoot.querySelectorAll(
      "[data-available-players] > .player"
    ).length;
    const groupedCount = this.shadowRoot.querySelector("[data-grouped-count]");
    const availableCount = this.shadowRoot.querySelector(
      "[data-available-count]"
    );
    const availableEmpty = this.shadowRoot.querySelector(
      "[data-available-empty]"
    );
    if (groupedCount) {
      groupedCount.textContent = String(grouped);
    }
    if (availableCount) {
      availableCount.textContent = String(available);
    }
    if (availableEmpty) {
      availableEmpty.hidden = available > 0;
    }
  }

  _updateGroupControls(active = this.shadowRoot.activeElement) {
    const selected = this._groupStates();
    const target = this.shadowRoot.querySelector(".group-target");
    const count = this.shadowRoot.querySelector(".group-count");
    if (target) {
      target.textContent = this._groupTargetSummary(selected);
    }
    if (count) {
      count.textContent = `${selected.length} player${
        selected.length === 1 ? "" : "s"
      }`;
    }

    const levels = this._volumeSnapshot();
    const average = this._averageVolume(levels);
    const slider = this.shadowRoot.querySelector("[data-group-volume]");
    const output = this.shadowRoot.querySelector("[data-group-volume-output]");
    slider.disabled = average === undefined || this._submitting;
    if (slider !== active && this._groupVolumeBaseline === undefined) {
      const value = average === undefined ? 0 : Math.round(average);
      setSliderProgress(slider, value);
      output.value = average === undefined ? "—" : `${value}%`;
      output.textContent = average === undefined ? "—" : `${value}%`;
    }

    const muteStates = this._muteStates();
    const allMuted =
      muteStates.length > 0 &&
      muteStates.every((state) => this._displayedMuted(state) === true);
    const groupMute = this.shadowRoot.querySelector(".group-mute-button");
    groupMute.disabled = !muteStates.length || this._submitting;
    this._updateGroupMuteButton(groupMute, allMuted);
  }

  _captureGroupVolumeBaseline(force) {
    if (this._groupVolumeBaseline !== undefined && !force) {
      return;
    }
    const levels = this._volumeSnapshot();
    const average = this._averageVolume(levels);
    this._groupVolumeBaseline =
      average === undefined ? undefined : { levels, average };
  }

  _setDisplayedVolume(entityId, value) {
    const slider = this.shadowRoot.querySelector(
      `[data-volume-id="${CSS.escape(entityId)}"]`
    );
    const output = this.shadowRoot.querySelector(
      `[data-volume-output="${CSS.escape(entityId)}"]`
    );
    if (slider) {
      setSliderProgress(slider, value);
    }
    if (output) {
      output.value = `${value}%`;
      output.textContent = `${value}%`;
    }
  }

  async _setGroupVolume(event) {
    this._captureGroupVolumeBaseline(false);
    const baseline = this._groupVolumeBaseline;
    if (!baseline) {
      return;
    }
    const requested = Math.max(0, Math.min(100, Number(event.currentTarget.value)));
    const delta = requested - baseline.average;
    const levels = new Map();
    for (const [entityId, value] of baseline.levels) {
      const adjusted =
        requested === 0 || requested === 100
          ? requested
          : Math.round(Math.max(0, Math.min(100, value + delta)));
      levels.set(entityId, adjusted);
      this._setDisplayedVolume(entityId, adjusted);
    }
    const average = Math.round(this._averageVolume(levels));
    event.currentTarget.value = String(average);
    const output = this.shadowRoot.querySelector("[data-group-volume-output]");
    output.value = `${average}%`;
    output.textContent = `${average}%`;
    this._groupVolumeBaseline = { levels, average };
    this._error = "";
    try {
      await Promise.all(
        [...levels].map(([entityId, value]) =>
          this._hass.callService(
            "media_player",
            "volume_set",
            { volume_level: value / 100 },
            { entity_id: entityId }
          )
        )
      );
    } catch (error) {
      this._groupVolumeBaseline = undefined;
      this._error = error?.message || String(error);
      this._render();
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

  _updateMuteButton(button, muted) {
    const state = this._hass.states[button.dataset.muteId];
    const action = muted ? "Unmute" : "Mute";
    button.classList.toggle("muted", muted);
    button.setAttribute("aria-pressed", muted ? "true" : "false");
    button.setAttribute("aria-label", `${action} ${stateName(state)}`);
    button.title = action;
    button.querySelector("ha-icon")?.setAttribute(
      "icon",
      muted ? "mdi:volume-off" : "mdi:volume-medium"
    );
  }

  _updateGroupMuteButton(button, allMuted) {
    const action = allMuted ? "Unmute all" : "Mute all";
    button.classList.toggle("muted", allMuted);
    button.setAttribute("aria-pressed", allMuted ? "true" : "false");
    button.setAttribute("aria-label", `${action} selected players`);
    button.title = action;
    button.querySelector("ha-icon")?.setAttribute(
      "icon",
      allMuted ? "mdi:volume-off" : "mdi:volume-medium"
    );
    const label = button.querySelector("span");
    if (label) {
      label.textContent = action;
    }
  }

  async _setGroupMute() {
    const states = this._muteStates();
    if (!states.length) {
      return;
    }
    const muted = !states.every(
      (state) => this._displayedMuted(state) === true
    );
    this._error = "";
    for (const state of states) {
      this._pendingMutes.set(state.entity_id, {
        value: muted,
        expiresAt: Date.now() + FEEDBACK_TIMEOUT,
      });
      const button = this.shadowRoot.querySelector(
        `[data-mute-id="${CSS.escape(state.entity_id)}"]`
      );
      if (button) {
        this._updateMuteButton(button, muted);
      }
    }
    this._updateGroupControls();
    try {
      await Promise.all(
        states.map((state) =>
          this._hass.callService(
            "media_player",
            "volume_mute",
            { is_volume_muted: muted },
            { entity_id: state.entity_id }
          )
        )
      );
    } catch (error) {
      for (const state of states) {
        this._pendingMutes.delete(state.entity_id);
      }
      this._error = error?.message || String(error);
      this._render();
    }
  }

  async _setMute(event) {
    const button = event.currentTarget;
    const entityId = button.dataset.muteId;
    const muted = button.getAttribute("aria-pressed") !== "true";
    this._error = "";
    this._pendingMutes.set(entityId, {
      value: muted,
      expiresAt: Date.now() + FEEDBACK_TIMEOUT,
    });
    this._updateMuteButton(button, muted);
    try {
      await this._hass.callService(
        "media_player",
        "volume_mute",
        { is_volume_muted: muted },
        { entity_id: entityId }
      );
    } catch (error) {
      this._pendingMutes.delete(entityId);
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
    .modal-shell {
      position: fixed;
      inset: 0;
      width: 100vw;
      max-width: none;
      height: 100vh;
      height: 100dvh;
      max-height: none;
      margin: 0;
      padding: 0;
      border: 0;
      color: inherit;
      background: transparent;
      overflow: hidden;
    }
    .modal-shell::backdrop { background: transparent; }
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
      grid-template-rows: auto auto auto minmax(0, 1fr) auto;
      width: min(520px, calc(100vw - 32px));
      height: min(760px, calc(100vh - 32px));
      height: min(760px, calc(100dvh - 32px));
      max-height: min(760px, calc(100vh - 32px));
      overflow: hidden;
      border-radius: var(--ha-card-border-radius, 16px);
      background: var(--card-background-color, #1c1c1c);
      box-shadow: 0 12px 36px rgba(0, 0, 0, 0.45);
    }
    header {
      display: grid;
      grid-template-columns: 44px 1fr auto;
      align-items: center;
      min-height: 60px;
      padding: 0 10px;
      border-bottom: 1px solid var(--divider-color, #444);
    }
    h2 { margin: 0; font-size: 22px; font-weight: 500; }
    button { font: inherit; cursor: pointer; }
    button:disabled { cursor: default; opacity: 0.5; }
    .icon-button, .text-button {
      border: 0;
      color: var(--primary-text-color, #fff);
      background: transparent;
    }
    .icon-button { width: 40px; height: 40px; font-size: 32px; line-height: 1; }
    .text-button { padding: 8px; color: var(--primary-color, #03a9f4); }
    .error {
      margin: 12px 16px 0;
      padding: 12px;
      border-radius: 8px;
      color: var(--error-color, #db4437);
      background: color-mix(in srgb, var(--error-color, #db4437) 15%, transparent);
    }
    .error[hidden] { display: none; }
    .group-controls {
      padding: 8px 16px 10px;
      border-bottom: 1px solid var(--divider-color, #444);
      background: color-mix(in srgb, var(--primary-color, #03a9f4) 7%, transparent);
    }
    .group-controls-heading {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 8px;
      margin-bottom: 6px;
    }
    .group-controls-heading > div { min-width: 0; }
    .group-name {
      font-size: 15px;
      font-weight: 500;
    }
    .group-target {
      margin-top: 2px;
      color: var(--secondary-text-color, #aaa);
      font-size: 12px;
      font-weight: 400;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .group-count {
      flex: 0 0 auto;
      padding: 2px 7px;
      border-radius: 12px;
      color: var(--secondary-text-color, #aaa);
      background: color-mix(in srgb, var(--primary-text-color, #fff) 8%, transparent);
      font-size: 12px;
    }
    .group-volume-row {
      display: grid;
      grid-template-columns: 104px minmax(0, 1fr) 40px;
      gap: 8px;
      align-items: center;
    }
    .group-volume-row input[type="range"],
    .volume-row input[type="range"] {
      width: 100%;
      min-width: 0;
      height: 24px;
      margin: 0;
      appearance: none;
      -webkit-appearance: none;
      background: transparent;
      accent-color: var(--primary-color, #03a9f4);
    }
    .group-volume-row input[type="range"]::-webkit-slider-runnable-track,
    .volume-row input[type="range"]::-webkit-slider-runnable-track {
      height: 3px;
      border-radius: 2px;
      background: linear-gradient(
        to right,
        var(--primary-color, #03a9f4) 0 var(--volume-value),
        var(--divider-color, #666) var(--volume-value) 100%
      );
    }
    .group-volume-row input[type="range"]::-webkit-slider-thumb,
    .volume-row input[type="range"]::-webkit-slider-thumb {
      width: 14px;
      height: 14px;
      margin-top: -5.5px;
      border: 0;
      border-radius: 50%;
      -webkit-appearance: none;
      background: var(--primary-color, #03a9f4);
    }
    .group-volume-row input[type="range"]::-moz-range-track,
    .volume-row input[type="range"]::-moz-range-track {
      height: 3px;
      border: 0;
      border-radius: 2px;
      background: var(--divider-color, #666);
    }
    .group-volume-row input[type="range"]::-moz-range-progress,
    .volume-row input[type="range"]::-moz-range-progress {
      height: 3px;
      border-radius: 2px;
      background: var(--primary-color, #03a9f4);
    }
    .group-volume-row input[type="range"]::-moz-range-thumb,
    .volume-row input[type="range"]::-moz-range-thumb {
      width: 14px;
      height: 14px;
      border: 0;
      border-radius: 50%;
      background: var(--primary-color, #03a9f4);
    }
    .group-volume-row output {
      color: var(--secondary-text-color, #aaa);
      text-align: end;
    }
    .group-mute-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
      min-height: 32px;
      padding: 4px 8px;
      border: 1px solid var(--divider-color, #555);
      border-radius: 20px;
      color: var(--primary-text-color, #fff);
      background: var(--card-background-color, #1c1c1c);
    }
    .group-mute-button ha-icon { width: 18px; }
    .group-mute-button.muted {
      border-color: var(--primary-color, #03a9f4);
      color: var(--primary-color, #03a9f4);
      background: color-mix(in srgb, var(--primary-color, #03a9f4) 16%, transparent);
    }
    main { min-height: 0; overflow-y: auto; padding: 8px 16px 12px; }
    .preset-section {
      margin-top: 12px;
      padding: 10px;
      border: 1px solid var(--divider-color, #444);
      border-radius: 12px;
      background: color-mix(in srgb, var(--primary-text-color, #fff) 3%, transparent);
    }
    .preset-heading {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .preset-heading h3 {
      margin: 0;
      font-size: 16px;
      font-weight: 500;
    }
    .preset-heading p {
      margin: 3px 0 0;
      color: var(--secondary-text-color, #aaa);
      font-size: 12px;
    }
    .save-current-preset {
      display: inline-flex;
      flex: 0 0 auto;
      align-items: center;
      gap: 4px;
      min-height: 32px;
      padding: 5px 8px;
      border: 1px solid var(--primary-color, #03a9f4);
      border-radius: 18px;
      color: var(--primary-color, #03a9f4);
      background: transparent;
    }
    .save-current-preset ha-icon { width: 18px; }
    .preset-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 8px;
    }
    .preset-card {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      min-height: 54px;
      overflow: hidden;
      border: 1px solid var(--divider-color, #555);
      border-radius: 10px;
      background: var(--card-background-color, #1c1c1c);
    }
    .preset-card.unavailable { opacity: 0.58; }
    .preset-activate {
      display: grid;
      grid-template-columns: 32px minmax(0, 1fr);
      align-items: center;
      gap: 8px;
      min-width: 0;
      padding: 6px 8px;
      border: 0;
      color: var(--primary-text-color, #fff);
      text-align: start;
      background: transparent;
    }
    .preset-activate ha-icon { color: var(--primary-color, #03a9f4); }
    .preset-activate span { min-width: 0; }
    .preset-activate strong,
    .preset-activate small {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .preset-activate strong { font-size: 14px; font-weight: 500; }
    .preset-activate small {
      margin-top: 3px;
      color: var(--secondary-text-color, #aaa);
      font-size: 11px;
    }
    .preset-actions {
      display: grid;
      grid-template-columns: repeat(2, 34px);
      border-left: 1px solid var(--divider-color, #444);
    }
    .preset-action {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      min-height: 27px;
      border: 0;
      color: var(--secondary-text-color, #aaa);
      background: transparent;
    }
    .preset-action:nth-child(odd) {
      border-right: 1px solid var(--divider-color, #444);
    }
    .preset-action:nth-child(-n + 2) {
      border-bottom: 1px solid var(--divider-color, #444);
    }
    .preset-action ha-icon { width: 18px; }
    .preset-delete { color: var(--error-color, #db4437); }
    .preset-empty {
      padding: 10px 2px 2px;
      color: var(--secondary-text-color, #aaa);
      font-size: 13px;
      line-height: 1.4;
    }
    .preset-editor {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .preset-editor input {
      flex: 1 1 190px;
      min-width: 0;
      padding: 9px 11px;
      border: 1px solid var(--divider-color, #555);
      border-radius: 8px;
      color: var(--primary-text-color, #fff);
      background: var(--card-background-color, #1c1c1c);
      font: inherit;
    }
    .preset-save,
    .preset-update,
    .preset-cancel {
      padding: 8px 12px;
      border-radius: 18px;
    }
    .preset-save,
    .preset-update {
      border: 0;
      color: var(--text-primary-color, #fff);
      background: var(--primary-color, #03a9f4);
    }
    .preset-cancel {
      border: 1px solid var(--divider-color, #555);
      color: var(--primary-text-color, #fff);
      background: transparent;
    }
    .preset-notice {
      margin-bottom: 10px;
      padding: 8px 10px;
      border-radius: 7px;
      font-size: 12px;
    }
    .preset-notice[hidden] { display: none; }
    .preset-error {
      color: var(--error-color, #db4437);
      background: color-mix(in srgb, var(--error-color, #db4437) 12%, transparent);
    }
    .preset-success {
      color: var(--primary-color, #03a9f4);
      background: color-mix(in srgb, var(--primary-color, #03a9f4) 12%, transparent);
    }
    .player-section + .player-section {
      margin-top: 12px;
      padding-top: 8px;
      border-top: 1px solid var(--divider-color, #444);
    }
    .player-section-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 0 2px 3px;
    }
    .player-section-heading h3 {
      margin: 0;
      color: var(--secondary-text-color, #aaa);
      font-size: 14px;
      font-weight: 500;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }
    .player-section-heading span {
      min-width: 24px;
      padding: 2px 7px;
      border-radius: 12px;
      color: var(--secondary-text-color, #aaa);
      background: color-mix(in srgb, var(--primary-text-color, #fff) 8%, transparent);
      font-size: 12px;
      text-align: center;
    }
    .empty-players {
      padding: 10px 2px;
      color: var(--secondary-text-color, #aaa);
      font-size: 14px;
    }
    .empty-players[hidden] { display: none; }
    .player { padding: 7px 0 8px; border-bottom: 1px solid var(--divider-color, #444); }
    .player:last-child { border-bottom: 0; }
    .player-heading {
      display: grid;
      grid-template-columns: 30px 1fr auto;
      gap: 8px;
      align-items: center;
    }
    .player-heading > ha-icon { width: 22px; color: var(--primary-text-color, #fff); }
    .player-info { min-width: 0; }
    .player-name { font-size: 16px; overflow: hidden; text-overflow: ellipsis; }
    .player-state {
      margin-top: 1px;
      color: var(--secondary-text-color, #aaa);
      font-size: 12px;
      text-transform: capitalize;
    }
    .volume-row {
      display: grid;
      grid-template-columns: 30px minmax(0, 1fr) 40px;
      gap: 8px;
      align-items: center;
      margin-top: 3px;
    }
    .mute-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      padding: 0;
      border: 0;
      border-radius: 50%;
      color: var(--secondary-text-color, #aaa);
      background: transparent;
    }
    .mute-button ha-icon { width: 19px; }
    .mute-button.muted {
      color: var(--primary-color, #03a9f4);
      background: color-mix(in srgb, var(--primary-color, #03a9f4) 16%, transparent);
    }
    .volume-row output { text-align: end; color: var(--secondary-text-color, #aaa); }
    .volume-row.disabled { opacity: 0.45; }
    .switch { position: relative; display: inline-flex; width: 44px; height: 24px; }
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
      width: 18px;
      height: 18px;
      left: 2px;
      top: 2px;
      border-radius: 50%;
      background: var(--secondary-text-color, #aaa);
      transition: 0.15s;
    }
    .switch input:checked + span {
      border-color: var(--primary-color, #03a9f4);
      background: color-mix(in srgb, var(--primary-color, #03a9f4) 28%, transparent);
    }
    .switch input:checked + span::before {
      transform: translateX(20px);
      background: var(--primary-color, #03a9f4);
    }
    .switch input:disabled + span { opacity: 0.55; }
    footer {
      display: flex;
      flex: 0 0 auto;
      justify-content: flex-end;
      gap: 8px;
      padding: 10px 16px;
      border-top: 1px solid var(--divider-color, #444);
      background: var(--card-background-color, #1c1c1c);
    }
    .apply {
      min-width: 92px;
      padding: 9px 18px;
      border: 0;
      border-radius: 22px;
      color: var(--text-primary-color, #fff);
      background: var(--primary-color, #03a9f4);
    }
    @media (max-width: 600px) {
      .backdrop { align-items: stretch; }
      .dialog {
        position: fixed;
        inset: 0;
        width: 100%;
        height: auto;
        max-height: none;
        border-radius: 0;
      }
      header { grid-template-columns: 40px minmax(0, 1fr) auto; min-height: 56px; }
      h2 { font-size: 20px; white-space: nowrap; }
      main {
        padding-inline: 14px;
        padding-bottom: calc(78px + env(safe-area-inset-bottom, 0px));
      }
      footer {
        position: fixed;
        z-index: 10000;
        right: 0;
        bottom: 0;
        left: 0;
        min-height: 60px;
        padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
        box-shadow: 0 -6px 16px rgba(0, 0, 0, 0.28);
      }
    }
  `;
}

const openControl4GroupDialog = (hass, entityId) => {
  document.querySelector(DIALOG_TAG)?.close?.();
  const dialog = document.createElement(DIALOG_TAG);
  document.body.appendChild(dialog);
  dialog.show(hass, entityId);
  return dialog;
};

const handleMoreInfoGrouping = (event) => {
  const path = event.composedPath();
  const groupingButton = path.find(
    (element) => element?.id === "grouping-button"
  );
  if (!groupingButton) {
    return;
  }
  const mediaControl = path.find(
    (element) => element?.localName === "more-info-media_player"
  );
  const moreInfoDialog = path.find(
    (element) => element?.localName === "ha-more-info-dialog"
  );
  const entityId = mediaControl?.stateObj?.entity_id;
  const hass =
    moreInfoDialog?.hass || document.querySelector("home-assistant")?.hass;
  const state = hass?.states?.[entityId];
  if (
    !state ||
    !supportsFeature(state, FEATURE_GROUPING) ||
    !(
      hass.entities?.[entityId]?.platform === CONTROL4_PLATFORM ||
      Number.isInteger(state.attributes.control4_room_id)
    )
  ) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
  openControl4GroupDialog(hass, entityId);
};

const registerMoreInfoGrouping = () => {
  const key = "__control4AdvancedMoreInfoGrouping";
  if (window[key]) {
    document.removeEventListener("click", window[key], true);
  }
  window[key] = handleMoreInfoGrouping;
  document.addEventListener("click", handleMoreInfoGrouping, true);
};

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
    this._dialog?.close?.();
    this._dialog = openControl4GroupDialog(
      this._hass,
      this._config.entity
    );
    this._dialog.addEventListener("control4-dialog-closed", () => {
      this._dialog = undefined;
    });
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
    this._dialog?.close?.();
    this._dialog = openControl4GroupDialog(
      this._hass,
      this._config.entity
    );
    this._dialog.addEventListener("control4-dialog-closed", () => {
      this._dialog = undefined;
    });
  }
}

class Control4MediaPresetCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._presets = [];
    this._loaded = false;
    this._loading = false;
    this._busy = false;
    this._status = "";
    this._error = "";
    this._statusTimer = undefined;
  }

  static getConfigElement() {
    return document.createElement(PRESET_EDITOR_TAG);
  }

  static getStubConfig() {
    return { preset_id: "" };
  }

  setConfig(config) {
    if (!config || typeof config.preset_id !== "string") {
      throw new Error("Select a Control4 group preset");
    }
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    if (!this._loaded && !this._loading) {
      void this._loadPresets();
    }
  }

  connectedCallback() {
    if (this._hass && !this._loaded && !this._loading) {
      void this._loadPresets();
    }
  }

  disconnectedCallback() {
    window.clearTimeout(this._statusTimer);
    this._statusTimer = undefined;
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { rows: 2, columns: 6, min_rows: 2, min_columns: 3 };
  }

  _sendPresetMessage(message) {
    const send = this._hass?.connection?.sendMessagePromise;
    if (typeof send !== "function") {
      throw new Error("Home Assistant WebSocket connection is unavailable");
    }
    return send.call(this._hass.connection, message);
  }

  async _loadPresets() {
    this._loading = true;
    this._render();
    try {
      const response = await this._sendPresetMessage({
        type: PRESET_LIST_COMMAND,
      });
      this._presets = Array.isArray(response?.presets) ? response.presets : [];
      this._error = "";
    } catch (error) {
      this._error = error?.message || String(error);
    } finally {
      this._loading = false;
      this._loaded = true;
      if (this.isConnected) {
        this._render();
      }
    }
  }

  _leader(preset) {
    const exact = this._hass?.states?.[preset.leader_entity_id];
    if (
      exact &&
      Number(exact.attributes.control4_room_id) === Number(preset.leader_room_id)
    ) {
      return exact;
    }
    return Object.values(this._hass?.states || {}).find(
      (state) =>
        state.entity_id.startsWith("media_player.") &&
        Number(state.attributes.control4_room_id) === Number(preset.leader_room_id)
    );
  }

  _render() {
    if (!this._config || !this._hass) {
      return;
    }
    const preset = this._presets.find(
      (item) => item.id === this._config.preset_id
    );
    const leader = preset ? this._leader(preset) : undefined;
    const title = this._config.name || preset?.name || "Control4 group preset";
    const count = preset?.members?.length || 0;
    const groupDetail = preset
      ? `${count} room${count === 1 ? "" : "s"} · ${
          leader ? stateName(leader) : "leader unavailable"
        } leader`
      : this._loading
        ? "Loading saved preset…"
        : this._config.preset_id
          ? "Saved preset not found"
          : "Choose a saved preset in card settings";
    const mediaDetail = preset?.media?.title || "Saved source and room settings";
    const unavailable = Boolean(preset && !leader);
    const disabled = !preset || unavailable || this._busy;
    const icon = this._busy ? "mdi:loading" : "mdi:play-circle-outline";
    const action = this._busy ? "Activating…" : "Activate";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        button {
          display: grid;
          grid-template-columns: 40px minmax(0, 1fr) auto;
          gap: 12px;
          align-items: center;
          width: 100%;
          min-height: 82px;
          padding: 12px 16px;
          border: 0;
          color: var(--primary-text-color);
          text-align: start;
          background: transparent;
          cursor: pointer;
        }
        button:disabled { cursor: default; opacity: 0.62; }
        .icon {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 40px;
          height: 40px;
          border-radius: 50%;
          color: var(--primary-color);
          background: color-mix(in srgb, var(--primary-color) 14%, transparent);
        }
        .icon.busy ha-icon { animation: spin 1s linear infinite; }
        .details { min-width: 0; }
        .title, .summary, .media, .notice {
          display: block;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .title { font-size: 16px; font-weight: 500; }
        .summary, .media, .notice {
          margin-top: 2px;
          color: var(--secondary-text-color);
          font-size: 12px;
        }
        .notice {
          overflow: visible;
          text-overflow: clip;
          white-space: normal;
          overflow-wrap: anywhere;
          line-height: 1.35;
          color: var(--primary-color);
        }
        .notice.error { color: var(--error-color); }
        .action { color: var(--primary-color); font-weight: 500; }
        @keyframes spin { to { transform: rotate(360deg); } }
      </style>
      <ha-card>
        <button
          aria-label="Activate ${escapeHtml(title)} group preset"
          aria-busy="${this._busy ? "true" : "false"}"
          ${disabled ? "disabled" : ""}
        >
          <span class="icon ${this._busy ? "busy" : ""}">
            <ha-icon icon="${icon}"></ha-icon>
          </span>
          <span class="details">
            <span class="title">${escapeHtml(title)}</span>
            <span class="summary">${escapeHtml(groupDetail)}</span>
            <span class="media">${escapeHtml(mediaDetail)}</span>
            ${
              this._error
                ? `<span class="notice error">${escapeHtml(this._error)}</span>`
                : this._status
                  ? `<span class="notice">${escapeHtml(this._status)}</span>`
                  : ""
            }
          </span>
          <span class="action">${action}</span>
        </button>
      </ha-card>`;
    this.shadowRoot.querySelector("button")?.addEventListener("click", () =>
      this._activate()
    );
  }

  async _activate() {
    if (this._busy || !this._config?.preset_id) {
      return;
    }
    const preset = this._presets.find(
      (item) => item.id === this._config.preset_id
    );
    if (!preset) {
      return;
    }
    window.clearTimeout(this._statusTimer);
    this._busy = true;
    this._error = "";
    this._status = `Activating ${preset.name}…`;
    this._render();
    try {
      await this._sendPresetMessage({
        type: PRESET_ACTIVATE_COMMAND,
        preset_id: preset.id,
      });
      this._status = `${preset.name} activated`;
      this._statusTimer = window.setTimeout(() => {
        this._status = "";
        if (this.isConnected) {
          this._render();
        }
      }, 4000);
    } catch (error) {
      this._status = "";
      this._error = error?.message || String(error);
    } finally {
      this._busy = false;
      if (this.isConnected) {
        this._render();
      }
    }
  }
}

class Control4MediaPresetCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._config = { preset_id: "" };
    this._presets = [];
    this._loaded = false;
    this._loading = false;
    this._error = "";
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    if (!this._loaded && !this._loading) {
      void this._loadPresets();
    }
  }

  setConfig(config) {
    this._config = { preset_id: "", ...config };
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

  async _loadPresets() {
    this._loading = true;
    this._render();
    try {
      const send = this._hass?.connection?.sendMessagePromise;
      if (typeof send !== "function") {
        throw new Error("Home Assistant WebSocket connection is unavailable");
      }
      const response = await send.call(this._hass.connection, {
        type: PRESET_LIST_COMMAND,
      });
      this._presets = Array.isArray(response?.presets) ? response.presets : [];
      this._error = "";
    } catch (error) {
      this._error = error?.message || String(error);
    } finally {
      this._loading = false;
      this._loaded = true;
      if (this.isConnected) {
        this._render();
      }
    }
  }

  _render() {
    if (!this._hass) {
      return;
    }
    const missingSelection =
      this._config.preset_id &&
      !this._presets.some((preset) => preset.id === this._config.preset_id);
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
        .message { color: var(--secondary-text-color); font-size: 13px; }
        .error { color: var(--error-color); }
      </style>
      <label>
        Group preset
        <select ${this._loading ? "disabled" : ""}>
          <option value="">${
            this._loading ? "Loading saved presets…" : "Select a saved preset"
          }</option>
          ${
            missingSelection
              ? `<option value="${escapeHtml(this._config.preset_id)}" selected>Saved preset not found</option>`
              : ""
          }
          ${this._presets
            .map(
              (preset) => `<option value="${escapeHtml(preset.id)}" ${
                preset.id === this._config.preset_id ? "selected" : ""
              }>${escapeHtml(preset.name)}</option>`
            )
            .join("")}
        </select>
      </label>
      <label>
        Card title (optional)
        <input type="text" value="${escapeHtml(this._config.name || "")}" />
      </label>
      ${
        this._error
          ? `<div class="message error">${escapeHtml(this._error)}</div>`
          : !this._loading && !this._presets.length
            ? '<div class="message">Create a preset from the Control4 media group popup first.</div>'
            : ""
      }`;
    this.shadowRoot.querySelector("select")?.addEventListener("change", (event) =>
      this._changed({ preset_id: event.currentTarget.value })
    );
    this.shadowRoot.querySelector("input")?.addEventListener("change", (event) =>
      this._changed({ name: event.currentTarget.value.trim() })
    );
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
  if (!customElements.get(PRESET_EDITOR_TAG)) {
    customElements.define(PRESET_EDITOR_TAG, Control4MediaPresetCardEditor);
  }
  if (!customElements.get(PRESET_CARD_TAG)) {
    customElements.define(PRESET_CARD_TAG, Control4MediaPresetCard);
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
  if (!window.customCards.some((card) => card.type === PRESET_CARD_TAG)) {
    window.customCards.push({
      type: PRESET_CARD_TAG,
      name: "Control4 Media Preset",
      description: "One-tap Control4 source, room group, volume, and mute recall",
      preview: true,
      documentationURL: "https://github.com/eonnet/control4-ha#control4-media-preset-card",
    });
  }
};

// Register immediately for normal Lovelace resource loading. Extra modules can
// execute before HA replaces the native custom-element registry with its scoped
// registry, so check the live registry again after the application is defined.
registerCard();
registerMoreInfoGrouping();
(async () => {
  for (let attempts = 0; attempts < 600; attempts += 1) {
    if (window.customElements?.get("home-assistant")) {
      registerCard();
      registerMoreInfoGrouping();
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 100));
  }
})();
