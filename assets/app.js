const dateSelect = document.querySelector('#date-select');
const refreshButton = document.querySelector('#refresh-button');
const statusElement = document.querySelector('#status');
const overviewElement = document.querySelector('#daily-overview');
const summaryBody = document.querySelector('#summary-body');
const chartsElement = document.querySelector('#charts');
const activityToggle = document.querySelector('#activity-toggle');

let dashboardData = null;

function formatNumber(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${remaining}s`;
  return `${remaining}s`;
}

async function getJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || 'Dashboard request failed.');
  return body;
}

async function loadDates(keepSelection = true) {
  const previous = keepSelection ? dateSelect.value : '';
  const result = await getJson('/api/dates');
  dateSelect.replaceChildren();

  result.dates.forEach((day) => {
    const option = document.createElement('option');
    option.value = day;
    option.textContent = new Date(`${day}T12:00:00`).toLocaleDateString(undefined, {
      weekday: 'short', year: 'numeric', month: 'short', day: 'numeric'
    });
    dateSelect.append(option);
  });

  if (previous && result.dates.includes(previous)) dateSelect.value = previous;
  return result.dates;
}

async function loadSelectedDay() {
  if (!dateSelect.value) {
    showEmpty('No valid CSV readings have been found yet.');
    return;
  }

  refreshButton.disabled = true;
  statusElement.textContent = 'Reading CSV logs…';

  try {
    dashboardData = await getJson(`/api/day/${encodeURIComponent(dateSelect.value)}`);
    renderDashboard(dashboardData);
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    statusElement.textContent = `Updated ${now}. ${dashboardData.sample_count.toLocaleString()} valid samples found.`;
  } catch (error) {
    showEmpty(error.message);
    statusElement.textContent = error.message;
  } finally {
    refreshButton.disabled = false;
  }
}

function showEmpty(message) {
  overviewElement.replaceChildren();
  summaryBody.replaceChildren();
  chartsElement.innerHTML = `<div class="empty card">${message}</div>`;
}

function renderDashboard(data) {
  const pollutantKeys = ['pm1', 'pm25', 'pm4', 'pm10', 'voc', 'nox', 'co2'];
  const totalEvents = pollutantKeys.reduce((total, key) => total + (data.summary[key].exceedance_events || 0), 0);
  const totalExposure = pollutantKeys.reduce((total, key) => total + (data.summary[key].seconds_above || 0), 0);

  overviewElement.innerHTML = `
    <div class="card"><strong>${data.sample_count.toLocaleString()}</strong><span>valid readings</span></div>
    <div class="card"><strong>${totalEvents.toLocaleString()}</strong><span>threshold exceedance events</span></div>
    <div class="card"><strong>${formatDuration(totalExposure)}</strong><span>combined time above thresholds</span></div>`;

  summaryBody.replaceChildren();
  Object.entries(data.metrics).forEach(([key, metric]) => {
    const summary = data.summary[key];
    if (!summary.valid_samples) return;
    const row = document.createElement('tr');
    const threshold = metric.threshold === null ? '' : `<span class="threshold-note">Limit ${formatNumber(metric.threshold)} ${metric.unit}</span>`;
    row.innerHTML = `
      <td><strong>${metric.label}</strong>${threshold}</td>
      <td>${formatNumber(summary.maximum)} ${metric.unit}</td>
      <td>${formatNumber(summary.average)} ${metric.unit}</td>
      <td>${summary.exceedance_events ?? '—'}</td>
      <td>${formatDuration(summary.seconds_above)}</td>`;
    summaryBody.append(row);
  });

  chartsElement.replaceChildren();
  Object.entries(data.metrics).forEach(([key, metric]) => {
    if (!data.summary[key].valid_samples) return;
    chartsElement.append(createChartCard(key, metric, data.points));
  });
}

function createChartCard(key, metric, points) {
  const visiblePoints = activityToggle.checked ? focusOnActivity(points, key, metric) : points;
  const card = document.createElement('article');
  card.className = 'chart-card card';
  card.innerHTML = `
    <div class="chart-title"><h3>${metric.label}</h3><span>${metric.unit} · ${visiblePoints.length < points.length ? 'activity view' : 'full day'}</span></div>
    <div class="chart-shell"><canvas aria-label="${metric.label} time-series graph"></canvas><div class="tooltip"></div></div>`;

  const canvas = card.querySelector('canvas');
  const tooltip = card.querySelector('.tooltip');
  const values = visiblePoints.map((point) => point[key]);

  const draw = () => drawChart(canvas, tooltip, visiblePoints, values, metric);
  requestAnimationFrame(draw);
  new ResizeObserver(draw).observe(canvas.parentElement);
  return card;
}

function focusOnActivity(points, key, metric) {
  if (metric.threshold === null || points.length < 10) return points;

  const valid = points
    .map((point, index) => ({ index, value: point[key] }))
    .filter((item) => item.value !== null);
  if (valid.length < 3) return points;

  const sorted = valid.map((item) => item.value).sort((a, b) => a - b);
  const baseline = sorted[Math.floor(sorted.length / 2)];
  const maximum = sorted[sorted.length - 1];
  const cutoff = Math.max(
    baseline + (maximum - baseline) * 0.1,
    metric.threshold * 0.2
  );
  if (maximum <= cutoff) return points;

  const active = valid.filter((item) => item.value >= cutoff);
  if (!active.length) return points;

  const firstTime = new Date(points[active[0].index].timestamp).getTime();
  const lastTime = new Date(points[active[active.length - 1].index].timestamp).getTime();
  const padding = Math.max((lastTime - firstTime) * 0.08, 60_000);
  const focused = points.filter((point) => {
    const time = new Date(point.timestamp).getTime();
    return time >= firstTime - padding && time <= lastTime + padding;
  });

  return focused.length >= 2 ? focused : points;
}

function drawChart(canvas, tooltip, points, values, metric) {
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(bounds.width * ratio);
  canvas.height = Math.round(bounds.height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);

  const width = bounds.width;
  const height = bounds.height;
  const pad = { left: 48, right: 12, top: 12, bottom: 28 };
  const valid = values.filter((value) => value !== null);
  const threshold = metric.threshold;
  let minimum = Math.min(...valid, 0);
  let maximum = Math.max(...valid, threshold ?? 0);
  if (minimum === maximum) maximum = minimum + 1;
  const margin = (maximum - minimum) * 0.08;
  maximum += margin;
  if (minimum !== 0) minimum -= margin;

  const start = new Date(points[0].timestamp).getTime();
  const end = new Date(points[points.length - 1].timestamp).getTime();
  const span = Math.max(end - start, 1000);
  const x = (point) => pad.left + ((new Date(point.timestamp).getTime() - start) / span) * (width - pad.left - pad.right);
  const y = (value) => pad.top + ((maximum - value) / (maximum - minimum)) * (height - pad.top - pad.bottom);

  ctx.clearRect(0, 0, width, height);
  ctx.font = '11px system-ui';
  ctx.fillStyle = '#64767a';
  ctx.strokeStyle = '#dbe4e2';
  ctx.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const value = minimum + ((maximum - minimum) * step) / 4;
    const lineY = y(value);
    ctx.beginPath(); ctx.moveTo(pad.left, lineY); ctx.lineTo(width - pad.right, lineY); ctx.stroke();
    ctx.fillText(formatNumber(value), 4, lineY + 4);
  }

  if (threshold !== null) {
    ctx.save();
    ctx.strokeStyle = '#d64545';
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(pad.left, y(threshold)); ctx.lineTo(width - pad.right, y(threshold)); ctx.stroke();
    ctx.restore();
  }

  let previous = null;
  points.forEach((point, index) => {
    const value = values[index];
    if (value === null) { previous = null; return; }
    if (previous) {
      const previousSafe = threshold === null || previous.value < threshold || (metric.ppe_protective && previous.point.ppe_worn);
      const currentSafe = threshold === null || value < threshold || (metric.ppe_protective && point.ppe_worn);
      const startX = x(previous.point);
      const startY = y(previous.value);
      const endX = x(point);
      const endY = y(value);

      if (previousSafe === currentSafe) {
        strokeSegment(ctx, startX, startY, endX, endY, currentSafe);
      } else {
        const crossesThreshold = threshold !== null && (previous.value - threshold) * (value - threshold) <= 0 && previous.value !== value;
        const ratio = crossesThreshold ? (threshold - previous.value) / (value - previous.value) : 0.5;
        const splitX = startX + (endX - startX) * ratio;
        const splitY = startY + (endY - startY) * ratio;
        strokeSegment(ctx, startX, startY, splitX, splitY, previousSafe);
        strokeSegment(ctx, splitX, splitY, endX, endY, currentSafe);
      }
    }
    previous = { point, value };
  });

  ctx.fillStyle = '#64767a';
  ctx.textAlign = 'left';
  ctx.fillText(new Date(start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), pad.left, height - 7);
  ctx.textAlign = 'right';
  ctx.fillText(new Date(end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), width - pad.right, height - 7);

  canvas.onpointermove = (event) => {
    const pointerX = event.clientX - bounds.left;
    let closest = null;
    points.forEach((point, index) => {
      if (values[index] === null) return;
      const distance = Math.abs(x(point) - pointerX);
      if (!closest || distance < closest.distance) closest = { point, value: values[index], distance, px: x(point), py: y(values[index]) };
    });
    if (!closest) return;
    tooltip.style.display = 'block';
    tooltip.style.left = `${closest.px}px`;
    tooltip.style.top = `${closest.py}px`;
    const above = metric.threshold !== null && closest.value >= metric.threshold;
    const protectedByPpe = above && metric.ppe_protective && closest.point.ppe_worn;
    let state = 'Below limit';
    if (above && protectedByPpe) state = 'Above limit · PPE confirmed';
    else if (above && metric.ppe_protective) state = 'Above limit · PPE not confirmed';
    else if (above) state = 'Above limit';
    tooltip.innerHTML = `${new Date(closest.point.timestamp).toLocaleTimeString()}<br><strong>${formatNumber(closest.value)} ${metric.unit}</strong><br>${state}`;
  };
  canvas.onpointerleave = () => { tooltip.style.display = 'none'; };
}

function strokeSegment(ctx, startX, startY, endX, endY, safe) {
  ctx.strokeStyle = safe ? '#168c75' : '#d64545';
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  ctx.moveTo(startX, startY);
  ctx.lineTo(endX, endY);
  ctx.stroke();
}

dateSelect.addEventListener('change', loadSelectedDay);
activityToggle.addEventListener('change', () => {
  if (dashboardData) renderDashboard(dashboardData);
});
refreshButton.addEventListener('click', async () => {
  await loadDates(true);
  await loadSelectedDay();
});

(async () => {
  try {
    const dates = await loadDates(false);
    if (dates.length) await loadSelectedDay();
    else showEmpty('No valid CSV readings have been found yet.');
  } catch (error) {
    showEmpty(error.message);
    statusElement.textContent = error.message;
  }
})();
