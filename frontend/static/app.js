const siteSelect = document.getElementById('site-select');
const comparisonSiteSelect = document.getElementById('comparison-site-select');
const sourceSelect = document.getElementById('source-select');
const horizonsOptions = document.getElementById('horizons-options');
const horizonsStepInput = document.getElementById('horizons-step-minutes');
const startDateInput = document.getElementById('start-date');
const endDateInput = document.getElementById('end-date');
const timelineList = document.getElementById('timeline');
const timelineHeading = document.getElementById('timeline-heading');
const timelineNote = document.getElementById('timeline-note');
const summaryBox = document.getElementById('summary-box');
const comparisonTableBody = document.getElementById('comparison-table-body');
const comparisonChart = document.getElementById('comparison-chart');
const comparisonSourceLabel = document.getElementById('comparison-source-label');
const visibilityTimeline = document.getElementById('visibility-timeline');
const runAnalysisButton = document.getElementById('run-analysis');
const runComparisonButton = document.getElementById('run-comparison');
const sourceBadge = document.getElementById('source-badge');
const dataLabel = document.getElementById('data-label');
let comparisonRequestVersion = 0;
let horizonsRequestVersion = 0;

async function loadHealthStatus() {
  const statusEl = document.getElementById('health-status');
  if (!statusEl) return;

  try {
    const response = await fetch('/api/health');
    const payload = await response.json();

    if (response.ok && payload.status === 'ok') {
      statusEl.textContent = `API status: ${payload.status} (${payload.service})`;
      statusEl.style.color = '#9fe7b4';
      statusEl.style.borderColor = 'rgba(159, 231, 180, 0.4)';
      statusEl.style.background = 'rgba(76, 175, 80, 0.12)';
      return;
    }

    throw new Error('Unexpected health payload');
  } catch (error) {
    statusEl.textContent = 'API status: unavailable';
    statusEl.style.color = '#f6c76a';
    statusEl.style.borderColor = 'rgba(246, 199, 106, 0.4)';
    statusEl.style.background = 'rgba(246, 199, 106, 0.12)';
    console.error(error);
  }
}

async function loadSites() {
  const response = await fetch('/api/sites');
  const payload = await response.json();

  if (!response.ok) {
    throw new Error('Unable to load sites');
  }

  const createSiteOption = (site) => {
    const option = document.createElement('option');
    option.value = site.site_id;
    option.textContent = site.name;
    option.dataset.latitudeDeg = site.latitude_deg;
    option.dataset.longitudeDeg = site.longitude_deg;
    option.dataset.elevationM = site.elevation_m;
    return option;
  };

  siteSelect.replaceChildren(...payload.sites.map(createSiteOption));
  comparisonSiteSelect.replaceChildren(...payload.sites.map(createSiteOption));

  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() - 1);
  const end = new Date(today);

  startDateInput.value = start.toISOString().slice(0, 10);
  endDateInput.value = end.toISOString().slice(0, 10);
}

function setDataSourceLabel(sourceType, requestedSource, apiStatus) {
  const labelMap = {
    synthetic: 'Synthetic demo data only. It is not an official NASA result.',
    reference: 'Reference snapshot mode. This is a reproducible local reference payload, not official ephemeris.',
    official: 'This analysis uses official-source data.',
  };

  const badgeMap = {
    synthetic: 'Synthetic',
    reference: 'Reference',
    official: 'Official',
  };

  sourceBadge.textContent = badgeMap[sourceType] || 'Synthetic';
  if (requestedSource === 'official' && apiStatus === 'ok') {
    dataLabel.textContent =
      'Analysis uses synthetic geometry. Horizons ephemeris was retrieved separately and is not used to calculate site-local sky positions.';
    return;
  }

  if (requestedSource === 'official' && apiStatus === 'unavailable') {
    dataLabel.textContent = 'Analysis uses synthetic geometry. Horizons data is unavailable for this request.';
    return;
  }

  dataLabel.textContent = labelMap[sourceType] || labelMap.synthetic;
}

function formatMinutes(minutes) {
  return Number.isInteger(minutes) ? `${minutes} min` : 'Unavailable';
}

function formatIntervals(intervals, state, validSampleCount) {
  const matchingIntervals = intervals.filter((interval) => interval.state === state);
  if (!matchingIntervals.length) {
    return validSampleCount ? 'None during sampled coverage.' : 'Unavailable (no valid elevation samples).';
  }

  return matchingIntervals
    .slice(0, 4)
    .map(
      (interval) =>
        `${interval.start_iso.replace('T', ' ')}–${interval.end_iso.replace('T', ' ')} UT (${interval.duration_minutes} min)`
    )
    .join(' | ');
}

function formatHemisphere(value, positiveHemisphere, negativeHemisphere) {
  if (!Number.isFinite(value)) {
    return 'unavailable';
  }
  return `${Math.abs(value).toFixed(3)}° ${value < 0 ? negativeHemisphere : positiveHemisphere}`;
}

function formatHorizonsAngle(angle) {
  if (!angle) {
    return 'angle unavailable';
  }
  const azimuth = Number.isFinite(angle.azimuth_deg)
    ? `${angle.azimuth_deg.toFixed(2)}° az`
    : 'azimuth unavailable';
  const elevation = Number.isFinite(angle.elevation_deg)
    ? `${angle.elevation_deg.toFixed(2)}° el`
    : 'elevation unavailable';
  return `${azimuth}, ${elevation}`;
}

function appendSummaryRow(label, value) {
  const row = document.createElement('div');
  const heading = document.createElement('strong');
  heading.textContent = `${label}:`;
  row.append(heading, ` ${value}`);
  summaryBox.append(row);
}

function renderHorizonsAnalysis(payload, siteOption, coordinates) {
  const analysis = payload.visibility_analysis;
  if (!analysis) {
    throw new Error('Horizons response did not include visibility analysis.');
  }
  const samples = Array.isArray(payload.samples) ? payload.samples : [];
  const hasValidElevation = samples.some(
    (sample) =>
      Number.isFinite(sample.sun && sample.sun.elevation_deg) ||
      Number.isFinite(sample.earth && sample.earth.elevation_deg)
  );

  sourceBadge.textContent = 'Official API';
  dataLabel.textContent =
    `NASA/JPL Horizons apparent angles at synthetic demo-site coordinates ` +
    `(${formatHemisphere(coordinates.latitude, 'N', 'S')} latitude, ` +
    `${formatHemisphere(coordinates.longitude, 'E', 'W')} longitude, ` +
    `${coordinates.altitudeKm.toFixed(3)} km). Sun/Earth center elevations are compared with the ideal 0° horizon. ` +
    'Sun center above 0° does not mean the whole solar disc is visible or power is available; Earth center above 0° does not imply Earth-disc visibility or communication availability. ' +
    'Intervals are sample-step estimates, not precise event times.';
  summaryBox.replaceChildren();

  appendSummaryRow('Site', siteOption.textContent);
  appendSummaryRow('Source', payload.source_name);
  appendSummaryRow('Time system', payload.time_system);
  appendSummaryRow(
    'Data status',
    hasValidElevation
      ? `Live Horizons samples received (${analysis.valid_sun_sample_count} Sun, ${analysis.valid_earth_sample_count} Earth elevations valid)`
      : 'Empty Data: no valid Sun or Earth center elevations were returned.'
  );
  appendSummaryRow(
    'Horizon reference',
    'Ideal horizon at 0°; excludes lunar terrain and equipment obstructions.'
  );
  appendSummaryRow('Sampling step', `${analysis.sample_step_minutes} min`);
  appendSummaryRow(
    'Sun samples',
    `${analysis.valid_sun_sample_count}/${analysis.sample_count} valid (${analysis.sun_data_complete ? 'complete' : 'incomplete'})`
  );
  appendSummaryRow(
    'Earth samples',
    `${analysis.valid_earth_sample_count}/${analysis.sample_count} valid (${analysis.earth_data_complete ? 'complete' : 'incomplete'})`
  );
  appendSummaryRow(
    'Total sunlight',
    formatMinutes(analysis.total_sunlight_minutes)
  );
  appendSummaryRow(
    'Longest continuous darkness',
    formatMinutes(analysis.longest_continuous_darkness_minutes)
  );
  appendSummaryRow(
    'Earth-center above ideal horizon',
    formatMinutes(analysis.total_earth_visible_minutes)
  );
  appendSummaryRow(
    'Longest Earth-center below-horizon interval',
    formatMinutes(analysis.longest_continuous_earth_hidden_minutes)
  );
  appendSummaryRow(
    'Sunlight intervals',
    formatIntervals(
      analysis.sunlight_intervals,
      'visible',
      analysis.valid_sun_sample_count
    )
  );
  appendSummaryRow(
    'Darkness intervals',
    formatIntervals(
      analysis.sunlight_intervals,
      'hidden',
      analysis.valid_sun_sample_count
    )
  );
  appendSummaryRow(
    'Earth-center above-horizon intervals',
    formatIntervals(
      analysis.earth_visibility_intervals,
      'visible',
      analysis.valid_earth_sample_count
    )
  );
  appendSummaryRow(
    'Earth-center below-horizon intervals',
    formatIntervals(
      analysis.earth_visibility_intervals,
      'hidden',
      analysis.valid_earth_sample_count
    )
  );
  renderSelectedHorizonsSamples(samples, siteOption, coordinates, payload.time_system);
}

function formatHorizonRelation(elevation) {
  if (!Number.isFinite(elevation)) {
    return 'elevation unavailable';
  }
  if (elevation > 0) {
    return 'above ideal horizon';
  }
  if (elevation < 0) {
    return 'below ideal horizon';
  }
  return 'at ideal horizon';
}

function renderSelectedHorizonsSamples(samples, siteOption, coordinates, timeSystem) {
  timelineHeading.textContent = 'NASA/JPL Horizons center elevations';
  timelineNote.textContent =
    `Timestamp labels use ${timeSystem}. Elevation is relative to the ideal 0° horizon, which excludes lunar terrain and equipment obstructions. ` +
    'A center above 0° does not mean the full disc is visible or that power or communication is available.';

  const items = [];
  const siteHeading = document.createElement('li');
  const siteTitle = document.createElement('strong');
  const siteCoordinates = document.createElement('small');
  siteHeading.className = 'timeline-site-heading';
  siteTitle.textContent = siteOption.textContent;
  siteCoordinates.textContent =
    `Synthetic demo observer; not a verified landing site. Coordinates: ` +
    `${formatHemisphere(coordinates.latitude, 'N', 'S')} latitude, ` +
    `${formatHemisphere(coordinates.longitude, 'E', 'W')} longitude, ` +
    `${coordinates.altitudeKm.toFixed(3)} km elevation.`;
  siteHeading.append(siteTitle, siteCoordinates);
  items.push(siteHeading);

  if (!samples.length) {
    const empty = document.createElement('li');
    empty.className = 'timeline-sample';
    empty.textContent = 'Empty Data: NASA/JPL Horizons returned no samples.';
    items.push(empty);
  } else {
    samples.forEach((sample) => {
      const item = document.createElement('li');
      const timestamp = document.createElement('span');
      const sun = document.createElement('span');
      const earth = document.createElement('span');
      const timestampValue = String(sample.timestamp_ut || 'Timestamp unavailable').replace('T', ' ');
      const sunElevation = sample.sun && sample.sun.elevation_deg;
      const earthElevation = sample.earth && sample.earth.elevation_deg;

      item.className = 'timeline-sample';
      timestamp.textContent = `Timestamp (${timeSystem}): ${timestampValue}`;
      const sunValue = Number.isFinite(sunElevation)
        ? `${sunElevation.toFixed(3)}°`
        : 'unavailable';
      const earthValue = Number.isFinite(earthElevation)
        ? `${earthElevation.toFixed(3)}°`
        : 'unavailable';
      sun.textContent = `Sun center: ${sunValue} (${formatHorizonRelation(sunElevation)})`;
      earth.textContent = `Earth center: ${earthValue} (${formatHorizonRelation(earthElevation)})`;
      item.append(timestamp, sun, earth);
      items.push(item);
    });
  }

  timelineList.replaceChildren(...items);
}

async function updateHorizonsAnalysis(siteOption, startDate, endDate) {
  const requestVersion = ++horizonsRequestVersion;
  timelineList.replaceChildren();
  timelineHeading.textContent = 'NASA/JPL Horizons center elevations (UT)';
  timelineNote.textContent = 'Loading live Sun/Earth samples from NASA/JPL Horizons…';

  if (endDate < startDate) {
    summaryBox.textContent = 'End date must be on or after start date.';
    return;
  }

  const stepMinutes = Number(horizonsStepInput.value);
  if (!Number.isInteger(stepMinutes) || stepMinutes < 1 || stepMinutes > 1440) {
    summaryBox.textContent = 'Horizons sample step must be a whole number from 1 to 1440 minutes.';
    return;
  }

  const coordinates = {
    longitude: Number(siteOption.dataset.longitudeDeg),
    latitude: Number(siteOption.dataset.latitudeDeg),
    altitudeKm: Number(siteOption.dataset.elevationM) / 1000,
  };
  if (!Object.values(coordinates).every(Number.isFinite)) {
    summaryBox.textContent = 'The selected demo site does not have valid observer coordinates.';
    return;
  }

  const exclusiveEndDate = new Date(`${endDate}T00:00:00Z`);
  exclusiveEndDate.setUTCDate(exclusiveEndDate.getUTCDate() + 1);
  const params = new URLSearchParams({
    start_time: `${startDate}T00:00:00Z`,
    stop_time: exclusiveEndDate.toISOString().replace('.000Z', 'Z'),
    site_longitude_deg: String(coordinates.longitude),
    site_latitude_deg: String(coordinates.latitude),
    site_altitude_km: String(coordinates.altitudeKm),
    step: `${stepMinutes}min`,
  });

  sourceBadge.textContent = 'Official API';
  dataLabel.textContent =
    'Querying NASA/JPL Horizons for apparent angles at synthetic demo-site coordinates. ' +
    'These coordinates are not verified mission locations.';
  summaryBox.textContent = 'Loading Horizons apparent-angle analysis…';

  let response;
  try {
    response = await fetch(`/api/analysis/horizons?${params}`);
  } catch (error) {
    if (requestVersion !== horizonsRequestVersion) return;
    summaryBox.textContent = `Horizons request failed: ${error.message}`;
    timelineNote.textContent = 'Live Horizons request failed; no synthetic data was substituted.';
    dataLabel.textContent = 'Live NASA/JPL Horizons data is unavailable for this request.';
    console.error(error);
    return;
  }

  if (requestVersion !== horizonsRequestVersion) return;

  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    summaryBox.textContent = 'Horizons request returned an invalid response.';
    timelineNote.textContent = 'The live API response could not be read; no synthetic data was substituted.';
    dataLabel.textContent = 'Live NASA/JPL Horizons response was invalid.';
    console.error(error);
    return;
  }

  if (requestVersion !== horizonsRequestVersion) return;

  if (!response.ok) {
    const detail =
      typeof payload.detail === 'string'
        ? payload.detail
        : JSON.stringify(payload.detail || 'unknown error');
    summaryBox.textContent = `Unable to load Horizons analysis: ${detail}`;
    timelineNote.textContent = 'Live Horizons request failed; no synthetic samples were substituted.';
    dataLabel.textContent = 'Live NASA/JPL Horizons data is unavailable for this request.';
    return;
  }

  try {
    renderHorizonsAnalysis(payload, siteOption, coordinates);
  } catch (error) {
    summaryBox.textContent = `Unable to display Horizons analysis: ${error.message}`;
    timelineNote.textContent = 'The live API response could not be displayed; no synthetic data was substituted.';
    console.error(error);
  }
}

async function updateAnalysis() {
  const siteId = siteSelect.value;
  const startDate = startDateInput.value;
  const endDate = endDateInput.value;
  const source = sourceSelect.value;

  if (!siteId || !startDate || !endDate) {
    return;
  }

  timelineHeading.textContent = 'Synthetic demo timeline';
  if (source === 'official' || source === 'horizons') {
    updateHorizonsAnalysis(siteSelect.selectedOptions[0], startDate, endDate);
    return;
  }
  horizonsRequestVersion += 1;

  setDataSourceLabel('synthetic', source, 'pending');

  const response = await fetch(
    `/api/analysis?site_id=${encodeURIComponent(siteId)}&start_date=${startDate}&end_date=${endDate}&step_minutes=60&source=${encodeURIComponent(source)}`
  );
  const payload = await response.json();

  if (!response.ok) {
    setDataSourceLabel('synthetic', source, 'unavailable');
    summaryBox.textContent = `Unable to load timeline: ${payload.detail || 'unknown error'}`;
    return;
  }

  setDataSourceLabel(payload.source_type, source, payload.summary.api_status);

  const sunlightIntervals = payload.sunlight_intervals || [];
  const earthIntervals = payload.earth_visibility_intervals || [];

  summaryBox.innerHTML = `
    <div><strong>Site:</strong> ${payload.site_name}</div>
    <div><strong>Source:</strong> ${payload.source_type}</div>
    <div><strong>Model status:</strong> ${payload.model_status}</div>
    <div><strong>Sunlit fraction:</strong> ${payload.summary.sunlit_fraction}</div>
    <div><strong>Earth-visible fraction:</strong> ${payload.summary.earth_visible_fraction}</div>
    <div><strong>Sunlight windows:</strong> ${sunlightIntervals.length ? sunlightIntervals.slice(0, 3).map((interval) => `${interval.start_iso.slice(11,16)}-${interval.end_iso.slice(11,16)} (${interval.duration_minutes} min)`).join(' | ') : 'No sunlight windows in this range.'}</div>
    <div><strong>Earth visibility windows:</strong> ${earthIntervals.length ? earthIntervals.slice(0, 3).map((interval) => `${interval.start_iso.slice(11,16)}-${interval.end_iso.slice(11,16)} (${interval.duration_minutes} min)`).join(' | ') : 'No Earth-visible windows in this range.'}</div>
    ${payload.summary.source_note ? `<div><strong>Note:</strong> ${payload.summary.source_note}</div>` : ''}
  `;

  timelineList.innerHTML = payload.visibility_series
    .slice(0, 6)
    .map(
      (item) => `
        <li>
          <span>${new Date(item.timestamp).toLocaleString()}</span>
          <span>sun=${item.sun_above_horizon ? 'visible' : 'hidden'}</span>
          <span>earth=${item.earth_above_horizon ? 'visible' : 'hidden'}</span>
        </li>
      `
    )
    .join('');
}

function renderComparisonChart(rows) {
  if (!rows.length) {
    comparisonChart.innerHTML = '';
    return;
  }

  comparisonChart.innerHTML = rows
    .map(
      (row) => `
        <div class="comparison-chart-row">
          <div class="comparison-chart-label">${row.site_name}</div>
          <div class="comparison-chart-group">
            <div class="comparison-metric">
              <div class="metric-head"><span>Sun center above 0°</span><strong>${formatComparisonFraction(row.sunlit_fraction)}</strong></div>
              <div class="metric-track"><div class="metric-bar sunlit" style="width: ${comparisonFractionWidth(row.sunlit_fraction)}"></div></div>
            </div>
            <div class="comparison-metric">
              <div class="metric-head"><span>Earth center above 0°</span><strong>${formatComparisonFraction(row.earth_visible_fraction)}</strong></div>
              <div class="metric-track"><div class="metric-bar earth" style="width: ${comparisonFractionWidth(row.earth_visible_fraction)}"></div></div>
            </div>
          </div>
        </div>
      `
    )
    .join('');
}

function formatComparisonFraction(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toFixed(2)
    : 'Unavailable';
}

function comparisonFractionWidth(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '0%';
  }
  return `${Math.min(Math.max(value * 100, 0), 100)}%`;
}

function formatComparisonDuration(value) {
  return Number.isFinite(value) ? `${value} min` : 'Unavailable';
}

function formatComparisonCoverage(row) {
  if (!Number.isInteger(row.sample_count)) {
    return 'Not reported';
  }
  return `Sun ${row.sun_valid_sample_count}/${row.sample_count}; Earth ${row.earth_valid_sample_count}/${row.sample_count}`;
}

function renderComparisonMessage(message) {
  const cell = document.createElement('td');
  cell.colSpan = 6;
  cell.className = 'muted';
  cell.textContent = message;

  const row = document.createElement('tr');
  row.append(cell);
  comparisonTableBody.replaceChildren(row);
  comparisonChart.replaceChildren();
  visibilityTimeline.replaceChildren();
}

function renderHorizonsSamples(rows, timeSystem) {
  timelineHeading.textContent = 'NASA/JPL Horizons apparent-angle samples (UT)';
  timelineNote.textContent =
    'All samples returned for the selected comparison sites are shown. These are apparent angles to the Sun and Earth centers at synthetic observer coordinates, not named real places or verified mission locations. Earth-center elevation does not indicate Earth-disc visibility or communication availability.';
  const items = [];

  rows.forEach((row) => {
    const heading = document.createElement('li');
    const title = document.createElement('strong');
    const coordinates = document.createElement('small');
    const latitude = formatHemisphere(row.latitude_deg, 'N', 'S');
    const longitude = formatHemisphere(row.longitude_deg, 'E', 'W');
    const altitude =
      Number.isFinite(row.altitude_km) ? `${row.altitude_km.toFixed(3)} km demo elevation` : 'demo elevation unavailable';

    heading.className = 'timeline-site-heading';
    title.textContent = row.site_name;
    coordinates.textContent =
      `Synthetic demo point; no confirmed named real-place or mission-site match. ` +
      `Observer coordinates: ${latitude} latitude, ${longitude} longitude; ${altitude}.`;
    heading.append(title, coordinates);
    items.push(heading);

    const samples = Array.isArray(row.samples) ? row.samples : [];
    if (!samples.length) {
      const empty = document.createElement('li');
      empty.className = 'timeline-sample';
      empty.textContent = 'No Horizons samples returned for this site.';
      items.push(empty);
      return;
    }

    samples.forEach((sample) => {
      const item = document.createElement('li');
      const timestamp = document.createElement('span');
      const sun = document.createElement('span');
      const earth = document.createElement('span');
      item.className = 'timeline-sample';
      timestamp.textContent =
        `${String(sample.timestamp_ut || 'Timestamp unavailable').replace('T', ' ')} ${timeSystem}`;
      sun.textContent = `Sun center: ${formatHorizonsAngle(sample.sun)}`;
      earth.textContent = `Earth center: ${formatHorizonsAngle(sample.earth)}`;
      item.append(timestamp, sun, earth);
      items.push(item);
    });
  });

  timelineList.replaceChildren(...items);
}

function renderVisibilityTimeline(rows, timeSystem, window) {
  if (!rows.length) {
    visibilityTimeline.innerHTML = '';
    return;
  }

  const parseUtcTime = (value) => {
    if (typeof value !== 'string') {
      return null;
    }
    const iso = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
    const parsed = new Date(iso);
    return Number.isFinite(parsed.getTime()) ? parsed : null;
  };
  const start = parseUtcTime(window && window.start_iso);
  const end = parseUtcTime(window && window.end_iso);
  if (!start || !end || start >= end) {
    visibilityTimeline.textContent = 'Comparison time window is unavailable.';
    return;
  }

  const hourSlots = [];
  for (let timestamp = start.getTime(); timestamp < end.getTime(); timestamp += 60 * 60 * 1000) {
    hourSlots.push(new Date(timestamp));
  }
  const statesByHour = (intervals) =>
    hourSlots.map((hourStart) => {
      const hourEnd = Math.min(hourStart.getTime() + 60 * 60 * 1000, end.getTime());
      const states = new Set();
      intervals.forEach((interval) => {
        const intervalStart = parseUtcTime(interval.start_iso);
        const intervalEnd = parseUtcTime(interval.end_iso);
        if (
          intervalStart &&
          intervalEnd &&
          intervalStart.getTime() < hourEnd &&
          intervalEnd.getTime() > hourStart.getTime()
        ) {
          states.add(interval.state);
        }
      });
      if (states.size > 1) {
        return 'mixed';
      }
      return states.values().next().value || 'unknown';
    });
  const rowsWithStates = rows.map((row) => ({
    ...row,
    sunStates: statesByHour(row.sunlight_intervals || []),
    earthStates: statesByHour(row.earth_visibility_intervals || []),
  }));
  const metrics = [
    { key: 'sunStates', type: 'sun', label: 'Sun center > 0°' },
    { key: 'earthStates', type: 'earth', label: 'Earth center > 0°' },
  ];
  const stateLabels = {
    visible: 'Above 0°',
    hidden: 'At/below 0°',
    mixed: 'Mixed within this hour',
    unknown: 'No valid coverage',
  };
  const dayGroups = hourSlots.reduce((groups, hour) => {
    const date = hour.toISOString().slice(0, 10);
    const lastGroup = groups[groups.length - 1];
    if (lastGroup && lastGroup.date === date) {
      lastGroup.hourCount += 1;
    } else {
      groups.push({ date, hourCount: 1 });
    }
    return groups;
  }, []);

  visibilityTimeline.innerHTML = `
    <div class="timeline-header">
      <strong>Hourly visibility (${timeSystem})</strong>
      <span>Each column is a distinct hour; selected dates are not combined</span>
    </div>
    <div
      class="timeline-grid"
      style="--timeline-hour-count: ${hourSlots.length}; min-width: ${Math.max(
        720,
        135 + hourSlots.length * 18
      )}px"
    >
      <div class="timeline-axis-label">Site / measure</div>
      ${dayGroups
        .map(
          (group) =>
            `<div class="timeline-day-label" style="grid-column: span ${group.hourCount}">${group.date}</div>`
        )
        .join('')}
      <div class="timeline-axis-label">UT hour</div>
      ${hourSlots
        .map((hour) => {
          const dateTime = hour.toISOString().slice(0, 16).replace('T', ' ');
          return `<div class="timeline-hour" title="${dateTime} ${timeSystem}">${hour
            .toISOString()
            .slice(11, 13)}</div>`;
        })
        .join('')}
      ${rowsWithStates
        .map((row) =>
          metrics
            .map(
              (metric) => `
                <div class="timeline-series-label">
                  <strong>${row.site_name}</strong>
                  <span class="timeline-metric-label ${metric.type}">${metric.label}</span>
                </div>
                ${row[metric.key]
                  .map(
                    (state, hourIndex) => {
                      const dateTime = hourSlots[hourIndex]
                        .toISOString()
                        .slice(0, 16)
                        .replace('T', ' ');
                      return `
                        <div
                          class="timeline-cell ${metric.type} ${state}"
                          role="img"
                          aria-label="${row.site_name}, ${metric.label}, ${dateTime} ${timeSystem}: ${stateLabels[state]}"
                          title="${row.site_name} · ${metric.label} · ${dateTime} ${timeSystem}: ${stateLabels[state]}"
                        ></div>
                      `;
                    }
                  )
                  .join('')}
              `
            )
            .join('')
        )
        .join('')}
    </div>
  `;
}

async function updateComparison() {
  const requestVersion = ++comparisonRequestVersion;
  const siteIds = [...comparisonSiteSelect.selectedOptions].map((option) => option.value);
  const requestedSource =
    sourceSelect.value === 'official' || sourceSelect.value === 'horizons'
      ? 'official'
      : 'synthetic';
  const stepMinutes =
    requestedSource === 'official' ? Number(horizonsStepInput.value) : 60;
  if (requestedSource === 'official') {
    timelineHeading.textContent = 'NASA/JPL Horizons apparent-angle samples (UT)';
    timelineNote.textContent = 'Loading Horizons samples for the selected comparison sites…';
    timelineList.replaceChildren();
  } else {
    timelineNote.textContent = '';
  }

  if (
    requestedSource === 'official' &&
    (!Number.isInteger(stepMinutes) || stepMinutes < 1 || stepMinutes > 1440)
  ) {
    renderComparisonMessage('Horizons sample step must be a whole number from 1 to 1440 minutes.');
    timelineNote.textContent = 'Samples are unavailable until a valid Horizons sample step is entered.';
    return;
  }

  if (siteIds.length < 2) {
    renderComparisonMessage('Choose at least two sites to compare.');
    comparisonSourceLabel.textContent = 'Select at least two sites to build a comparison.';
    if (requestedSource === 'official') {
      timelineNote.textContent = 'Select at least two comparison sites to load the Horizons sample window.';
    }
    return;
  }

  comparisonSourceLabel.textContent =
    requestedSource === 'official'
      ? 'Loading NASA/JPL Horizons comparison at the selected demo-site coordinates…'
      : 'Loading synthetic demo comparison…';
  renderComparisonMessage('Loading comparison…');

  try {
    const response = await fetch(
      `/api/comparison?site_ids=${encodeURIComponent(siteIds.join(','))}&start_date=${startDateInput.value}&end_date=${endDateInput.value}&step_minutes=${stepMinutes}&source=${requestedSource}`
    );
    const payload = await response.json();
    if (requestVersion !== comparisonRequestVersion) {
      return;
    }

    if (!response.ok) {
      const errorMessage =
        typeof payload.detail === 'string'
          ? payload.detail
          : JSON.stringify(payload.detail || 'Unable to compare sites.');
      renderComparisonMessage(errorMessage);
      comparisonSourceLabel.textContent = 'Comparison request failed; no fallback data was used.';
      if (requestedSource === 'official') {
        timelineNote.textContent = 'Horizons samples could not be loaded; no synthetic samples were substituted.';
      }
      return;
    }

    if (!Array.isArray(payload.rows)) {
      renderComparisonMessage('Comparison response did not contain rows.');
      return;
    }

    const rows = payload.rows;
    renderComparisonChart(rows);
    renderVisibilityTimeline(rows, payload.time_system || 'UTC', payload.window);
    if (payload.source_type === 'official') {
      renderHorizonsSamples(rows, payload.time_system || 'UT');
    }
    comparisonSourceLabel.textContent =
      payload.source_type === 'official'
        ? `${payload.source_name || 'NASA/JPL Horizons'} apparent target-center elevation against the ideal 0° horizon (${payload.time_system}). Site coordinates are synthetic demo points, not official landing locations. Fractions use valid samples only.`
        : 'Synthetic demo comparison; not NASA ephemeris. Earth values are center-elevation samples against the ideal 0° horizon.';
    comparisonTableBody.innerHTML = rows
      .map(
        (row) => `
          <tr>
            <td>${row.site_name}</td>
            <td>${formatComparisonFraction(row.sunlit_fraction)}</td>
            <td>${formatComparisonDuration(row.longest_no_sunlight_interval_minutes)}</td>
            <td>${formatComparisonFraction(row.earth_visible_fraction)}</td>
            <td>${formatComparisonDuration(row.longest_no_earth_visibility_interval_minutes)}</td>
            <td>${formatComparisonCoverage(row)}</td>
          </tr>
        `
      )
      .join('');
  } catch (error) {
    if (requestVersion !== comparisonRequestVersion) {
      return;
    }
    renderComparisonMessage('Unable to load site comparison.');
    comparisonSourceLabel.textContent = 'Comparison request failed; no fallback data was used.';
    if (requestedSource === 'official') {
      timelineNote.textContent = 'Horizons samples could not be loaded; no synthetic samples were substituted.';
    }
    console.error(error);
  }
}

runAnalysisButton.addEventListener('click', updateAnalysis);
runComparisonButton.addEventListener('click', updateComparison);
siteSelect.addEventListener('change', updateAnalysis);
comparisonSiteSelect.addEventListener('change', updateComparison);
startDateInput.addEventListener('change', updateComparison);
endDateInput.addEventListener('change', updateComparison);
sourceSelect.addEventListener('change', () => {
  horizonsOptions.hidden =
    sourceSelect.value !== 'official' && sourceSelect.value !== 'horizons';
  updateAnalysis();
  updateComparison();
});
horizonsStepInput.addEventListener('change', () => {
  if (sourceSelect.value === 'official' || sourceSelect.value === 'horizons') {
    updateAnalysis();
    updateComparison();
  }
});

loadHealthStatus();
loadSites().then(() => {
  if (comparisonSiteSelect.options.length >= 2) {
    comparisonSiteSelect.options[0].selected = true;
    comparisonSiteSelect.options[1].selected = true;
    updateComparison();
  }
  updateAnalysis();
}).catch((error) => {
  console.error(error);
  summaryBox.textContent = 'Unable to load the synthetic site list.';
});
