class AirTouchTimerPicker extends HTMLElement {
  static getStubConfig() {
    return {
      hours_entity: "input_number.airtouch_auto_off_hours",
      minutes_entity: "input_number.airtouch_auto_off_minute_block",
      start_entity: "script.airtouch_turn_on_with_timer",
      cancel_entity: "script.airtouch_cancel_auto_off",
      timer_entity: "timer.airtouch_auto_off",
    };
  }

  setConfig(config) {
    if (!config.hours_entity || !config.minutes_entity) {
      throw new Error("hours_entity and minutes_entity are required");
    }

    this._config = { ...AirTouchTimerPicker.getStubConfig(), ...config };
    this._hours = Array.from({ length: 9 }, (_, value) => value);
    this._minutes = [0, 15, 30, 45];
    this._pendingUpdates = {};
    this._scrollStartTimeouts = {};
    this._hasUserScrolled = {};
    this._selectedValues = {};
    this._optimisticValues = {};
    this._userScrolling = {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._sync();
  }

  disconnectedCallback() {
    clearInterval(this._countdownInterval);
    this._countdownInterval = undefined;
    for (const kind of ["hours", "minutes"]) {
      clearTimeout(this._pendingUpdates[kind]);
      clearTimeout(this._scrollStartTimeouts[kind]);
    }
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
          <div class="actions">
            <button class="action secondary" id="cancel" type="button">Cancel timer</button>
            <button class="action" id="start" type="button">Turn on &amp; start</button>
          </div>
        </div>
      </ha-card>
    `;

    for (const kind of ["hours", "minutes"]) {
      const wheel = root.querySelector(`[data-wheel="${kind}"]`);
      const markUserScroll = () => this._startUserScroll(kind);
      wheel.addEventListener("pointerdown", markUserScroll, { passive: true });
      wheel.addEventListener("wheel", markUserScroll, { passive: true });
      wheel.addEventListener("keydown", markUserScroll);
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

    root.querySelector("#start").addEventListener("click", () => {
      this._hass?.callService("script", "turn_on", {
        entity_id: this._config.start_entity,
      });
    });
    root.querySelector("#cancel").addEventListener("click", () => {
      this._hass?.callService("script", "turn_on", {
        entity_id: this._config.cancel_entity,
      });
    });
  }

  _wheelMarkup(kind, values, label) {
    return `
      <div class="wheel-frame">
        <div class="selection"></div>
        <div class="wheel" data-wheel="${kind}" aria-label="${label}" role="listbox">
          ${values
            .map(
              (value) => `
                <button class="option" data-value="${value}" type="button" role="option">
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
    const value = this._valueAtWheel(kind);

    this._markSelected(kind, value);
    if (!this._userScrolling[kind]) {
      return;
    }
    this._hasUserScrolled[kind] = true;
    clearTimeout(this._pendingUpdates[kind]);
    // Older browsers do not dispatch scrollend. This fallback is deliberately
    // longer than a wheel snap so it cannot commit an intermediate hour.
    this._pendingUpdates[kind] = setTimeout(
      () => this._commitUserScroll(kind),
      750,
    );
  }

  _wheelScrollEnded(kind) {
    if (this._userScrolling[kind] && this._hasUserScrolled[kind]) {
      this._commitUserScroll(kind);
    }
  }

  _commitUserScroll(kind) {
    clearTimeout(this._pendingUpdates[kind]);
    this._pendingUpdates[kind] = undefined;
    const settledValue = this._valueAtWheel(kind);
    this._markSelected(kind, settledValue);
    this._setValue(kind, settledValue);
    this._hasUserScrolled[kind] = false;
    this._userScrolling[kind] = false;
  }

  _startUserScroll(kind) {
    if (!this._userScrolling[kind]) {
      this._hasUserScrolled[kind] = false;
    }
    this._userScrolling[kind] = true;
    clearTimeout(this._scrollStartTimeouts[kind]);
    // A pointer or wheel event at the end of the list may not produce a
    // scroll event. Do not leave that wheel protected from sync indefinitely.
    this._scrollStartTimeouts[kind] = setTimeout(() => {
      if (!this._hasUserScrolled[kind]) {
        this._userScrolling[kind] = false;
      }
    }, 300);
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

    if (scroll) {
      clearTimeout(this._pendingUpdates[kind]);
      clearTimeout(this._scrollStartTimeouts[kind]);
      this._pendingUpdates[kind] = undefined;
      this._hasUserScrolled[kind] = false;
      this._userScrolling[kind] = false;
      wheel.scrollTo({ top: index * 44, behavior: "smooth" });
    }
    this._markSelected(kind, value);
    this._setValue(kind, value);
  }

  _setValue(kind, value) {
    if (!this._hass) {
      return;
    }

    this._selectedValues[kind] = value;
    this._optimisticValues[kind] = value;
    const entityId =
      kind === "hours"
        ? this._config.hours_entity
        : this._config.minutes_entity;
    this._hass.callService("input_number", "set_value", {
      entity_id: entityId,
      value,
    });
  }

  _sync() {
    if (!this.shadowRoot || !this._hass) {
      return;
    }

    this._syncWheel("hours", this._config.hours_entity, this._hours);
    this._syncWheel("minutes", this._config.minutes_entity, this._minutes);
    this._updateCountdown();
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
      status.textContent = "Auto-off countdown: Idle";
      return;
    }

    const finishesAt = Date.parse(timer.attributes.finishes_at);
    if (Number.isFinite(finishesAt)) {
      status.textContent = `Auto-off countdown: ${this._formatCountdown(
        finishesAt - Date.now(),
      )}`;
      if (!this._countdownInterval) {
        this._countdownInterval = setInterval(() => this._updateCountdown(), 1000);
      }
      return;
    }

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
    if (this._optimisticValues[kind] !== undefined) {
      if (this._optimisticValues[kind] !== value) {
        return;
      }
      delete this._optimisticValues[kind];
    }
    if (this._selectedValues[kind] === value) {
      return;
    }

    const wheel = this.shadowRoot.querySelector(`[data-wheel="${kind}"]`);
    wheel.scrollTo({ top: values.indexOf(value) * 44, behavior: "auto" });
    this._markSelected(kind, value);
  }

  _markSelected(kind, value) {
    this._selectedValues[kind] = value;
    const wheel = this.shadowRoot.querySelector(`[data-wheel="${kind}"]`);
    wheel.querySelectorAll(".option").forEach((option) => {
      const selected = Number(option.dataset.value) === value;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-selected", String(selected));
    });
  }
}

customElements.define("airtouch-timer-picker", AirTouchTimerPicker);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "airtouch-timer-picker",
  name: "AirTouch timer picker",
  description: "Touch-friendly hour and minute picker for the AirTouch auto-off timer.",
});
