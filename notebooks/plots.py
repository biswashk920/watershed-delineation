"""Plotting helpers for the watershed notebooks."""
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling

INK = "#202020"


def _read_preview(raster_path, max_px=2400, resampling=Resampling.average):
    """Read a downsampled, masked preview of band 1. Returns (array, extent, bounds)."""
    with rasterio.open(raster_path) as src:
        scale = min(1.0, max_px / max(src.width, src.height))
        h = max(1, round(src.height * scale))
        w = max(1, round(src.width * scale))
        data = src.read(1, out_shape=(h, w), resampling=resampling)
        nodata = src.read_masks(1, out_shape=(h, w), resampling=Resampling.nearest) == 0
        bounds = src.bounds
    if nodata.all():
        raise ValueError(f"{Path(raster_path).name} contains no valid pixels to plot.")
    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
    return np.ma.array(data, mask=nodata), extent, bounds


def _add_north_arrow(ax, x=0.95):
    ax.annotate(
        "", xy=(x, 0.97), xytext=(x, 0.87), xycoords="axes fraction",
        arrowprops={"arrowstyle": "-|>", "color": INK, "linewidth": 1.8}, zorder=8,
    )
    ax.text(x, 0.985, "N", transform=ax.transAxes, ha="center", va="bottom",
            fontsize=12, fontweight="bold", color=INK)


def _add_scale_bar(ax, bounds, offset=0.05):
    map_w = bounds.right - bounds.left
    map_h = bounds.top - bounds.bottom
    target = map_w * 0.18
    power = 10 ** np.floor(np.log10(target))
    length = next(f * power for f in (5, 2, 1) if f * power <= target)
    x0 = bounds.left + map_w * offset
    y0 = bounds.bottom + map_h * offset
    label = f"{length / 1000:g} km" if length >= 1000 else f"{length:g} m"

    ax.plot([x0, x0 + length], [y0, y0], color=INK, linewidth=3,
            solid_capstyle="butt", zorder=8)
    for x in (x0, x0 + length):
        ax.plot([x, x], [y0 - map_h * 0.007, y0 + map_h * 0.007],
                color=INK, linewidth=1.5, zorder=8)
    ax.text(x0 + length / 2, y0 + map_h * 0.013, label, ha="center", va="bottom",
            fontsize=10, fontweight="bold", color=INK, zorder=9,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.82, "pad": 2})


def _add_watershed_boundary(ax, watershed_gdf, color, linewidth, label, zorder):
    line_parts = []

    def plot_lines(geometry):
        if geometry is None or geometry.is_empty:
            return
        if geometry.geom_type in ("Polygon", "MultiPolygon"):
            geometry = geometry.boundary
        if geometry.geom_type == "LineString":
            line_parts.append(np.asarray(geometry.coords))
            line_parts.append(np.array([[np.nan, np.nan]]))
            return
        for part in getattr(geometry, "geoms", ()):
            plot_lines(part)

    for geometry in watershed_gdf.geometry:
        plot_lines(geometry)
    if line_parts:
        coordinates = np.concatenate(line_parts)
        ax.plot(
            coordinates[:, 0], coordinates[:, 1], color=color,
            linewidth=linewidth, label=label, zorder=zorder,
        )


def plot_dem_map(dem_path, watershed_gdf, outlet_path, title, png_path,
                 boundary_label=None, boundary_width=1.0, padding=0.045,
                 dpi=240, figsize=(11, 9)):
    """DEM colour ramp + watershed boundary + snapped outlet, north arrow, scale bar.

    Saves a PNG and returns the matplotlib Figure (call plt.show() to display).
    """
    dem, extent, bounds = _read_preview(dem_path)
    low, high = np.percentile(dem.compressed(), [2, 98])
    if low == high:
        low, high = float(dem.min()), float(dem.max()) + 1

    cmap = plt.get_cmap("terrain").copy()
    cmap.set_bad((1, 1, 1, 0))

    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    ax.set_facecolor("#e9eef2")
    image = ax.imshow(dem, extent=extent, origin="upper", cmap=cmap,
                      vmin=float(low), vmax=float(high), interpolation="bilinear")

    _add_watershed_boundary(ax, watershed_gdf, "#252525", boundary_width,
                            boundary_label, zorder=4)

    outlet = gpd.read_file(outlet_path).to_crs(watershed_gdf.crs).geometry.iloc[0]
    ax.scatter(outlet.x, outlet.y, s=58, marker="o", color="#d73027",
               edgecolors="white", linewidths=1.2, label="Snapped outlet", zorder=6)

    ax.set_title(title, fontsize=15, weight="bold")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal")

    map_w = bounds.right - bounds.left
    map_h = bounds.top - bounds.bottom
    ax.set_xlim(bounds.left - map_w * padding, bounds.right + map_w * padding)
    ax.set_ylim(bounds.bottom - map_h * padding, bounds.top + map_h * padding)

    _add_north_arrow(ax)
    _add_scale_bar(ax, bounds)
    ax.legend(loc="upper left", frameon=True, framealpha=0.9)
    fig.colorbar(image, ax=ax, shrink=0.82, pad=0.03).set_label("Elevation (m)")

    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    return fig


def plot_raster_map(raster_path, watershed_gdf, outlet_path, title, cbar_label, png_path,
                   cmap="viridis", vlim=None, pct=(2, 98), symmetric=False,
                   transform=None, resampling=Resampling.average, extra=None,
                   boundary_width=0.8, padding=0.055, dpi=240, figsize=(11, 9),
                   boundary_label="Watershed boundary", outlet_label="Snapped outlet"):
    """Generic watershed-only raster map with boundary, outlet, north arrow, scale bar.

    transform: optional function applied to the data before plotting (e.g. np.log10).
    extra: optional callable(ax) for overlaying vector/layer annotations.
    """
    data, extent, bounds = _read_preview(raster_path, resampling=resampling)
    if transform is not None:
        with np.errstate(all="ignore"):
            data = np.ma.masked_invalid(transform(data))
    if data.count() == 0:
        raise ValueError(f"{Path(raster_path).name} has no valid pixels to plot.")

    if vlim is None:
        low, high = np.percentile(data.compressed(), pct)
        if symmetric:
            high = max(abs(low), abs(high))
            low = -high
        if low == high:
            high = low + 1
    else:
        low, high = vlim

    cm = plt.get_cmap(cmap).copy()
    cm.set_bad((1, 1, 1, 0))

    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
    ax.set_facecolor("#e9eef2")
    image = ax.imshow(data, extent=extent, origin="upper", cmap=cm,
                      vmin=float(low), vmax=float(high), interpolation="nearest")

    _add_watershed_boundary(ax, watershed_gdf, "#252525", boundary_width,
                            boundary_label, zorder=4)
    outlet = gpd.read_file(outlet_path).to_crs(watershed_gdf.crs).geometry.iloc[0]
    ax.scatter(outlet.x, outlet.y, s=58, marker="o", color="#d73027",
               edgecolors="white", linewidths=1.2, label=outlet_label, zorder=6)
    if extra is not None:
        extra(ax)

    ax.set_title(title, fontsize=15, weight="bold")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.set_aspect("equal")

    map_w = bounds.right - bounds.left
    map_h = bounds.top - bounds.bottom
    ax.set_xlim(bounds.left - map_w * padding, bounds.right + map_w * padding)
    ax.set_ylim(bounds.bottom - map_h * padding, bounds.top + map_h * padding)

    _add_north_arrow(ax)
    _add_scale_bar(ax, bounds)
    ax.legend(loc="upper left", frameon=True, framealpha=0.9, fontsize=9)
    fig.colorbar(image, ax=ax, shrink=0.82, pad=0.03).set_label(cbar_label)
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    return fig
