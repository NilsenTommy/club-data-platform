const DATA_URL = "./data/portfolio.json";
const SVG_NS = "http://www.w3.org/2000/svg";

const ROUTES = [
  {
    id: "overview",
    label: "Oversikt",
    kicker: "Oversikt",
    title: "Hovedtallene",
    lead: "",
    render: renderOverview,
  },
  {
    id: "matches",
    label: "Kamper",
    kicker: "Kamper",
    title: "Alle kamper",
    lead:
      "Filtrer og sorter kampene. Klikk på en kamp for å se resultat, billettsalg og vær.",
    render: renderMatches,
  },
  {
    id: "supporters",
    label: "Supportere",
    kicker: "Supportere",
    title: "Supportere og samtykke",
    lead:
      "Hvor mye supporterne kjøper, og hvem klubben har lov til å kontakte.",
    render: renderSupporters,
  },
  {
    id: "ml",
    label: "Maskinlæring",
    kicker: "Forsøk",
    title: "Forsøk med maskinlæring",
    lead:
      "En modell delte supporterne inn i grupper på egen hånd, for å se om den fant noe reglene ikke fanger opp.",
    render: renderMl,
  },
];

const SEGMENT_COLORS = {
  INACTIVE: "#94a5aa",
  OCCASIONAL: "#f3d43a",
  ENGAGED: "#36b7c5",
  HIGHLY_ENGAGED: "#07899a",
};

const DEFAULT_FILTERS = {
  side: "all",
  opponent: "all",
  from: "",
  to: "",
  weather: "all",
  sort: "date-desc",
};

const state = {
  data: null,
  routeId: ROUTES[0].id,
  filters: { ...DEFAULT_FILTERS },
  chartMode: "bars",
  segment: null,
  initialRender: true,
};

const numberFormat = new Intl.NumberFormat("nb-NO");
const decimalFormat = new Intl.NumberFormat("nb-NO", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function formatNumber(value) {
  return value === null || value === undefined ? "ukjent" : numberFormat.format(value);
}

function formatDecimal(value) {
  return value === null || value === undefined ? "ukjent" : decimalFormat.format(value);
}

function formatDate(isoDate) {
  return new Intl.DateTimeFormat("nb-NO", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(`${isoDate.slice(0, 10)}T12:00:00Z`));
}

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

function query(selector) {
  return document.querySelector(selector);
}

function temperatureLabel(match) {
  const value = match.weather.temperatureC;
  return value === null ? "ukjent" : `${formatDecimal(value)} °C`;
}

/* ---------------------------------------------------------------- Oversikt */

function renderOverview() {
  const { questions, findings } = state.data.overview;

  query("#overview-questions").replaceChildren(
    ...questions.map((question) => {
      const card = createElement("article", "question-card");
      card.append(
        createElement("h3", "question-card__title", question.title),
        createElement("p", "question-card__summary", question.summary),
        createRouteLink(question.view),
      );
      return card;
    }),
  );

  query("#overview-findings").replaceChildren(
    ...findings.map((finding) => createFindingCard(finding)),
  );
}

function createRouteLink(routeId) {
  const route = ROUTES.find((item) => item.id === routeId);
  const link = createElement("a", "text-link", `${route ? route.label : "Åpne"} →`);
  link.href = `#${routeId}`;
  return link;
}

function createFindingCard(finding) {
  const card = createElement("article", "finding-card");
  const header = createElement("header", "finding-card__header");
  header.append(
    createElement("h3", "finding-card__title", finding.title),
    createElement("p", "finding-card__value", finding.value),
    createElement("p", "finding-card__unit", finding.unit),
  );
  card.append(header);

  const tabDefinitions = [
    ["see", "Tallet"],
    ["missing", "Forbehold"],
    ["decision", "Løsning"],
  ];
  const tablist = createElement("div", "tabs");
  tablist.setAttribute("role", "tablist");
  tablist.setAttribute("aria-label", finding.title);
  const panels = [];

  tabDefinitions.forEach(([key, label], index) => {
    const tabId = `tab-${finding.id}-${key}`;
    const panelId = `panel-${finding.id}-${key}`;
    const tab = createElement("button", "tabs__tab", label);
    tab.type = "button";
    tab.id = tabId;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", panelId);
    tab.setAttribute("aria-selected", String(index === 0));
    tab.tabIndex = index === 0 ? 0 : -1;

    const panel = createElement("div", "tabs__panel", finding.tabs[key]);
    panel.id = panelId;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tabId);
    panel.hidden = index !== 0;

    tab.addEventListener("click", () => selectTab(tablist, panels, index));
    tablist.append(tab);
    panels.push(panel);
  });

  tablist.addEventListener("keydown", (event) => {
    const tabs = [...tablist.children];
    const current = tabs.indexOf(document.activeElement);
    if (current === -1) return;
    const offsets = {
      ArrowRight: 1,
      ArrowLeft: -1,
      Home: -current,
      End: tabs.length - 1 - current,
    };
    const offset = offsets[event.key];
    if (offset === undefined) return;
    event.preventDefault();
    const next = (current + offset + tabs.length) % tabs.length;
    selectTab(tablist, panels, next);
    tabs[next].focus();
  });

  card.append(tablist, ...panels, createRouteLink(finding.view));
  return card;
}

function selectTab(tablist, panels, activeIndex) {
  [...tablist.children].forEach((tab, index) => {
    const active = index === activeIndex;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    panels[index].hidden = !active;
  });
}

/* ------------------------------------------------------------------ Kamper */

function filteredMatches() {
  const { filters } = state;
  const rows = state.data.matches.rows.filter((match) => {
    if (filters.side === "home" && !match.isHome) return false;
    if (filters.side === "away" && match.isHome) return false;
    if (filters.opponent !== "all" && match.opponent !== filters.opponent) return false;
    if (filters.from && match.date < filters.from) return false;
    if (filters.to && match.date > filters.to) return false;
    if (filters.weather === "temperature" && !match.coverage.hasTemperature) return false;
    if (filters.weather === "complete" && !match.coverage.hasCompleteWeather) return false;
    if (filters.weather === "missing" && match.coverage.hasTemperature) return false;
    return true;
  });

  const comparators = {
    "date-desc": (left, right) => right.date.localeCompare(left.date),
    "date-asc": (left, right) => left.date.localeCompare(right.date),
    "tickets-desc": (left, right) => right.ticketsSold - left.ticketsSold,
    "tickets-asc": (left, right) => left.ticketsSold - right.ticketsSold,
    "temperature-desc": (left, right) => {
      const leftValue = left.weather.temperatureC;
      const rightValue = right.weather.temperatureC;
      if (leftValue === null && rightValue === null) return 0;
      if (leftValue === null) return 1;
      if (rightValue === null) return -1;
      return rightValue - leftValue;
    },
  };
  return rows.sort(comparators[filters.sort]);
}

function renderMatches() {
  const select = query("#filter-opponent");
  if (select.options.length === 1) {
    state.data.matches.opponents.forEach((opponent) => {
      select.append(new Option(opponent, opponent));
    });
  }
  query("#match-proxy-note").textContent = state.data.metadata.ticketsProxyNote;
  renderMatchResults();
}

function renderMatchResults() {
  const matches = filteredMatches();
  const total = state.data.matches.rows.length;
  const home = matches.filter((match) => match.isHome).length;

  query("#match-count").textContent = matches.length === total
    ? `Viser alle ${formatNumber(total)} kamper · ${formatNumber(home)} hjemme`
    : `Viser ${formatNumber(matches.length)} av ${formatNumber(total)} kamper · ${formatNumber(home)} hjemme`;

  const withTemperature = matches.filter((match) => match.coverage.hasTemperature).length;
  query("#match-chart-meta").textContent = `${formatNumber(withTemperature)} kamper har værdata`;

  const container = query("#match-chart");
  if (!matches.length) {
    container.replaceChildren(
      createElement(
        "p",
        "empty-state",
        "Ingen kamper passer til filtrene. Nullstill for å se alle kamper igjen.",
      ),
    );
    return;
  }

  const renderers = {
    bars: renderMatchBars,
    timeline: renderMatchTimeline,
    table: renderMatchTable,
  };
  container.replaceChildren(renderers[state.chartMode](matches));
}

function renderMatchBars(matches) {
  const maximum = Math.max(...matches.map((match) => match.ticketsSold));
  const list = createElement("div", "match-chart");

  matches.forEach((match) => {
    const row = createElement("button", "match-row");
    row.type = "button";
    row.setAttribute(
      "aria-label",
      `${match.opponent}, ${match.isHome ? "hjemme" : "borte"}, ${formatDate(match.date)}: ${formatNumber(match.ticketsSold)} solgte billetter. Åpne detaljer.`,
    );

    const label = createElement("span", "match-label");
    label.append(
      createElement("span", "match-date", formatDate(match.date)),
      document.createTextNode(` ${match.opponent}`),
    );

    const track = createElement("span", "bar-track");
    const fill = createElement("span", match.isHome ? "bar-fill bar-fill--home" : "bar-fill");
    track.append(fill);
    requestAnimationFrame(() => {
      const share = maximum === 0 ? 0 : (match.ticketsSold / maximum) * 100;
      fill.style.width = `${share}%`;
    });

    row.append(
      label,
      createElement("span", "side-tag", match.isHome ? "H" : "B"),
      track,
      createElement("span", "bar-value", formatNumber(match.ticketsSold)),
    );
    row.addEventListener("click", () => openMatchDialog(match));
    list.append(row);
  });
  return list;
}

function renderMatchTimeline(matches) {
  const ordered = [...matches].sort((left, right) => left.date.localeCompare(right.date));
  const width = 920;
  const height = 360;
  const margin = { top: 24, right: 30, bottom: 54, left: 64 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const times = ordered.map((match) => new Date(`${match.date}T12:00:00Z`).getTime());
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const maxTickets = Math.max(...ordered.map((match) => match.ticketsSold));
  const xScale = (time) =>
    margin.left +
    (maxTime === minTime ? innerWidth / 2 : ((time - minTime) / (maxTime - minTime)) * innerWidth);
  const yScale = (value) =>
    maxTickets === 0
      ? margin.top + innerHeight
      : margin.top + innerHeight - (value / maxTickets) * innerHeight;

  const figure = createElement("figure", "chart-figure");
  const svg = createSvgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `Tidslinje over solgte billetter for ${ordered.length} kamper.`,
  });

  const tickValues = maxTickets === 0
    ? [0]
    : Array.from({ length: 5 }, (_, index) => (maxTickets / 4) * index);
  tickValues.forEach((value) => {
    const y = yScale(value);
    svg.append(
      createSvgElement("line", {
        x1: margin.left,
        x2: width - margin.right,
        y1: y,
        y2: y,
        class: "grid-line",
      }),
    );
    const label = createSvgElement("text", {
      x: margin.left - 12,
      y: y + 4,
      "text-anchor": "end",
      class: "tick-label",
    });
    label.textContent = formatNumber(Math.round(value));
    svg.append(label);
  });

  if (ordered.length > 1) {
    const path = ordered
      .map((match, index) => {
        const command = index === 0 ? "M" : "L";
        return `${command}${xScale(times[index]).toFixed(1)} ${yScale(match.ticketsSold).toFixed(1)}`;
      })
      .join(" ");
    svg.append(createSvgElement("path", { d: path, class: "timeline-path" }));
  }

  ordered.forEach((match, index) => {
    const point = createSvgElement("circle", {
      cx: xScale(times[index]),
      cy: yScale(match.ticketsSold),
      r: 7,
      class: match.isHome ? "data-point data-point--home" : "data-point",
      tabindex: "0",
      role: "button",
      "aria-label": `${formatDate(match.date)}, ${match.opponent}, ${match.isHome ? "hjemme" : "borte"}: ${formatNumber(match.ticketsSold)} solgte billetter, temperatur ${temperatureLabel(match)}. Åpne detaljer.`,
    });
    const title = createSvgElement("title");
    title.textContent = `${formatDate(match.date)} · ${match.opponent} · ${formatNumber(match.ticketsSold)} billetter`;
    point.append(title);
    point.addEventListener("click", () => openMatchDialog(match));
    point.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openMatchDialog(match);
    });
    svg.append(point);
  });

  [
    [margin.left, ordered[0], "start"],
    [width - margin.right, ordered[ordered.length - 1], "end"],
  ].forEach(([x, match, anchor]) => {
    const label = createSvgElement("text", {
      x,
      y: height - margin.bottom + 26,
      "text-anchor": anchor,
      class: "tick-label",
    });
    label.textContent = formatDate(match.date);
    svg.append(label);
  });

  figure.append(
    svg,
    createElement(
      "figcaption",
      "chart-caption",
      "Gule punkter er hjemmekamper, blå er bortekamper. Tabellvisningen viser de samme tallene som tekst.",
    ),
  );
  return figure;
}

function renderMatchTable(matches) {
  const wrapper = createElement("div", "table-wrapper");
  const table = createElement("table", "data-table");
  table.append(
    createElement(
      "caption",
      null,
      "Solgte billetter, resultat og vær for hver kamp. Resultatet er sett fra Bodø/Glimt sin side.",
    ),
  );

  const head = createElement("thead");
  const headRow = createElement("tr");
  ["Dato", "Motstander", "Kamptype", "Billetter", "Resultat", "Temperatur", "Detaljer"].forEach(
    (label) => {
      const cell = createElement("th", null, label);
      cell.scope = "col";
      headRow.append(cell);
    },
  );
  head.append(headRow);

  const body = createElement("tbody");
  matches.forEach((match) => {
    const row = createElement("tr");
    const dateCell = createElement("th", null, formatDate(match.date));
    dateCell.scope = "row";

    const button = createElement("button", "button button--small", "Åpne");
    button.type = "button";
    button.setAttribute(
      "aria-label",
      `Åpne detaljer for ${match.opponent} ${formatDate(match.date)}`,
    );
    button.addEventListener("click", () => openMatchDialog(match));
    const actionCell = createElement("td");
    actionCell.append(button);

    row.append(
      dateCell,
      createElement("td", null, match.opponent),
      createElement("td", null, match.isHome ? "Hjemme" : "Borte"),
      createElement("td", "numeric", formatNumber(match.ticketsSold)),
      createElement("td", null, resultLabel(match)),
      createElement("td", "numeric", temperatureLabel(match)),
      actionCell,
    );
    body.append(row);
  });

  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

function resultLabel(match) {
  const labels = { win: "Seier", loss: "Tap", draw: "Uavgjort" };
  if (!match.result) return "ukjent";
  if (match.homeScore === null || match.awayScore === null) {
    return labels[match.result] ?? match.result;
  }
  const clubScore = match.isHome ? match.homeScore : match.awayScore;
  const opponentScore = match.isHome ? match.awayScore : match.homeScore;
  return `${labels[match.result] ?? match.result} ${clubScore}–${opponentScore}`;
}

function openMatchDialog(match) {
  query("#match-dialog-title").textContent = `${match.isHome ? "Hjemme" : "Borte"} mot ${match.opponent}`;

  const facts = [
    ["Dato", formatDate(match.date)],
    ["Turnering", match.competition ?? "ukjent"],
    ["Resultat", resultLabel(match)],
    ["Solgte billetter", formatNumber(match.ticketsSold)],
    ["Stadion", match.stadiumName ?? "mangler"],
    ["Land", match.country ?? "mangler"],
    ["Temperatur", temperatureLabel(match)],
    [
      "Nedbør",
      match.weather.precipitationMm === null
        ? "ukjent"
        : `${formatDecimal(match.weather.precipitationMm)} mm`,
    ],
    [
      "Vind",
      match.weather.windSpeedMs === null
        ? "ukjent"
        : `${formatDecimal(match.weather.windSpeedMs)} m/s`,
    ],
    [
      "Vær målt",
      match.weather.observedAt ? `${formatDate(match.weather.observedAt)} (UTC)` : "ingen måling",
    ],
  ];

  const list = createElement("dl", "fact-list");
  facts.forEach(([label, value]) => {
    list.append(createElement("dt", null, label), createElement("dd", null, value));
  });

  const coverage = createElement("ul", "coverage-list");
  [
    ["Stadionets posisjon", match.coverage.hasCoordinates],
    ["Temperatur ved avspark", match.coverage.hasTemperature],
    ["Nedbør og vind", match.coverage.hasCompleteWeather],
  ].forEach(([label, present]) => {
    const item = createElement(
      "li",
      present ? "coverage-list__item is-present" : "coverage-list__item",
      `${label}: ${present ? "finnes" : "mangler"}`,
    );
    coverage.append(item);
  });

  query("#match-dialog-body").replaceChildren(
    list,
    createElement("h3", "dialog-subheading", "Hva det finnes data på"),
    coverage,
  );
  query("#match-dialog").showModal();
}

/* -------------------------------------------------------------- Supportere */

function renderSupporters() {
  const supporters = state.data.supporters;
  const maximum = supporters.funnel[0].count;

  query("#funnel-meta").textContent = `${formatNumber(supporters.totalFans)} supportere`;
  query("#supporter-funnel").replaceChildren(
    ...supporters.funnel.map((stage) => {
      const share = (stage.count / maximum) * 100;
      const item = createElement("div", "funnel-stage");
      const track = createElement("div", "funnel-track");
      const fill = createElement("div", "funnel-fill");
      track.append(fill);
      requestAnimationFrame(() => {
        fill.style.width = `${share}%`;
      });
      item.append(
        createElement("p", "funnel-stage__label", stage.label),
        createElement(
          "p",
          "funnel-stage__value",
          `${formatNumber(stage.count)} · ${formatDecimal(share)} %`,
        ),
        track,
        createElement("p", "funnel-stage__note", stage.note),
      );
      return item;
    }),
  );

  const consent = supporters.consent;
  const consentTotal = consent.granted + consent.declined + consent.unknown;
  query("#consent-split").replaceChildren(
    ...[
      ["Har sagt ja", consent.granted, "#07899a"],
      ["Har sagt nei", consent.declined, "#e56a54"],
      ["Vet ikke", consent.unknown, "#94a5aa"],
    ].map(([label, count, color]) => {
      const row = createElement("div", "consent-row");
      const track = createElement("div", "consent-track");
      const fill = createElement("div", "consent-fill");
      fill.style.backgroundColor = color;
      track.append(fill);
      requestAnimationFrame(() => {
        fill.style.width = `${(count / consentTotal) * 100}%`;
      });
      row.append(
        createElement("p", "consent-row__label", label),
        track,
        createElement(
          "p",
          "consent-row__value",
          `${formatNumber(count)} · ${formatDecimal((count / consentTotal) * 100)} %`,
        ),
      );
      return row;
    }),
  );

  query("#segment-meta").textContent =
    `${formatNumber(supporters.activatable)} av ${formatNumber(supporters.totalFans)} kan kontaktes`;
  if (!state.segment) state.segment = supporters.segments[0].segment;
  renderSegmentList();
  renderSegmentDetail();
  query("#supporter-governance-note").textContent =
    `${supporters.governance.separation} ${supporters.governance.activationRule} ${supporters.governance.synthetic}`;
}

function renderSegmentList() {
  const segments = state.data.supporters.segments;
  const maximum = Math.max(...segments.map((segment) => segment.count));

  query("#segment-list").replaceChildren(
    ...segments.map((segment) => {
      const button = createElement("button", "segment-option");
      button.type = "button";
      button.setAttribute("aria-pressed", String(segment.segment === state.segment));
      button.style.setProperty("--segment-color", SEGMENT_COLORS[segment.segment]);

      const track = createElement("span", "segment-track");
      const fill = createElement("span", "segment-fill");
      track.append(fill);
      requestAnimationFrame(() => {
        fill.style.width = `${(segment.count / maximum) * 100}%`;
      });

      button.append(
        createElement("span", "segment-option__label", segment.label),
        track,
        createElement(
          "span",
          "segment-option__value",
          `${formatNumber(segment.count)} · ${formatDecimal(segment.share)} %`,
        ),
      );
      button.addEventListener("click", () => {
        state.segment = segment.segment;
        renderSegmentList();
        renderSegmentDetail();
      });
      return button;
    }),
  );
}

function renderSegmentDetail() {
  const segment = state.data.supporters.segments.find((item) => item.segment === state.segment);
  const activatableShare = segment.count ? (segment.activatable / segment.count) * 100 : 0;
  const blocked = segment.count - segment.activatable;

  const facts = [
    ["Definisjon", segment.rule],
    [
      "Størrelse",
      `${formatNumber(segment.count)} supportere · ${formatDecimal(segment.share)} % av alle`,
    ],
    ["Kamper kjøpt", formatDecimal(segment.medians.matchesPurchased)],
    ["Kjøp", formatDecimal(segment.medians.purchaseTransactions)],
    ["Billetter", formatDecimal(segment.medians.ticketsPurchased)],
    ["Brukt", `${formatNumber(segment.medians.totalSpend)} kr`],
    [
      "Kan kontaktes",
      `${formatNumber(segment.activatable)} · ${formatDecimal(activatableShare)} % av gruppen`,
    ],
  ];

  const list = createElement("dl", "fact-list");
  facts.forEach(([label, value]) => {
    list.append(createElement("dt", null, label), createElement("dd", null, value));
  });

  query("#segment-detail").replaceChildren(
    createElement("h3", "segment-detail__title", segment.label),
    list,
    createElement(
      "p",
      "segment-detail__note",
      `Kamper, kjøp, billetter og beløp er medianer for gruppen, målt over de siste 12 månedene. ${formatNumber(blocked)} av dem kan ikke kontaktes, fordi de mangler samtykke eller e-postadresse.`,
    ),
  );
}

/* ---------------------------------------------------------- ML-eksperiment */

function renderMl() {
  const ml = state.data.ml;

  renderMlSilhouette(ml);
  renderMlProfiles(ml);
  renderMlCrossTab(ml);
  renderMlGuardrails(ml);

  query("#ml-provenance-note").textContent = ml.promotion.note;
}

function renderMlSilhouette(ml) {
  const candidates = ml.selection.candidates;
  const scores = candidates.map((candidate) => candidate.silhouetteScore ?? 0);
  const minimum = Math.min(...scores);
  const maximum = Math.max(...scores);
  const span = maximum - minimum || 1;

  const width = 640;
  const height = 260;
  const padding = { top: 24, right: 24, bottom: 48, left: 56 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const barWidth = plotWidth / candidates.length;

  const svg = createSvgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `Poengsum for ${candidates[0].k} til ${candidates[candidates.length - 1].k} grupper. Modellen valgte ${ml.selection.selectedK} grupper.`,
  });

  candidates.forEach((candidate, index) => {
    const score = candidate.silhouetteScore ?? 0;
    // Skalaen starter litt under laveste score for å gjøre marginale forskjeller lesbare.
    const ratio = (score - minimum + span * 0.15) / (span * 1.15);
    const barHeight = Math.max(4, ratio * plotHeight);
    const x = padding.left + index * barWidth + barWidth * 0.2;
    const y = padding.top + plotHeight - barHeight;

    svg.append(
      createSvgElement("rect", {
        x,
        y,
        width: barWidth * 0.6,
        height: barHeight,
        rx: 4,
        class: candidate.selected ? "ml-bar ml-bar--selected" : "ml-bar",
      }),
    );

    const value = createSvgElement("text", {
      x: x + barWidth * 0.3,
      y: y - 8,
      "text-anchor": "middle",
      class: "tick-label",
    });
    value.textContent = formatSilhouette(score);
    svg.append(value);

    const label = createSvgElement("text", {
      x: x + barWidth * 0.3,
      y: padding.top + plotHeight + 22,
      "text-anchor": "middle",
      class: "tick-label",
    });
    label.textContent = `${candidate.k} grupper`;
    svg.append(label);

    if (candidate.selected) {
      const badge = createSvgElement("text", {
        x: x + barWidth * 0.3,
        y: padding.top + plotHeight + 40,
        "text-anchor": "middle",
        class: "tick-label tick-label--strong",
      });
      badge.textContent = "valgt";
      svg.append(badge);
    }
  });

  svg.append(
    createSvgElement("line", {
      x1: padding.left,
      y1: padding.top + plotHeight,
      x2: width - padding.right,
      y2: padding.top + plotHeight,
      class: "axis-line",
    }),
  );

  const figure = createElement("figure", "chart-figure");
  figure.append(svg);
  const caption = createElement(
    "figcaption",
    "chart-caption",
    "Poengsummen viser hvor tydelig gruppene skiller seg fra hverandre – høyere er bedre. Y-aksen er beskåret, så les de faktiske tallene over stolpene.",
  );
  figure.append(caption);

  query("#ml-silhouette").replaceChildren(figure);
  query("#ml-selection-meta").textContent = ml.selection.rule;
  query("#ml-caveat").textContent = ml.selection.caveat;
}

function formatSilhouette(value) {
  return new Intl.NumberFormat("nb-NO", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  }).format(value);
}

function renderMlProfiles(ml) {
  const table = createElement("table", "data-table");
  const features = ml.setup.features;

  const headRow = createElement("tr");
  headRow.append(createElement("th", null, "Gruppe"));
  headRow.append(createElement("th", null, "Antall"));
  features.forEach((feature) => {
    const cell = createElement("th", "numeric", featureLabel(feature));
    cell.scope = "col";
    headRow.append(cell);
  });
  const head = createElement("thead");
  head.append(headRow);
  table.append(head);

  const body = createElement("tbody");
  ml.profiles.forEach((profile) => {
    const row = createElement("tr");
    const name = createElement("th", null, profile.label);
    name.scope = "row";
    row.append(name);
    row.append(
      createElement(
        "td",
        "numeric",
        `${formatNumber(profile.count)} (${formatDecimal(profile.share)} %)`,
      ),
    );
    features.forEach((feature) => {
      row.append(createElement("td", "numeric", formatDecimal(profile.medians[feature])));
    });
    body.append(row);
  });
  table.append(body);

  const wrapper = createElement("div", "table-wrapper");
  wrapper.append(table);

  const notes = createElement("ul", "profile-notes");
  ml.profiles.forEach((profile) => {
    const item = createElement("li");
    item.append(
      createElement("strong", null, `${profile.label}. `),
      document.createTextNode(profile.interpretation),
    );
    notes.append(item);
  });

  query("#ml-profiles").replaceChildren(wrapper, notes);
  query("#ml-profile-meta").textContent = "Tallene er medianer for hver gruppe";
}

function featureLabel(feature) {
  const labels = {
    recency_days: "Dager siden sist",
    matches_purchased_12m: "Kamper",
    purchase_transactions_12m: "Kjøp",
    tickets_purchased_12m: "Billetter",
    total_spend_12m: "Brukt",
    cancelled_transactions_12m: "Kansellert",
    refunded_transactions_12m: "Refundert",
  };
  return labels[feature] ?? feature;
}

function renderMlCrossTab(ml) {
  const table = createElement("table", "data-table heatmap");
  const ruleSegments = ml.ruleComparison.ruleSegments;
  const groupLabels = new Map(ml.profiles.map((profile) => [profile.segment, profile.label]));

  const groupRow = createElement("tr");
  const modelHeading = createElement("th", null, "Modellens gruppe");
  modelHeading.scope = "col";
  modelHeading.rowSpan = 2;
  const ruleHeading = createElement("th", "heatmap__group-heading", "Regelbasert segment");
  ruleHeading.scope = "colgroup";
  ruleHeading.colSpan = ruleSegments.length;
  const totalHeading = createElement("th", "numeric", "Totalt i ML-gruppen");
  totalHeading.scope = "col";
  totalHeading.rowSpan = 2;
  groupRow.append(modelHeading, ruleHeading, totalHeading);

  const segmentRow = createElement("tr");
  ruleSegments.forEach((rule) => {
    const cell = createElement("th", "numeric", rule.label);
    cell.scope = "col";
    segmentRow.append(cell);
  });
  const head = createElement("thead");
  head.append(groupRow, segmentRow);
  table.append(head);

  const body = createElement("tbody");
  ml.ruleComparison.crossTab.forEach((row) => {
    const groupLabel = groupLabels.get(row.segment) ?? row.segment;
    const tableRow = createElement("tr");
    const name = createElement("th", null, groupLabel);
    name.scope = "row";
    tableRow.append(name);
    row.cells.forEach((cell) => {
      const element = createElement(
        "td",
        "numeric heatmap__cell",
        `${formatNumber(cell.count)} · ${formatDecimal(cell.share)} %`,
      );
      // Tallet står alltid synlig; fargen er kun en ekstra lesehjelp.
      element.style.setProperty("--intensity", (cell.share / 100).toFixed(3));
      if (cell.share >= 60) element.classList.add("heatmap__cell--strong");
      element.title = `${formatDecimal(cell.share)} % av ${groupLabel}`;
      tableRow.append(element);
    });
    tableRow.append(createElement("td", "numeric heatmap__total", formatNumber(row.total)));
    body.append(tableRow);
  });
  table.append(body);

  const wrapper = createElement("div", "table-wrapper");
  wrapper.append(table);

  query("#ml-crosstab").replaceChildren(
    wrapper,
    createElement(
      "p",
      "chart-caption",
      "Hver celle viser hvor mange i modellens gruppe som fikk det aktuelle regelbaserte segmentet. Prosenten beregnes innenfor modellgruppen.",
    ),
  );
  query("#ml-crosstab-meta").textContent =
    `Likhet med reglene: ${formatSilhouette(ml.ruleComparison.adjustedRandIndex)} av 1`;
  query("#ml-ari-note").textContent = ml.ruleComparison.note;
}

function renderMlGuardrails(ml) {
  const list = query("#ml-guardrails");
  list.replaceChildren();
  ml.guardrails.forEach((guardrail) => {
    const item = createElement("li", "coverage-list__item", guardrail.check);
    if (guardrail.passed) item.classList.add("is-present");
    list.append(item);
  });
}

/* ------------------------------------------------------------------ Router */

function routeFromHash() {
  const id = window.location.hash.replace("#", "");
  return ROUTES.find((route) => route.id === id) ?? ROUTES[0];
}

function applyRoute() {
  const route = routeFromHash();
  state.routeId = route.id;

  const dialog = query("#match-dialog");
  if (dialog.open) dialog.close();

  ROUTES.forEach((item) => {
    query(`#view-${item.id}`).hidden = item.id !== route.id;
  });
  document.querySelectorAll(".nav__link").forEach((link) => {
    const active = link.dataset.route === route.id;
    link.classList.toggle("nav__link--active", active);
    if (active) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });

  query("#view-kicker").textContent = route.kicker;
  query("#view-title").textContent = route.title;
  query("#view-lead").textContent = route.lead;
  document.title = `Klubbdata | ${route.label}`;

  route.render();

  if (!state.initialRender) query("#view-title").focus();
  state.initialRender = false;
}

function bindMatchControls() {
  const form = query("#match-filters");
  form.addEventListener("change", (event) => {
    if (!event.target.name) return;
    state.filters[event.target.name] = event.target.value;
    renderMatchResults();
  });
  form.addEventListener("reset", () => {
    state.filters = { ...DEFAULT_FILTERS };
    renderMatchResults();
  });

  document.querySelectorAll(".segmented__button").forEach((button) => {
    button.addEventListener("click", () => {
      state.chartMode = button.dataset.chart;
      document.querySelectorAll(".segmented__button").forEach((item) => {
        item.setAttribute("aria-pressed", String(item === button));
      });
      renderMatchResults();
    });
  });
}

function renderError() {
  query("#main").replaceChildren(query("#error-template").content.cloneNode(true));
}

async function init() {
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
    state.data = await response.json();
  } catch (error) {
    console.error(error);
    renderError();
    return;
  }

  const { sources } = state.data.metadata;
  query("#topbar-snapshot").textContent = `Kamper til og med ${formatDate(sources.matches.dataThroughAt)}`;
  query("#sidebar-snapshot").textContent = `Per ${formatDate(sources.supporters.asOfAt)}`;
  query("#footer-note").textContent = state.data.metadata.syntheticNote;

  bindMatchControls();
  window.addEventListener("hashchange", applyRoute);
  applyRoute();
}

init();
