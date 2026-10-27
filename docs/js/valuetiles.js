import { PMTiles } from "https://cdn.jsdelivr.net/npm/pmtiles@4.5.0/+esm";

const TILE_SIZE = 256;
const CACHE_LIMIT = 128;
const MAX_LATITUDE = 85.0511287798066;

export class ValueTileStore {
  constructor(manifest, dataBaseUrl) {
    this.layers = new Map(manifest.layers.map((layer) => [layer.name, layer]));
    this.dataBaseUrl = dataBaseUrl;
    this.archives = new Map();
    this.decodedTiles = new Map();
    this.lutImages = new Map();
    this.emptyPng = null;
  }

  #archive(layerName) {
    let archive = this.archives.get(layerName);
    if (!archive) {
      const layer = this.layers.get(layerName);
      if (!layer) throw new Error(`Unknown value tile layer: ${layerName}`);
      archive = new PMTiles(new URL(layer.tile, this.dataBaseUrl).href);
      this.archives.set(layerName, archive);
    }
    return archive;
  }

  async #decodeImage(data) {
    const bitmap = await createImageBitmap(
      new Blob([data], { type: "image/png" }),
      { premultiplyAlpha: "none", colorSpaceConversion: "none" },
    );
    try {
      const canvas = typeof OffscreenCanvas === "function"
        ? new OffscreenCanvas(TILE_SIZE, TILE_SIZE)
        : Object.assign(document.createElement("canvas"), {
          width: TILE_SIZE,
          height: TILE_SIZE,
        });
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) throw new Error("Unable to create a tile decoding canvas.");
      context.drawImage(bitmap, 0, 0);
      return context.getImageData(0, 0, TILE_SIZE, TILE_SIZE).data;
    } finally {
      bitmap.close();
    }
  }

  #remember(key, pixels) {
    this.decodedTiles.delete(key);
    this.decodedTiles.set(key, pixels);
    if (this.decodedTiles.size > CACHE_LIMIT) {
      this.decodedTiles.delete(this.decodedTiles.keys().next().value);
    }
  }

  async getDecodedTile(layerName, zoom, x, y) {
    const key = `${layerName}/${zoom}/${x}/${y}`;
    if (this.decodedTiles.has(key)) {
      const cached = this.decodedTiles.get(key);
      this.decodedTiles.delete(key);
      this.decodedTiles.set(key, cached);
      return cached;
    }
    const result = await this.#archive(layerName).getZxy(zoom, x, y);
    if (!result?.data) {
      this.#remember(key, null);
      return null;
    }
    const pixels = await this.#decodeImage(result.data);
    this.#remember(key, pixels);
    return pixels;
  }

  #valueFromPixel(layer, pixels, pixelX, pixelY) {
    const offset = (pixelY * TILE_SIZE + pixelX) * 4;
    const alpha = pixels[offset + 3];
    if (alpha !== 255) return null;

    // Contract: Python writes high byte in R, low byte in G, B=0, and binary alpha.
    const encoded = pixels[offset] * 256 + pixels[offset + 1];
    const encoding = layer.encoding;
    const transformed = encoding.min
      + (encoded / 65535) * (encoding.max - encoding.min);
    const value = layer.transform === "log10" ? 10 ** transformed : transformed;
    return Number.isFinite(value) ? value : null;
  }

  #tileAt(lng, lat, zoom) {
    const clampedLat = Math.max(-MAX_LATITUDE, Math.min(MAX_LATITUDE, lat));
    const count = 2 ** zoom;
    const xFloat = ((lng + 180) / 360) * count;
    const latitudeRadians = clampedLat * Math.PI / 180;
    const yFloat = (
      1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI
    ) * 0.5 * count;
    const x = Math.max(0, Math.min(count - 1, Math.floor(xFloat)));
    const y = Math.max(0, Math.min(count - 1, Math.floor(yFloat)));
    return {
      x,
      y,
      pixelX: Math.max(0, Math.min(255, Math.floor((xFloat - x) * TILE_SIZE))),
      pixelY: Math.max(0, Math.min(255, Math.floor((yFloat - y) * TILE_SIZE))),
    };
  }

  async getValue(layerName, lng, lat) {
    const layer = this.layers.get(layerName);
    if (!layer) throw new Error(`Unknown layer: ${layerName}`);
    const zoom = layer.maxzoom;
    const tile = this.#tileAt(lng, lat, zoom);
    const pixels = await this.getDecodedTile(layerName, zoom, tile.x, tile.y);
    if (!pixels) return null;
    return this.#valueFromPixel(layer, pixels, tile.pixelX, tile.pixelY);
  }

  async #transparentTile() {
    if (!this.emptyPng) {
      const canvas = typeof OffscreenCanvas === "function"
        ? new OffscreenCanvas(TILE_SIZE, TILE_SIZE)
        : Object.assign(document.createElement("canvas"), {
          width: TILE_SIZE,
          height: TILE_SIZE,
        });
      if (!canvas.getContext("2d")) {
        throw new Error("Unable to create a transparent tile canvas.");
      }
      if (typeof canvas.convertToBlob === "function") {
        this.emptyPng = canvas.convertToBlob({ type: "image/png" })
          .then((blob) => blob.arrayBuffer());
      } else {
        this.emptyPng = new Promise((resolve, reject) => {
          canvas.toBlob((blob) => {
            if (blob) resolve(blob.arrayBuffer());
            else reject(new Error("Unable to create a transparent map tile."));
          }, "image/png");
        }).then((arrayBuffer) => arrayBuffer);
      }
    }
    return this.emptyPng;
  }

  async renderTile(layerName, zoom, x, y) {
    const layer = this.layers.get(layerName);
    if (!layer) throw new Error(`Unknown layer in tile URL: ${layerName}`);
    const pixels = await this.getDecodedTile(layerName, zoom, x, y);
    if (!pixels) return this.#transparentTile();
    const lutKey = layerName;
    let lut = this.lutImages.get(lutKey);
    if (!lut) {
      lut = new Uint8ClampedArray(layer.lut.flat());
      this.lutImages.set(lutKey, lut);
    }

    const rgba = new Uint8ClampedArray(TILE_SIZE * TILE_SIZE * 4);
    const encoding = layer.encoding;
    const display = layer.display;
    const encodingSpan = encoding.max - encoding.min;
    for (let sourceOffset = 0; sourceOffset < pixels.length; sourceOffset += 4) {
      const alpha = pixels[sourceOffset + 3];
      if (alpha !== 255) continue;
      const encoded = pixels[sourceOffset] * 256 + pixels[sourceOffset + 1];
      const value = encoding.min + (encoded / 65535) * encodingSpan;
      const normalized = display.max === display.min
        ? 0
        : (value - display.min) / (display.max - display.min);
      const lutIndex = Math.max(0, Math.min(255, Math.round(normalized * 255)));
      const destinationOffset = sourceOffset;
      const lutOffset = lutIndex * 4;
      rgba[destinationOffset] = lut[lutOffset];
      rgba[destinationOffset + 1] = lut[lutOffset + 1];
      rgba[destinationOffset + 2] = lut[lutOffset + 2];
      rgba[destinationOffset + 3] = 255;
    }
    const imageData = new ImageData(rgba, TILE_SIZE, TILE_SIZE);
    const canvas = typeof OffscreenCanvas === "function"
      ? new OffscreenCanvas(TILE_SIZE, TILE_SIZE)
      : Object.assign(document.createElement("canvas"), {
        width: TILE_SIZE,
        height: TILE_SIZE,
      });
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Unable to create a coloured tile canvas.");
    context.putImageData(imageData, 0, 0);
    const blob = typeof canvas.convertToBlob === "function"
      ? await canvas.convertToBlob({ type: "image/png" })
      : await new Promise((resolve, reject) => {
        canvas.toBlob((result) => {
          if (result) resolve(result);
          else reject(new Error("Unable to encode a coloured map tile."));
        }, "image/png");
      });
    return blob.arrayBuffer();
  }
}

export function registerValueTileProtocol(maplibregl, store) {
  maplibregl.addProtocol("watershed", async (params, abortController) => {
    const match = /^watershed:\/\/([a-z0-9_]+)\/(\d+)\/(\d+)\/(\d+)$/.exec(params.url);
    if (!match) throw new Error(`Invalid watershed tile URL: ${params.url}`);
    const [, layerName, z, x, y] = match;
    if (abortController.signal.aborted) {
      throw new DOMException("The tile request was aborted.", "AbortError");
    }
    const data = await store.renderTile(layerName, Number(z), Number(x), Number(y));
    if (!data) throw new Error(`No tile data for ${layerName} z${z}/${x}/${y}.`);
    return { data };
  });
}

export function formatLayerValue(layer, value) {
  if (value === null || !Number.isFinite(value)) return "No data";
  const formatted = layer.units === "degrees"
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)
    : new Intl.NumberFormat(undefined, { maximumSignificantDigits: 5 }).format(value);
  if (layer.name === "aspect") {
    const directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    const direction = directions[Math.round(((value % 360) + 360) % 360 / 45) % 8];
    return `${formatted}° ${direction}`;
  }
  if (layer.transform === "log10") {
    const rawUnits = layer.units === "raw index" ? "" : ` ${layer.units}`;
    return `${formatted}${rawUnits} (raw value; displayed with log₁₀ stretch)`;
  }
  if (layer.name === "stream_network_25km2") {
    return `Strahler order ${Math.round(value)}`;
  }
  return layer.units ? `${formatted} ${layer.units}` : formatted;
}
