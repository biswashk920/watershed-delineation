import { registerValueTileProtocol } from "./valuetiles.js";

const BASEMAPS = {
  osm: {
    source: "basemap-osm",
    layer: "basemap-osm-layer",
    sourceDefinition: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
    },
  },
  eox: {
    source: "basemap-eox",
    layer: "basemap-eox-layer",
    sourceDefinition: {
      type: "raster",
      tiles: [
        "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg",
      ],
      tileSize: 256,
      minzoom: 0,
      maxzoom: 19,
    },
  },
};

const RASTER_LAYER_ID = "active-value-raster";
const OUTSIDE_LAYER_ID = "outside-watershed-dim";
const STREAM_OUTLINE_ID = "stream-outline";
const STREAM_LINE_ID = "stream-network";
const BOUNDARY_LAYER_ID = "watershed-boundary";
const OUTLET_LAYER_ID = "snapped-outlet";

function firstOverlayLayer(map) {
  return [
    OUTSIDE_LAYER_ID,
    RASTER_LAYER_ID,
    STREAM_OUTLINE_ID,
    STREAM_LINE_ID,
    BOUNDARY_LAYER_ID,
    OUTLET_LAYER_ID,
  ].find((id) => map.getLayer(id));
}

function makeOutsideMask(boundary) {
  const coordinates = boundary.features.flatMap((feature) => {
    const geometry = feature.geometry;
    if (geometry.type === "Polygon") return [geometry.coordinates];
    if (geometry.type === "MultiPolygon") return geometry.coordinates;
    throw new Error(`Unsupported watershed boundary geometry: ${geometry.type}`);
  });
  const outer = [
    [-179.999, -84.999],
    [179.999, -84.999],
    [179.999, 84.999],
    [-179.999, 84.999],
    [-179.999, -84.999],
  ];
  const holes = coordinates.map((polygon) => polygon[0]);
  return {
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [outer, ...holes] },
    }],
  };
}

function baseStyle() {
  return {
    version: 8,
    sources: {},
    layers: [{
      id: "map-background",
      type: "background",
      paint: { "background-color": "#dfe8e7" },
    }],
  };
}

export class WatershedMap {
  constructor({ maplibregl, manifest, boundary, outlet, streams, tileStore, ui }) {
    this.maplibregl = maplibregl;
    this.manifest = manifest;
    this.boundary = boundary;
    this.outlet = outlet;
    this.streams = streams;
    this.tileStore = tileStore;
    this.ui = ui;
    this.currentBasemap = null;
    this.sourcesReady = false;
    this.layerRequestId = 0;
    this.clickRequestId = 0;
    registerValueTileProtocol(maplibregl, tileStore);
    this.map = new maplibregl.Map({
      container: "map",
      style: baseStyle(),
      center: [
        (manifest.bounds[0] + manifest.bounds[2]) / 2,
        (manifest.bounds[1] + manifest.bounds[3]) / 2,
      ],
      zoom: 6.7,
      minZoom: 5.5,
      maxZoom: 18,
      maxPitch: 70,
      attributionControl: false,
      cooperativeGestures: true,
    });
    this.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    this.map.on("load", () => this.#onLoad());
    this.map.on("error", (event) => {
      const message = event.error?.message || "A map tile or map resource could not be loaded.";
      this.#showError(message);
    });
    this.map.on("click", (event) => this.#readClick(event.lngLat));
    this.#connectUI();
  }

  #connectUI() {
    this.ui.onLayerChange = (layer) => this.setLayer(layer);
    this.ui.onViewChange = (view) => this.setView(view);
    this.ui.onBasemapChange = (basemap) => this.setBasemap(basemap);
    this.ui.onOpacityChange = (opacity) => this.setOpacity(opacity);
    this.ui.onExaggerationChange = (value) => this.setExaggeration(value);
    this.ui.onOutsideChange = (enabled) => this.setOutsideDim(enabled);
    this.ui.onOverlayChange = (visibility) => this.setOverlayVisibility(visibility);
    document.getElementById("reset-view").addEventListener("click", () => this.fitWatershed());
  }

  #onLoad() {
    this.map.addSource("watershed-boundary-data", {
      type: "geojson",
      data: this.boundary,
    });
    this.map.addSource("watershed-outlet-data", {
      type: "geojson",
      data: this.outlet,
    });
    this.map.addSource("watershed-outside-mask-data", {
      type: "geojson",
      data: makeOutsideMask(this.boundary),
    });
    this.map.addSource("watershed-stream-data", {
      type: "geojson",
      data: this.streams,
    });
    this.map.addSource("aws-terrarium", {
      type: "raster-dem",
      tiles: [
        "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      maxzoom: 15,
      encoding: "terrarium",
    });
    this.map.addLayer({
      id: OUTSIDE_LAYER_ID,
      type: "fill",
      source: "watershed-outside-mask-data",
      layout: { visibility: "none" },
      paint: { "fill-color": "#172a35", "fill-opacity": 0.26 },
    });
    this.map.addLayer({
      id: BOUNDARY_LAYER_ID,
      type: "line",
      source: "watershed-boundary-data",
      paint: {
        "line-color": "#202020",
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 12, 2.4],
        "line-opacity": 0.95,
      },
    });
    this.map.addLayer({
      id: OUTLET_LAYER_ID,
      type: "circle",
      source: "watershed-outlet-data",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 4, 12, 7],
        "circle-color": "#d73027",
        "circle-stroke-color": "#fff",
        "circle-stroke-width": 1.6,
      },
    });
    this.sourcesReady = true;
    this.setBasemap(this.ui.basemap);
    this.setLayer(this.ui.activeLayer);
    this.setOpacity(this.ui.opacity);
    this.setView(this.ui.view);
    this.setOverlayVisibility({
      boundary: document.getElementById("show-boundary").checked,
      outlet: document.getElementById("show-outlet").checked,
    });
    this.fitWatershed();
    document.getElementById("loading-indicator").hidden = true;
  }

  #showError(message) {
    const element = document.getElementById("error-message");
    element.textContent = `Map data could not be loaded: ${message}`;
    element.hidden = false;
  }

  #clearError() {
    document.getElementById("error-message").hidden = true;
  }

  #addBaseMap(basemap) {
    if (this.currentBasemap) {
      const old = BASEMAPS[this.currentBasemap];
      if (this.map.getLayer(old.layer)) this.map.removeLayer(old.layer);
      if (this.map.getSource(old.source)) this.map.removeSource(old.source);
      this.currentBasemap = null;
    }
    if (basemap === "none") return;
    const definition = BASEMAPS[basemap];
    if (!definition) throw new Error(`Unknown basemap: ${basemap}`);
    this.map.addSource(definition.source, definition.sourceDefinition);
    const before = firstOverlayLayer(this.map);
    this.map.addLayer({
      id: definition.layer,
      type: "raster",
      source: definition.source,
      paint: { "raster-opacity": 1, "raster-resampling": "nearest" },
    }, before);
    this.currentBasemap = basemap;
  }

  setBasemap(basemap) {
    if (!this.sourcesReady) return;
    this.#addBaseMap(basemap);
    this.#clearError();
  }

  setLayer(layer) {
    if (!this.sourcesReady) return;
    const requestId = ++this.layerRequestId;
    this.#clearError();
    if (this.map.getLayer(RASTER_LAYER_ID)) this.map.removeLayer(RASTER_LAYER_ID);
    if (this.map.getSource("active-value-source")) this.map.removeSource("active-value-source");
    if (this.map.getLayer(STREAM_LINE_ID)) this.map.removeLayer(STREAM_LINE_ID);
    if (this.map.getLayer(STREAM_OUTLINE_ID)) this.map.removeLayer(STREAM_OUTLINE_ID);

    const rasterName = layer.isVectorOverlay ? "hillshade" : layer.name;
    const rasterLayer = this.manifest.layers.find((item) => item.name === rasterName);
    this.map.addSource("active-value-source", {
      type: "raster",
      tiles: [`watershed://${rasterName}/{z}/{x}/{y}`],
      minzoom: rasterLayer.minzoom,
      maxzoom: rasterLayer.maxzoom,
      bounds: rasterLayer.bounds,
      tileSize: 256,
      scheme: "xyz",
    });
    this.map.addLayer({
      id: RASTER_LAYER_ID,
      type: "raster",
      source: "active-value-source",
      paint: {
        "raster-opacity": this.ui.opacity,
        "raster-resampling": "nearest",
      },
    }, BOUNDARY_LAYER_ID);

    if (layer.isVectorOverlay) {
      this.map.addLayer({
        id: STREAM_OUTLINE_ID,
        type: "line",
        source: "watershed-stream-data",
        paint: {
          "line-color": "#fff",
          "line-width": [
            "interpolate", ["linear"], ["get", "strahler_order"],
            1, 1.3, 5, 4.7,
          ],
          "line-opacity": 0.9,
        },
      }, BOUNDARY_LAYER_ID);
      this.map.addLayer({
        id: STREAM_LINE_ID,
        type: "line",
        source: "watershed-stream-data",
        paint: {
          "line-color": "#1464a5",
          "line-width": [
            "interpolate", ["linear"], ["get", "strahler_order"],
            1, 0.55, 5, 2.8,
          ],
          "line-opacity": 1,
        },
      }, BOUNDARY_LAYER_ID);
    }
    this.mapLayerTitle(layer.title);
    this.map.once("idle", () => {
      if (requestId === this.layerRequestId) this.#clearError();
    });
  }

  mapLayerTitle(title) {
    document.getElementById("map-layer-title").textContent = title;
  }

  async #readClick(lngLat) {
    const requestId = ++this.clickRequestId;
    const layerName = this.ui.activeLayer.name;
    try {
      await this.ui.showValue(lngLat, (layer, lng, lat) => this.tileStore.getValue(layer.name, lng, lat));
      if (requestId !== this.clickRequestId || layerName !== this.ui.activeLayer.name) return;
    } catch (error) {
      if (requestId !== this.clickRequestId) return;
      this.#showError(error.message);
    }
  }

  setOpacity(opacity) {
    if (this.map.getLayer(RASTER_LAYER_ID)) {
      this.map.setPaintProperty(RASTER_LAYER_ID, "raster-opacity", opacity);
    }
    if (this.map.getLayer(STREAM_LINE_ID)) {
      this.map.setPaintProperty(STREAM_LINE_ID, "line-opacity", opacity);
      this.map.setPaintProperty(STREAM_OUTLINE_ID, "line-opacity", opacity * 0.9);
    }
  }

  setView(view) {
    if (!this.sourcesReady) return;
    if (view === "3d") {
      const exaggeration = Number(document.getElementById("exaggeration-slider").value);
      this.map.setTerrain({ source: "aws-terrarium", exaggeration });
      this.map.easeTo({ pitch: 62, duration: 500 });
    } else {
      this.map.setTerrain(null);
      this.map.easeTo({ pitch: 0, bearing: 0, duration: 400 });
    }
  }

  setExaggeration(value) {
    if (this.ui.view === "3d" && this.map.getTerrain()) {
      this.map.setTerrain({ source: "aws-terrarium", exaggeration: value });
    }
  }

  setOutsideDim(enabled) {
    if (this.map.getLayer(OUTSIDE_LAYER_ID)) {
      this.map.setLayoutProperty(
        OUTSIDE_LAYER_ID,
        "visibility",
        enabled ? "visible" : "none",
      );
    }
  }

  setOverlayVisibility({ boundary, outlet }) {
    if (this.map.getLayer(BOUNDARY_LAYER_ID)) {
      this.map.setLayoutProperty(
        BOUNDARY_LAYER_ID,
        "visibility",
        boundary ? "visible" : "none",
      );
    }
    if (this.map.getLayer(OUTLET_LAYER_ID)) {
      this.map.setLayoutProperty(
        OUTLET_LAYER_ID,
        "visibility",
        outlet ? "visible" : "none",
      );
    }
  }

  fitWatershed() {
    this.map.fitBounds(
      [
        [this.manifest.bounds[0], this.manifest.bounds[1]],
        [this.manifest.bounds[2], this.manifest.bounds[3]],
      ],
      { padding: { top: 76, right: 40, bottom: 76, left: 40 }, maxZoom: 8.5, duration: 600 },
    );
  }
}
