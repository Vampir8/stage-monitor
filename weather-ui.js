'use strict';
/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
document.addEventListener('stage:ready', () => {
  const dashboard = document.querySelector('.dashboard');
  let context = null, card = null, timer = null, snapshot = null, loading = false, generation = 0, configRevision = 0, fetchGeneration = 0;
  const dialog = document.createElement('dialog');
  dialog.className = 'settings-dialog';
  document.body.append(dialog);
  function node(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className || '';
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function button(text, action) {
    const element = node('button', 'btn', text);
    element.type = 'button'; element.addEventListener('click', action); return element;
  }
  function number(value, unit) { return value === null || value === undefined ? '—' : `${Number(value).toFixed(1)} ${unit}`; }
  function time(stamp) {
    return new Intl.DateTimeFormat(window.StageI18n?.locale || 'en-GB', {timeZone: snapshot.config.timezone, hour:'2-digit', minute:'2-digit'}).format(new Date(stamp));
  }
  async function refresh() {
    if (!context || loading) return;
    const stamp = generation, fetchStamp = fetchGeneration, current = context;
    loading = true;
    try {
      const data = await current.request('/api/weather/current');
      if (stamp !== generation || fetchStamp !== fetchGeneration || !context) return;
      if (data.config.revision < configRevision) return;
      configRevision = data.config.revision;
      snapshot = data; render();
    } catch (error) {
      if (stamp !== generation || fetchStamp !== fetchGeneration || !context) return;
      if (card) {
        card.querySelector('.weather-health').textContent = stageText("Weather unavailable · retrying");
        card.classList.add('weather-stale');
      } else {
        ensureCard();
        card.querySelector('.weather-health').textContent = stageText("Weather unavailable");
        window.dispatchEvent(new Event('stage:devices-rendered'));
      }
    } finally {
      loading = false;
      if ((stamp !== generation || fetchStamp !== fetchGeneration) && context) refresh();
    }
  }
  function ensureCard() {
    if (card) return;
    card = node('section', 'card live-device weather-live weather-card expandable-card');
    card.dataset.cat = 'weather';
    const header = node('div', 'header weather-head');
    const meta = node('div', 'meta');
    const subtitle = node('p', 'subtitle');
    subtitle.append(node('span', 'weather-location'), node('span', 'weather-health'));
    meta.append(node('h2', 'title', stageText("Weather")), subtitle);
    const expand = node('button', 'expand-toggle', stageText("EXPAND"));
    expand.type = 'button'; expand.setAttribute('aria-expanded', 'false');
    expand.setAttribute('aria-label', stageText("Expand the Weather column"));
    header.append(node('div', 'status idle', stageText("WAITING")), node('span', 'simulation-badge'), expand);
    const content = node('div', 'weather-content');
    const values = node('div', 'weather-values');
    values.append(node('div', 'weather-metrics'), node('p', 'telemetry-time'));
    const map = node('div', 'weather-map-panel');
    map.append(node('div', 'weather-map-header', stageText("WINDY · WIND")), node('div', 'weather-map'),
      node('p', 'weather-note', stageText("The Windy map shows its own data, including in mock mode.")));
    content.append(values, map); card.append(header, meta, node('div', 'divider'), content);
    dashboard.append(card);
  }
  function render() {
    ensureCard();
    card.classList.toggle('weather-stale', snapshot.stale);
    card.querySelector('.simulation-badge').textContent = snapshot.simulation ? stageText("SIMULATION") : 'OPEN-METEO';
    const status = card.querySelector('.status');
    status.textContent = snapshot.stale ? stageText("STALE") : 'ONLINE';
    status.title = status.textContent;
    status.className = snapshot.stale ? 'status warn' : 'status ok';
    card.querySelector('.weather-location').textContent = snapshot.config.location_name;
    card.querySelector('.weather-health').textContent = snapshot.stale ? stageText("Stale data") : '';
    const current = snapshot.current, metrics = card.querySelector('.weather-metrics');
    metrics.replaceChildren();
    for (const [name, value, unit] of [
      [stageText("Temperature"), current.temperature, '°C'], [stageText("Feels like"), current.feels_like, '°C'],
      [stageText("Wind"), current.wind, 'm/s'], [stageText("Gusts"), current.gusts, 'm/s'],
      [stageText("Humidity"), current.humidity, '%'], [stageText("Pressure"), current.pressure, 'hPa'],
      [stageText("Rain"), current.precipitation, 'mm'], [stageText("Direction"), current.wind_direction, '°'],
    ]) {
      const item = node('div', 'info-row weather-metric');
      item.append(node('div', 'info-label', name), node('div', 'info-value', number(value, unit))); metrics.append(item);
    }
    card.querySelector('.telemetry-time').textContent = stageText("Data time: ") + time(current.time);
    let source = card.querySelector('.weather-source');
    if (snapshot.source_url && !source) {
      source = node('a', 'weather-source weather-note', stageText("Data: Open-Meteo"));
      source.href = snapshot.source_url; source.target = '_blank'; source.rel = 'noopener noreferrer';
      card.querySelector('.weather-values').append(source);
    }
    if (!snapshot.source_url) source?.remove();
    if (card.classList.contains('expanded')) loadMap();
    window.dispatchEvent(new Event('stage:devices-rendered'));
  }
  function loadMap() {
    if (!snapshot || !card) return;
    const map = card.querySelector('.weather-map');
    const coordinates = snapshot.config.latitude + ',' + snapshot.config.longitude;
    if (map.dataset.coordinates === coordinates) return;
    const iframe = node('iframe'); iframe.title = stageText("Windy weather map");
    iframe.referrerPolicy = 'no-referrer'; iframe.setAttribute('scrolling', 'no');
    const query = new URLSearchParams({type:'map', location:'coordinates', overlay:'wind', level:'surface', zoom:'8',
      lat:String(snapshot.config.latitude), lon:String(snapshot.config.longitude), metricWind:'m/s'});
    iframe.src = 'https://embed.windy.com/embed.html?' + query;
    map.replaceChildren(iframe); map.dataset.coordinates = coordinates;
  }
  async function configure() {
    if (!context?.user.roles.includes('admin')) return;
    const stamp = generation;
    let config;
    try { config = await context.request('/api/weather/config'); }
    catch (error) { return; }
    if (stamp !== generation || !context) return;
    dialog.replaceChildren();
    const heading = node('div', 'settings-heading');
    heading.append(node('h2', '', stageText("Show weather")), button(stageText("Close"), () => dialog.close())); dialog.append(heading);
    const form = node('form'), grid = node('div', 'settings-grid');
    const fields = {};
    for (const [key, title, type, options] of [
      ['event_name',stageText("Show name"),'text'], ['location_name',stageText("Location"),'text'],
      ['latitude',stageText("Latitude"),'number'], ['longitude',stageText("Longitude"),'number'],
      ['timezone',stageText("IANA time zone"),'text'], ['provider',stageText("Data source"),'select', [['open_meteo',stageText("Open-Meteo — live data")]]],
      ['gust_threshold',stageText("Gust threshold (m/s)"),'number'], ['rain_threshold',stageText("Rain threshold (mm)"),'number'],
    ]) {
      const label = node('label', '', title), input = node(type === 'select' ? 'select' : 'input');
      input.name = key; input.required = true;
      if (type === 'select') for (const [value, text] of options) { const option = node('option','',text); option.value = value; input.append(option); }
      else { input.type = type; if (type === 'number') input.step = 'any'; else input.maxLength = 120; }
      if (key === 'latitude') {input.min = '-90'; input.max = '90';}
      if (key === 'longitude') {input.min = '-180'; input.max = '180';}
      if (key.endsWith('threshold')) input.min = '0.1';
      input.value = config[key]; fields[key] = input; label.append(input); grid.append(label);
    }
    form.append(grid, node('p','settings-help',stageText("Coordinates determine weather for the active show. Open-Meteo sends them to an external API. The Windy map loads when the column is expanded.")));
    const terms = node('a', 'settings-help', stageText("Open-Meteo terms — check the plan for commercial use"));
    terms.href = 'https://open-meteo.com/en/pricing'; terms.target = '_blank'; terms.rel = 'noopener noreferrer'; form.append(terms);
    const error = node('p','settings-error'), save = node('button','btn',stageText("Save weather")); save.type = 'submit';
    form.append(error, save); dialog.append(form);
    form.addEventListener('submit', async event => {
      event.preventDefault(); save.disabled = true; error.textContent = '';
      const next = {...config};
      for (const [key, input] of Object.entries(fields)) next[key] = input.type === 'number' ? Number(input.value) : input.value.trim();
      try {
        await context.request('/api/weather/config', {method:'PUT', body:JSON.stringify(next)});
        if (stamp !== generation) return;
        dialog.close(); await refresh();
      } catch (exception) { error.textContent = stageText("Configuration not saved. Check values and time zone; reopen the form after a conflict or connection loss."); }
      finally { save.disabled = false; }
    });
    dialog.showModal();
  }
  window.addEventListener('stage:session', event => {
    const previous = context;
    context = event.detail;
    if (!context || context.user.id !== previous?.user.id) {
      generation++; clearInterval(timer); timer = null;
      card?.remove(); card = null; snapshot = null; configRevision = 0; dialog.close();
    }
    if (!context) return;
    if (!context.user.roles.includes('admin')) dialog.close();
    if (!timer) { refresh(); timer = setInterval(refresh, 30000); }
    if (snapshot) render();
  });
  window.addEventListener('stage:configure-weather', configure);
  window.addEventListener('stage:column-expanded', event => {
    if (event.detail.card === card && event.detail.expanded) loadMap();
  });
  window.addEventListener('stage:weather', event => {
    if (!context) return;
    const revision = event.detail.config?.revision || event.detail.config_revision || 0;
    if (revision < configRevision) return;
    if (revision > configRevision) {
      fetchGeneration++; configRevision = revision; snapshot = null;
      if (card) {
        card.querySelector('.weather-metrics').replaceChildren();
        card.querySelector('.telemetry-time').textContent = '';
        card.querySelector('.weather-location').textContent = '';
        card.querySelector('.weather-health').textContent = stageText("Loading show weather…");
        card.querySelector('.simulation-badge').textContent = '';
        card.querySelector('.status').textContent = stageText("CONNECTING"); card.querySelector('.status').className = 'status idle';
        card.querySelector('.weather-source')?.remove();
        card.querySelector('.weather-map').replaceChildren(); delete card.querySelector('.weather-map').dataset.coordinates;
        card.classList.add('weather-stale');
      }
    }
    if (event.detail.current) { snapshot = event.detail; render(); }
    else refresh();
  });
  window.addEventListener('stage:devices-rendered', () => {
    if (card && !card.parentElement?.classList.contains('dashboard-zone') && dashboard.lastElementChild !== card) dashboard.append(card);
  });
});
