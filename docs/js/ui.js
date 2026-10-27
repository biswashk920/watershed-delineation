import { formatLayerValue } from "./valuetiles.js";

const LAYER_HASH_ALIASES = new Map([["slope", "slope_deg"]]);

export class ViewerUI {
  constructor(manifest) {
    this.manifest = manifest;
    this.layers = manifest.layers;
    this.layerIndex = new Map(this.layers.map((layer, index) => [layer.name, index]));
    this.activeLayer = this.layers[0];
    this.view = "2d";
    this.basemap = "osm";
    this.opacity = 0.85;
    this.exaggeration = 1.5;
    this.onLayerChange = () => {};
    this.onViewChange = () => {};
    this.onBasemapChange = () => {};
    this.onOpacityChange = () => {};
    this.onExaggerationChange = () => {};
    this.onOutsideChange = () => {};
    this.onOverlayChange = () => {};
  }

  initialize() {
    this.#populateLayers();
    this.#bindEvents();
    this.applyHashState();
    this.updateLayer(this.activeLayer.name, { updateHash: false });
    this.setView(this.view, { updateHash: false });
    return this;
  }

  #element(id) {
    const element = document.getElementById(id);
    if (!element) throw new Error(`Missing viewer control element #${id}.`);
    return element;
  }

  #populateLayers() {
    const select = this.#element("layer-select");
    select.replaceChildren();
    for (const groupName of ["Terrain", "Hydrology"]) {
      const group = document.createElement("optgroup");
      group.label = groupName;
      for (const layer of this.layers.filter((item) => item.group === groupName)) {
        const option = document.createElement("option");
        option.value = layer.name;
        option.textContent = layer.title;
        group.append(option);
      }
      select.append(group);
    }
    select.disabled = false;
    this.#element("layer-count").textContent = `${this.layers.length} layers`;
    for (const id of ["previous-layer", "next-layer"]) {
      this.#element(id).disabled = false;
    }
  }

  #bindEvents() {
    this.#element("layer-select").addEventListener("change", (event) => {
      this.updateLayer(event.currentTarget.value);
    });
    this.#element("previous-layer").addEventListener("click", () => this.stepLayer(-1));
    this.#element("next-layer").addEventListener("click", () => this.stepLayer(1));
    document.addEventListener("keydown", (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const target = event.target;
      if (
        target instanceof HTMLElement
        && (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(target.tagName))
      ) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        this.stepLayer(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        this.stepLayer(1);
      }
    });
    this.#element("view-2d").addEventListener("click", () => this.setView("2d"));
    this.#element("view-3d").addEventListener("click", () => this.setView("3d"));
    this.#element("basemap-select").addEventListener("change", (event) => {
      this.basemap = event.currentTarget.value;
      this.onBasemapChange(this.basemap);
    });
    this.#element("opacity-slider").addEventListener("input", (event) => {
      this.opacity = Number(event.currentTarget.value) / 100;
      this.#element("opacity-value").textContent = `${Math.round(this.opacity * 100)}%`;
      this.onOpacityChange(this.opacity);
    });
    this.#element("exaggeration-slider").addEventListener("input", (event) => {
      this.exaggeration = Number(event.currentTarget.value);
      this.#element("exaggeration-value").textContent = `${this.exaggeration.toFixed(1)}×`;
      this.onExaggerationChange(this.exaggeration);
    });
    this.#element("dim-outside").addEventListener("change", (event) => {
      this.onOutsideChange(event.currentTarget.checked);
    });
    for (const id of ["show-boundary", "show-outlet"]) {
      this.#element(id).addEventListener("change", () => this.#sendOverlayVisibility());
    }
  }

  #sendOverlayVisibility() {
    this.onOverlayChange({
      boundary: this.#element("show-boundary").checked,
      outlet: this.#element("show-outlet").checked,
    });
  }

  #readHash() {
    const parameters = new URLSearchParams(location.hash.slice(1));
    const layerName = parameters.get("layer");
    const view = parameters.get("view");
    const resolvedLayer = LAYER_HASH_ALIASES.get(layerName) || layerName;
    if (resolvedLayer && this.layerIndex.has(resolvedLayer)) {
      this.activeLayer = this.layers[this.layerIndex.get(resolvedLayer)];
    }
    if (view === "2d" || view === "3d") this.view = view;
  }

  applyHashState() {
    this.#readHash();
  }

  #writeHash() {
    const layerName = this.activeLayer.name === "slope_deg"
      ? "slope"
      : this.activeLayer.name;
    const hash = new URLSearchParams({
      layer: layerName,
      view: this.view,
    });
    history.replaceState(null, "", `${location.pathname}${location.search}#${hash}`);
  }

  updateLayer(layerName, { updateHash = true } = {}) {
    const index = this.layerIndex.get(layerName);
    if (index === undefined) throw new Error(`Unknown layer selected: ${layerName}`);
    this.activeLayer = this.layers[index];
    this.#element("layer-select").value = layerName;
    this.#element("layer-position").textContent = `${index + 1} / ${this.layers.length}`;
    this.#element("layer-title").textContent = this.activeLayer.title;
    this.#element("layer-description").textContent = this.activeLayer.description;
    this.#element("map-layer-title").textContent = this.activeLayer.title;
    this.#element("value-readout").textContent = "Click within the watershed to inspect this layer.";
    this.#updateLegend();
    this.onLayerChange(this.activeLayer);
    if (updateHash) this.#writeHash();
  }

  stepLayer(step) {
    const index = this.layerIndex.get(this.activeLayer.name);
    const nextIndex = (index + step + this.layers.length) % this.layers.length;
    this.updateLayer(this.layers[nextIndex].name);
  }

  setView(view, { updateHash = true } = {}) {
    if (!["2d", "3d"].includes(view)) throw new Error(`Unsupported map view: ${view}`);
    this.view = view;
    this.#element("view-2d").setAttribute("aria-pressed", String(view === "2d"));
    this.#element("view-3d").setAttribute("aria-pressed", String(view === "3d"));
    this.#element("exaggeration-slider").disabled = view !== "3d";
    this.onViewChange(view);
    if (updateHash) this.#writeHash();
  }

  #updateLegend() {
    const layer = this.activeLayer;
    const ramp = this.#element("legend-ramp");
    const minimum = layer.display.min;
    const maximum = layer.display.max;
    this.#element("legend-units").textContent = layer.units || "";
    this.#element("legend-min").textContent = this.#formatTick(minimum);
    this.#element("legend-max").textContent = this.#formatTick(maximum);
    const note = layer.transform === "log10"
      ? "Colour ramp uses log₁₀ values; clicks report raw values."
      : layer.isVectorOverlay
        ? "Stream line width represents Strahler order."
        : `Display stretch: ${this.#formatTick(minimum)} to ${this.#formatTick(maximum)}.`;
    this.#element("legend-note").textContent = note;
    if (layer.name === "stream_network_25km2") {
      ramp.classList.add("is-categorical");
      ramp.setAttribute(
        "aria-label",
        "Stream line legend showing increasing Strahler order from 1 to 5",
      );
      ramp.replaceChildren();
      for (let order = 1; order <= 5; order += 1) {
        const item = document.createElement("span");
        const line = document.createElement("i");
        line.style.height = `${Math.max(2, order)}px`;
        item.append(line, document.createTextNode(String(order)));
        ramp.append(item);
      }
      return;
    }
    ramp.classList.remove("is-categorical");
    ramp.setAttribute("aria-label", `Colour legend for ${layer.title}`);
    ramp.replaceChildren();
    const gradient = layer.lut.map((color, index) => {
      return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${color[3] / 255}) ${(index / 255) * 100}%`;
    }).join(", ");
    ramp.style.background = `linear-gradient(90deg, ${gradient})`;
  }

  #formatTick(value) {
    return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4 }).format(value);
  }

  async showValue(lngLat, valueReader) {
    const readout = this.#element("value-readout");
    readout.textContent = `Reading ${this.activeLayer.title}…`;
    try {
      const value = await valueReader(this.activeLayer, lngLat.lng, lngLat.lat);
      const formatted = formatLayerValue(this.activeLayer, value);
      readout.textContent = `${formatted} · ${lngLat.lng.toFixed(5)}°, ${lngLat.lat.toFixed(5)}°`;
    } catch (error) {
      readout.textContent = "Value unavailable.";
      throw error;
    }
  }

  setBasemap(value, { updateHash = false } = {}) {
    if (!["osm", "eox", "none"].includes(value)) {
      throw new Error(`Unsupported basemap: ${value}`);
    }
    this.basemap = value;
    this.#element("basemap-select").value = value;
    this.onBasemapChange(value);
    if (updateHash) this.#writeHash();
  }
}
