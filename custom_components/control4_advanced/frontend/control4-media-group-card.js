const CARD_TAG = "control4-media-group-card";
const EDITOR_TAG = "control4-media-group-card-editor";
const PLAYER_CARD_TAG = "control4-media-player-card";
const PRESET_CARD_TAG = "control4-media-preset-card";
const PRESET_EDITOR_TAG = "control4-media-preset-card-editor";
const FAVORITES_CARD_TAG = "control4-media-favorites-card";
const FAVORITES_EDITOR_TAG = "control4-media-favorites-card-editor";
const ROOM_DASHBOARD_CARD_TAG = "control4-room-dashboard-card";
const ROOM_DASHBOARD_EDITOR_TAG = "control4-room-dashboard-card-editor";
const DIALOG_TAG = "control4-media-group-dialog";
const CONTROL4_PLATFORM = "control4_advanced";
const FEATURE_VOLUME_SET = 4;
const FEATURE_VOLUME_MUTE = 8;
const FEATURE_BROWSE_MEDIA = 131072;
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

const control4BrowsePlayers = (hass) =>
  Object.values(hass?.states || {})
    .filter(
      (state) =>
        state.entity_id.startsWith("media_player.") &&
        (hass.entities?.[state.entity_id]?.platform === CONTROL4_PLATFORM ||
          Number.isInteger(state.attributes.control4_room_id)) &&
        supportsFeature(state, FEATURE_BROWSE_MEDIA)
    )
    .sort((left, right) => stateName(left).localeCompare(stateName(right)));

const entityAreaId = (hass, entityId) => {
  const entry = hass?.entities?.[entityId];
  if (entry?.area_id) {
    return entry.area_id;
  }
  return entry?.device_id ? hass?.devices?.[entry.device_id]?.area_id : undefined;
};

const entitiesInArea = (hass, areaId, domain) =>
  !areaId
    ? []
    : Object.values(hass?.states || {})
        .filter(
          (state) =>
            state.entity_id.startsWith(`${domain}.`) &&
            entityAreaId(hass, state.entity_id) === areaId
        )
        .sort((left, right) => stateName(left).localeCompare(stateName(right)));

const browseControl4Media = (hass, entityId, item) => {
  if (typeof hass?.callWS !== "function") {
    throw new Error("Home Assistant WebSocket connection is unavailable");
  }
  const message = {
    type: "media_player/browse_media",
    entity_id: entityId,
  };
  if (item?.media_content_id) {
    message.media_content_id = item.media_content_id;
  }
  if (item?.media_content_type) {
    message.media_content_type = item.media_content_type;
  }
  return hass.callWS(message);
};

const control4MediaSectionFor = (item) => {
  const mediaId = String(item?.media_content_id || "").toLowerCase();
  if (mediaId.includes(":sources:watch")) {
    return { key: "watch", title: "Watch", icon: "mdi:television-play" };
  }
  if (mediaId.includes(":sources:listen")) {
    return { key: "listen", title: "Listen", icon: "mdi:music" };
  }
  if (mediaId.startsWith("control4_advanced:tidal:")) {
    return { key: "tidal", title: "TIDAL", icon: "mdi:playlist-music" };
  }
  if (mediaId.startsWith("control4_advanced:tunein:")) {
    return { key: "tunein", title: "TuneIn", icon: "mdi:radio" };
  }
  return undefined;
};

const expandControl4MediaFolder = async (
  hass,
  entityId,
  folder,
  section,
  inheritedThumbnail,
  depth = 0
) => {
  const node = folder.can_expand
    ? await browseControl4Media(hass, entityId, folder)
    : folder;
  const thumbnail = node.thumbnail || folder.thumbnail || inheritedThumbnail;
  const items = [];
  for (const child of node.children || []) {
    if (child.can_play) {
      items.push({
        ...child,
        key: `${child.media_content_type}|${child.media_content_id}`,
        section: section.key,
        sectionTitle: section.title,
        icon: section.icon,
        subtitle:
          section.key === "tidal" || section.key === "tunein"
            ? node.title
            : "",
        thumbnail: child.thumbnail || thumbnail,
      });
    }
    if (child.can_expand && depth < 2) {
      items.push(
        ...(await expandControl4MediaFolder(
          hass,
          entityId,
          child,
          section,
          child.thumbnail || thumbnail,
          depth + 1
        ))
      );
    }
  }
  return items;
};

const loadControl4MediaSections = async (hass, entityId) => {
  const root = await browseControl4Media(hass, entityId);
  const folders = (root.children || [])
    .map((folder) => ({ folder, section: control4MediaSectionFor(folder) }))
    .filter(({ section }) => section);
  const results = await Promise.allSettled(
    folders.map(async ({ folder, section }) => ({
      ...section,
      items: await expandControl4MediaFolder(
        hass,
        entityId,
        folder,
        section,
        folder.thumbnail
      ),
    }))
  );
  return {
    sections: results
      .filter((result) => result.status === "fulfilled")
      .map((result) => result.value)
      .filter((section) => section.items.length),
    errors: results
      .filter((result) => result.status === "rejected")
      .map((result) => result.reason),
  };
};

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

class Control4MediaFavoritesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._allSections = [];
    this._sections = [];
    this._loadedEntity = undefined;
    this._loading = false;
    this._error = "";
    this._status = "";
    this._busyId = undefined;
    this._pendingId = undefined;
    this._loadSequence = 0;
    this._statusTimer = undefined;
  }

  static getConfigElement() {
    return document.createElement(FAVORITES_EDITOR_TAG);
  }

  static getStubConfig(hass) {
    return { entity: control4BrowsePlayers(hass)[0]?.entity_id || "" };
  }

  setConfig(config) {
    if (!config?.entity || !String(config.entity).startsWith("media_player.")) {
      throw new Error("Select a Control4 media player entity");
    }
    const entityChanged = config.entity !== this._config?.entity;
    this._config = { ...config };
    if (entityChanged) {
      this._loadSequence += 1;
      this._loadedEntity = undefined;
      this._loading = false;
      this._allSections = [];
      this._sections = [];
      this._error = "";
    } else {
      this._sections = this._filterSections(this._allSections);
    }
    this._render();
    this._maybeLoad();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    this._maybeLoad();
  }

  connectedCallback() {
    this._maybeLoad();
  }

  disconnectedCallback() {
    this._loadSequence += 1;
    this._loading = false;
    this._loadedEntity = undefined;
    window.clearTimeout(this._statusTimer);
    this._statusTimer = undefined;
  }

  getCardSize() {
    const itemCount = this._sections.reduce(
      (total, section) => total + section.items.length,
      0
    );
    return Math.max(2, Math.min(8, 1 + Math.ceil(itemCount / 2)));
  }

  getGridOptions() {
    return { rows: 4, columns: 6, min_rows: 2, min_columns: 3 };
  }

  _maybeLoad() {
    if (
      !this.isConnected ||
      !this._hass ||
      !this._config ||
      this._loading ||
      this._loadedEntity === this._config.entity
    ) {
      return;
    }
    void this._loadFavorites();
  }

  _filterSections(sections) {
    if (!Array.isArray(this._config?.media_ids)) {
      return sections;
    }
    const selected = new Set(this._config.media_ids);
    return sections
      .map((section) => ({
        ...section,
        items: section.items.filter((item) =>
          selected.has(item.media_content_id)
        ),
      }))
      .filter((section) => section.items.length);
  }

  async _loadFavorites() {
    const entityId = this._config.entity;
    const sequence = ++this._loadSequence;
    this._loading = true;
    this._error = "";
    this._render();
    try {
      const loaded = await loadControl4MediaSections(
        this._hass,
        entityId
      );
      if (sequence !== this._loadSequence || !this.isConnected) {
        return;
      }
      this._allSections = loaded.sections;
      this._sections = this._filterSections(this._allSections);
      this._error = loaded.errors.length
        ? this._sections.length
          ? "Some media favorites could not be loaded. Tap refresh to try again."
          : loaded.errors[0]?.message || String(loaded.errors[0])
        : "";
      this._loadedEntity = entityId;
    } catch (error) {
      if (sequence !== this._loadSequence || !this.isConnected) {
        return;
      }
      this._allSections = [];
      this._sections = [];
      this._error = error?.message || String(error);
      this._loadedEntity = entityId;
    } finally {
      if (sequence === this._loadSequence) {
        this._loading = false;
        if (this.isConnected) {
          this._render();
        }
      }
    }
  }

  _refresh() {
    if (this._loading) {
      return;
    }
    this._loadedEntity = undefined;
    this._error = "";
    this._maybeLoad();
  }

  _renderArtwork(item) {
    const fallback = `<ha-icon icon="${escapeHtml(item.icon)}"></ha-icon>`;
    if (!item.thumbnail) {
      return `<span class="art fallback">${fallback}</span>`;
    }
    return `<span class="art"><img src="${escapeHtml(
      item.thumbnail
    )}" alt="" loading="lazy" />${fallback}</span>`;
  }

  _renderItem(item, activeId) {
    const active = item.media_content_id === activeId || item.key === this._pendingId;
    const busy = item.key === this._busyId;
    const detail = item.subtitle
      ? `<span class="item-detail">${escapeHtml(item.subtitle)}</span>`
      : "";
    return `
      <button
        class="media-item ${active ? "active" : ""}"
        data-media-key="${escapeHtml(item.key)}"
        aria-label="Play ${escapeHtml(item.title)} in ${escapeHtml(
          stateName(this._hass.states[this._config.entity])
        )}"
        aria-busy="${busy ? "true" : "false"}"
        ${this._busyId ? "disabled" : ""}
      >
        ${this._renderArtwork(item)}
        <span class="item-copy">
          <span class="item-title">${escapeHtml(item.title)}</span>
          ${detail}
        </span>
        <ha-icon class="play ${busy ? "busy" : ""}" icon="${
          busy ? "mdi:loading" : active ? "mdi:play-circle" : "mdi:play"
        }"></ha-icon>
      </button>`;
  }

  _render() {
    if (!this._config || !this._hass) {
      return;
    }
    const state = this._hass.states[this._config.entity];
    const title = this._config.name || "Media favorites";
    const activeId = state?.attributes?.control4_media_content_id;
    const body = !state
      ? `<div class="empty error">Entity ${escapeHtml(
          this._config.entity
        )} was not found</div>`
      : this._loading && !this._sections.length
        ? '<div class="empty"><ha-icon class="spinner" icon="mdi:loading"></ha-icon> Loading media…</div>'
        : this._sections.length
          ? this._sections
              .map(
                (section) => `
                  <section>
                    <div class="section-heading">
                      <ha-icon icon="${escapeHtml(section.icon)}"></ha-icon>
                      <span>${escapeHtml(section.title)}</span>
                      <span class="count">${section.items.length}</span>
                    </div>
                    <div class="media-grid">
                      ${section.items
                        .map((item) => this._renderItem(item, activeId))
                        .join("")}
                    </div>
                  </section>`
              )
              .join("")
          : `<div class="empty">${
              Array.isArray(this._config.media_ids)
                ? "No selected media items are available for this player."
                : "No media favorites are available for this player."
            }</div>`;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        * { box-sizing: border-box; }
        ha-card { overflow: hidden; color: var(--primary-text-color); }
        .header {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 14px 14px 8px 16px;
        }
        .heading { min-width: 0; flex: 1; }
        .card-title {
          overflow: hidden;
          font-size: 18px;
          font-weight: 500;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .room {
          overflow: hidden;
          margin-top: 2px;
          color: var(--secondary-text-color);
          font-size: 12px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .refresh {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 40px;
          height: 40px;
          border: 0;
          border-radius: 50%;
          color: var(--secondary-text-color);
          background: transparent;
          cursor: pointer;
        }
        .refresh:hover { background: var(--secondary-background-color); }
        .refresh:disabled { cursor: default; opacity: 0.55; }
        .content { padding: 0 12px 14px; }
        section + section { margin-top: 18px; }
        .section-heading {
          display: flex;
          align-items: center;
          gap: 7px;
          min-height: 28px;
          padding: 0 4px 4px;
          color: var(--secondary-text-color);
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.04em;
          text-transform: uppercase;
        }
        .section-heading ha-icon { --mdc-icon-size: 17px; }
        .count {
          min-width: 20px;
          margin-left: auto;
          padding: 1px 6px;
          border-radius: 10px;
          text-align: center;
          background: var(--secondary-background-color);
        }
        .media-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }
        .media-item {
          position: relative;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 7px;
          width: 100%;
          height: clamp(132px, 36vw, 152px);
          padding: 9px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 12px;
          color: var(--primary-text-color);
          text-align: center;
          background: color-mix(
            in srgb,
            var(--primary-text-color, #fff) 3%,
            transparent
          );
          cursor: pointer;
        }
        .media-item:hover { background: var(--secondary-background-color); }
        .media-item.active {
          border-color: color-mix(in srgb, var(--primary-color) 55%, var(--divider-color));
          background: color-mix(in srgb, var(--primary-color) 10%, transparent);
        }
        .media-item:disabled { cursor: default; opacity: 0.65; }
        .art {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          flex: 0 0 auto;
          width: 52px;
          height: 52px;
          overflow: hidden;
          border-radius: 12px;
          color: var(--primary-color);
          background: var(--secondary-background-color);
        }
        .art img { width: 100%; height: 100%; object-fit: contain; }
        .art ha-icon { display: none; --mdc-icon-size: 30px; }
        .art.fallback img { display: none; }
        .art.fallback ha-icon { display: block; }
        .item-copy {
          display: flex;
          flex-direction: column;
          align-items: center;
          width: 100%;
          min-width: 0;
        }
        .item-title, .item-detail {
          max-width: 100%;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .item-title {
          display: -webkit-box;
          font-size: 16px;
          font-weight: 500;
          line-height: 1.15;
          white-space: normal;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
        }
        .item-detail {
          margin-top: 2px;
          color: var(--secondary-text-color);
          font-size: 12px;
          white-space: nowrap;
        }
        .play {
          position: absolute;
          top: 9px;
          right: 9px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 28px;
          height: 28px;
          border-radius: 50%;
          color: var(--primary-color);
          background: color-mix(in srgb, var(--primary-color) 12%, transparent);
          --mdc-icon-size: 19px;
        }
        .spinner, .busy { animation: spin 1s linear infinite; }
        .empty, .notice {
          padding: 16px 8px;
          color: var(--secondary-text-color);
          text-align: center;
        }
        .notice { padding: 7px 4px 2px; font-size: 12px; }
        .notice.error, .empty.error { color: var(--error-color); }
        @keyframes spin { to { transform: rotate(360deg); } }
      </style>
      <ha-card>
        <div class="header">
          <div class="heading">
            <div class="card-title">${escapeHtml(title)}</div>
            <div class="room">${escapeHtml(state ? stateName(state) : this._config.entity)}</div>
          </div>
          <button class="refresh" aria-label="Refresh media favorites" title="Refresh" ${
            this._loading ? "disabled" : ""
          }>
            <ha-icon class="${this._loading ? "spinner" : ""}" icon="mdi:refresh"></ha-icon>
          </button>
        </div>
        <div class="content">
          ${body}
          ${
            this._error
              ? `<div class="notice error">${escapeHtml(this._error)}</div>`
              : this._status
                ? `<div class="notice">${escapeHtml(this._status)}</div>`
                : ""
          }
        </div>
      </ha-card>`;
    this.shadowRoot.querySelector(".refresh")?.addEventListener("click", () =>
      this._refresh()
    );
    for (const image of this.shadowRoot.querySelectorAll(".art img")) {
      image.addEventListener("error", () =>
        image.parentElement?.classList.add("fallback")
      );
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-media-key]")) {
      button.addEventListener("click", () => {
        const item = this._sections
          .flatMap((section) => section.items)
          .find((candidate) => candidate.key === button.dataset.mediaKey);
        if (item) {
          void this._play(item);
        }
      });
    }
  }

  async _play(item) {
    if (this._busyId || !this._hass || !this._config) {
      return;
    }
    window.clearTimeout(this._statusTimer);
    this._busyId = item.key;
    this._error = "";
    this._status = `Starting ${item.title}…`;
    this._render();
    try {
      await this._hass.callService(
        "media_player",
        "play_media",
        {
          media_content_id: item.media_content_id,
          media_content_type: item.media_content_type,
        },
        { entity_id: this._config.entity }
      );
      this._pendingId = item.key;
      this._status = `${item.title} started`;
      this._statusTimer = window.setTimeout(() => {
        this._pendingId = undefined;
        this._status = "";
        if (this.isConnected) {
          this._render();
        }
      }, 4000);
    } catch (error) {
      this._status = "";
      this._error = error?.message || String(error);
    } finally {
      this._busyId = undefined;
      if (this.isConnected) {
        this._render();
      }
    }
  }
}

class Control4RoomDashboardCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._sections = [];
    this._loadedEntity = undefined;
    this._loading = false;
    this._drawerOpen = false;
    this._view = "home";
    this._lightTab = "lights";
    this._error = "";
    this._status = "";
    this._busyId = undefined;
    this._loadSequence = 0;
    this._statusTimer = undefined;
    this._pendingLightLevels = new Map();
  }

  static getConfigElement() {
    return document.createElement(ROOM_DASHBOARD_EDITOR_TAG);
  }

  static getStubConfig(hass) {
    const entity = control4BrowsePlayers(hass)[0]?.entity_id || "";
    const areaId = entityAreaId(hass, entity);
    return {
      entity,
      ...(areaId ? { area_id: areaId } : {}),
    };
  }

  setConfig(config) {
    if (!config?.entity || !String(config.entity).startsWith("media_player.")) {
      throw new Error("Select a Control4 media player entity");
    }
    const entityChanged = config.entity !== this._config?.entity;
    this._config = { ...config };
    if (entityChanged) {
      this._loadSequence += 1;
      this._sections = [];
      this._loadedEntity = undefined;
      this._loading = false;
      this._error = "";
    }
    this._render();
    this._maybeLoad();
  }

  set hass(hass) {
    this._hass = hass;
    for (const [entityId, pending] of this._pendingLightLevels) {
      const state = hass?.states?.[entityId];
      const reported =
        typeof state?.attributes?.brightness === "number"
          ? Math.round((state.attributes.brightness / 255) * 100)
          : state?.state === "on"
            ? 100
            : 0;
      if (
        Math.abs(reported - pending.value) <= 1 ||
        Date.now() >= pending.expires
      ) {
        this._pendingLightLevels.delete(entityId);
      }
    }
    this._render();
    this._maybeLoad();
  }

  connectedCallback() {
    this._maybeLoad();
  }

  disconnectedCallback() {
    this._loadSequence += 1;
    this._loading = false;
    this._loadedEntity = undefined;
    window.clearTimeout(this._statusTimer);
    this._statusTimer = undefined;
  }

  getCardSize() {
    return 12;
  }

  getGridOptions() {
    return { rows: 12, columns: 12, min_rows: 6, min_columns: 6 };
  }

  _areaId() {
    return (
      this._config?.area_id ||
      entityAreaId(this._hass, this._config?.entity)
    );
  }

  _areaName() {
    const area = this._hass?.areas?.[this._areaId()];
    return this._config?.name || area?.name || stateName(
      this._hass?.states?.[this._config?.entity]
    );
  }

  _background() {
    return (
      this._config?.background ||
      this._hass?.areas?.[this._areaId()]?.picture ||
      ""
    );
  }

  _areaEntities(domain) {
    return entitiesInArea(this._hass, this._areaId(), domain);
  }

  _domainEntities(domain) {
    return Object.values(this._hass?.states || {})
      .filter((state) => state.entity_id.startsWith(`${domain}.`))
      .sort((left, right) => stateName(left).localeCompare(stateName(right)));
  }

  _items(sectionKeys) {
    const keys = new Set(sectionKeys);
    return this._sections
      .filter((section) => keys.has(section.key))
      .flatMap((section) => section.items);
  }

  _maybeLoad() {
    if (
      !this.isConnected ||
      !this._hass ||
      !this._config ||
      this._loading ||
      this._loadedEntity === this._config.entity
    ) {
      return;
    }
    void this._loadMedia();
  }

  async _loadMedia() {
    const entityId = this._config.entity;
    const sequence = ++this._loadSequence;
    this._loading = true;
    this._error = "";
    this._render();
    try {
      const loaded = await loadControl4MediaSections(this._hass, entityId);
      if (sequence !== this._loadSequence || !this.isConnected) {
        return;
      }
      this._sections = loaded.sections;
      this._error = loaded.errors.length
        ? this._sections.length
          ? "Some Control4 media could not be loaded."
          : loaded.errors[0]?.message || String(loaded.errors[0])
        : "";
      this._loadedEntity = entityId;
    } catch (error) {
      if (sequence !== this._loadSequence || !this.isConnected) {
        return;
      }
      this._sections = [];
      this._error = error?.message || String(error);
      this._loadedEntity = entityId;
    } finally {
      if (sequence === this._loadSequence) {
        this._loading = false;
        if (this.isConnected) {
          this._render();
        }
      }
    }
  }

  _mediaArtwork(item, className = "") {
    const fallback = `<ha-icon icon="${escapeHtml(
      item.icon || "mdi:play-circle-outline"
    )}"></ha-icon>`;
    if (!item.thumbnail) {
      return `<span class="c4-art fallback ${className}">${fallback}</span>`;
    }
    return `<span class="c4-art ${className}"><img src="${escapeHtml(
      item.thumbnail
    )}" alt="" loading="lazy" />${fallback}</span>`;
  }

  _renderMediaTile(item, compact = false) {
    const state = this._hass.states[this._config.entity];
    const active =
      state?.attributes?.control4_media_content_id === item.media_content_id;
    const busy = this._busyId === item.key;
    return `
      <button
        type="button"
        class="c4-media-tile ${compact ? "compact" : ""} ${
          active ? "active" : ""
        }"
        data-c4-media-key="${escapeHtml(item.key)}"
        aria-label="Play ${escapeHtml(item.title)}"
        aria-busy="${busy ? "true" : "false"}"
        ${this._busyId ? "disabled" : ""}
      >
        ${this._mediaArtwork(item)}
        <span class="c4-media-title">${escapeHtml(item.title)}</span>
        ${
          item.subtitle
            ? `<span class="c4-media-detail">${escapeHtml(item.subtitle)}</span>`
            : ""
        }
        ${
          busy
            ? '<ha-icon class="tile-busy" icon="mdi:loading"></ha-icon>'
            : ""
        }
      </button>`;
  }

  _renderClimateSummary() {
    const climates = this._areaEntities("climate").slice(0, 2);
    if (!climates.length) {
      return "";
    }
    return `
      <div class="climate-summary">
        ${climates
          .map((state) => {
            const current = state.attributes.current_temperature;
            const target = state.attributes.temperature;
            const value =
              typeof current === "number"
                ? current
                : typeof target === "number"
                  ? target
                  : "—";
            return `
              <button
                type="button"
                class="climate-orb"
                data-more-info="${escapeHtml(state.entity_id)}"
              >
                <span class="orb-value">${escapeHtml(value)}</span>
                <span class="orb-unit">${escapeHtml(
                  state.attributes.temperature_unit || "°"
                )}</span>
                <span class="orb-name">${escapeHtml(stateName(state))}</span>
              </button>`;
          })
          .join("")}
      </div>`;
  }

  _renderLauncher(view, icon, label) {
    return `
      <button type="button" class="launcher" data-c4-view="${view}">
        <ha-icon icon="${icon}"></ha-icon>
        <span>${label}</span>
      </button>`;
  }

  _renderHome() {
    const favorites = this._items(["watch", "listen", "tidal", "tunein"]).slice(
      0,
      12
    );
    return `
      <div class="c4-view home-view">
        ${this._renderClimateSummary()}
        <div class="launcher-grid">
          ${this._renderLauncher("watch", "mdi:television-classic", "Watch")}
          ${this._renderLauncher("listen", "mdi:speaker-wireless", "Listen")}
          ${this._renderLauncher("lighting", "mdi:lightbulb", "Lighting")}
        </div>
        ${
          this._loading && !favorites.length
            ? '<div class="view-message"><ha-icon class="spin" icon="mdi:loading"></ha-icon> Loading media…</div>'
            : favorites.length
              ? `<div class="home-media-grid">${favorites
                  .map((item) => this._renderMediaTile(item, true))
                  .join("")}</div>`
              : '<div class="view-message">No room favorites are available.</div>'
        }
      </div>`;
  }

  _renderMediaView(kind) {
    const isWatch = kind === "watch";
    const keys = isWatch ? ["watch"] : ["listen", "tidal", "tunein"];
    const sections = this._sections.filter((section) => keys.includes(section.key));
    const player = this._hass.states[this._config.entity];
    const recentTitle =
      player?.attributes?.media_title ||
      player?.attributes?.control4_media_title ||
      player?.attributes?.source;
    return `
      <div class="c4-view media-view">
        ${
          !isWatch && recentTitle
            ? `
              <section class="recent-section">
                <h3>Recently Played</h3>
                <button
                  type="button"
                  class="recent-media"
                  data-more-info="${escapeHtml(player.entity_id)}"
                >
                  ${
                    player.attributes.entity_picture
                      ? `<img src="${escapeHtml(
                          player.attributes.entity_picture
                        )}" alt="" />`
                      : '<ha-icon icon="mdi:music-circle"></ha-icon>'
                  }
                  <span>
                    <strong>${escapeHtml(recentTitle)}</strong>
                    <small>${escapeHtml(
                      player.attributes.media_artist || "Now playing"
                    )}</small>
                  </span>
                </button>
              </section>`
            : ""
        }
        ${
          this._loading && !sections.length
            ? '<div class="view-message"><ha-icon class="spin" icon="mdi:loading"></ha-icon> Loading media…</div>'
            : sections.length
              ? sections
                  .map(
                    (section) => `
                      <section class="media-section">
                        <h3>${
                          isWatch ? "" : escapeHtml(section.title)
                        }</h3>
                        <div class="large-media-grid">
                          ${section.items
                            .map((item) => this._renderMediaTile(item))
                            .join("")}
                        </div>
                      </section>`
                  )
                  .join("")
              : `<div class="view-message">No ${
                  isWatch ? "Watch sources" : "Listen sources"
                } are available.</div>`
        }
      </div>`;
  }

  _renderLightRow(state) {
    const on = state.state === "on";
    const reportedBrightness =
      typeof state.attributes.brightness === "number"
        ? Math.round((state.attributes.brightness / 255) * 100)
        : on
          ? 100
          : 0;
    const pending = this._pendingLightLevels.get(state.entity_id);
    if (pending && Date.now() >= pending.expires) {
      this._pendingLightLevels.delete(state.entity_id);
    }
    const brightness =
      pending && Date.now() < pending.expires
        ? pending.value
        : reportedBrightness;
    const dimmable =
      typeof state.attributes.brightness === "number" ||
      (state.attributes.supported_color_modes || []).includes("brightness");
    return `
      <article class="light-row ${on ? "on" : ""}">
        <button
          type="button"
          class="light-toggle"
          data-light-toggle="${escapeHtml(state.entity_id)}"
          aria-label="Toggle ${escapeHtml(stateName(state))}"
        >
          <ha-icon icon="${on ? "mdi:lightbulb" : "mdi:lightbulb-outline"}"></ha-icon>
        </button>
        <div class="light-details">
          <button
            type="button"
            class="entity-name"
            data-more-info="${escapeHtml(state.entity_id)}"
          >${escapeHtml(stateName(state))}</button>
          ${
            dimmable
              ? `
                <div class="light-level">
                  <output data-light-output="${escapeHtml(state.entity_id)}">${brightness}</output>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    value="${brightness}"
                    data-light-slider="${escapeHtml(state.entity_id)}"
                    aria-label="${escapeHtml(stateName(state))} brightness"
                  />
                </div>`
              : `<span class="binary-state">${on ? "On" : "Off"}</span>`
          }
        </div>
        <button
          type="button"
          class="row-more"
          data-more-info="${escapeHtml(state.entity_id)}"
          aria-label="Open ${escapeHtml(stateName(state))}"
        ><ha-icon icon="mdi:chevron-right"></ha-icon></button>
      </article>`;
  }

  _renderLighting() {
    const lights = this._areaEntities("light");
    const scenes = this._areaEntities("scene");
    const content =
      this._lightTab === "scenes"
        ? scenes.length
          ? `<div class="scene-grid">${scenes
              .map(
                (state) => `
                  <button
                    type="button"
                    class="scene-tile"
                    data-scene-id="${escapeHtml(state.entity_id)}"
                  >
                    <ha-icon icon="mdi:palette-outline"></ha-icon>
                    <span>${escapeHtml(stateName(state))}</span>
                  </button>`
              )
              .join("")}</div>`
          : '<div class="view-message">No scenes are assigned to this area.</div>'
        : lights.length
          ? `<div class="light-list">${lights
              .map((state) => this._renderLightRow(state))
              .join("")}</div>`
          : '<div class="view-message">No lights are assigned to this area.</div>';
    return `
      <div class="c4-view lighting-view">
        <div class="lighting-tabs">
          <button
            type="button"
            class="${this._lightTab === "lights" ? "selected" : ""}"
            data-light-tab="lights"
          >Lights</button>
          <button
            type="button"
            class="${this._lightTab === "scenes" ? "selected" : ""}"
            data-light-tab="scenes"
          >Scenes</button>
        </div>
        <div class="area-label">${escapeHtml(this._areaName())}</div>
        ${content}
      </div>`;
  }

  _renderComfort() {
    const climates = this._domainEntities("climate");
    if (!climates.length) {
      return '<div class="view-message full">No climate entities are available.</div>';
    }
    return `
      <div class="c4-view feature-view comfort-view">
        <div class="feature-grid">
          ${climates
            .map((state) => {
              const value =
                state.attributes.current_temperature ??
                state.attributes.temperature ??
                "—";
              const action = state.attributes.hvac_action || state.state;
              const activeClass =
                action === "heating"
                  ? "heating"
                  : action === "cooling"
                    ? "cooling"
                    : "";
              return `
                <button
                  type="button"
                  class="feature-tile comfort-tile ${activeClass}"
                  data-more-info="${escapeHtml(state.entity_id)}"
                >
                  <span class="comfort-dial">
                    <span class="comfort-value">${escapeHtml(value)}</span>
                    <span class="comfort-unit">${escapeHtml(
                      state.attributes.temperature_unit || "°"
                    )}</span>
                    ${
                      activeClass
                        ? `<ha-icon icon="${
                            activeClass === "heating"
                              ? "mdi:fire"
                              : "mdi:snowflake"
                          }"></ha-icon>`
                        : ""
                    }
                  </span>
                  <span class="feature-name">${escapeHtml(
                    stateName(state)
                  )}</span>
                  <span class="feature-state">${escapeHtml(
                    state.state === "unavailable" ? "Unavailable" : action
                  )}</span>
                </button>`;
            })
            .join("")}
        </div>
      </div>`;
  }

  _securityIcon(state) {
    if (state.attributes.icon) {
      return state.attributes.icon;
    }
    const domain = state.entity_id.split(".", 1)[0];
    const deviceClass = state.attributes.device_class;
    if (domain === "alarm_control_panel") {
      return state.state === "disarmed"
        ? "mdi:shield-off-outline"
        : "mdi:shield-home";
    }
    if (domain === "lock") {
      return state.state === "locked" ? "mdi:lock" : "mdi:lock-open-variant";
    }
    if (domain === "camera") {
      return "mdi:cctv";
    }
    if (domain === "siren") {
      return "mdi:alarm-light";
    }
    if (domain === "cover") {
      return ["garage", "gate"].includes(deviceClass)
        ? "mdi:garage"
        : "mdi:door";
    }
    return {
      door: "mdi:door-closed",
      garage_door: "mdi:garage",
      gas: "mdi:gas-cylinder",
      lock: "mdi:lock",
      moisture: "mdi:water-alert",
      motion: "mdi:motion-sensor",
      occupancy: "mdi:home-account",
      opening: "mdi:door-open",
      safety: "mdi:shield-alert",
      smoke: "mdi:smoke-detector-alert",
      tamper: "mdi:shield-lock-open",
      window: "mdi:window-closed-variant",
    }[deviceClass] || "mdi:shield-outline";
  }

  _securityEntities() {
    const securityBinaryClasses = new Set([
      "door",
      "garage_door",
      "gas",
      "lock",
      "moisture",
      "motion",
      "occupancy",
      "opening",
      "safety",
      "smoke",
      "tamper",
      "window",
    ]);
    const covers = this._domainEntities("cover").filter((state) =>
      ["door", "garage", "gate"].includes(state.attributes.device_class)
    );
    const binarySensors = this._domainEntities("binary_sensor").filter((state) =>
      securityBinaryClasses.has(state.attributes.device_class)
    );
    return [
      ...this._domainEntities("alarm_control_panel"),
      ...this._domainEntities("lock"),
      ...covers,
      ...this._domainEntities("camera"),
      ...this._domainEntities("siren"),
      ...binarySensors,
    ];
  }

  _renderSecurity() {
    const entities = this._securityEntities();
    if (!entities.length) {
      return '<div class="view-message full">No security entities are available.</div>';
    }
    return `
      <div class="c4-view feature-view security-view">
        <div class="feature-grid">
          ${entities
            .map(
              (state) => `
                <button
                  type="button"
                  class="feature-tile security-tile ${
                    ["on", "open", "unlocked", "triggered"].includes(state.state)
                      ? "attention"
                      : ""
                  }"
                  data-more-info="${escapeHtml(state.entity_id)}"
                >
                  <span class="security-icon">
                    <ha-icon icon="${escapeHtml(
                      this._securityIcon(state)
                    )}"></ha-icon>
                  </span>
                  <span class="feature-name">${escapeHtml(
                    stateName(state)
                  )}</span>
                  <span class="feature-state">${escapeHtml(state.state)}</span>
                </button>`
            )
            .join("")}
        </div>
      </div>`;
  }

  _intercomEntities() {
    const direct = this._domainEntities("assist_satellite");
    const related = Object.values(this._hass?.states || {}).filter((state) => {
      if (!["binary_sensor", "button", "camera"].includes(
        state.entity_id.split(".", 1)[0]
      )) {
        return false;
      }
      const text = `${state.entity_id} ${stateName(state)}`.toLowerCase();
      return text.includes("intercom") || text.includes("doorbell");
    });
    return [...direct, ...related].filter(
      (state, index, states) =>
        states.findIndex((candidate) => candidate.entity_id === state.entity_id) ===
        index
    );
  }

  _renderIntercom() {
    const entities = this._intercomEntities();
    return `
      <div class="c4-view intercom-view">
        <div class="intercom-heading">All</div>
        ${
          entities.length
            ? `<div class="intercom-list">${entities
                .map(
                  (state) => `
                    <button
                      type="button"
                      class="intercom-row"
                      data-more-info="${escapeHtml(state.entity_id)}"
                    >
                      <ha-icon icon="${escapeHtml(
                        state.attributes.icon || "mdi:account-voice"
                      )}"></ha-icon>
                      <span>${escapeHtml(stateName(state))}</span>
                      <small>${escapeHtml(state.state)}</small>
                    </button>`
                )
                .join("")}</div>`
            : '<div class="view-message">No Assist satellite, intercom, or doorbell entities are available.</div>'
        }
      </div>`;
  }

  _routineEntity(kind) {
    const areaMatches = [...this._areaEntities("scene"), ...this._areaEntities("script")];
    const allMatches = [...this._domainEntities("scene"), ...this._domainEntities("script")];
    const matches = (state) =>
      stateName(state).toLowerCase().includes(kind) ||
      state.entity_id.toLowerCase().includes(kind);
    return areaMatches.find(matches) || allMatches.find(matches);
  }

  _routineHelper(kind) {
    const domains = kind === "wake" ? ["input_datetime"] : ["timer"];
    const matches = (state) => {
      const text = `${state.entity_id} ${stateName(state)}`.toLowerCase();
      return text.includes(kind) || text.includes(kind === "wake" ? "alarm" : "bed");
    };
    const areaStates = domains.flatMap((domain) => this._areaEntities(domain));
    const allStates = domains.flatMap((domain) => this._domainEntities(domain));
    return areaStates.find(matches) || allStates.find(matches);
  }

  _routineDisplay(kind, helper) {
    if (!helper) {
      return kind === "wake"
        ? { value: "—", suffix: "", frequency: "DAILY" }
        : { value: "—", suffix: "MIN", frequency: "" };
    }
    if (kind === "wake") {
      let date;
      if (helper.attributes.timestamp) {
        date = new Date(helper.attributes.timestamp * 1000);
      } else if (/^\d{1,2}:\d{2}/.test(helper.state)) {
        const [hours, minutes] = helper.state.split(":").map(Number);
        date = new Date();
        date.setHours(hours, minutes, 0, 0);
      }
      const time = date
        ? date.toLocaleTimeString(this._hass?.locale?.language || undefined, {
            hour: "numeric",
            minute: "2-digit",
          })
        : helper.state;
      const match = String(time || "").match(/^(.*?)(?:\s+([AP]M))?$/i);
      return {
        value: match?.[1] || "—",
        suffix: match?.[2] || "",
        frequency: "DAILY",
      };
    }
    const remaining = helper.attributes.remaining || helper.state;
    const parts = String(remaining || "").split(":").map(Number);
    const minutes = parts.length === 3
      ? Math.ceil(parts[0] * 60 + parts[1] + parts[2] / 60)
      : remaining;
    return { value: minutes || "—", suffix: "MIN", frequency: "" };
  }

  _renderRoutine(kind) {
    const routine = this._routineEntity(kind);
    const helper = this._routineHelper(kind);
    const display = this._routineDisplay(kind, helper);
    const title = kind === "wake" ? "Wake" : "Sleep";
    const description =
      routine?.attributes?.description ||
      (routine
        ? `${title} routine for ${this._areaName()}`
        : `Create a ${title} scene or script to enable this routine.`);
    return `
      <div class="c4-view routine-view ${kind}">
        <p class="routine-description">${escapeHtml(description)}</p>
        <div class="routine-dial">
          <div class="routine-dial-inner">
            <span class="routine-frequency">${escapeHtml(
              display.frequency
            )}</span>
            <strong>${escapeHtml(display.value)}</strong>
            <small>${escapeHtml(display.suffix)}</small>
          </div>
        </div>
        <div class="routine-actions">
          <button
            type="button"
            data-routine-options="${escapeHtml(
              helper?.entity_id || routine?.entity_id || ""
            )}"
            ${helper || routine ? "" : "disabled"}
          >
            <ha-icon icon="mdi:tune-variant"></ha-icon>
            <span>Options</span>
          </button>
          <button
            type="button"
            data-routine-start="${escapeHtml(routine?.entity_id || "")}"
            ${routine ? "" : "disabled"}
          >
            <ha-icon icon="${
              kind === "wake" ? "mdi:weather-sunset-up" : "mdi:timer-play-outline"
            }"></ha-icon>
            <span>${kind === "wake" ? "Activate" : "Start"}</span>
          </button>
        </div>
      </div>`;
  }

  _renderCurrentView() {
    if (this._view === "watch") {
      return this._renderMediaView("watch");
    }
    if (this._view === "listen") {
      return this._renderMediaView("listen");
    }
    if (this._view === "lighting") {
      return this._renderLighting();
    }
    if (this._view === "comfort") {
      return this._renderComfort();
    }
    if (this._view === "security") {
      return this._renderSecurity();
    }
    if (this._view === "intercom") {
      return this._renderIntercom();
    }
    if (this._view === "wake" || this._view === "sleep") {
      return this._renderRoutine(this._view);
    }
    return this._renderHome();
  }

  _viewTitle() {
    return (
      {
        home: this._areaName(),
        watch: "Watch",
        listen: "Listen",
        lighting: "Lighting",
        comfort: "Comfort",
        security: "Security",
        intercom: "Intercom",
        wake: "Wake",
        sleep: "Sleep",
      }[this._view] || this._areaName()
    );
  }

  _renderHeader() {
    return `
      <header class="c4-header ${this._view === "home" ? "home" : ""}">
        <button
          type="button"
          class="header-action"
          ${this._view === "home" ? "data-toggle-drawer" : "data-c4-home"}
          aria-label="${
            this._view === "home" ? "Open Control4 navigation" : "Room home"
          }"
        ><ha-icon icon="${
          this._view === "home" ? "mdi:menu" : "mdi:home-outline"
        }"></ha-icon></button>
        ${
          this._view !== "home"
            ? '<button type="button" class="header-action" data-c4-back aria-label="Back"><ha-icon icon="mdi:chevron-left"></ha-icon></button>'
            : ""
        }
        <div class="header-title">
          <span class="page-dots">●○○○○○</span>
          <strong>${escapeHtml(this._viewTitle())}</strong>
        </div>
        ${
          this._view === "home"
            ? '<button type="button" class="header-action" data-c4-home aria-label="Room home"><ha-icon icon="mdi:home-outline"></ha-icon></button>'
            : '<span class="header-spacer"></span>'
        }
      </header>`;
  }

  _drawerItem(view, icon, title, extra = "") {
    return `
      <button type="button" class="drawer-item" data-c4-view="${view}">
        <span class="drawer-icon"><ha-icon icon="${icon}"></ha-icon>${extra}</span>
        <span>${title}</span>
      </button>`;
  }

  _renderDrawer() {
    if (!this._drawerOpen) {
      return "";
    }
    return `
      <div class="drawer-layer">
        <button
          type="button"
          class="drawer-scrim"
          data-close-drawer
          aria-label="Close navigation"
        ></button>
        <nav class="c4-drawer" aria-label="Control4 room navigation">
          ${this._drawerItem("watch", "mdi:television-classic", "Watch")}
          ${this._drawerItem("listen", "mdi:music-box-outline", "Listen")}
          ${this._drawerItem("lighting", "mdi:lightbulb-group", "Lighting")}
          ${this._drawerItem("comfort", "mdi:snowflake-thermometer", "Comfort")}
          ${this._drawerItem("security", "mdi:shield-home", "Security")}
          ${this._drawerItem("intercom", "mdi:access-point", "Intercom")}
          ${this._drawerItem("wake", "mdi:white-balance-sunny", "Wake")}
          ${this._drawerItem("sleep", "mdi:weather-night", "Sleep")}
          <div class="drawer-footer">
            <ha-icon icon="mdi:lifebuoy"></ha-icon>
            <ha-icon icon="mdi:email-outline"></ha-icon>
            <ha-icon icon="mdi:headset"></ha-icon>
          </div>
        </nav>
      </div>`;
  }

  _renderNowPlaying() {
    if (!["home", "watch", "listen"].includes(this._view)) {
      return "";
    }
    const state = this._hass.states[this._config.entity];
    const title =
      state?.attributes?.media_title ||
      state?.attributes?.control4_media_title ||
      state?.attributes?.source;
    if (!title) {
      return "";
    }
    return `
      <button
        type="button"
        class="now-playing"
        data-more-info="${escapeHtml(state.entity_id)}"
      >
        ${
          state.attributes.entity_picture
            ? `<img src="${escapeHtml(state.attributes.entity_picture)}" alt="" />`
            : '<ha-icon icon="mdi:play-circle"></ha-icon>'
        }
        <span>
          <strong>${escapeHtml(title)}</strong>
          <small>${escapeHtml(
            state.attributes.media_artist || stateName(state)
          )}</small>
        </span>
        <ha-icon icon="mdi:chevron-up"></ha-icon>
      </button>`;
  }

  _render() {
    if (!this._config || !this._hass) {
      return;
    }
    const previousScroll =
      this.shadowRoot.querySelector(".c4-content")?.scrollTop || 0;
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          color: #fff;
          --c4-accent: var(--primary-color, #26a9e0);
        }
        * { box-sizing: border-box; }
        ha-card {
          height: calc(100dvh - var(--header-height, 56px) - 12px);
          min-height: 620px;
          overflow: hidden;
          border: 0;
          border-radius: var(--ha-card-border-radius, 12px);
          background: #171717;
        }
        button { font: inherit; }
        .dashboard {
          position: relative;
          display: grid;
          grid-template-rows: 72px minmax(0, 1fr) auto;
          width: 100%;
          height: 100%;
          overflow: hidden;
          background-color: #26221f;
          background-position: center;
          background-size: cover;
        }
        .dashboard::before {
          position: absolute;
          inset: 0;
          z-index: 0;
          content: "";
          background: rgba(0, 0, 0, 0.56);
          pointer-events: none;
        }
        .c4-header, .c4-content, .now-playing { position: relative; z-index: 1; }
        .c4-header {
          display: grid;
          grid-template-columns: 52px auto minmax(0, 1fr) 52px;
          align-items: center;
          min-height: 72px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.13);
          background: rgba(0, 0, 0, 0.38);
          backdrop-filter: blur(8px);
        }
        .c4-header.home { grid-template-columns: 52px minmax(0, 1fr) 52px; }
        .header-action {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 52px;
          height: 52px;
          border: 0;
          color: #fff;
          background: transparent;
          cursor: pointer;
        }
        .header-action ha-icon { --mdc-icon-size: 31px; }
        .header-title {
          min-width: 0;
          text-align: center;
        }
        .header-title strong {
          display: block;
          overflow: hidden;
          font-size: 23px;
          font-weight: 300;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .page-dots {
          display: block;
          height: 13px;
          color: rgba(255, 255, 255, 0.36);
          font-size: 9px;
          letter-spacing: 2px;
        }
        .c4-content {
          min-height: 0;
          overflow-x: hidden;
          overflow-y: auto;
          scrollbar-width: thin;
        }
        .c4-view { min-height: 100%; padding: 18px 14px 28px; }
        .climate-summary {
          display: flex;
          justify-content: center;
          gap: 48px;
          padding: 8px 0 22px;
        }
        .climate-orb {
          position: relative;
          display: grid;
          place-content: center;
          width: 88px;
          height: 88px;
          border: 1px solid rgba(255, 255, 255, 0.23);
          border-radius: 50%;
          color: #fff;
          background: radial-gradient(
            circle at 40% 35%,
            rgba(255, 255, 255, 0.18),
            rgba(0, 0, 0, 0.54) 68%
          );
          box-shadow: 0 6px 16px rgba(0, 0, 0, 0.35);
          cursor: pointer;
        }
        .orb-value { font-size: 25px; font-weight: 300; }
        .orb-unit { position: absolute; top: 24px; right: 16px; font-size: 11px; }
        .orb-name {
          position: absolute;
          top: calc(100% + 7px);
          left: 50%;
          width: 130px;
          overflow: hidden;
          color: rgba(255, 255, 255, 0.82);
          font-size: 13px;
          text-overflow: ellipsis;
          white-space: nowrap;
          transform: translateX(-50%);
        }
        .launcher-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          margin-top: 12px;
          border-top: 1px solid rgba(255, 255, 255, 0.15);
          border-left: 1px solid rgba(255, 255, 255, 0.15);
        }
        .launcher {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 10px;
          min-height: 138px;
          border: 0;
          border-right: 1px solid rgba(255, 255, 255, 0.15);
          border-bottom: 1px solid rgba(255, 255, 255, 0.15);
          color: #fff;
          background: rgba(0, 0, 0, 0.14);
          cursor: pointer;
        }
        .launcher ha-icon { color: #fff; --mdc-icon-size: 57px; }
        .launcher span { font-size: 18px; font-weight: 300; }
        .home-media-grid, .large-media-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          border-left: 1px solid rgba(255, 255, 255, 0.15);
        }
        .c4-media-tile {
          position: relative;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 9px;
          min-width: 0;
          height: clamp(190px, 51vw, 238px);
          padding: 16px 12px;
          border: 0;
          border-right: 1px solid rgba(255, 255, 255, 0.15);
          border-bottom: 1px solid rgba(255, 255, 255, 0.15);
          color: #fff;
          text-align: center;
          background: rgba(0, 0, 0, 0.13);
          cursor: pointer;
        }
        .c4-media-tile.compact { height: 148px; }
        .c4-media-tile.active {
          background: color-mix(in srgb, var(--c4-accent) 24%, rgba(0, 0, 0, 0.2));
          box-shadow: inset 0 0 0 1px var(--c4-accent);
        }
        .c4-media-tile:disabled { opacity: 0.62; cursor: default; }
        .c4-art {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 100px;
          height: 100px;
          overflow: hidden;
          border-radius: 4px;
          color: #fff;
          background: rgba(0, 0, 0, 0.52);
        }
        .compact .c4-art { width: 66px; height: 66px; }
        .c4-art img { width: 100%; height: 100%; object-fit: contain; }
        .c4-art ha-icon { display: none; --mdc-icon-size: 54px; }
        .c4-art.fallback img { display: none; }
        .c4-art.fallback ha-icon { display: block; }
        .c4-media-title {
          display: -webkit-box;
          max-width: 100%;
          overflow: hidden;
          font-size: 20px;
          font-weight: 300;
          line-height: 1.15;
          text-overflow: ellipsis;
          white-space: normal;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
        }
        .compact .c4-media-title { font-size: 15px; }
        .c4-media-detail {
          max-width: 100%;
          overflow: hidden;
          color: rgba(255, 255, 255, 0.65);
          font-size: 12px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .tile-busy {
          position: absolute;
          top: 12px;
          right: 12px;
          color: var(--c4-accent);
          animation: spin 1s linear infinite;
        }
        .media-section + .media-section { margin-top: 20px; }
        .media-section h3, .recent-section h3 {
          margin: 0;
          padding: 0 0 10px;
          color: rgba(255, 255, 255, 0.82);
          font-size: 20px;
          font-weight: 300;
        }
        .media-section h3:empty { display: none; }
        .recent-section { margin-bottom: 20px; }
        .recent-media {
          display: grid;
          grid-template-columns: 70px minmax(0, 1fr);
          gap: 12px;
          align-items: center;
          width: 100%;
          padding: 10px;
          border: 1px solid rgba(255, 255, 255, 0.15);
          color: #fff;
          text-align: start;
          background: rgba(0, 0, 0, 0.32);
          cursor: pointer;
        }
        .recent-media img, .recent-media > ha-icon {
          width: 70px;
          height: 70px;
          object-fit: cover;
          --mdc-icon-size: 62px;
        }
        .recent-media strong, .recent-media small { display: block; }
        .recent-media strong {
          overflow: hidden;
          font-size: 17px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .recent-media small {
          margin-top: 4px;
          color: rgba(255, 255, 255, 0.62);
        }
        .lighting-view { padding: 0 0 28px; }
        .lighting-tabs {
          display: grid;
          grid-template-columns: 1fr 1fr;
          background: rgba(0, 0, 0, 0.47);
        }
        .lighting-tabs button {
          padding: 16px;
          border: 0;
          border-bottom: 3px solid transparent;
          color: rgba(255, 255, 255, 0.65);
          font-size: 21px;
          background: transparent;
          cursor: pointer;
        }
        .lighting-tabs button.selected {
          border-bottom-color: #fff;
          color: #fff;
        }
        .area-label {
          padding: 12px 18px;
          color: rgba(255, 255, 255, 0.62);
          font-size: 15px;
          text-transform: uppercase;
          background: rgba(0, 0, 0, 0.36);
        }
        .light-row {
          display: grid;
          grid-template-columns: 74px minmax(0, 1fr) 48px;
          align-items: center;
          min-height: 112px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.17);
          background: rgba(0, 0, 0, 0.18);
        }
        .light-toggle, .row-more, .entity-name {
          border: 0;
          color: #fff;
          background: transparent;
          cursor: pointer;
        }
        .light-toggle {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 74px;
          height: 74px;
        }
        .light-toggle ha-icon { --mdc-icon-size: 42px; }
        .light-row.on .light-toggle { color: #ffd06f; }
        .light-details { min-width: 0; padding: 12px 0; }
        .entity-name {
          display: block;
          width: 100%;
          overflow: hidden;
          padding: 0;
          font-size: 21px;
          font-weight: 300;
          text-align: start;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .light-level {
          display: grid;
          grid-template-columns: 66px minmax(0, 1fr);
          gap: 10px;
          align-items: center;
          margin-top: 7px;
        }
        .light-level output {
          padding: 6px 12px;
          border: 1px solid rgba(255, 255, 255, 0.82);
          border-radius: 20px;
          font-size: 17px;
          text-align: center;
        }
        .light-level input { width: 100%; accent-color: #fff; }
        .binary-state {
          display: block;
          margin-top: 7px;
          color: rgba(255, 255, 255, 0.65);
        }
        .row-more {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 48px;
          height: 64px;
        }
        .row-more ha-icon { --mdc-icon-size: 30px; }
        .scene-grid, .entity-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
          padding: 14px;
        }
        .scene-tile, .entity-tile {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 10px;
          min-height: 132px;
          padding: 12px;
          border: 1px solid rgba(255, 255, 255, 0.18);
          border-radius: 8px;
          color: #fff;
          background: rgba(0, 0, 0, 0.27);
          cursor: pointer;
        }
        .scene-tile ha-icon, .entity-tile ha-icon { --mdc-icon-size: 42px; }
        .entity-tile strong, .entity-tile span { text-align: center; }
        .entity-tile span { color: rgba(255, 255, 255, 0.68); }
        .feature-view { padding: 0 0 28px; }
        .feature-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          border-top: 1px solid rgba(255, 255, 255, 0.15);
          border-left: 1px solid rgba(255, 255, 255, 0.15);
        }
        .feature-tile {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 8px;
          min-width: 0;
          min-height: clamp(210px, 52vw, 252px);
          padding: 16px 10px;
          border: 0;
          border-right: 1px solid rgba(255, 255, 255, 0.15);
          border-bottom: 1px solid rgba(255, 255, 255, 0.15);
          color: #fff;
          background: rgba(0, 0, 0, 0.13);
          cursor: pointer;
        }
        .feature-name, .feature-state {
          display: block;
          width: 100%;
          overflow: hidden;
          text-align: center;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .feature-name { font-size: 20px; font-weight: 300; }
        .feature-state {
          color: rgba(255, 255, 255, 0.61);
          font-size: 12px;
          text-transform: capitalize;
        }
        .comfort-dial {
          position: relative;
          display: grid;
          place-content: center;
          width: 112px;
          height: 112px;
          border: 2px solid rgba(255, 255, 255, 0.28);
          border-radius: 50%;
          background: radial-gradient(
            circle at 42% 35%,
            #444,
            #242424 66%,
            #171717
          );
          box-shadow: 0 7px 14px rgba(0, 0, 0, 0.45);
        }
        .comfort-tile.heating .comfort-dial {
          border-color: #e07132;
          box-shadow: 0 0 0 3px rgba(224, 113, 50, 0.24),
            0 7px 14px rgba(0, 0, 0, 0.45);
        }
        .comfort-tile.cooling .comfort-dial {
          border-color: #4fc4ee;
          box-shadow: 0 0 0 3px rgba(79, 196, 238, 0.22),
            0 7px 14px rgba(0, 0, 0, 0.45);
        }
        .comfort-value { font-size: 38px; font-weight: 300; }
        .comfort-unit {
          position: absolute;
          right: 21px;
          bottom: 18px;
          color: rgba(255, 255, 255, 0.72);
          font-size: 18px;
        }
        .comfort-dial ha-icon {
          position: absolute;
          top: 10px;
          left: 50%;
          color: #f17430;
          transform: translateX(-50%);
          --mdc-icon-size: 18px;
        }
        .comfort-tile.cooling .comfort-dial ha-icon { color: #61cdf2; }
        .security-icon {
          position: relative;
          display: grid;
          place-content: center;
          width: 108px;
          height: 108px;
          border: 1px solid rgba(255, 255, 255, 0.23);
          border-radius: 24px;
          color: #f4f4f4;
          background: linear-gradient(145deg, #f0f0f0 0 18%, #8f9294 19% 100%);
          box-shadow: 0 7px 15px rgba(0, 0, 0, 0.35);
        }
        .security-icon::before {
          position: absolute;
          inset: 7px;
          border-radius: 18px;
          content: "";
          background: rgba(18, 18, 18, 0.76);
        }
        .security-icon ha-icon { position: relative; --mdc-icon-size: 65px; }
        .security-tile.attention .security-icon { color: #ff5c52; }
        .intercom-view { padding: 0 0 28px; }
        .intercom-heading {
          padding: 22px 22%;
          border-bottom: 1px solid rgba(255, 255, 255, 0.16);
          color: rgba(255, 255, 255, 0.84);
          font-size: 24px;
          font-weight: 300;
          background: rgba(0, 0, 0, 0.25);
        }
        .intercom-row {
          display: grid;
          grid-template-columns: 56px minmax(0, 1fr) auto;
          gap: 14px;
          align-items: center;
          width: 100%;
          min-height: 96px;
          padding: 12px 22px;
          border: 0;
          border-bottom: 1px solid rgba(255, 255, 255, 0.16);
          color: #fff;
          text-align: start;
          background: rgba(0, 0, 0, 0.2);
          cursor: pointer;
        }
        .intercom-row ha-icon { --mdc-icon-size: 38px; }
        .intercom-row span { font-size: 22px; font-weight: 300; }
        .intercom-row small {
          color: rgba(255, 255, 255, 0.58);
          text-transform: capitalize;
        }
        .routine-view {
          display: grid;
          grid-template-rows: auto minmax(300px, 1fr) auto;
          place-items: center;
          min-height: 100%;
          padding: 54px 24px 38px;
          background: rgba(0, 0, 0, 0.35);
        }
        .routine-description {
          max-width: 430px;
          margin: 0;
          color: rgba(255, 255, 255, 0.62);
          font-size: 21px;
          font-weight: 300;
          line-height: 1.35;
          text-align: center;
        }
        .routine-dial {
          display: grid;
          place-content: center;
          width: min(72vw, 310px);
          height: min(72vw, 310px);
          border-radius: 50%;
          background: conic-gradient(
            from 215deg,
            #61266f,
            #8d184d,
            #9a4b19,
            #4d1d72,
            #61266f
          );
          box-shadow: 0 12px 30px rgba(0, 0, 0, 0.48);
        }
        .routine-dial-inner {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          width: calc(min(72vw, 310px) - 16px);
          height: calc(min(72vw, 310px) - 16px);
          border: 32px solid rgba(0, 0, 0, 0.35);
          border-radius: 50%;
          background: rgba(62, 62, 62, 0.76);
        }
        .routine-frequency {
          min-height: 24px;
          color: rgba(255, 255, 255, 0.78);
          font-size: 17px;
        }
        .routine-dial strong { font-size: 62px; font-weight: 200; }
        .routine-dial small {
          min-height: 26px;
          color: rgba(255, 255, 255, 0.78);
          font-size: 18px;
        }
        .routine-actions { display: flex; gap: 56px; }
        .routine-actions button {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 8px;
          border: 0;
          color: #fff;
          background: transparent;
          cursor: pointer;
        }
        .routine-actions button:disabled { opacity: 0.36; cursor: default; }
        .routine-actions ha-icon {
          width: 68px;
          height: 68px;
          padding: 16px;
          border-radius: 50%;
          background: rgba(65, 65, 65, 0.82);
          --mdc-icon-size: 36px;
        }
        .routine-actions span { font-size: 18px; font-weight: 300; }
        .view-message {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          min-height: 120px;
          padding: 24px;
          color: rgba(255, 255, 255, 0.68);
          text-align: center;
        }
        .view-message.full { min-height: 100%; }
        .spin { animation: spin 1s linear infinite; }
        .now-playing {
          display: grid;
          grid-template-columns: 46px minmax(0, 1fr) 32px;
          gap: 10px;
          align-items: center;
          width: 100%;
          min-height: 64px;
          padding: 8px 14px;
          border: 0;
          border-top: 1px solid rgba(255, 255, 255, 0.17);
          color: #fff;
          text-align: start;
          background: rgba(0, 0, 0, 0.88);
          cursor: pointer;
        }
        .now-playing img {
          width: 46px;
          height: 46px;
          object-fit: cover;
        }
        .now-playing > ha-icon:first-child {
          color: var(--c4-accent);
          --mdc-icon-size: 42px;
        }
        .now-playing strong, .now-playing small {
          display: block;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .now-playing small {
          margin-top: 2px;
          color: rgba(255, 255, 255, 0.6);
        }
        .drawer-layer {
          position: absolute;
          inset: 0;
          z-index: 20;
        }
        .drawer-scrim {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          border: 0;
          background: rgba(0, 0, 0, 0.5);
        }
        .c4-drawer {
          position: absolute;
          inset: 0 auto 0 0;
          display: grid;
          grid-template-rows: repeat(8, minmax(62px, 1fr)) 72px;
          width: min(72%, 390px);
          color: #fff;
          background: rgba(0, 0, 0, 0.9);
          backdrop-filter: blur(12px);
          box-shadow: 10px 0 30px rgba(0, 0, 0, 0.42);
        }
        .drawer-item {
          display: grid;
          grid-template-columns: 72px minmax(0, 1fr);
          gap: 10px;
          align-items: center;
          width: 100%;
          padding: 0;
          border: 0;
          border-bottom: 1px solid rgba(255, 255, 255, 0.12);
          color: #fff;
          font-size: 24px;
          font-weight: 300;
          text-align: start;
          background: transparent;
          cursor: pointer;
        }
        .drawer-icon {
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }
        .drawer-icon ha-icon { --mdc-icon-size: 39px; }
        .drawer-item:nth-child(1) .drawer-icon { color: #3fd5ff; }
        .drawer-item:nth-child(3) .drawer-icon { color: #ffbd72; }
        .drawer-item:nth-child(5) .drawer-icon { color: #71dd43; }
        .drawer-footer {
          display: flex;
          align-items: center;
          justify-content: space-around;
          border-top: 1px solid rgba(255, 255, 255, 0.17);
        }
        .drawer-footer ha-icon { --mdc-icon-size: 34px; }
        .dashboard-notice {
          position: absolute;
          right: 14px;
          bottom: 76px;
          left: 14px;
          z-index: 15;
          padding: 9px 12px;
          border-radius: 8px;
          color: #fff;
          text-align: center;
          background: rgba(0, 0, 0, 0.82);
        }
        .dashboard-notice.error { color: #ff8b8b; }
        @keyframes spin { to { transform: rotate(360deg); } }
        @media (min-width: 700px) {
          .c4-view { padding-right: 24px; padding-left: 24px; }
          .launcher-grid, .home-media-grid, .large-media-grid {
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }
          .c4-media-tile { height: 220px; }
          .c4-media-tile.compact { height: 165px; }
        }
      </style>
      <ha-card>
        <div class="dashboard">
          ${this._renderHeader()}
          <main class="c4-content">${this._renderCurrentView()}</main>
          ${this._renderNowPlaying()}
          ${this._renderDrawer()}
          ${
            this._error
              ? `<div class="dashboard-notice error">${escapeHtml(
                  this._error
                )}</div>`
              : this._status
                ? `<div class="dashboard-notice">${escapeHtml(
                    this._status
                  )}</div>`
                : ""
          }
        </div>
      </ha-card>`;
    const dashboard = this.shadowRoot.querySelector(".dashboard");
    const background = this._background();
    if (dashboard && background) {
      dashboard.style.backgroundImage = `url(${JSON.stringify(background)})`;
    }
    requestAnimationFrame(() => {
      const content = this.shadowRoot.querySelector(".c4-content");
      if (content) {
        content.scrollTop = previousScroll;
      }
    });
    this._bindEvents();
  }

  _bindEvents() {
    this.shadowRoot
      .querySelector("[data-toggle-drawer]")
      ?.addEventListener("click", () => {
        this._drawerOpen = !this._drawerOpen;
        this._render();
      });
    this.shadowRoot
      .querySelector("[data-close-drawer]")
      ?.addEventListener("click", () => {
        this._drawerOpen = false;
        this._render();
      });
    this.shadowRoot.querySelector("[data-c4-home]")?.addEventListener("click", () => {
      this._view = "home";
      this._drawerOpen = false;
      this._render();
    });
    this.shadowRoot.querySelector("[data-c4-back]")?.addEventListener("click", () => {
      this._view = "home";
      this._render();
    });
    for (const button of this.shadowRoot.querySelectorAll("[data-c4-view]")) {
      button.addEventListener("click", () =>
        this._navigate(button.dataset.c4View)
      );
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-c4-media-key]")) {
      button.addEventListener("click", () => {
        const item = this._sections
          .flatMap((section) => section.items)
          .find((candidate) => candidate.key === button.dataset.c4MediaKey);
        if (item) {
          void this._playMedia(item);
        }
      });
    }
    for (const image of this.shadowRoot.querySelectorAll(".c4-art img")) {
      image.addEventListener("error", () =>
        image.parentElement?.classList.add("fallback")
      );
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-more-info]")) {
      button.addEventListener("click", () =>
        this._openMoreInfo(button.dataset.moreInfo)
      );
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-light-toggle]")) {
      button.addEventListener("click", () =>
        void this._toggleLight(button.dataset.lightToggle)
      );
    }
    for (const slider of this.shadowRoot.querySelectorAll("[data-light-slider]")) {
      slider.addEventListener("input", () => {
        const output = this.shadowRoot.querySelector(
          `[data-light-output="${CSS.escape(slider.dataset.lightSlider)}"]`
        );
        if (output) {
          output.value = slider.value;
          output.textContent = slider.value;
        }
      });
      slider.addEventListener("change", () =>
        void this._setLightLevel(
          slider.dataset.lightSlider,
          Number(slider.value)
        )
      );
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-scene-id]")) {
      button.addEventListener("click", () =>
        void this._activateScene(button.dataset.sceneId)
      );
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-light-tab]")) {
      button.addEventListener("click", () => {
        this._lightTab = button.dataset.lightTab;
        this._render();
      });
    }
    for (const button of this.shadowRoot.querySelectorAll(
      "[data-routine-options]"
    )) {
      button.addEventListener("click", () => {
        if (button.dataset.routineOptions) {
          this._openMoreInfo(button.dataset.routineOptions);
        }
      });
    }
    for (const button of this.shadowRoot.querySelectorAll(
      "[data-routine-start]"
    )) {
      button.addEventListener("click", () => {
        if (button.dataset.routineStart) {
          void this._activateRoutine(button.dataset.routineStart);
        }
      });
    }
  }

  _navigate(view) {
    this._view = view;
    this._drawerOpen = false;
    this._render();
  }

  _openMoreInfo(entityId) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      })
    );
  }

  async _playMedia(item) {
    if (this._busyId) {
      return;
    }
    this._busyId = item.key;
    this._error = "";
    this._status = `Starting ${item.title}…`;
    this._render();
    try {
      await this._hass.callService(
        "media_player",
        "play_media",
        {
          media_content_id: item.media_content_id,
          media_content_type: item.media_content_type,
        },
        { entity_id: this._config.entity }
      );
      this._status = `${item.title} started`;
      window.clearTimeout(this._statusTimer);
      this._statusTimer = window.setTimeout(() => {
        this._status = "";
        if (this.isConnected) {
          this._render();
        }
      }, 3500);
    } catch (error) {
      this._status = "";
      this._error = error?.message || String(error);
    } finally {
      this._busyId = undefined;
      if (this.isConnected) {
        this._render();
      }
    }
  }

  async _toggleLight(entityId) {
    const state = this._hass.states[entityId];
    if (!state) {
      return;
    }
    try {
      await this._hass.callService(
        "light",
        state.state === "on" ? "turn_off" : "turn_on",
        {},
        { entity_id: entityId }
      );
    } catch (error) {
      this._error = error?.message || String(error);
      this._render();
    }
  }

  async _setLightLevel(entityId, brightnessPct) {
    this._pendingLightLevels.set(entityId, {
      value: brightnessPct,
      expires: Date.now() + FEEDBACK_TIMEOUT,
    });
    try {
      await this._hass.callService(
        "light",
        "turn_on",
        { brightness_pct: brightnessPct },
        { entity_id: entityId }
      );
    } catch (error) {
      this._pendingLightLevels.delete(entityId);
      this._error = error?.message || String(error);
      this._render();
    }
  }

  async _activateScene(entityId) {
    try {
      await this._hass.callService(
        "scene",
        "turn_on",
        {},
        { entity_id: entityId }
      );
      this._status = `${stateName(this._hass.states[entityId])} activated`;
      this._render();
    } catch (error) {
      this._error = error?.message || String(error);
      this._render();
    }
  }

  async _activateRoutine(entityId) {
    const state = this._hass.states[entityId];
    if (!state) {
      return;
    }
    const domain = entityId.split(".", 1)[0];
    this._status = `Activating ${stateName(state)}…`;
    this._error = "";
    this._render();
    try {
      await this._hass.callService(
        domain,
        "turn_on",
        {},
        { entity_id: entityId }
      );
      this._status = `${stateName(state)} activated`;
      window.clearTimeout(this._statusTimer);
      this._statusTimer = window.setTimeout(() => {
        this._status = "";
        if (this.isConnected) {
          this._render();
        }
      }, 3500);
    } catch (error) {
      this._status = "";
      this._error = error?.message || String(error);
      this._render();
    }
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
    this.shadowRoot
      .querySelector('input[type="text"]')
      ?.addEventListener("change", (event) =>
        this._changed({ name: event.currentTarget.value.trim() })
      );
  }
}

class Control4MediaFavoritesCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = undefined;
    this._config = {};
    this._sections = [];
    this._loadedEntity = undefined;
    this._loading = false;
    this._error = "";
    this._loadSequence = 0;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    this._maybeLoad();
  }

  setConfig(config) {
    const entityChanged = config?.entity !== this._config.entity;
    this._config = { ...config };
    if (entityChanged) {
      this._loadSequence += 1;
      this._sections = [];
      this._loadedEntity = undefined;
      this._loading = false;
      this._error = "";
    }
    this._render();
    this._maybeLoad();
  }

  connectedCallback() {
    this._maybeLoad();
  }

  disconnectedCallback() {
    this._loadSequence += 1;
    this._loading = false;
    this._loadedEntity = undefined;
  }

  _changed(updates) {
    const config = { ...this._config, ...updates };
    if (!config.name) {
      delete config.name;
    }
    if (
      Object.prototype.hasOwnProperty.call(updates, "media_ids") &&
      updates.media_ids === undefined
    ) {
      delete config.media_ids;
    }
    this._config = config;
    this._render();
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }

  _allItems() {
    return this._sections.flatMap((section) => section.items);
  }

  _selectedIds() {
    if (Array.isArray(this._config.media_ids)) {
      return new Set(this._config.media_ids);
    }
    return new Set(this._allItems().map((item) => item.media_content_id));
  }

  _updateSelection(selected) {
    const items = this._allItems();
    const ordered = items
      .filter((item) => selected.has(item.media_content_id))
      .map((item) => item.media_content_id);
    this._changed({
      media_ids: ordered.length === items.length ? undefined : ordered,
    });
  }

  _changeEntity(entityId) {
    this._loadSequence += 1;
    this._sections = [];
    this._loadedEntity = undefined;
    this._loading = false;
    this._error = "";
    this._changed({ entity: entityId, media_ids: undefined });
    this._maybeLoad();
  }

  _maybeLoad() {
    if (
      !this.isConnected ||
      !this._hass ||
      !this._config.entity ||
      this._loading ||
      this._loadedEntity === this._config.entity
    ) {
      return;
    }
    void this._loadItems();
  }

  async _loadItems() {
    const entityId = this._config.entity;
    const sequence = ++this._loadSequence;
    this._loading = true;
    this._error = "";
    this._render();
    try {
      const loaded = await loadControl4MediaSections(this._hass, entityId);
      if (sequence !== this._loadSequence || !this.isConnected) {
        return;
      }
      this._sections = loaded.sections;
      this._error = loaded.errors.length
        ? this._sections.length
          ? "Some items could not be loaded."
          : loaded.errors[0]?.message || String(loaded.errors[0])
        : "";
      this._loadedEntity = entityId;
    } catch (error) {
      if (sequence !== this._loadSequence || !this.isConnected) {
        return;
      }
      this._sections = [];
      this._error = error?.message || String(error);
      this._loadedEntity = entityId;
    } finally {
      if (sequence === this._loadSequence) {
        this._loading = false;
        if (this.isConnected) {
          this._render();
        }
      }
    }
  }

  _renderItemSelector() {
    if (this._loading && !this._sections.length) {
      return '<div class="message loading"><ha-icon icon="mdi:loading"></ha-icon> Loading media items…</div>';
    }
    const items = this._allItems();
    if (!items.length) {
      return '<div class="message">No media items are available for this player.</div>';
    }
    const selected = this._selectedIds();
    const selectedCount = items.filter((item) =>
      selected.has(item.media_content_id)
    ).length;
    return `
      <div class="selector-heading">
        <div>
          <strong>Items to show</strong>
          <span>${selectedCount} of ${items.length} selected</span>
        </div>
        <div class="selection-actions">
          <button type="button" data-select-all>Show all</button>
          <button type="button" data-select-none>Hide all</button>
        </div>
      </div>
      <div class="section-list">
        ${this._sections
          .map(
            (section) => `
              <section>
                <label class="section-choice">
                  <input
                    type="checkbox"
                    data-section-key="${escapeHtml(section.key)}"
                    ${
                      section.items.every((item) =>
                        selected.has(item.media_content_id)
                      )
                        ? "checked"
                        : ""
                    }
                  />
                  <ha-icon icon="${escapeHtml(section.icon)}"></ha-icon>
                  <strong>${escapeHtml(section.title)}</strong>
                  <span>${section.items.length}</span>
                </label>
                <div class="item-list">
                  ${section.items
                    .map(
                      (item) => `
                        <label class="item-choice">
                          <input
                            type="checkbox"
                            data-media-id="${escapeHtml(item.media_content_id)}"
                            ${
                              selected.has(item.media_content_id)
                                ? "checked"
                                : ""
                            }
                          />
                          <span>
                            <strong>${escapeHtml(item.title)}</strong>
                            <small>${escapeHtml(
                              item.subtitle || section.title
                            )}</small>
                          </span>
                        </label>`
                    )
                    .join("")}
                </div>
              </section>`
          )
          .join("")}
      </div>`;
  }

  _render() {
    if (!this._hass) {
      return;
    }
    const players = control4BrowsePlayers(this._hass);
    const missingSelection =
      this._config.entity &&
      !players.some((state) => state.entity_id === this._config.entity);
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        * { box-sizing: border-box; }
        label.field {
          display: block;
          margin: 16px 0;
          color: var(--primary-text-color);
        }
        select, input[type="text"] {
          width: 100%;
          margin-top: 8px;
          padding: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          color: var(--primary-text-color);
          background: var(--card-background-color);
          font: inherit;
        }
        input[type="checkbox"] {
          width: 20px;
          height: 20px;
          margin: 0;
          accent-color: var(--primary-color);
        }
        .message {
          padding: 12px 0;
          color: var(--secondary-text-color);
          font-size: 13px;
        }
        .message.loading {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .message.loading ha-icon { animation: spin 1s linear infinite; }
        .message.error { color: var(--error-color); }
        .selector-heading {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin: 22px 0 10px;
        }
        .selector-heading strong, .selector-heading span { display: block; }
        .selector-heading span {
          margin-top: 3px;
          color: var(--secondary-text-color);
          font-size: 12px;
        }
        .selection-actions { display: flex; gap: 6px; }
        .selection-actions button {
          padding: 7px 9px;
          border: 1px solid var(--divider-color);
          border-radius: 9px;
          color: var(--primary-color);
          background: transparent;
          font: inherit;
          font-size: 12px;
          cursor: pointer;
        }
        .section-list {
          overflow: hidden;
          border: 1px solid var(--divider-color);
          border-radius: 12px;
        }
        .section-list section + section {
          border-top: 1px solid var(--divider-color);
        }
        .section-choice {
          display: grid;
          grid-template-columns: 20px 22px minmax(0, 1fr) auto;
          gap: 9px;
          align-items: center;
          padding: 11px 12px;
          color: var(--primary-text-color);
          background: var(--secondary-background-color);
          cursor: pointer;
        }
        .section-choice ha-icon { --mdc-icon-size: 19px; }
        .section-choice > span {
          color: var(--secondary-text-color);
          font-size: 12px;
        }
        .item-list { padding: 2px 12px 6px 43px; }
        .item-choice {
          display: grid;
          grid-template-columns: 20px minmax(0, 1fr);
          gap: 10px;
          align-items: center;
          min-height: 45px;
          color: var(--primary-text-color);
          cursor: pointer;
        }
        .item-choice > span,
        .item-choice strong,
        .item-choice small {
          display: block;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .item-choice strong { font-size: 14px; font-weight: 500; }
        .item-choice small {
          margin-top: 2px;
          color: var(--secondary-text-color);
          font-size: 11px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      </style>
      <label class="field">
        Control4 media player
        <select>
          ${
            missingSelection
              ? `<option value="${escapeHtml(this._config.entity)}" selected>${escapeHtml(
                  this._config.entity
                )} (unavailable)</option>`
              : ""
          }
          ${players
            .map(
              (state) => `<option value="${escapeHtml(state.entity_id)}" ${
                state.entity_id === this._config.entity ? "selected" : ""
              }>${escapeHtml(stateName(state))}</option>`
            )
            .join("")}
        </select>
      </label>
      <label class="field">
        Card title (optional)
        <input type="text" value="${escapeHtml(this._config.name || "")}" />
      </label>
      ${this._renderItemSelector()}
      ${
        this._error
          ? `<div class="message error">${escapeHtml(this._error)}</div>`
          : ""
      }`;
    this.shadowRoot.querySelector("select")?.addEventListener("change", (event) =>
      this._changeEntity(event.currentTarget.value)
    );
    this.shadowRoot
      .querySelector('input[type="text"]')
      ?.addEventListener("change", (event) =>
        this._changed({ name: event.currentTarget.value.trim() })
      );
    this.shadowRoot.querySelector("[data-select-all]")?.addEventListener(
      "click",
      () => this._updateSelection(
        new Set(this._allItems().map((item) => item.media_content_id))
      )
    );
    this.shadowRoot.querySelector("[data-select-none]")?.addEventListener(
      "click",
      () => this._updateSelection(new Set())
    );
    for (const checkbox of this.shadowRoot.querySelectorAll("[data-media-id]")) {
      checkbox.addEventListener("change", (event) => {
        const selected = this._selectedIds();
        if (event.currentTarget.checked) {
          selected.add(event.currentTarget.dataset.mediaId);
        } else {
          selected.delete(event.currentTarget.dataset.mediaId);
        }
        this._updateSelection(selected);
      });
    }
    for (const checkbox of this.shadowRoot.querySelectorAll("[data-section-key]")) {
      const section = this._sections.find(
        (candidate) => candidate.key === checkbox.dataset.sectionKey
      );
      const selected = this._selectedIds();
      const selectedCount = section.items.filter((item) =>
        selected.has(item.media_content_id)
      ).length;
      checkbox.indeterminate =
        selectedCount > 0 && selectedCount < section.items.length;
      checkbox.addEventListener("change", (event) => {
        const next = this._selectedIds();
        for (const item of section.items) {
          if (event.currentTarget.checked) {
            next.add(item.media_content_id);
          } else {
            next.delete(item.media_content_id);
          }
        }
        this._updateSelection(next);
      });
    }
  }
}

class Control4RoomDashboardCardEditor extends HTMLElement {
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
    for (const key of ["area_id", "name", "background"]) {
      if (!config[key]) {
        delete config[key];
      }
    }
    this._config = config;
    this._render();
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }

  _changeEntity(entityId) {
    const updates = { entity: entityId };
    const previousEntityArea = entityAreaId(
      this._hass,
      this._config.entity
    );
    if (!this._config.area_id || this._config.area_id === previousEntityArea) {
      const areaId = entityAreaId(this._hass, entityId);
      if (areaId) {
        updates.area_id = areaId;
      } else {
        updates.area_id = "";
      }
    }
    this._changed(updates);
  }

  _render() {
    if (!this._hass) {
      return;
    }
    const players = control4BrowsePlayers(this._hass);
    const areas = Object.values(this._hass.areas || {}).sort((left, right) =>
      String(left.name || left.area_id).localeCompare(
        String(right.name || right.area_id)
      )
    );
    const selectedArea = this._config.area_id || "";
    const missingPlayer =
      this._config.entity &&
      !players.some((state) => state.entity_id === this._config.entity);
    const missingArea =
      selectedArea && !areas.some((area) => area.area_id === selectedArea);
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        * { box-sizing: border-box; }
        .intro {
          display: grid;
          grid-template-columns: 34px minmax(0, 1fr);
          gap: 10px;
          align-items: start;
          margin-bottom: 18px;
          padding: 12px;
          border-radius: 10px;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color);
          font-size: 13px;
          line-height: 1.45;
        }
        .intro ha-icon {
          color: var(--primary-color);
          --mdc-icon-size: 27px;
        }
        label {
          display: block;
          margin: 16px 0;
          color: var(--primary-text-color);
        }
        label span, label small { display: block; }
        label small {
          margin-top: 4px;
          color: var(--secondary-text-color);
          font-size: 12px;
          line-height: 1.35;
        }
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
      <div class="intro">
        <ha-icon icon="mdi:monitor-dashboard"></ha-icon>
        <span>
          Full-screen room interface with Home, Watch, Listen, Lighting,
          Comfort, Security, Intercom, Wake, Sleep, and room media. For the
          closest app-style layout, place this card in a Home Assistant Panel
          view.
        </span>
      </div>
      <label>
        <span>Control4 media player</span>
        <select data-field="entity">
          ${
            missingPlayer
              ? `<option value="${escapeHtml(
                  this._config.entity
                )}" selected>${escapeHtml(
                  this._config.entity
                )} (unavailable)</option>`
              : ""
          }
          ${players
            .map(
              (state) => `<option value="${escapeHtml(state.entity_id)}" ${
                state.entity_id === this._config.entity ? "selected" : ""
              }>${escapeHtml(stateName(state))}</option>`
            )
            .join("")}
        </select>
        <small>Provides Watch, Listen, TIDAL, TuneIn, and now-playing data.</small>
      </label>
      <label>
        <span>Home Assistant area</span>
        <select data-field="area_id">
          <option value="">Use the media player's area</option>
          ${
            missingArea
              ? `<option value="${escapeHtml(
                  selectedArea
                )}" selected>${escapeHtml(selectedArea)} (unavailable)</option>`
              : ""
          }
          ${areas
            .map(
              (area) => `<option value="${escapeHtml(area.area_id)}" ${
                area.area_id === selectedArea ? "selected" : ""
              }>${escapeHtml(area.name || area.area_id)}</option>`
            )
            .join("")}
        </select>
        <small>Lights, scenes, climate, and security entities come from this area.</small>
      </label>
      <label>
        <span>Room title (optional)</span>
        <input
          type="text"
          data-field="name"
          value="${escapeHtml(this._config.name || "")}"
          placeholder="Use the area name"
        />
      </label>
      <label>
        <span>Background image (optional)</span>
        <input
          type="text"
          data-field="background"
          value="${escapeHtml(this._config.background || "")}"
          placeholder="/local/living-room.jpg"
        />
        <small>Leave empty to use the selected Home Assistant area's picture.</small>
      </label>`;
    this.shadowRoot
      .querySelector('[data-field="entity"]')
      ?.addEventListener("change", (event) =>
        this._changeEntity(event.currentTarget.value)
      );
    this.shadowRoot
      .querySelector('[data-field="area_id"]')
      ?.addEventListener("change", (event) =>
        this._changed({ area_id: event.currentTarget.value })
      );
    for (const field of this.shadowRoot.querySelectorAll(
      '[data-field="name"], [data-field="background"]'
    )) {
      field.addEventListener("change", (event) =>
        this._changed({
          [event.currentTarget.dataset.field]: event.currentTarget.value.trim(),
        })
      );
    }
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
  if (!customElements.get(FAVORITES_EDITOR_TAG)) {
    customElements.define(
      FAVORITES_EDITOR_TAG,
      Control4MediaFavoritesCardEditor
    );
  }
  if (!customElements.get(FAVORITES_CARD_TAG)) {
    customElements.define(FAVORITES_CARD_TAG, Control4MediaFavoritesCard);
  }
  if (!customElements.get(ROOM_DASHBOARD_EDITOR_TAG)) {
    customElements.define(
      ROOM_DASHBOARD_EDITOR_TAG,
      Control4RoomDashboardCardEditor
    );
  }
  if (!customElements.get(ROOM_DASHBOARD_CARD_TAG)) {
    customElements.define(ROOM_DASHBOARD_CARD_TAG, Control4RoomDashboardCard);
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
  if (!window.customCards.some((card) => card.type === FAVORITES_CARD_TAG)) {
    window.customCards.push({
      type: FAVORITES_CARD_TAG,
      name: "Control4 Media Favorites",
      description: "Selectable one-tap Watch, Listen, TIDAL, and TuneIn media",
      preview: true,
      documentationURL: "https://github.com/eonnet/control4-ha#control4-media-favorites-card",
    });
  }
  if (!window.customCards.some((card) => card.type === ROOM_DASHBOARD_CARD_TAG)) {
    window.customCards.push({
      type: ROOM_DASHBOARD_CARD_TAG,
      name: "Control4 Room Dashboard",
      description: "Full-screen Control4-style room controls and media",
      preview: true,
      documentationURL: "https://github.com/eonnet/control4-ha#control4-room-dashboard-card",
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
