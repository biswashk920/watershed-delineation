"""Export value-preserving watershed assets for the static web viewer."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib
import mercantile
import numpy as np
import rasterio
from PIL import Image, ImageDraw
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import transform_bounds
from rasterio.windows import Window
from scipy import ndimage
from shapely.geometry import MultiPolygon, Polygon

MIN_ZOOM = 6
MAX_ZOOM = 11
TILE_SIZE = 256
BLOCK_LONG_SIDE = 1000
TEXTURE_LONG_SIDE = 2048
PLOT_PREVIEW_LONG_SIDE = 2400
STREAM_COLOR = (20, 100, 165, 255)


@dataclass(frozen=True)
class LayerSpec:
    name: str
    title: str
    group: str
    description: str
    units: str
    cmap: str
    transform: str = "identity"
    percentile: tuple[float, float] = (2.0, 98.0)
    symmetric: bool = False
    display_limits: tuple[float, float] | None = None
    nearest: bool = False
    is_stream: bool = False


LAYERS = (
    LayerSpec(
        "elevation", "Elevation", "Terrain",
        "Elevation is the land-surface height above the raster's vertical datum. It provides the basic context for interpreting relief, drainage, and where water is likely to move across the basin.",
        "m", "terrain",
    ),
    LayerSpec(
        "slope_deg", "Slope", "Terrain",
        "Slope measures how steep the land is, in degrees. Steeper ground can encourage faster runoff and erosion, while gentler slopes are more likely to slow water and collect sediment.",
        "degrees", "magma",
    ),
    LayerSpec(
        "aspect", "Aspect", "Terrain",
        "Aspect is the compass direction that a slope faces. It helps compare sun exposure, vegetation and snow conditions, and other processes that vary with slope orientation.",
        "degrees", "hsv", display_limits=(0.0, 360.0), nearest=True,
    ),
    LayerSpec(
        "profile_curvature", "Profile curvature", "Terrain",
        "Profile curvature describes whether the land surface bends along the direction of slope. It helps identify places where flow may accelerate or decelerate and where erosion or deposition may be more likely. Curvature sign conventions can vary between tools.",
        "", "RdBu_r", symmetric=True,
    ),
    LayerSpec(
        "plan_curvature", "Plan curvature", "Terrain",
        "Plan curvature describes surface bending across the slope. It helps reveal converging terrain that gathers flow and diverging terrain that spreads it out, which is useful for understanding runoff pathways and erosion patterns.",
        "", "RdBu_r", symmetric=True,
    ),
    LayerSpec(
        "hillshade", "Hillshade", "Terrain",
        "Hillshade simulates illumination across the terrain to make ridges, valleys, and landforms easier to see. It is a visual aid rather than a direct measurement of hydrologic conditions.",
        "intensity", "gray",
    ),
    LayerSpec(
        "tpi_300m", "TPI (300 m radius)", "Terrain",
        "TPI compares each elevation cell with the average elevation in its surrounding neighbourhood. Positive values mark locally higher ground and negative values mark locally lower ground. The 300 m neighbourhood emphasizes smaller landforms such as local ridges and hollows.",
        "m", "RdBu_r", symmetric=True,
    ),
    LayerSpec(
        "tpi_1km", "TPI (1 km radius)", "Terrain",
        "This TPI uses a broader 1 km neighbourhood to show larger terrain positions. Comparing it with the 300 m map helps distinguish local features from broader valleys, slopes, and uplands.",
        "m", "RdBu_r", symmetric=True,
    ),
    LayerSpec(
        "tri_3x3", "TRI (3x3)", "Terrain",
        "The 3-by-3 Terrain Ruggedness Index (TRI) summarizes elevation differences between a cell and its immediate neighbours. Higher values indicate rougher, more uneven terrain, which can affect access, runoff, and erosion.",
        "m", "viridis",
    ),
    LayerSpec(
        "relative_relief_1km", "Relative relief (1 km radius)", "Terrain",
        "Relative relief here is the local elevation range (maximum minus minimum) in a 1 km neighbourhood. It highlights how much terrain variation occurs nearby and helps distinguish comparatively smooth areas from rugged ones.",
        "m", "terrain",
    ),
    LayerSpec(
        "flow_accumulation_km2", "Flow accumulation", "Hydrology",
        "Flow accumulation is shown as the upstream contributing area in square kilometres. Larger values identify places receiving runoff from more of the basin and help outline the drainage network.",
        "km^2", "plasma", transform="log10",
    ),
    LayerSpec(
        "stream_network_25km2", "Stream network (>25 km2) over hillshade", "Hydrology",
        "The stream network is extracted where contributing area exceeds 25 km². Strahler order describes the hierarchy of tributaries: when two streams of the same order meet, the downstream order increases. The map uses wider lines for higher-order streams. The corresponding vector network is saved as a GeoPackage in `Output Data/feature_vectors/`.",
        "Strahler order", "stream-blue", display_limits=(1.0, 5.0),
        nearest=True, is_stream=True,
    ),
    LayerSpec(
        "distance_to_river", "Distance to river", "Hydrology",
        "This map gives the straight-line distance from each watershed cell to the nearest mapped stream. It can help identify areas close to channels, although it does not represent the route water travels over the land surface.",
        "m", "Blues",
    ),
    LayerSpec(
        "hand", "Height Above Nearest Drainage (HAND)", "Hydrology",
        "HAND measures the elevation of each cell relative to its nearest drainage channel. Lower values indicate land closer in height to the drainage network, which can help screen for potentially flood-connected areas; it is not, by itself, a flood prediction.",
        "m", "viridis",
    ),
    LayerSpec(
        "downstream_flow_length", "Downstream flow length to outlet", "Hydrology",
        "This layer estimates the distance along the drainage path from a cell toward the watershed outlet. It helps compare how far locations are from the outlet along the flow network, rather than by straight-line distance.",
        "m", "cividis",
    ),
    LayerSpec(
        "dinf_specific_catchment_area", "D-infinity specific catchment area", "Hydrology",
        "Specific catchment area represents the upslope contributing area per unit contour width. The D-infinity method distributes flow between downslope directions, giving a more continuous picture of flow concentration than assigning all flow to just one neighbour.",
        "m", "plasma", transform="log10",
    ),
    LayerSpec(
        "twi", "Topographic Wetness Index (TWI)", "Hydrology",
        "TWI combines contributing area and slope to indicate where terrain may favour the accumulation of water. Higher values often occur in convergent, lower-slope areas. It is a terrain-based wetness indicator, not a direct soil-moisture measurement.",
        "ln", "Spectral",
    ),
    LayerSpec(
        "spi", "Stream Power Index (SPI)", "Hydrology",
        "SPI combines contributing area with slope to represent the relative potential for flowing water to exert erosive force. Higher values flag locations where concentrated flow and steep terrain coincide.",
        "raw index", "magma", transform="log10",
    ),
    LayerSpec(
        "sti", "Sediment Transport Index (STI)", "Hydrology",
        "STI is a terrain-based indicator of relative sediment transport potential, combining information about contributing area and slope. It can help identify areas for closer erosion assessment, but does not measure sediment movement directly.",
        "raw index", "YlGnBu", transform="log10",
    ),
)


def _apply_transform(values: np.ndarray, transform: str) -> np.ndarray:
    if transform == "identity":
        return values
    if transform == "log10":
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.log10(values)
    raise ValueError(f"Unsupported display transform: {transform}")


def _valid_values(values: np.ndarray, mask: np.ndarray, spec: LayerSpec) -> np.ndarray:
    valid = mask & np.isfinite(values)
    if spec.transform == "log10":
        valid &= values > 0
    return _apply_transform(values[valid], spec.transform)


def _plot_range(dataset: rasterio.DatasetReader, spec: LayerSpec) -> tuple[float, float]:
    scale = min(1.0, PLOT_PREVIEW_LONG_SIDE / max(dataset.width, dataset.height))
    out_height = max(1, round(dataset.height * scale))
    out_width = max(1, round(dataset.width * scale))
    method = Resampling.nearest if spec.nearest else Resampling.average
    values = dataset.read(1, out_shape=(out_height, out_width), resampling=method)
    mask = dataset.read_masks(
        1, out_shape=(out_height, out_width), resampling=Resampling.nearest
    ) > 0
    plotted = _valid_values(values, mask, spec)
    if plotted.size == 0:
        raise ValueError(f"{spec.name} has no valid values for its display stretch.")
    if spec.display_limits is not None:
        return spec.display_limits
    low, high = (float(value) for value in np.percentile(plotted, spec.percentile))
    if spec.symmetric:
        high = max(abs(low), abs(high))
        low = -high
    if low == high:
        high = low + 1.0
    return low, high


def _true_range(dataset: rasterio.DatasetReader, spec: LayerSpec) -> tuple[float, float]:
    low = math.inf
    high = -math.inf
    for row in range(0, dataset.height, 512):
        height = min(512, dataset.height - row)
        window = Window(0, row, dataset.width, height)
        values = dataset.read(1, window=window)
        mask = dataset.read_masks(1, window=window) > 0
        valid = mask & np.isfinite(values)
        if spec.transform == "log10":
            valid &= values > 0
        if not valid.any():
            continue
        transformed = _apply_transform(values[valid], spec.transform)
        low = min(low, float(np.min(transformed)))
        high = max(high, float(np.max(transformed)))
    if not math.isfinite(low) or not math.isfinite(high):
        raise ValueError(f"{spec.name} has no finite values for its encoding range.")
    return low, high


def _lut(spec: LayerSpec) -> list[list[int]]:
    if spec.is_stream:
        return [list(STREAM_COLOR)] * 256
    cmap = matplotlib.colormaps[spec.cmap]
    return cmap(np.linspace(0, 1, 256), bytes=True).tolist()


def _to_geojson(gdf: gpd.GeoDataFrame, path: Path, precision: int = 5) -> None:
    payload = json.loads(gdf.to_json(drop_id=True))

    def round_coordinates(value: Any) -> Any:
        if isinstance(value, list):
            if value and isinstance(value[0], (float, int)):
                return [round(float(component), precision) for component in value]
            return [round_coordinates(item) for item in value]
        return value

    for feature in payload["features"]:
        feature["geometry"]["coordinates"] = round_coordinates(
            feature["geometry"]["coordinates"]
        )
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _tile_ids(bounds: tuple[float, float, float, float], min_zoom: int,
              max_zoom: int) -> list[tuple[int, int, int, int]]:
    west, south, east, north = bounds
    tiles: list[tuple[int, int, int, int]] = []
    for zoom in range(min_zoom, max_zoom + 1):
        for tile in mercantile.tiles(west, south, east, north, [zoom]):
            tiles.append((zxy_to_tileid(tile.z, tile.x, tile.y), tile.z, tile.x, tile.y))
    tiles.sort()
    return tiles


def _png_bytes(rgba: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _encode_value_tile(
    source: rasterio.DatasetReader,
    tile: tuple[int, int, int, int],
    spec: LayerSpec,
    encoding_range: tuple[float, float],
) -> bytes | None:
    _, zoom, x, y = tile
    bounds = mercantile.xy_bounds(x, y, zoom)
    tile_transform = Affine(
        (bounds.right - bounds.left) / TILE_SIZE, 0, bounds.left,
        0, -(bounds.top - bounds.bottom) / TILE_SIZE, bounds.top,
    )
    values = np.full((TILE_SIZE, TILE_SIZE), np.nan, dtype=np.float32)
    reproject_resampling = Resampling.nearest if spec.nearest else Resampling.average
    rasterio.warp.reproject(
        source=rasterio.band(source, 1),
        destination=values,
        src_transform=source.transform,
        src_crs=source.crs,
        src_nodata=source.nodata,
        dst_transform=tile_transform,
        dst_crs="EPSG:3857",
        dst_nodata=np.nan,
        resampling=reproject_resampling,
        init_dest_nodata=True,
    )
    valid = np.isfinite(values)
    if spec.transform == "log10":
        valid &= values > 0
    if not valid.any():
        return None

    low, high = encoding_range
    if low == high:
        encoded = np.zeros(values.shape, dtype=np.uint16)
    else:
        transformed = _apply_transform(values[valid], spec.transform)
        encoded = np.zeros(values.shape, dtype=np.uint16)
        # Contract: R is the 16-bit value's high byte, G its low byte, B is 0,
        # and alpha is exactly 255 for valid cells or 0 for nodata.
        encoded[valid] = np.rint(
            np.clip((transformed - low) / (high - low), 0.0, 1.0) * 65535
        ).astype(np.uint16)

    rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
    rgba[:, :, 0] = (encoded >> 8).astype(np.uint8)
    rgba[:, :, 1] = (encoded & 255).astype(np.uint8)
    rgba[:, :, 3] = valid.astype(np.uint8) * 255
    return _png_bytes(rgba)


def _write_pmtiles(
    source_path: Path,
    destination: Path,
    spec: LayerSpec,
    encoding_range: tuple[float, float],
    bounds: tuple[float, float, float, float],
    min_zoom: int,
    max_zoom: int,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tiles = _tile_ids(bounds, min_zoom, max_zoom)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    min_lon, min_lat, max_lon, max_lat = bounds
    header = {
        "version": 3,
        "root_offset": 0, "root_length": 0,
        "metadata_offset": 0, "metadata_length": 0,
        "leaf_directory_offset": 0, "leaf_directory_length": 0,
        "tile_data_offset": 0, "tile_data_length": 0,
        "addressed_tiles_count": 0, "tile_entries_count": 0, "tile_contents_count": 0,
        "clustered": True,
        "internal_compression": Compression.GZIP,
        "tile_compression": Compression.NONE,
        "tile_type": TileType.PNG,
        "min_zoom": min_zoom, "max_zoom": max_zoom,
        "min_lon_e7": round(min_lon * 1e7), "min_lat_e7": round(min_lat * 1e7),
        "max_lon_e7": round(max_lon * 1e7), "max_lat_e7": round(max_lat * 1e7),
        "center_zoom": min_zoom,
        "center_lon_e7": round((min_lon + max_lon) * 0.5 * 1e7),
        "center_lat_e7": round((min_lat + max_lat) * 0.5 * 1e7),
    }
    metadata = {
        "name": spec.title,
        "format": "png",
        "type": "overlay",
        "version": "1",
        "description": spec.description,
        "minzoom": min_zoom,
        "maxzoom": max_zoom,
        "bounds": ",".join(str(value) for value in bounds),
    }
    try:
        with temporary_path.open("wb") as file:
            writer = Writer(file)
            with rasterio.open(source_path) as source:
                written = 0
                for tile in tiles:
                    data = _encode_value_tile(source, tile, spec, encoding_range)
                    if data is not None:
                        writer.write_tile(tile[0], data)
                        written += 1
            if written == 0:
                raise ValueError(f"{spec.name} produced no tiles.")
            writer.finalize(header, metadata)
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination.stat().st_size


def _block_shape(width: int, height: int, long_side: int) -> tuple[int, int]:
    scale = long_side / max(width, height)
    return max(1, round(height * scale)), max(1, round(width * scale))


def _write_block_height(dem_path: Path, block_dir: Path) -> dict[str, Any]:
    block_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dem_path) as source:
        out_height, out_width = _block_shape(
            source.width, source.height, BLOCK_LONG_SIDE
        )
        values = source.read(
            1, out_shape=(out_height, out_width), resampling=Resampling.average
        )
        valid = source.read_masks(
            1, out_shape=(out_height, out_width), resampling=Resampling.nearest
        ) > 0
        valid &= np.isfinite(values)
        if not valid.any():
            raise ValueError("The watershed DEM has no valid heights.")
        z_min = float(np.min(values[valid]))
        z_max = float(np.max(values[valid]))
        filled = values.copy()
        nearest = ndimage.distance_transform_edt(~valid, return_distances=False,
                                                 return_indices=True)
        filled[~valid] = values[tuple(index[~valid] for index in nearest)]
        if z_max == z_min:
            quantized = np.zeros(values.shape, dtype="<u2")
        else:
            quantized = np.rint(
                np.clip((filled - z_min) / (z_max - z_min), 0.0, 1.0) * 65535
            ).astype("<u2")
        quantized.tofile(block_dir / "height.bin")

        transform = source.transform * Affine.scale(
            source.width / out_width, source.height / out_height
        )
        metadata = {
            "width": out_width,
            "height": out_height,
            "origin": [float(transform.c), float(transform.f)],
            "cell_size_m": {
                "x": float(transform.a),
                "y": abs(float(transform.e)),
            },
            "crs": source.crs.to_string(),
            "z_min": z_min,
            "z_max": z_max,
            "base_z": z_min - max((z_max - z_min) * 0.12, 30.0),
            "encoding": "uint16 little-endian, linear z_min..z_max",
        }
    (block_dir / "height.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def _texture_shape(width: int, height: int) -> tuple[int, int]:
    return _block_shape(width, height, TEXTURE_LONG_SIDE)


def _write_layer_texture(
    source_path: Path,
    destination: Path,
    spec: LayerSpec,
    display_range: tuple[float, float],
    lut: list[list[int]],
) -> None:
    with rasterio.open(source_path) as source:
        out_height, out_width = _texture_shape(source.width, source.height)
        method = Resampling.nearest if spec.nearest else Resampling.average
        values = source.read(1, out_shape=(out_height, out_width), resampling=method)
        valid = source.read_masks(
            1, out_shape=(out_height, out_width), resampling=Resampling.nearest
        ) > 0
        valid &= np.isfinite(values)
    if spec.transform == "log10":
        valid &= values > 0
    transformed = np.zeros(values.shape, dtype=np.float32)
    transformed[valid] = _apply_transform(values[valid], spec.transform)
    low, high = display_range
    if high == low:
        indices = np.zeros(values.shape, dtype=np.uint8)
    else:
        indices = np.zeros(values.shape, dtype=np.uint8)
        indices[valid] = np.rint(
            np.clip((transformed[valid] - low) / (high - low), 0.0, 1.0) * 255
        ).astype(np.uint8)
    color_lut = np.asarray(lut, dtype=np.uint8)
    rgba = color_lut[indices]
    rgba[~valid] = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(destination, format="PNG", optimize=True)


def _boundary_rings(geometry: Any) -> list[list[tuple[float, float]]]:
    polygons = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    rings: list[list[tuple[float, float]]] = []
    for polygon in polygons:
        if not isinstance(polygon, Polygon):
            continue
        rings.append([(float(x), float(y)) for x, y in polygon.exterior.coords])
        rings.extend(
            [(float(x), float(y)) for x, y in ring.coords]
            for ring in polygon.interiors
        )
    return rings


def _sample_dem_heights(
    dem_path: Path, coordinates: list[tuple[float, float]]
) -> list[float]:
    heights: list[float] = []
    with rasterio.open(dem_path) as source:
        samples = list(source.sample(coordinates))
        for (x, y), sample in zip(coordinates, samples, strict=True):
            row, col = source.index(x, y)
            value = float(sample[0])
            if (
                np.isfinite(value)
                and (source.nodata is None or value != source.nodata)
                and source.read_masks(1, window=Window(col, row, 1, 1))[0, 0] > 0
            ):
                heights.append(float(value))
                continue
            row_start = max(0, min(source.height - 1, row - 1))
            col_start = max(0, min(source.width - 1, col - 1))
            window = Window(
                col_start,
                row_start,
                min(3, source.width - col_start),
                min(3, source.height - row_start),
            )
            local = source.read(1, window=window, masked=True)
            valid = (
                (source.read_masks(1, window=window) > 0)
                & np.isfinite(local.data)
            )
            if source.nodata is not None:
                valid &= local.data != source.nodata
            candidate_rows, candidate_cols = np.nonzero(valid)
            if candidate_rows.size == 0:
                raise ValueError(f"No valid DEM height near boundary coordinate {(x, y)}.")
            candidate_rows += row_start
            candidate_cols += col_start
            candidate_x, candidate_y = rasterio.transform.xy(
                source.transform, candidate_rows, candidate_cols, offset="center"
            )
            distances = (np.asarray(candidate_x) - x) ** 2 + (
                np.asarray(candidate_y) - y
            ) ** 2
            nearest = int(np.argmin(distances))
            heights.append(float(local.data[
                candidate_rows[nearest] - row_start,
                candidate_cols[nearest] - col_start,
            ]))
    return heights


def _write_block_boundary(
    boundary_path: Path, dem_path: Path, block_dir: Path
) -> None:
    boundary = gpd.read_file(boundary_path)
    with rasterio.open(dem_path) as dem:
        boundary = boundary.to_crs(dem.crs)
        crs = dem.crs.to_string()
    geometry = boundary.geometry.union_all().simplify(15.0, preserve_topology=True)
    ring_coordinates = _boundary_rings(geometry)
    rings: list[list[list[float]]] = []
    for ring in ring_coordinates:
        heights = _sample_dem_heights(dem_path, ring)
        rings.append([
            [round(x, 2), round(y, 2), round(z, 2)]
            for (x, y), z in zip(ring, heights, strict=True)
        ])
    payload = {"crs": crs, "rings": rings}
    (block_dir / "boundary.json").write_text(
        json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def _write_stream_texture(
    stream_path: Path, dem_path: Path, block_dir: Path
) -> None:
    with rasterio.open(dem_path) as source:
        out_height, out_width = _texture_shape(source.width, source.height)
        transform = source.transform * Affine.scale(
            source.width / out_width, source.height / out_height
        )
        left, top = float(transform.c), float(transform.f)
        pixel_x, pixel_y = float(transform.a), abs(float(transform.e))
        width, height = out_width, out_height
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    streams = gpd.read_file(stream_path)
    with rasterio.open(dem_path) as dem:
        streams = streams.to_crs(dem.crs)
    order_field = "strahler_order"
    if order_field not in streams.columns:
        raise ValueError(f"Stream file does not contain {order_field!r}.")
    for feature in streams.itertuples():
        geometry = feature.geometry.simplify(5.0, preserve_topology=False)
        parts = list(geometry.geoms) if geometry.geom_type == "MultiLineString" else [geometry]
        order = int(getattr(feature, order_field))
        line_width = max(1, round(order * 0.7))
        for line in parts:
            pixels = [
                (round((x - left) / pixel_x), round((top - y) / pixel_y))
                for x, y in line.coords
            ]
            if len(pixels) > 1:
                draw.line(pixels, fill=STREAM_COLOR, width=line_width)
    image.save(block_dir / "streams.png", format="PNG", optimize=True)


def _export_geojson(
    project_root: Path, data_dir: Path, boundary: gpd.GeoDataFrame
) -> tuple[Path, Path, Path]:
    boundary_4326 = boundary.to_crs("EPSG:4326")
    boundary_path = data_dir / "boundary.geojson"
    _to_geojson(boundary_4326, boundary_path)

    outlet = gpd.read_file(project_root / "Preprocessing" / "outlet_snapped.shp")
    outlet_path = data_dir / "outlet.geojson"
    _to_geojson(outlet.to_crs("EPSG:4326"), outlet_path)

    streams_path = project_root / "Output Data" / "feature_vectors" / "stream_network_25km2.gpkg"
    streams = gpd.read_file(streams_path)
    if "strahler_order" not in streams.columns:
        raise ValueError("Stream GeoPackage is missing the strahler_order field.")
    if streams["strahler_order"].isna().any():
        raise ValueError("Stream GeoPackage has a missing Strahler order.")
    streams = streams.to_crs(boundary.crs).copy()
    streams["strahler_order"] = streams["strahler_order"].astype(int)
    streams.geometry = streams.geometry.simplify(10.0, preserve_topology=False)
    streams = streams.to_crs("EPSG:4326")
    streams_path_out = data_dir / "streams.geojson"
    _to_geojson(streams, streams_path_out)
    return boundary_path, outlet_path, streams_path_out


def export_web_data(
    project_root: str | Path | None = None,
    min_zoom: int = MIN_ZOOM,
    max_zoom: int = MAX_ZOOM,
) -> dict[str, Any]:
    """Create the PMTiles, GeoJSON, manifest, and block assets under docs/data."""
    root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[1]
    if min_zoom < 0 or max_zoom < min_zoom or max_zoom > 22:
        raise ValueError(f"Invalid zoom range {min_zoom}..{max_zoom}.")
    output_dir = root / "Output Data"
    data_dir = root / "docs" / "data"
    tiles_dir = data_dir / "tiles"
    block_dir = data_dir / "block"
    data_dir.mkdir(parents=True, exist_ok=True)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    block_dir.mkdir(parents=True, exist_ok=True)

    for spec in LAYERS:
        source_path = output_dir / "feature_layers" / f"{spec.name}.tif"
        if not source_path.is_file():
            raise FileNotFoundError(f"Required feature raster is missing: {source_path}")

    boundary_path = output_dir / "watershedBoundary.shp"
    dem_path = output_dir / "watershedDEM.tif"
    boundary = gpd.read_file(boundary_path)
    with rasterio.open(dem_path) as dem:
        boundary = boundary.to_crs(dem.crs)
        lonlat_bounds = transform_bounds(
            dem.crs, "EPSG:4326", *dem.bounds, densify_pts=21
        )
    boundary_geojson, outlet_geojson, streams_geojson = _export_geojson(
        root, data_dir, boundary
    )

    block_metadata = _write_block_height(dem_path, block_dir)
    _write_block_boundary(boundary_path, dem_path, block_dir)
    _write_stream_texture(
        output_dir / "feature_vectors" / "stream_network_25km2.gpkg",
        dem_path,
        block_dir,
    )

    layer_records: list[dict[str, Any]] = []
    for index, spec in enumerate(LAYERS, start=1):
        source_path = output_dir / "feature_layers" / f"{spec.name}.tif"
        with rasterio.open(source_path) as source:
            encoding_range = _true_range(source, spec)
            display_range = _plot_range(source, spec)
            native_resolution = max(abs(source.res[0]), abs(source.res[1]))
        lut = _lut(spec)
        tile_path = tiles_dir / f"{spec.name}.pmtiles"
        byte_count = _write_pmtiles(
            source_path, tile_path, spec, encoding_range, lonlat_bounds,
            min_zoom, max_zoom,
        )
        record: dict[str, Any] = {
            "name": spec.name,
            "title": spec.title,
            "group": spec.group,
            "description": spec.description,
            "units": spec.units,
            "transform": spec.transform,
            "encoding": {
                "min": encoding_range[0],
                "max": encoding_range[1],
                "format": "RGBA8: R=high byte, G=low byte, B=0, alpha=255 valid/0 nodata",
            },
            "display": {
                "min": display_range[0],
                "max": display_range[1],
            },
            "colormap": spec.cmap,
            "lut": lut,
            "bounds": list(lonlat_bounds),
            "minzoom": min_zoom,
            "maxzoom": max_zoom,
            "nativeResolutionM": native_resolution,
            "resampling": "nearest" if spec.nearest else "area-average",
            "tile": f"tiles/{spec.name}.pmtiles",
            "tileBytes": byte_count,
            "isVectorOverlay": spec.is_stream,
        }
        if spec.is_stream:
            record["vector"] = "streams.geojson"
            record["blockTexture"] = "block/hillshade.png"
            record["blockOverlay"] = "block/streams.png"
            record["valueUnits"] = "Strahler order"
        else:
            texture_path = block_dir / f"{spec.name}.png"
            _write_layer_texture(source_path, texture_path, spec, display_range, lut)
            record["blockTexture"] = f"block/{spec.name}.png"
        layer_records.append(record)
        print(
            f"[{index:02d}/{len(LAYERS)}] {spec.name}: "
            f"{byte_count / 1024**2:.2f} MiB tiles"
        )

    manifest = {
        "schemaVersion": 1,
        "crs": "EPSG:3857",
        "minzoom": min_zoom,
        "maxzoom": max_zoom,
        "bounds": list(lonlat_bounds),
        "boundary": "boundary.geojson",
        "outlet": "outlet.geojson",
        "streams": "streams.geojson",
        "block": {
            "height": "block/height.bin",
            "metadata": "block/height.json",
            "boundary": "block/boundary.json",
            "streams": "block/streams.png",
            "grid": block_metadata,
        },
        "layers": layer_records,
    }
    (data_dir / "layers.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    sizes = sorted(
        ((path, path.stat().st_size) for path in data_dir.rglob("*") if path.is_file()),
        key=lambda item: item[1],
        reverse=True,
    )
    total_bytes = sum(size for _, size in sizes)
    print("\nExport files:")
    for path, size in sizes:
        print(f"  {path.relative_to(root)}: {size / 1024**2:.2f} MiB")
    print(f"Total docs/data: {total_bytes / 1024**2:.2f} MiB")
    oversized = [(path, size) for path, size in sizes if size >= 50_000_000]
    if oversized:
        details = ", ".join(f"{path.name} ({size / 1024**2:.2f} MiB)"
                            for path, size in oversized)
        raise ValueError(f"Files exceed the 50 MiB target: {details}")
    if total_bytes > 400_000_000:
        raise ValueError(
            f"docs/data is {total_bytes / 1_000_000:.2f} MB, above the 400 MB target."
        )
    return {"files": len(sizes), "total_bytes": total_bytes, "layers": len(layer_records)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Repository root.")
    parser.add_argument("--min-zoom", type=int, default=MIN_ZOOM)
    parser.add_argument("--max-zoom", type=int, default=MAX_ZOOM)
    args = parser.parse_args()
    export_web_data(args.root, args.min_zoom, args.max_zoom)


if __name__ == "__main__":
    main()
