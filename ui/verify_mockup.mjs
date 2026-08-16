#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const htmlPath = fileURLToPath(new URL("./index.html", import.meta.url));
let html;

try {
  // Keep this as the only read of index.html so verification always evaluates one snapshot.
  html = readFileSync(htmlPath, "utf8");
} catch (error) {
  console.error(`Mockup contract verification failed: cannot read ${htmlPath}`);
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

const errors = [];
const fail = (message) => errors.push(message);

function attributes(source) {
  const result = new Map();
  const pattern = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  for (const match of source.matchAll(pattern)) {
    result.set(match[1].toLowerCase(), match[2] ?? match[3] ?? match[4] ?? "");
  }
  return result;
}

function mask(source, pattern) {
  return source.replace(pattern, (value) => " ".repeat(value.length));
}

// Data hooks must exist in the static HTML, rather than only in generated script markup.
let structuralHtml = mask(html, /<!--[\s\S]*?-->/g);
structuralHtml = mask(structuralHtml, /<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi);

const tags = [];
const tagPattern = /<([a-z][\w:-]*)\b([^>]*)>/gi;
for (const match of structuralHtml.matchAll(tagPattern)) {
  tags.push({
    name: match[1].toLowerCase(),
    attrs: attributes(match[2]),
    index: match.index,
    source: match[0],
  });
}

function hooks(attributeName, tagName) {
  return tags.filter(
    (tag) => (!tagName || tag.name === tagName) && tag.attrs.has(attributeName),
  );
}

function values(entries, attributeName) {
  return entries.map((entry) => entry.attrs.get(attributeName));
}

function assertExactSequence(actual, expected, label) {
  if (actual.length !== expected.length || actual.some((value, index) => value !== expected[index])) {
    fail(`${label} must be exactly [${expected.join(", ")}]; found [${actual.join(", ")}].`);
  }
}

function assertInside(entries, start, end, label) {
  for (const entry of entries) {
    if (entry.index <= start || entry.index >= end) {
      fail(`${label} hook at source offset ${entry.index} is outside its required screen section.`);
    }
  }
}

// Contract 1: exactly three desktop screen sections, in the target flow order.
const expectedScreens = ["setup", "graph", "highlander"];
const screens = hooks("data-screen", "section");
assertExactSequence(values(screens, "data-screen"), expectedScreens, "data-screen sections");

const screenStart = Object.fromEntries(
  screens.map((screen) => [screen.attrs.get("data-screen"), screen.index]),
);
const setupStart = screenStart.setup ?? -1;
const graphStart = screenStart.graph ?? Number.POSITIVE_INFINITY;
const highlanderStart = screenStart.highlander ?? Number.POSITIVE_INFINITY;

// Contract 2: every static id is non-empty and unique.
const ids = new Map();
for (const tag of tags) {
  if (!tag.attrs.has("id")) continue;
  const id = tag.attrs.get("id");
  if (!id || /\s/.test(id)) {
    fail(`Invalid id ${JSON.stringify(id)} on <${tag.name}> at source offset ${tag.index}.`);
    continue;
  }
  if (ids.has(id)) {
    fail(`Duplicate id ${JSON.stringify(id)} at source offsets ${ids.get(id)} and ${tag.index}.`);
  } else {
    ids.set(id, tag.index);
  }
}

// Contract 3: the persistent shell carries the three non-live truth labels.
const truthStrips = hooks("data-truth-strip");
if (truthStrips.length !== 1) {
  fail(`Expected exactly one data-truth-strip hook; found ${truthStrips.length}.`);
} else if (truthStrips[0].index >= setupStart) {
  fail("data-truth-strip must be in the persistent shell before the first data-screen section.");
}
const persistentShell = setupStart > 0 ? html.slice(0, setupStart) : "";
for (const label of ["PROPOSED TARGET", "ILLUSTRATIVE MOCK DATA", "NO MODULES WIRED"]) {
  if (!persistentShell.includes(label)) {
    fail(`Persistent shell is missing the exact truth label ${JSON.stringify(label)}.`);
  }
}
for (const truthId of ["truth-design-status", "truth-data-basis", "truth-runtime-status"]) {
  if (!ids.has(truthId)) {
    fail(`Persistent truth strip is missing stable id ${JSON.stringify(truthId)}.`);
  }
}

// Contract 4: the six setup groups are statically hooked in the PRD order.
const expectedSetupInputs = [
  "clinical-indication",
  "biomarker-range",
  "max-biomarkers",
  "max-literature-papers",
  "hypothesis-range",
  "max-hypotheses-per-biomarker",
];
const setupInputs = hooks("data-setup-input");
assertExactSequence(values(setupInputs, "data-setup-input"), expectedSetupInputs, "data-setup-input hooks");
assertInside(setupInputs, setupStart, graphStart, "data-setup-input");

// Contract 4a: both exploration selectors expose ten discrete points, 1 through 10.
for (const rangeId of ["biomarker-low", "biomarker-high", "hypothesis-low", "hypothesis-high"]) {
  const range = tags.find((tag) => tag.name === "input" && tag.attrs.get("id") === rangeId);
  if (!range) {
    fail(`Missing ten-point range input ${JSON.stringify(rangeId)}.`);
    continue;
  }
  const expectedValue = rangeId.endsWith("-low") ? "1" : "10";
  const actual = {
    type: range.attrs.get("type"),
    min: range.attrs.get("min"),
    max: range.attrs.get("max"),
    step: range.attrs.get("step"),
    value: range.attrs.get("value"),
  };
  const expected = { type: "range", min: "1", max: "10", step: "1", value: expectedValue };
  if (Object.keys(expected).some((key) => actual[key] !== expected[key])) {
    fail(`${rangeId} must be a 1-10 range with step 1 and default ${expectedValue}; found ${JSON.stringify(actual)}.`);
  }
}

// Contract 4b: graph records keep the expanded review geometry requested for richer node content.
for (const [token, value] of [
  ["--node-w", "224px"],
  ["--node-h", "144px"],
  ["--lane", "272px"],
  ["--band", "440px"],
]) {
  const declaration = new RegExp(`${token.replace(/[.*+?^\${}()|[\]\\]/g, "\\$&")}\\s*:\\s*${value.replace(".", "\\.")}\\s*;`);
  if (!declaration.test(html)) {
    fail(`Expanded graph geometry must declare ${token}: ${value}.`);
  }
}

// Contract 5: the graph exposes one root and only the five scientific stage bands.
const graphRoots = hooks("data-graph-root");
if (graphRoots.length !== 1 || graphRoots[0].attrs.get("data-graph-root") !== "indication") {
  fail(`Expected one data-graph-root="indication" hook; found [${values(graphRoots, "data-graph-root").join(", ")}].`);
}
assertInside(graphRoots, graphStart, highlanderStart, "data-graph-root");

const expectedGraphStages = ["biomarker", "hypothesis", "roi", "recruitability", "simulation"];
const graphStages = hooks("data-graph-stage");
assertExactSequence(values(graphStages, "data-graph-stage"), expectedGraphStages, "data-graph-stage bands");
assertInside(graphStages, graphStart, highlanderStart, "data-graph-stage");
for (const stage of values(graphStages, "data-graph-stage")) {
  if (/evidence|highlander/i.test(stage)) {
    fail(`Graph stage ${JSON.stringify(stage)} is not allowed; evidence is inspection-only and Highlander is screen 3.`);
  }
}

// Contract 6: the Highlander screen has stable comparison, detail, and chat surfaces.
const expectedHighlanderSections = ["comparison", "detail", "chat"];
const highlanderSections = hooks("data-highlander-section");
assertExactSequence(
  values(highlanderSections, "data-highlander-section"),
  expectedHighlanderSections,
  "data-highlander-section hooks",
);
assertInside(highlanderSections, highlanderStart, Number.POSITIVE_INFINITY, "data-highlander-section");

// Contract 6a: the Pareto view includes one labeled nominal frontier projection.
const paretoFrontierPlots = hooks("data-pareto-frontier", "svg");
assertExactSequence(
  values(paretoFrontierPlots, "data-pareto-frontier"),
  ["nominal-projection"],
  "data-pareto-frontier hooks",
);
assertInside(paretoFrontierPlots, highlanderStart, Number.POSITIVE_INFINITY, "data-pareto-frontier");
const paretoFrontierLines = hooks("data-pareto-frontier-line", "polyline");
assertExactSequence(
  values(paretoFrontierLines, "data-pareto-frontier-line"),
  ["nominal-projection"],
  "data-pareto-frontier-line hooks",
);
assertInside(paretoFrontierLines, highlanderStart, Number.POSITIVE_INFINITY, "data-pareto-frontier-line");
if (!html.includes('frontierLine.setAttribute("points"')) {
  fail("The nominal Pareto frontier line must be positioned from rendered non-dominated records.");
}

// Contract 7: status changes have at least one polite announcement region.
const politeRegions = tags.filter((tag) => tag.attrs.get("aria-live")?.toLowerCase() === "polite");
if (politeRegions.length === 0) {
  fail('Expected at least one static aria-live="polite" status region.');
}

// Contract 8: assets stay self-contained and the only transport is guarded, same-origin fetch.
const externalScriptTags = [...html.matchAll(/<script\b([^>]*)>/gi)].filter((match) =>
  attributes(match[1]).has("src"),
);
if (externalScriptTags.length > 0) {
  fail(`External script references are forbidden; found ${externalScriptTags.length} <script src> tag(s).`);
}

const externalLinkTags = tags.filter((tag) => tag.name === "link" && tag.attrs.has("href"));
if (externalLinkTags.length > 0) {
  fail(`External stylesheet/font/link references are forbidden; found ${externalLinkTags.length} <link href> tag(s).`);
}

const assetAttributes = ["src", "srcset", "poster", "data"];
for (const tag of tags) {
  for (const attributeName of assetAttributes) {
    if (tag.attrs.has(attributeName) && tag.attrs.get(attributeName)) {
      fail(`External asset attribute ${attributeName} is forbidden on <${tag.name}> at source offset ${tag.index}.`);
    }
  }
  for (const attributeName of ["href", "xlink:href"]) {
    if (!tag.attrs.has(attributeName)) continue;
    const reference = tag.attrs.get(attributeName);
    if (reference && !reference.startsWith("#")) {
      fail(`Only in-document fragment links are allowed; found ${attributeName}=${JSON.stringify(reference)} on <${tag.name}>.`);
    }
  }
  for (const attributeName of ["action", "formaction"]) {
    const destination = tag.attrs.get(attributeName);
    if (destination && destination !== "#") {
      fail(`Form network destination ${attributeName}=${JSON.stringify(destination)} is forbidden.`);
    }
  }
}

const forbiddenSourcePatterns = [
  [/(?:https?|wss?):\/\//i, "remote URL scheme"],
  [/@import\b/i, "CSS @import"],
  [/@font-face\b/i, "CSS @font-face"],
  [/\burl\s*\(/i, "CSS url() asset reference"],
  [/\beval\s*\(/i, "eval()"],
  [/\bnew\s+Function\s*\(/i, "new Function()"],
  [/\b(?:XMLHttpRequest|WebSocket|EventSource|importScripts)\b/i, "non-fetch network API"],
  [/\bnavigator\s*\.\s*sendBeacon\b/i, "sendBeacon()"],
  [/\bimport\s*\(/i, "dynamic import()"],
  [/\b(?:setTimeout|setInterval)\s*\(\s*["'`]/i, "string-evaluating timer"],
];
for (const [pattern, description] of forbiddenSourcePatterns) {
  if (pattern.test(html)) {
    fail(`Forbidden ${description} detected in index.html.`);
  }
}

const csp = tags.find(
  (tag) => tag.name === "meta" && tag.attrs.get("http-equiv")?.toLowerCase() === "content-security-policy",
);
if (!csp || !/\bconnect-src\s+'self'(?:\s*;|\s|$)/i.test(csp.attrs.get("content") || "")) {
  fail("Content Security Policy must restrict connect-src to 'self'.");
}

const fetchCalls = [...html.matchAll(/\b(?:window\s*\.\s*)?fetch\s*\(/g)];
if (fetchCalls.length !== 1 || !/\bwindow\s*\.\s*fetch\s*\(/.test(fetchCalls[0]?.[0] || "")) {
  fail(`Expected exactly one guarded window.fetch call; found ${fetchCalls.length}.`);
}
for (const transportContract of [
  ["/api/runs", "run creation route"],
  ["/state", "run-state polling route"],
  ["/highlander", "Highlander launch route"],
  ["acknowledgeGaps", "Highlander terminal-gap acknowledgement"],
  ["Blocked non-run API path.", "relative run-path guard"],
  ['window.location.protocol === "file:"', "explicit file fixture mode"],
  ['get("mode") === "fixture"', "explicit served fixture mode"],
  ["DEMO_FALLBACK", "fallback truth vocabulary"],
  ["reasonCode", "machine-readable reason mapping"],
]) {
  if (!html.includes(transportContract[0])) {
    fail(`Missing ${transportContract[1]} contract marker ${JSON.stringify(transportContract[0])}.`);
  }
}

if (errors.length > 0) {
  console.error(`Mockup contract verification failed with ${errors.length} issue${errors.length === 1 ? "" : "s"}:`);
  for (const error of errors) console.error(`  - ${error}`);
  process.exit(1);
}

console.log("Mockup contract verification passed.");
console.log(`  Screens: ${expectedScreens.join(" -> ")}`);
console.log(`  Setup groups: ${expectedSetupInputs.length}`);
console.log("  Setup ranges: biomarker 1-10; hypothesis boldness 1-10");
console.log("  Graph geometry: 224x144 nodes; 272px lanes; 440px bands");
console.log(`  Graph: 1 indication root + ${expectedGraphStages.length} stage bands`);
console.log(`  Highlander surfaces: ${expectedHighlanderSections.join(", ")}`);
console.log("  Pareto: nominal 2-D frontier projection");
console.log(`  Unique static ids: ${ids.size}`);
console.log("  Transport: no external assets or remote URLs; one guarded same-origin fetch; explicit fixture mode");
