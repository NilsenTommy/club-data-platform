const DATA_URL = "./data/visualizations.json";
const CLUB_NAME = "FK Bodo - Glimt";
const SVG_NS = "http://www.w3.org/2000/svg";

const segmentConfig = {
  INACTIVE: { label: "Inaktiv", color: "#94a5aa" },
  OCCASIONAL: { label: "Sporadisk", color: "#f3d43a" },
  ENGAGED: { label: "Engasjert", color: "#36b7c5" },
  HIGHLY_ENGAGED: { label: "Svært engasjert", color: "#07899a" },
};

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function createSvgElement(tag, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, value));
  return element;
}

function formatDate(date) {
  return new Intl.DateTimeFormat("nb-NO", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(`${date}T12:00:00Z`));
}

function opponent(match) {
  return match.home === CLUB_NAME ? match.away : match.home;
}

function renderSummary(data) {
  const summary = document.querySelector("#summary");
  const fans = data.segments.reduce((total, item) => total + item.count, 0);
  const highlyEngaged = data.segments.find((item) => item.segment === "HIGHLY_ENGAGED");
  const weather = data.matches.filter(
    (match) => match.temperatureC !== null && match.precipitationMm !== null,
  ).length;
  const metrics = [
    { value: data.matches.length, label: "Kamper", meta: "Komplett kampgrunnlag", accent: "#f3d43a" },
    { value: weather, label: "Komplett vær", meta: `${Math.round((weather / data.matches.length) * 100)} % datadekning`, accent: "#36b7c5" },
    { value: fans.toLocaleString("nb-NO"), label: "Supportere", meta: "Segmenterte profiler", accent: "#07899a" },
    { value: `${((highlyEngaged.count / fans) * 100).toFixed(1)} %`, label: "Svært engasjert", meta: `${highlyEngaged.count} supportere`, accent: "#e56a54" },
  ];

  summary.replaceChildren(
    ...metrics.map(({ value, label, meta, accent }) => {
      const metric = createElement("div", "metric");
      metric.style.setProperty("--metric-accent", accent);
      metric.append(
        createElement("span", "metric__label", label),
        createElement("span", "metric__value", value),
        createElement("span", "metric__meta", meta),
      );
      return metric;
    }),
  );
}

function renderAttendance(matches) {
  const container = document.querySelector("#attendance-chart");
  const sortedMatches = [...matches].sort((left, right) => right.ticketsSold - left.ticketsSold);
  const maximum = Math.max(...sortedMatches.map((match) => match.ticketsSold));

  const rows = sortedMatches.map((match) => {
    const row = createElement("div", "match-row");
    const label = createElement("div", "match-label");
    label.title = `${match.home} – ${match.away}`;
    label.append(
      createElement("span", "match-date", formatDate(match.date)),
      document.createTextNode(` ${opponent(match)}`),
    );

    const track = createElement("div", "bar-track");
    const fill = createElement("div", "bar-fill");
    fill.setAttribute("role", "img");
    fill.setAttribute(
      "aria-label",
      `${match.home} mot ${match.away}: ${match.ticketsSold} solgte billetter`,
    );
    track.append(fill);
    row.append(label, track, createElement("span", "bar-value", match.ticketsSold));

    requestAnimationFrame(() => {
      fill.style.width = `${(match.ticketsSold / maximum) * 100}%`;
    });
    return row;
  });

  container.replaceChildren(...rows);
}

function renderWeather(matches) {
  const container = document.querySelector("#weather-chart");
  const observations = matches.filter((match) => match.temperatureC !== null);
  const width = 920;
  const height = 390;
  const margin = { top: 28, right: 38, bottom: 58, left: 66 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const xMin = Math.floor(Math.min(...observations.map((item) => item.temperatureC)) - 1);
  const xMax = Math.ceil(Math.max(...observations.map((item) => item.temperatureC)) + 1);
  const yMin = Math.floor(Math.min(...observations.map((item) => item.ticketsSold)) / 20) * 20 - 20;
  const yMax = Math.ceil(Math.max(...observations.map((item) => item.ticketsSold)) / 20) * 20 + 20;
  const xScale = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * innerWidth;
  const yScale = (value) => margin.top + innerHeight - ((value - yMin) / (yMax - yMin)) * innerHeight;

  const svg = createSvgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": "weather-svg-title weather-svg-description",
  });
  const title = createSvgElement("title", { id: "weather-svg-title" });
  title.textContent = "Solgte billetter mot temperatur og nedbør";
  const description = createSvgElement("desc", { id: "weather-svg-description" });
  description.textContent = `${observations.length} kamper med temperaturmåling. Punktfarge viser nedbør.`;
  svg.append(title, description);

  const xTicks = Array.from({ length: 5 }, (_, index) => xMin + ((xMax - xMin) / 4) * index);
  const yTicks = Array.from({ length: 5 }, (_, index) => yMin + ((yMax - yMin) / 4) * index);

  yTicks.forEach((value) => {
    const y = yScale(value);
    svg.append(createSvgElement("line", {
      x1: margin.left,
      x2: width - margin.right,
      y1: y,
      y2: y,
      class: "grid-line",
    }));
    const label = createSvgElement("text", {
      x: margin.left - 12,
      y: y + 4,
      "text-anchor": "end",
      class: "tick-label",
    });
    label.textContent = Math.round(value);
    svg.append(label);
  });

  xTicks.forEach((value) => {
    const x = xScale(value);
    svg.append(createSvgElement("line", {
      x1: x,
      x2: x,
      y1: margin.top,
      y2: height - margin.bottom,
      class: "grid-line",
    }));
    const label = createSvgElement("text", {
      x,
      y: height - margin.bottom + 24,
      "text-anchor": "middle",
      class: "tick-label",
    });
    label.textContent = `${value.toFixed(1)}°`;
    svg.append(label);
  });

  svg.append(
    createSvgElement("line", {
      x1: margin.left,
      x2: width - margin.right,
      y1: height - margin.bottom,
      y2: height - margin.bottom,
      class: "axis-line",
    }),
    createSvgElement("line", {
      x1: margin.left,
      x2: margin.left,
      y1: margin.top,
      y2: height - margin.bottom,
      class: "axis-line",
    }),
  );

  const xLabel = createSvgElement("text", {
    x: margin.left + innerWidth / 2,
    y: height - 10,
    "text-anchor": "middle",
    class: "axis-label",
  });
  xLabel.textContent = "Temperatur (°C)";
  const yLabel = createSvgElement("text", {
    x: 16,
    y: margin.top + innerHeight / 2,
    transform: `rotate(-90 16 ${margin.top + innerHeight / 2})`,
    "text-anchor": "middle",
    class: "axis-label",
  });
  yLabel.textContent = "Solgte billetter";
  svg.append(xLabel, yLabel);

  observations.forEach((match, index) => {
    const color = match.precipitationMm === null
      ? "#94a5aa"
      : match.precipitationMm > 0
        ? "#e56a54"
        : "#07899a";
    const circle = createSvgElement("circle", {
      cx: xScale(match.temperatureC),
      cy: yScale(match.ticketsSold),
      r: 7,
      fill: color,
      class: "data-point",
      tabindex: "0",
      role: "img",
      "aria-label": `${opponent(match)}: ${match.ticketsSold} billetter, ${match.temperatureC} grader, ${match.precipitationMm ?? "ukjent"} millimeter nedbør`,
    });
    const pointTitle = createSvgElement("title");
    pointTitle.textContent = `${formatDate(match.date)} · ${opponent(match)} · ${match.ticketsSold} billetter · ${match.temperatureC} °C · ${match.precipitationMm ?? "ukjent"} mm`;
    circle.append(pointTitle);
    svg.append(circle);

    const label = createSvgElement("text", {
      x: xScale(match.temperatureC) + 10,
      y: yScale(match.ticketsSold) + (index % 2 === 0 ? -8 : 16),
      class: "point-label",
    });
    label.textContent = opponent(match);
    svg.append(label);
  });

  const legend = createElement("div", "legend");
  [
    ["#07899a", "Ingen nedbør"],
    ["#e56a54", "Nedbør"],
    ["#94a5aa", "Mangler nedbørsmåling"],
  ].forEach(([color, label]) => {
    const item = createElement("span", "legend__item");
    const swatch = createElement("span", "legend__swatch");
    swatch.style.backgroundColor = color;
    item.append(swatch, document.createTextNode(label));
    legend.append(item);
  });

  container.replaceChildren(svg, legend);
}

function renderSegments(segments) {
  const container = document.querySelector("#segment-chart");
  const maximum = Math.max(...segments.map((item) => item.count));
  const total = segments.reduce((sum, item) => sum + item.count, 0);

  const columns = segments.map((item) => {
    const config = segmentConfig[item.segment];
    const percentage = (item.count / total) * 100;
    const column = createElement("div", "segment-column");
    const bar = createElement("div", "segment-bar");
    bar.style.setProperty("--segment-color", config.color);
    bar.setAttribute("role", "img");
    bar.setAttribute(
      "aria-label",
      `${config.label}: ${item.count} supportere, ${percentage.toFixed(1)} prosent`,
    );
    column.append(
      createElement("div", "segment-value", `${item.count} · ${percentage.toFixed(1)} %`),
      bar,
      createElement("div", "segment-label", config.label),
    );
    requestAnimationFrame(() => {
      bar.style.height = `${(item.count / maximum) * 100}%`;
    });
    return column;
  });

  container.replaceChildren(...columns);
}

function renderError() {
  const error = document.querySelector("#error-template").content.cloneNode(true);
  document.querySelector("#summary").replaceChildren(error);
  ["#attendance-chart", "#weather-chart", "#segment-chart"].forEach((selector) => {
    document.querySelector(selector).replaceChildren(
      document.querySelector("#error-template").content.cloneNode(true),
    );
  });
}

async function init() {
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
    const data = await response.json();
    renderSummary(data);
    renderAttendance(data.matches);
    renderWeather(data.matches);
    renderSegments(data.segments);
    document.querySelector("#attendance-count").textContent = `${data.matches.length} kamper`;
    document.querySelector("#weather-count").textContent = `${data.matches.filter((match) => match.temperatureC !== null).length} målinger`;
    document.querySelector("#segment-count").textContent = `${data.segments.reduce((total, item) => total + item.count, 0)} supportere`;
    document.querySelector("#updated-at").textContent = `Data oppdatert ${formatDate(data.generatedAt.slice(0, 10))}`;
  } catch (error) {
    console.error(error);
    renderError();
  }
}

init();
