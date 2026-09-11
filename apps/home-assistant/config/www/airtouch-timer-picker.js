class AirTouchTimerPicker extends HTMLElement {
  static getStubConfig() {
    return {
      hours_entity: "input_number.airtouch_auto_off_hours",
      minutes_entity: "input_number.airtouch_auto_off_minute_block",
      start_entity: "script.airtouch_turn_on_with_timer",
      cancel_entity: "script.airtouch_cancel_auto_off",
      timer_entity: "timer.airtouch_auto_off",
      climate_entity: "climate.at2plus_ac_0",
      armed_entity: "input_boolean.airtouch_auto_off_armed",
    };
  }

  setConfig(config) {
    if (!config.hours_entity || !config.minutes_entity) {
      throw new Error("hours_entity and minutes_entity are required");
    }

    this._cleanup();
    this._config = { ...AirTouchTimerPicker.getStubConfig(), ...config };
    this._hours = Array.from({ length: 9 }, (_, value) => value);
    this._minutes = [0, 15, 30, 45];
    this._pendingUpdates = {};
    this._selectedValues = {};
    this._optimisticValues = {};
    this._writes = {};
    this._ackTimeouts = {};
    this._pointerDown = {};
    this._busy = false;
    this._userScrolling = {};
    this._render();
    if (this.isConnected) this.connectedCallback();
  }

  set hass(hass) {
    this._hass = hass;
    this._sync();
  }

  connectedCallback() {
    if (!this._config) return;
    // HA may assign hass while the card is detached or its view is hidden.
    // Reconcile position after layout, even if the selected value is unchanged.
    this._resizeObserver?.disconnect();
    this._resizeObserver = new ResizeObserver(() => this._sync());
    this._resizeObserver.observe(this);
    this._sync();
  }

  disconnectedCallback() {
    this._cleanup();
  }

  _cleanup() {
    clearInterval(this._countdownInterval);
    this._countdownInterval = undefined;
    this._resizeObserver?.disconnect();
    for (const kind of ["hours", "minutes"]) {
      clearTimeout(this._pendingUpdates?.[kind]);
      clearTimeout(this._ackTimeouts?.[kind]);
    }
    this._userScrolling = {};
    this._pointerDown = {};
    this._optimisticValues = {};
  }

  getCardSize() {
    return 5;
  }

  _render() {
    if (!this._config) {
      return;
    }

    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        .content { padding: 16px; }
        .title { font-size: 1.1rem; font-weight: 500; margin-bottom: 12px; }
        .picker {
          align-items: center;
          display: grid;
          gap: 8px;
          grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto;
        }
        .wheel-frame { height: 176px; min-width: 82px; position: relative; }
        .wheel {
          -ms-overflow-style: none;
          box-sizing: border-box;
          height: 176px;
          overflow-y: auto;
          overscroll-behavior: contain;
          padding: 66px 0;
          scrollbar-width: none;
          scroll-snap-type: y mandatory;
          touch-action: pan-y;
        }
        .wheel::-webkit-scrollbar { display: none; }
        .option {
          margin: 0;
          padding: 0;
          align-items: center;
          background: transparent;
          border: 0;
          box-sizing: border-box;
          color: var(--secondary-text-color);
          cursor: pointer;
          display: flex;
          font: inherit;
          font-size: 1.05rem;
          height: 44px;
          justify-content: center;
          scroll-snap-align: center;
          transition: color 120ms ease, font-size 120ms ease;
          width: 100%;
        }
        .option.selected {
          color: var(--primary-text-color);
          font-size: 1.2rem;
          font-weight: 600;
        }
        .selection {
          background: color-mix(in srgb, var(--primary-color) 10%, transparent);
          border-bottom: 1px solid var(--primary-color);
          border-top: 1px solid var(--primary-color);
          height: 42px;
          left: 0;
          pointer-events: none;
          position: absolute;
          right: 0;
          top: 66px;
        }
        .unit { color: var(--secondary-text-color); font-size: .9rem; min-width: 42px; }
        .status {
          color: var(--secondary-text-color);
          font-size: .9rem;
          margin-top: 14px;
          text-align: center;
        }
        .actions { display: grid; gap: 10px; grid-template-columns: 1fr 1fr; margin-top: 14px; }
        .action {
          background: var(--primary-color);
          border: 0;
          border-radius: 20px;
          color: var(--text-primary-color);
          cursor: pointer;
          font: inherit;
          font-weight: 500;
          min-height: 40px;
          padding: 0 14px;
        }
        .action:disabled { opacity: .5; cursor: default; }
        .error { color: var(--error-color, #db4437); margin-top: 10px; }
        .action.secondary {
          background: transparent;
          border: 1px solid var(--primary-color);
          color: var(--primary-color);
        }
        .action:focus-visible, .option:focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 2px;
        }
      </style>
      <ha-card>
        <div class="content">
          <div class="title">AirTouch auto-off</div>
          <div class="picker">
            ${this._wheelMarkup("hours", this._hours, "Hours")}
            <span class="unit">hours</span>
            ${this._wheelMarkup("minutes", this._minutes, "Minutes")}
            <span class="unit">minutes</span>
          </div>
          <div class="status" id="status">Auto-off countdown: Idle</div>
          <div class="error" id="error" role="alert" hidden></div>
          <div class="actions">
            <button class="action secondary" id="cancel" type="button">Cancel timer</button>
            <button class="action" id="start" type="button">Turn on &amp; start</button>
          </div>
        </div>
      </ha-card>
    `;

    for (const kind of ["hours", "minutes"]) {
      const wheel = root.querySelector(`[data-wheel="${kind}"]`);
      wheel.addEventListener("pointerdown", () => {
        this._pointerDown[kind] = true;
        this._startUserScroll(kind);
      }, { passive: true });
      for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) {
        wheel.addEventListener(event, () => {
          this._pointerDown[kind] = false;
          this._scheduleCommit(kind);
        }, { passive: true });
      }
      wheel.addEventListener("pointerleave", (event) => {
        if (event.pointerType !== "touch") {
          this._pointerDown[kind] = false;
          this._scheduleCommit(kind);
        }
      }, { passive: true });
      wheel.addEventListener("wheel", () => this._startUserScroll(kind), { passive: true });
      wheel.addEventListener("keydown", (event) => {
        const values = kind === "hours" ? this._hours : this._minutes;
        const index = values.indexOf(this._selectedValues[kind]);
        const next = { ArrowUp: index - 1, ArrowDown: index + 1, Home: 0, End: values.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        this._selectValue(kind, values[Math.max(0, Math.min(values.length - 1, next))], true);
      });
      wheel.addEventListener("scroll", () => this._wheelScrolled(kind), {
        passive: true,
      });
      wheel.addEventListener("scrollend", () => this._wheelScrollEnded(kind));
      wheel.querySelectorAll(".option").forEach((option) => {
        option.addEventListener("click", () => {
          this._selectValue(kind, Number(option.dataset.value), true);
        });
      });
    }

    root.querySelector("#start").addEventListener("click", () => this._runAction(true));
    root.querySelector("#cancel").addEventListener("click", () => this._runAction(false));
  }

  async _runAction(start) {
    if (!this._hass || this._busy) return;
    this._showError("");
    const variables = {};
    if (start) {
      // Freeze momentum and use the numbers actually centred when Start is pressed.
      for (const kind of ["hours", "minutes"]) {
        if (this._userScrolling[kind]) this._commitUserScroll(kind);
      }
      variables.duration_minutes = this._selectedValues.hours * 60 + this._selectedValues.minutes;
      if (!Number.isFinite(variables.duration_minutes) || variables.duration_minutes <= 0) {
        this._showError("Select at least 15 minutes.");
        return;
      }
    }
    this._busy = true;
    this._sync();
    try {
      // Calling the named script waits for it and surfaces controller errors.
      // Explicit duration avoids racing delayed input_number service updates.
      const entity = start ? this._config.start_entity : this._config.cancel_entity;
      await this._hass.callService("script", entity.replace(/^script\./, ""), variables);
    } catch (error) {
      this._showError(error.message || "The request failed. Please try again.");
    } finally {
      this._busy = false;
      this._sync();
    }
  }

  _showError(message) {
    const error = this.shadowRoot?.querySelector("#error");
    if (!error) return;
    error.textContent = message;
    error.hidden = !message;
  }

  _wheelMarkup(kind, values, label) {
    return `
      <div class="wheel-frame">
        <div class="selection"></div>
        <div class="wheel" data-wheel="${kind}" aria-label="${label}" role="listbox" tabindex="0">
          ${values
            .map(
              (value) => `
                <button class="option" data-value="${value}" type="button" role="option" tabindex="-1">
                  ${String(value).padStart(2, "0")}
                </button>
              `,
            )
            .join("")}
        </div>
      </div>
    `;
  }

  _wheelScrolled(kind) {
    if (!this._userScrolling[kind]) return;
    this._markSelected(kind, this._valueAtWheel(kind));
    this._scheduleCommit(kind);
  }

  _scheduleCommit(kind) {
    clearTimeout(this._pendingUpdates[kind]);
    this._pendingUpdates[kind] = setTimeout(() => {
      if (this._pointerDown[kind]) {
        this._scheduleCommit(kind);
      } else if (this._userScrolling[kind]) {
        this._commitUserScroll(kind);
      }
    }, 250);
  }

  _wheelScrollEnded(kind) {
    if (this._userScrolling[kind] && !this._pointerDown[kind]) {
      this._commitUserScroll(kind);
    }
  }

  _commitUserScroll(kind) {
    const value = this._valueAtWheel(kind);
    this._selectValue(kind, value, true);
  }

  _startUserScroll(kind) {
    if (this._busy) return;
    this._userScrolling[kind] = true;
    this._scheduleCommit(kind);
  }

  _valueAtWheel(kind) {
    const wheel = this.shadowRoot.querySelector(`[data-wheel="${kind}"]`);
    const values = kind === "hours" ? this._hours : this._minutes;
    const index = Math.max(
      0,
      Math.min(values.length - 1, Math.round(wheel.scrollTop / 44)),
    );
    return values[index];
  }

  _selectValue(kind, value, scroll) {
    const wheel = this.shadowRoot.querySelector(`[data-wheel="${kind}"]`);
    const values = kind === "hours" ? this._hours : this._minutes;
    const index = values.indexOf(value);
    if (index === -1) {
      return;
    }

    if (this._busy) return;
    if (scroll) {
      clearTimeout(this._pendingUpdates[kind]);
      this._pendingUpdates[kind] = undefined;
      this._userScrolling[kind] = false;
      this._pointerDown[kind] = false;
      // Immediate positioning prevents an animation from changing the highlighted
      // value after a tap or after Start snapshots the selected duration.
      wheel.scrollTo({ top: index * 44, behavior: "instant" });
    }
    this._markSelected(kind, value);
    this._setValue(kind, value);
  }

  _setValue(kind, value) {
    if (!this._hass) return;
    const entityId = kind === "hours" ? this._config.hours_entity : this._config.minutes_entity;
    const writes = this._writes;
    const optimistic = this._optimisticValues;
    optimistic[kind] = value;
    clearTimeout(this._ackTimeouts[kind]);
    // Serialize writes per helper so a slower earlier request cannot win.
    writes[kind] = (writes[kind] || Promise.resolve()).then(async () => {
      try {
        await this._hass.callService("input_number", "set_value", { entity_id: entityId, value });
        if (this._optimisticValues !== optimistic || optimistic[kind] !== value) return;
        // A lost state acknowledgement must never block external changes forever.
        this._ackTimeouts[kind] = setTimeout(() => {
          delete optimistic[kind];
          this._sync();
        }, 2000);
      } catch (error) {
        if (this._optimisticValues !== optimistic || optimistic[kind] !== value) return;
        delete optimistic[kind];
        this._showError(error.message || "Could not save the timer selection.");
        this._sync();
      }
    });
    this._updateActions();
  }

  _sync() {
    if (!this.shadowRoot || !this._hass) {
      return;
    }

    this._syncWheel("hours", this._config.hours_entity, this._hours);
    this._syncWheel("minutes", this._config.minutes_entity, this._minutes);
    this._updateCountdown();
    this._updateActions();
  }

  _updateActions() {
    if (!this.shadowRoot || !this._hass) return;
    const timer = this._hass.states[this._config.timer_entity];
    const climate = this._hass.states[this._config.climate_entity];
    const unavailable = !climate || ["unknown", "unavailable"].includes(climate.state);
    const valid = this._hours.includes(this._selectedValues.hours) && this._minutes.includes(this._selectedValues.minutes);
    this.shadowRoot.querySelector("#start").disabled = this._busy || unavailable || !valid || this._selectedValues.hours * 60 + this._selectedValues.minutes <= 0;
    this.shadowRoot.querySelector("#cancel").disabled = this._busy || !timer || ["unknown", "unavailable"].includes(timer.state);
    this.shadowRoot.querySelector("#start").textContent = this._busy ? "Please wait…" : "Turn on & start";
    for (const kind of ["hours", "minutes"]) {
      this.shadowRoot.querySelector(`[data-wheel="${kind}"]`).style.pointerEvents = this._busy ? "none" : "";
    }
  }

  _updateCountdown() {
    if (!this.shadowRoot || !this._hass) {
      return;
    }

    const timer = this._hass.states[this._config.timer_entity];
    const status = this.shadowRoot.querySelector("#status");
    if (timer?.state !== "active") {
      clearInterval(this._countdownInterval);
      this._countdownInterval = undefined;
      const state = timer?.state;
      status.textContent = state === "paused"
        ? `Auto-off countdown: Paused (${timer.attributes.remaining || "—"})`
        : state === "idle"
          ? this._hass.states[this._config.armed_entity]?.state === "on"
            ? "Auto-off pending: waiting for confirmed shutdown"
            : "Auto-off countdown: Idle"
          : "Auto-off countdown: Unavailable";
      return;
    }

    const finishesAt = Date.parse(timer.attributes.finishes_at);
    if (Number.isFinite(finishesAt)) {
      status.textContent = `Auto-off countdown: ${this._formatCountdown(
        finishesAt - Date.now(),
      )}`;
      if (this.isConnected && !this._countdownInterval) {
        this._countdownInterval = setInterval(() => this._updateCountdown(), 1000);
      }
      return;
    }

    clearInterval(this._countdownInterval);
    this._countdownInterval = undefined;
    // Fallback for timer providers that do not expose an absolute finish time.
    status.textContent = `Auto-off countdown: ${timer.attributes.remaining || "Active"}`;
  }

  _formatCountdown(milliseconds) {
    const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainingSeconds = seconds % 60;
    return [hours, minutes, remainingSeconds]
      .map((value) => String(value).padStart(2, "0"))
      .join(":");
  }

  _syncWheel(kind, entityId, values) {
    const value = Number(this._hass.states[entityId]?.state);
    if (!values.includes(value)) {
      return;
    }
    // Home Assistant can publish unrelated state updates before the delayed
    // input_number update arrives. Keep the user's in-progress wheel position
    // instead of scrolling it back to that older entity value.
    if (this._userScrolling[kind]) {
      return;
    }
    let selected = value;
    if (this._optimisticValues[kind] !== undefined) {
      selected = this._optimisticValues[kind];
      if (selected === value) {
        clearTimeout(this._ackTimeouts[kind]);
        delete this._optimisticValues[kind];
      }
    }
    const wheel = this.shadowRoot.querySelector(`[data-wheel="${kind}"]`);
    // No layout means scrollTo is a no-op. ResizeObserver retries when visible.
    if (wheel.clientHeight === 0) return;
    const top = values.indexOf(selected) * 44;
    if (Math.abs(wheel.scrollTop - top) > 0.5) {
      wheel.scrollTo({ top, behavior: "instant" });
    }
    this._markSelected(kind, selected);
  }

  _markSelected(kind, value) {
    this._selectedValues[kind] = value;
    const wheel = this.shadowRoot.querySelector(`[data-wheel="${kind}"]`);
    wheel.querySelectorAll(".option").forEach((option) => {
      const selected = Number(option.dataset.value) === value;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-selected", String(selected));
    });
    this._updateActions();
  }
}

if (!customElements.get("airtouch-timer-picker")) {
  customElements.define("airtouch-timer-picker", AirTouchTimerPicker);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "airtouch-timer-picker")) window.customCards.push({
  type: "airtouch-timer-picker",
  name: "AirTouch timer picker",
  description: "Touch-friendly hour and minute picker for the AirTouch auto-off timer.",
});
