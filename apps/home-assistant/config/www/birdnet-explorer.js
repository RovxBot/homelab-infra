/* BirdNET explorer: HA authentication, database totals, and signed clip playback. */
(() => {
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const https = (value) => {
    try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : ''; }
    catch { return ''; }
  };
  const percent = (value) => `${Math.round(Number(value || 0) * 100)}%`;
  const localDay = (zone, when = new Date()) => new Intl.DateTimeFormat('en-CA', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(when);
  const shiftDay = (day, amount) => {
    const date = new Date(`${day}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + amount);
    return date.toISOString().slice(0, 10);
  };
  function reviewLabel(value) {
    return value === 'correct' ? 'Reviewed correct' : value === 'false_positive' ? 'Rejected' : 'Unverified';
  }
  function filtersQuery(filters) {
    return new URLSearchParams({ date: filters.date, source: filters.source,
      confidence: String(filters.confidence), search: filters.search,
      offset: String(filters.offset), rejected: filters.rejected ? '1' : '0' }).toString();
  }

  class BirdnetExplorer extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._filters = { date: '', source: '', confidence: 0, search: '', offset: 0, rejected: false };
      this._sequence = 0;
      this._visibility = () => { if (!document.hidden) this._load(); };
    }
    setConfig(config) {
      this._config = config;
      this._base = https(config.birdnet_url || 'https://birds.cooked.beer');
      if (!this._base) throw new Error('birdnet_url must be an HTTPS URL');
      if (!this.shadowRoot.querySelector('.explorer')) this._build();
    }
    set hass(value) {
      const previous = this._hass;
      this._hass = value;
      this.toggleAttribute('dark', Boolean(value.themes?.darkMode));
      if (!this._config) return;
      const zone = value.config.time_zone || 'Australia/Adelaide';
      this._zone = zone;
      if (!this._filters.date) {
        this._filters.date = localDay(zone);
        this._el('date').value = this._filters.date;
        this._el('date').max = localDay(zone);
        this._load();
      }
      this._updateLive();
      const entity = 'sensor.birdnet_latest_detection';
      if (previous && previous.states[entity] !== value.states[entity] && Date.now() - (this._loadedAt || 0) > 5000) {
        clearTimeout(this._eventRefresh);
        this._eventRefresh = setTimeout(() => this._load(), 700);
      }
    }
    connectedCallback() {
      if (!this._poll) this._poll = setInterval(() => { if (!document.hidden) this._load(); }, 20000);
      document.addEventListener('visibilitychange', this._visibility);
      if (this._hass && this._filters.date) this._load();
    }
    disconnectedCallback() {
      clearInterval(this._poll); this._poll = null;
      clearTimeout(this._eventRefresh); clearTimeout(this._searchTimer);
      document.removeEventListener('visibilitychange', this._visibility);
      this._el('audio')?.pause();
      this._sequence++;
    }
    getCardSize() { return 16; }
    _el(id) { return this.shadowRoot.getElementById(id); }
    _time(epoch) {
      return epoch ? new Intl.DateTimeFormat(this._hass?.locale?.language || 'en-AU', {
        timeZone: this._zone, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      }).format(new Date(epoch * 1000)) : '—';
    }
    _photo(image, name, className = '') {
      const url = https(image?.url);
      return url ? `<img class="${className}" loading="lazy" referrerpolicy="no-referrer" src="${esc(url)}" alt="${esc(name)}">`
        : `<div class="placeholder ${className}" aria-label="No photograph available"><ha-icon icon="mdi:bird"></ha-icon></div>`;
    }
    _credit(image) {
      if (!https(image?.url)) return '';
      const license = https(image.license_url);
      const author = https(image.author_url);
      return `<span class="credit">${author ? `<a href="${esc(author)}" target="_blank" rel="noopener noreferrer">${esc(image.author_name || 'Photographer')}</a>` : esc(image.author_name || 'Species photograph')}${image.license_name ? ' · ' : ''}${license
        ? `<a href="${esc(license)}" target="_blank" rel="noopener noreferrer">${esc(image.license_name || 'License')}</a>`
        : esc(image.license_name)}</span>`;
    }
    _reviewLink(row) {
      const url = new URL('ui/detections', this._base.endsWith('/') ? this._base : this._base + '/');
      url.search = new URLSearchParams({ queryType: 'species', species: row.scientific_name,
        date: this._data?.date || this._filters.date, numResults: '25', offset: '0' });
      return url.href;
    }
    _build() {
      this.shadowRoot.innerHTML = `<style>${STYLES}</style>
        <div class="explorer">
          <header><div class="brand"><span class="birdmark"><ha-icon icon="mdi:bird"></ha-icon></span><div>
            <p class="eyebrow">AROUND HOME</p><h1>Hartley birdwatch</h1><p class="sub">Detections, recordings and daily activity.</p>
          </div></div><div class="header-actions"><span id="status" class="status">Connecting</span>
            <a class="button secondary" href="${esc(this._base)}" target="_blank" rel="noopener noreferrer">Open BirdNET <span aria-hidden="true">↗</span></a>
          </div></header>
          <div class="toolbar"><div class="date-controls"><button id="previous-day" aria-label="Previous day">‹</button>
            <input id="date" type="date" aria-label="Detection date" min="2000-01-01"><button id="next-day" aria-label="Next day">›</button>
            <button id="today" class="text-button">Today</button></div>
            <div class="refresh"><span id="updated" class="muted" aria-live="polite"></span><button id="refresh" aria-label="Refresh detections"><ha-icon icon="mdi:refresh"></ha-icon></button></div>
          </div>
          <div id="error" class="error" role="alert" hidden></div>
          <div class="filters"><label class="search"><ha-icon icon="mdi:magnify"></ha-icon><input id="search" type="search" placeholder="Search birds…" aria-label="Search bird names" maxlength="80"></label>
            <label>Microphone<select id="source" aria-label="Microphone"><option value="">Both cameras</option></select></label>
            <label>Confidence<select id="confidence" aria-label="Minimum confidence"><option value="0">All scores</option><option value="0.8">80% or higher</option><option value="0.9">90% or higher</option><option value="0.95">95% or higher</option></select></label>
            <label class="check"><input id="rejected" type="checkbox"> Include rejected</label><button id="clear" class="text-button">Clear filters</button></div>
          <div id="metrics" class="metrics" aria-live="polite"><div class="metric">Loading detections…</div></div>
          <div class="overview"><section class="surface"><div class="section-heading"><div><p class="eyebrow">THROUGH THE DAY</p><h2>Hourly activity</h2></div><span id="activity-count" class="muted"></span></div><div id="chart" class="chart"></div></section>
            <section id="latest" class="surface latest"><div class="empty">Waiting for detections</div></section></div>
          <section class="surface species-section"><div class="section-heading"><div><p class="eyebrow">THE DAILY LINEUP</p><h2>Species detected</h2></div><span class="muted">Select a bird to filter</span></div><div id="species" class="species-grid"></div></section>
          <section class="surface"><div class="section-heading"><div><p class="eyebrow">LISTEN & REVIEW</p><h2>Detection journal</h2></div><span id="result-count" class="muted"></span></div>
            <p class="note">Model identifications need review. Rejected detections are hidden unless selected above.</p><div id="detections"></div>
            <nav class="pagination" aria-label="Detection pages"><button id="previous-page">← Newer</button><span id="page" class="muted"></span><button id="next-page">Older →</button></nav></section>
          <div class="bottom-grid"><section class="surface"><div class="section-heading"><div><p class="eyebrow">IN THE GARDEN</p><h2>Camera microphones</h2></div><span class="muted">Live sound levels</span></div><div id="cameras" class="cameras"></div></section>
            <section class="surface"><div class="section-heading"><div><p class="eyebrow">KEEP ME POSTED</p><h2>Phone alerts</h2></div><button id="alerts-toggle" class="button" aria-pressed="false">Loading…</button></div>
              <div class="alert-controls"><label>Minimum confidence <output id="alert-confidence-value"></output><input id="alert-confidence" type="range" min="80" max="100" step="1"></label>
                <label>Cooldown <output id="alert-cooldown-value"></output><input id="alert-cooldown" type="range" min="1" max="120" step="1"></label></div>
              <p class="note">One shared cooldown across both cameras. Rachael’s phone receives alerts while locally connected.</p></section></div>
          <div id="player" class="player" hidden><div><span class="eyebrow">RECORDING</span><strong id="playing-name"></strong><span id="playing-info" class="muted"></span><span id="audio-error" class="audio-error" role="status"></span></div><audio id="audio" controls preload="none"></audio><button id="close-player" aria-label="Close recording">×</button></div>
        </div>`;
      this._el('previous-day').onclick = () => this._setDay(shiftDay(this._filters.date, -1));
      this._el('next-day').onclick = () => this._setDay(shiftDay(this._filters.date, 1));
      this._el('today').onclick = () => this._setDay(localDay(this._zone));
      this._el('date').onchange = (e) => this._setDay(e.target.value);
      this._el('refresh').onclick = () => this._load();
      this._el('search').oninput = (e) => {
        this._filters.search = e.target.value; this._filters.offset = 0;
        clearTimeout(this._searchTimer); this._searchTimer = setTimeout(() => this._load(), 350);
      };
      for (const id of ['source', 'confidence', 'rejected']) this._el(id).onchange = (e) => {
        this._filters[id] = id === 'rejected' ? e.target.checked : id === 'confidence' ? Number(e.target.value) : e.target.value;
        this._filters.offset = 0; this._load();
      };
      this._el('clear').onclick = () => {
        Object.assign(this._filters, { source: '', confidence: 0, search: '', offset: 0, rejected: false });
        for (const id of ['search', 'source', 'confidence']) this._el(id).value = id === 'confidence' ? '0' : '';
        this._el('rejected').checked = false; this._load();
      };
      this._el('previous-page').onclick = () => { this._filters.offset = Math.max(0, this._filters.offset - 24); this._load(); };
      this._el('next-page').onclick = () => { this._filters.offset += 24; this._load(); };
      this.shadowRoot.addEventListener('click', (e) => {
        const bird = e.target.closest('[data-species]');
        if (bird) { this._filters.search = bird.dataset.species; this._el('search').value = bird.dataset.species; this._filters.offset = 0; this._load(); }
        const listen = e.target.closest('[data-listen]');
        if (listen) this._play(Number(listen.dataset.listen));
      });
      this._el('alerts-toggle').onclick = () => this._service('input_boolean', 'toggle', { entity_id: 'input_boolean.birdnet_phone_alerts' });
      for (const [id, entity, suffix] of [['alert-confidence', 'input_number.birdnet_alert_confidence', '%'], ['alert-cooldown', 'input_number.birdnet_alert_cooldown', ' min']]) {
        this._el(id).setAttribute('aria-label', id === 'alert-confidence' ? 'Alert minimum confidence' : 'Alert cooldown minutes');
        this._el(id).oninput = (e) => { this._el(id + '-value').textContent = e.target.value + suffix; };
        this._el(id).onchange = (e) => this._service('input_number', 'set_value', { entity_id: entity, value: Number(e.target.value) });
      }
      this._el('close-player').onclick = () => { this._el('audio').pause(); this._el('player').hidden = true; this._playingId = null; };
      this._el('audio').onerror = () => { this._el('audio-error').textContent = 'Recording unavailable. Select Listen to retry.'; };
      this.shadowRoot.addEventListener('error', (e) => {
        if (e.target.tagName === 'IMG') {
          const fallback = document.createElement('div');
          fallback.className = 'placeholder ' + e.target.className;
          fallback.setAttribute('aria-label', 'No photograph available');
          fallback.innerHTML = '<ha-icon icon="mdi:bird"></ha-icon>';
          e.target.replaceWith(fallback);
        }
      }, true);
    }
    _setDay(day) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day > localDay(this._zone) || day < '2000-01-01') return;
      this._filters.date = day; this._filters.offset = 0; this._el('date').value = day;
      this._load();
    }
    async _service(domain, service, data) {
      try { await this._hass.callService(domain, service, data); }
      catch { this._el('error').hidden = false; this._el('error').textContent = 'Unable to update alerts. Check your connection and try again.'; }
    }
    async _load() {
      if (!this._hass || !this._filters.date || !this.isConnected) return;
      const sequence = ++this._sequence;
      this._el('refresh').disabled = true;
      this._el('next-day').disabled = this._filters.date >= localDay(this._zone);
      this._el('previous-day').disabled = this._filters.date <= '2000-01-01';
      try {
        const data = await this._hass.callApi('GET', 'birdnet_dashboard/dashboard?' + filtersQuery(this._filters));
        if (sequence !== this._sequence) return;
        if (this._filters.offset && this._filters.offset >= data.totals.detections) {
          this._filters.offset = Math.max(0, Math.floor((data.totals.detections - 1) / 24) * 24);
          this._load(); return;
        }
        this._data = data; this._loadedAt = Date.now();
        this._el('error').hidden = true;
        this._el('updated').textContent = `Updated ${this._time(Date.now() / 1000)}`;
        const fingerprint = JSON.stringify([data.date, data.offset, data.totals, data.species, data.detections]);
        if (fingerprint !== this._fingerprint) { this._fingerprint = fingerprint; this._renderData(); }
        this._updateLive();
      } catch {
        if (sequence !== this._sequence) return;
        this._el('error').hidden = false;
        this._el('error').textContent = 'Bird detections could not be refreshed. Check the connection and try Refresh.';
        this._el('updated').textContent = 'Updates paused';
      } finally { if (sequence === this._sequence) this._el('refresh').disabled = false; }
    }
    _renderData() {
      const data = this._data, totals = data.totals;
      this._el('metrics').innerHTML = [['Detections', totals.detections], ['Species', totals.species], ['Highest confidence', percent(totals.highest_confidence)], ['Latest call', this._time(totals.latest)]]
        .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${esc(value)}</strong></div>`).join('');
      this._el('activity-count').textContent = `${totals.detections} detections`;
      const maximum = Math.max(1, ...data.hourly);
      this._el('chart').innerHTML = `<div class="bars" role="img" aria-label="${esc(data.hour_labels.map((h, i) => `${h}: ${data.hourly[i]}`).join('; '))}">${data.hourly.map((count, i) =>
        `<div class="bar-column" title="${esc(data.hour_labels[i])} · ${count} detections"><div class="bar" style="height:${count ? Math.max(3, count / maximum * 100) : 0}%"><span>${count || ''}</span></div><span class="hour">${i % 3 === 0 || i === data.hourly.length - 1 ? esc(data.hour_labels[i].slice(0, 2)) : ''}</span></div>`).join('')}</div>
        <p class="chart-caption">${totals.detections ? 'Activity across the selected day and filters' : 'No detections on this day with these filters'}</p>`;
      const latest = data.detections[0];
      this._el('latest').innerHTML = latest ? `${this._photo(latest.image, latest.common_name, 'latest-photo')}<div class="latest-copy"><p class="eyebrow">LATEST IN VIEW</p><h2>${esc(latest.common_name)}</h2><p class="scientific">${esc(latest.scientific_name)}</p><p>${esc(latest.source || 'Unknown microphone')} · ${this._time(latest.detected_at)}</p><div class="badges"><span class="confidence">${percent(latest.confidence)}</span><span class="review ${esc(latest.review)}">${reviewLabel(latest.review)}</span></div><button class="button" data-listen="${latest.id}" ${latest.has_audio ? '' : 'disabled'}><ha-icon icon="mdi:play"></ha-icon> ${latest.has_audio ? 'Listen to call' : 'No recording'}</button>${this._credit(latest.image)}</div>`
        : '<div class="empty"><ha-icon icon="mdi:bird"></ha-icon><h2>No detections in this view</h2><p>Choose another date or clear the filters.</p></div>';
      this._el('species').innerHTML = data.species.length ? data.species.map((bird) => `<div class="species-card"><button class="species-select" data-species="${esc(bird.scientific_name)}" aria-label="Filter ${esc(bird.common_name)}">${this._photo(bird.image, bird.common_name)}<span class="species-content"><strong>${esc(bird.common_name)}</strong><em>${esc(bird.scientific_name)}</em><span class="species-meta"><b>${bird.count} ${bird.count === 1 ? 'detection' : 'detections'}</b><span>Last ${this._time(bird.last)}</span></span></span></button>${this._credit(bird.image)}</div>`).join('')
        : '<p class="empty">No species match the selected filters.</p>';
      this._el('result-count').textContent = `${totals.detections} matching detections`;
      this._el('detections').innerHTML = data.detections.length ? data.detections.map((row) => `<article class="detection"><div class="row-photo">${this._photo(row.image, row.common_name)}</div><div class="row-bird"><strong>${esc(row.common_name)}</strong><span class="scientific">${esc(row.scientific_name)}</span><span class="row-context">${this._time(row.detected_at)} · ${esc(row.source || 'Unknown')} · ${esc(row.model)} ${esc(row.model_version)}</span></div><div class="row-scores"><b class="confidence">${percent(row.confidence)}</b><span class="review ${esc(row.review)}">${reviewLabel(row.review)}</span></div><div class="row-actions"><button data-listen="${row.id}" ${row.has_audio ? '' : 'disabled'} aria-label="Listen to ${esc(row.common_name)} at ${this._time(row.detected_at)}"><ha-icon icon="mdi:play"></ha-icon><span>${row.has_audio ? 'Listen' : 'No clip'}</span></button><a href="${esc(this._reviewLink(row))}" target="_blank" rel="noopener noreferrer">Review ↗</a></div></article>`).join('')
        : '<p class="empty">No detections to display.</p>';
      this._el('previous-page').disabled = data.offset === 0;
      this._el('next-page').disabled = !data.has_more;
      this._el('page').textContent = totals.detections ? `${data.offset + 1}–${data.offset + data.detections.length} of ${totals.detections}` : '0 detections';
      const options = `<option value="">Both cameras</option>${data.sources.map((source) => `<option value="${source.id}">${esc(source.name || 'Microphone ' + source.id)}</option>`).join('')}`;
      if (options !== this._sourceOptions) { this._sourceOptions = options; this._el('source').innerHTML = options; this._el('source').value = this._filters.source; }
    }
    _updateLive() {
      if (!this._hass || !this._el('status')) return;
      const states = this._hass.states;
      const online = states['binary_sensor.cooked_birds_status']?.state === 'on';
      this._el('status').textContent = online ? 'BirdNET online' : 'BirdNET offline';
      this._el('status').classList.toggle('online', online);
      const cameras = [['shed_front', 'Shed front'], ['shed', 'Shed']].map(([key, name]) => {
        const prefix = 'sensor.cooked_birds_' + key;
        const sound = states[prefix + '_sound_level'];
        const fresh = online && sound && Date.now() - new Date(sound.last_updated).getTime() < 90000;
        const numeric = sound && !['unknown', 'unavailable'].includes(sound.state) && Number.isFinite(Number(sound.state));
        const level = numeric ? Number(sound.state) : null;
        const bird = states[prefix + '_last_species']?.state;
        const confidence = states[prefix + '_confidence']?.state;
        return `<div class="camera"><div class="camera-title"><strong>${name}</strong><span class="mic-state ${fresh ? 'active' : ''}">${fresh ? 'Receiving audio' : 'No recent audio'}</span></div><div class="sound"><span>${level === null ? '—' : level.toFixed(1)} <small>${esc(sound?.attributes?.unit_of_measurement || 'dB')}</small></span><div class="meter"><i style="width:${level === null ? 0 : Math.max(0, Math.min(100, (level + 80) / 80 * 100))}%"></i></div></div><p class="muted">Last reported: <b>${bird && !['unknown', 'unavailable'].includes(bird) ? esc(bird) : 'Waiting for a call'}</b>${confidence && !['unknown', 'unavailable'].includes(confidence) ? ' · ' + esc(confidence) + '%' : ''}</p></div>`;
      }).join('');
      if (this._el('cameras').innerHTML !== cameras) this._el('cameras').innerHTML = cameras;
      const enabled = states['input_boolean.birdnet_phone_alerts']?.state === 'on';
      const available = ['on', 'off'].includes(states['input_boolean.birdnet_phone_alerts']?.state);
      const toggle = this._el('alerts-toggle');
      toggle.textContent = enabled ? 'Alerts on' : 'Alerts off'; toggle.disabled = !available;
      toggle.setAttribute('aria-pressed', String(enabled)); toggle.classList.toggle('enabled', enabled);
      for (const [id, entity, fallback, suffix] of [['alert-confidence', 'input_number.birdnet_alert_confidence', 90, '%'], ['alert-cooldown', 'input_number.birdnet_alert_cooldown', 30, ' min']]) {
        const state = states[entity], valid = state && !['unknown', 'unavailable'].includes(state.state);
        const value = valid ? Number(state.state) : fallback;
        if (this.shadowRoot.activeElement !== this._el(id)) { this._el(id).value = String(value); this._el(id + '-value').textContent = value + suffix; }
        this._el(id).disabled = !valid;
      }
    }
    async _play(id) {
      const row = this._data?.detections.find((item) => item.id === id);
      if (!row?.has_audio) return;
      this._playingId = id;
      const audio = this._el('audio'); audio.pause();
      this._el('player').hidden = false;
      this._el('playing-name').textContent = row.common_name;
      this._el('playing-info').textContent = `${row.source || 'Unknown'} · ${this._time(row.detected_at)} · ${percent(row.confidence)} · ${reviewLabel(row.review)}`;
      this._el('audio-error').textContent = '';
      try {
        const signed = await this._hass.callWS({ type: 'auth/sign_path', path: '/api/birdnet_dashboard/audio/' + id, expires: 300 });
        if (this._playingId !== id || !this.isConnected) return;
        audio.src = signed.path;
        try { await audio.play(); } catch { this._el('audio-error').textContent = 'Press play to listen.'; }
      } catch { this._el('audio-error').textContent = 'Unable to load this recording. Try Listen again.'; }
    }
  }

  const STYLES = `
    :host{display:block;color:var(--primary-text-color,#20372e);font-family:var(--primary-font-family,Roboto,Arial,sans-serif)}*{box-sizing:border-box}[hidden]{display:none!important}
    .explorer{max-width:1480px;margin:auto;padding:26px 28px 44px}h1,h2,p{margin:0}h1{font-size:30px;font-weight:650;letter-spacing:-.8px}h2{font-size:19px;font-weight:600;letter-spacing:-.25px}a{color:var(--primary-color,#317453);text-decoration:none}a:hover{text-decoration:underline}
    header,.brand,.header-actions,.toolbar,.date-controls,.refresh,.section-heading,.badges,.pagination,.camera-title,.species-meta{display:flex;align-items:center;gap:12px}header,.toolbar,.section-heading,.pagination,.camera-title,.species-meta{justify-content:space-between}header{margin-bottom:26px}.brand{gap:16px}.birdmark{background:#e4efe5;color:#376b46;border-radius:18px;display:grid;place-items:center;width:64px;height:64px}.birdmark ha-icon{--mdc-icon-size:35px}.eyebrow{font-size:10px;letter-spacing:1.7px;font-weight:700;color:var(--secondary-text-color,#6e7c73);margin-bottom:5px}.sub{font-size:14px;color:var(--secondary-text-color);margin-top:6px}
    button,input,select{font:inherit;color:inherit}button,.button{border:1px solid var(--divider-color,#dce2dc);border-radius:9px;background:var(--ha-card-background,var(--card-background-color,#fff));padding:9px 13px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:5px;font-size:13px;font-weight:500;white-space:nowrap;text-decoration:none}button:hover:not(:disabled),.button:hover{background:var(--secondary-background-color,#f3f6f2);text-decoration:none}button:disabled{opacity:.4;cursor:default}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--primary-color,#317453);outline-offset:3px}.text-button{border:0;background:none;color:var(--primary-color,#317453)}.status{font-size:12px;display:inline-flex;align-items:center;gap:6px}.status:before,.mic-state:before{content:'';width:7px;height:7px;border-radius:50%;background:#a5a9a6}.status.online:before,.mic-state.active:before{background:#4b9866}.toolbar{margin-bottom:15px}.date-controls>button:not(.text-button){font-size:22px;padding:2px 13px;min-height:38px}.date-controls input{background:var(--ha-card-background,var(--card-background-color,#fff));border:1px solid var(--divider-color,#dce2dc);border-radius:8px;padding:8px;font-size:14px;color-scheme:var(--ha-color-scheme,light dark)}.refresh .muted{font-size:11px}.refresh button{padding:8px}.refresh ha-icon{--mdc-icon-size:19px}
    .filters{display:flex;align-items:end;flex-wrap:wrap;gap:12px;margin-bottom:20px}.filters label:not(.check){display:flex;flex-direction:column;gap:6px;font-size:11px;color:var(--secondary-text-color)}.filters .search{position:relative;flex:1;min-width:190px;display:block!important}.search ha-icon{position:absolute;top:10px;left:10px;--mdc-icon-size:19px}.filters input[type=search],.filters select{border:1px solid var(--divider-color,#dce2dc);background:var(--ha-card-background,var(--card-background-color,#fff));border-radius:8px;min-height:40px;padding:8px 10px;color:var(--primary-text-color);font-size:13px}.filters input[type=search]{width:100%;padding-left:36px}.check{display:flex;align-items:center;gap:5px;font-size:12px;min-height:40px}.check input{accent-color:var(--primary-color,#317453)}.filters .text-button{min-height:40px}.muted,.note{color:var(--secondary-text-color,#718078);font-size:12px}.note{line-height:1.6;margin-top:10px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}.metric,.surface{background:var(--ha-card-background,var(--card-background-color,#fff));border:1px solid var(--divider-color,#dde3dc);border-radius:14px}.metric{padding:18px 21px;display:flex;flex-direction:column;gap:10px}.metric>span{font-size:12px;color:var(--secondary-text-color)}.metric>strong{font-size:30px;letter-spacing:-1px;font-weight:600}.surface{padding:22px;min-width:0}.section-heading{margin-bottom:20px}.overview{display:grid;grid-template-columns:1.35fr 1fr;gap:20px;margin-bottom:20px}
    .bars{height:170px;display:flex;gap:5px;align-items:end;padding-top:16px;border-bottom:1px solid var(--divider-color);margin-bottom:28px}.bar-column{height:100%;position:relative;flex:1;display:flex;align-items:end;min-width:0}.bar{background:#69a17b;width:100%;border-radius:4px 4px 0 0;position:relative;min-height:0}.bar-column:hover .bar{background:#37734c}.bar>span{position:absolute;bottom:100%;left:50%;transform:translateX(-50%);font-size:10px;padding-bottom:3px;color:var(--secondary-text-color)}.hour{position:absolute;bottom:-22px;left:50%;transform:translateX(-50%);font-size:10px;color:var(--secondary-text-color)}.chart-caption{font-size:11px;color:var(--secondary-text-color)}.latest{display:flex;padding:0;overflow:hidden}.latest-photo{width:40%;object-fit:cover;background:var(--secondary-background-color);min-height:245px}.latest-copy{padding:22px 18px;min-width:0;display:flex;flex-direction:column;align-items:start;gap:10px}.latest-copy h2{font-size:22px}.latest-copy>p:not(.eyebrow){font-size:12px}.scientific{font-style:italic;color:var(--secondary-text-color);font-size:12px}.badges{gap:7px}.confidence{font-size:13px;color:#31714d;background:#e8f1e9;padding:4px 8px;border-radius:6px;white-space:nowrap}.review{font-size:10px;color:var(--secondary-text-color);white-space:nowrap}.review.correct{color:#378958}.review.false_positive{color:#bd5048}.credit{font-size:9px;color:var(--secondary-text-color);line-height:1.4;display:block}.latest .credit{margin-top:auto}.placeholder{display:grid;place-items:center;background:var(--secondary-background-color,#eef2ed);color:var(--secondary-text-color)}.placeholder ha-icon{--mdc-icon-size:35px}
    .species-section{margin-bottom:20px}.species-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:16px}.species-card{border:1px solid var(--divider-color);border-radius:11px;overflow:hidden;min-width:0}.species-select{display:block;padding:0;border:0;border-radius:0;text-align:left;white-space:normal;width:100%;height:calc(100% - 28px)}.species-select>img,.species-select>.placeholder{height:120px;width:100%;object-fit:cover}.species-content{padding:12px;display:flex;flex-direction:column;gap:6px}.species-content>strong{font-size:13px}.species-content>em{font-size:10px;color:var(--secondary-text-color);min-height:23px}.species-meta{font-size:10px;gap:3px}.species-meta>b{color:var(--primary-color,#317453)}.species-meta>span{color:var(--secondary-text-color)}.species-card>.credit{padding:2px 10px 7px;min-height:28px}.detection{display:flex;align-items:center;gap:15px;padding:16px 0;border-bottom:1px solid var(--divider-color)}.row-photo{flex:0 0 58px;height:58px;border-radius:10px;overflow:hidden}.row-photo>img,.row-photo>.placeholder{width:100%;height:100%;object-fit:cover}.row-bird{display:flex;flex-direction:column;gap:4px;flex:1;min-width:0}.row-bird>strong{font-size:14px}.row-context{font-size:11px;color:var(--secondary-text-color)}.row-scores{display:flex;align-items:center;gap:10px;min-width:135px;justify-content:end}.row-actions{display:flex;align-items:center;gap:12px;margin-left:10px}.row-actions button{padding:7px 10px}.row-actions ha-icon{--mdc-icon-size:17px}.row-actions a{font-size:11px}.pagination{margin-top:18px}.empty{padding:22px;text-align:center;width:100%;color:var(--secondary-text-color);font-size:13px}.empty>h2{margin:12px 0}.empty ha-icon{--mdc-icon-size:40px}.bottom-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:20px;margin-top:20px}.cameras{display:grid;grid-template-columns:1fr 1fr;gap:20px}.camera{min-width:0}.camera-title{gap:8px;flex-wrap:wrap}.camera-title>strong{font-size:14px}.mic-state{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--secondary-text-color)}.sound{margin:15px 0 12px}.sound>span{font-size:24px;font-weight:500}.sound small{font-size:11px;color:var(--secondary-text-color)}.meter{height:5px;border-radius:3px;background:var(--divider-color);margin-top:9px;overflow:hidden}.meter>i{display:block;height:100%;background:#6c9e7a;transition:width .3s}.camera>p{font-size:11px;line-height:1.7}.alert-controls{display:grid;grid-template-columns:1fr 1fr;gap:20px}.alert-controls>label{font-size:12px;display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between}.alert-controls input{width:100%;accent-color:var(--primary-color,#317453)}.enabled{background:#e4efe5!important;color:#316647!important;border-color:#bad3bf!important}.error{background:var(--error-color,#b84a40);color:white;padding:12px 16px;margin-bottom:16px;border-radius:9px;font-size:13px}.player{position:sticky;bottom:14px;z-index:2;margin-top:24px;border:1px solid var(--divider-color);border-radius:14px;padding:14px 18px;background:var(--ha-card-background,var(--card-background-color,#fff));box-shadow:0 6px 28px #0002;display:flex;align-items:center;gap:20px}.player>div{display:flex;flex-direction:column;gap:5px;flex:1}.player strong{font-size:15px}.player audio{width:330px;max-width:100%}.player>button{font-size:23px;border:0;padding:5px}.audio-error{font-size:11px;color:var(--error-color)}
    :host([dark]) .birdmark{background:#263f2d;color:#a3c8a7}:host([dark]) .confidence{background:#263e2f;color:#acd0b6}:host([dark]) .enabled{background:#263f2d!important;color:#acd0b6!important;border-color:#42644d!important}
    @media(max-width:1050px){.overview{grid-template-columns:1fr}.latest-photo{min-height:220px;width:36%}.bottom-grid{grid-template-columns:1fr}.row-scores{min-width:auto;flex-direction:column;gap:4px}.header-actions{flex-direction:column;align-items:end;gap:8px}.species-grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}}
    @media(max-width:600px){.explorer{padding:18px 13px 30px}h1{font-size:23px}.birdmark{width:46px;height:46px;border-radius:13px}.birdmark ha-icon{--mdc-icon-size:28px}.brand{gap:10px}.eyebrow{font-size:9px;letter-spacing:1.3px}.sub{font-size:11px;max-width:180px}header{gap:7px;align-items:start;margin-bottom:20px}.header-actions .button{font-size:11px;padding:7px 9px}.status{font-size:10px}.date-controls{gap:5px}.date-controls input{font-size:12px;max-width:140px}.date-controls>button:not(.text-button){padding:2px 10px}.date-controls .text-button{font-size:11px;padding:5px}.refresh>.muted{display:none}.filters{gap:10px}.filters .search{flex-basis:100%}.filters label:not(.search):not(.check){flex:1;min-width:120px}.check{font-size:11px}.metrics{grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}.metric{padding:14px 16px}.metric>strong{font-size:26px}.metric>span{font-size:11px}.surface{padding:17px}.section-heading{gap:8px}.section-heading>.muted{font-size:10px;text-align:right}h2{font-size:17px}.overview{gap:14px;margin-bottom:14px}.bars{gap:3px;height:135px}.bar>span{font-size:8px}.latest{padding:0}.latest-photo{width:34%;min-height:225px}.latest-copy{padding:16px 13px;gap:8px}.latest-copy h2{font-size:20px}.latest-copy>p:not(.eyebrow){font-size:11px}.species-grid{grid-template-columns:1fr 1fr;gap:10px}.species-select>img,.species-select>.placeholder{height:100px}.species-content{padding:10px}.species-content>strong{font-size:12px}.species-meta{flex-direction:column;align-items:start;gap:3px}.species-section{margin-bottom:14px}.detection{gap:9px;flex-wrap:wrap;padding:13px 0}.row-photo{flex-basis:46px;height:46px}.row-bird{flex-basis:calc(100% - 135px)}.row-bird>strong{font-size:12px}.row-bird>.scientific,.row-context{font-size:10px}.row-scores{margin-left:auto}.row-scores .confidence{font-size:11px}.row-actions{margin:0 0 0 55px;gap:16px}.row-actions button{font-size:11px;padding:5px 9px}.pagination button{font-size:11px;padding:7px 10px}.pagination .muted{font-size:10px}.bottom-grid{gap:14px;margin-top:14px}.cameras{grid-template-columns:1fr;gap:20px}.camera+.camera{border-top:1px solid var(--divider-color);padding-top:18px}.alert-controls{gap:15px}.alert-controls>label{font-size:11px}.player{flex-wrap:wrap;gap:9px;padding:13px;bottom:7px}.player>div{flex-basis:calc(100% - 45px);order:0}.player>button{order:1}.player audio{order:2;width:100%}.player .muted{font-size:10px}.note{font-size:11px}}
  `;
  if (typeof customElements !== 'undefined' && !customElements.get('birdnet-explorer')) {
    customElements.define('birdnet-explorer', BirdnetExplorer);
    window.customCards = window.customCards || [];
    window.customCards.push({ type: 'birdnet-explorer', name: 'BirdNET Explorer', description: 'Bird detections, recordings and daily activity.' });
  }
  if (typeof module !== 'undefined') module.exports = { esc, https, percent, localDay, shiftDay, filtersQuery, reviewLabel };
})();
