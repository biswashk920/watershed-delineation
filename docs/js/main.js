import * as maplibregl from "https://cdn.jsdelivr.net/npm/maplibre-gl@6.16.0/+esm";
import { WatershedMap } from "./map.js";
import { ViewerUI } from "./ui.js";
import { ValueTileStore } from "./valuetiles.js";

function showGlobalError(error) {
  const element = document.getElementById("global-error");
  element.textContent = `The watershed viewer could not start: ${error.message}`;
  element.hidden = false;
  document.getElementById("loading-indicator").hidden = true;
}

async function loadJson(url, label) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Could not load ${label} (${response.status} ${response.statusText}).`);
  }
  return response.json();
}

async function startViewer() {
  const dataBaseUrl = new URL("./data/", document.baseURI);
  const [manifest, boundary, outlet, streams] = await Promise.all([
    loadJson(new URL("layers.json", dataBaseUrl), "layer catalogue"),
    loadJson(new URL("boundary.geojson", dataBaseUrl), "watershed boundary"),
    loadJson(new URL("outlet.geojson", dataBaseUrl), "snapped outlet"),
    loadJson(new URL("streams.geojson", dataBaseUrl), "stream network"),
  ]);
  if (!Array.isArray(manifest.layers) || manifest.layers.length !== 19) {
    throw new Error("The layer catalogue is incomplete.");
  }
  const tileStore = new ValueTileStore(manifest, dataBaseUrl);
  const ui = new ViewerUI(manifest).initialize();
  const map = new WatershedMap({
    maplibregl,
    manifest,
    boundary,
    outlet,
    streams,
    tileStore,
    ui,
  });
  return { map, ui, tileStore };
}

startViewer().catch(showGlobalError);
