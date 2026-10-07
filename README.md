# Watershed Delineation

This project uses Google Earth Engine, GeoPandas, Rasterio, and WhiteboxTools to
download a DEM and delineate the catchment upstream of a user-selected outlet.
It currently runs as two Jupyter notebooks; it is not yet a hosted web
application.

## Requirements

- Python 3.11
- A Google Earth Engine account with access to the Copernicus GLO-30 DEM
- The Python packages listed in [`requirements.txt`](requirements.txt)

Create an environment and install the pinned dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run the workflow

1. Start JupyterLab from the project root:

   ```bash
   jupyter lab
   ```

2. Open [`notebooks/GEE_Data_Acquisation.ipynb`](notebooks/GEE_Data_Acquisation.ipynb)
   and run its cells in order. Authenticate Earth Engine when prompted. On the
   interactive map, draw exactly one rectangle that fully contains the
   catchment with a generous buffer, and one outlet point inside the rectangle.
   The notebook saves the geometries and downloads the GLO-30 DEM. Large
   downloads are subdivided and mosaicked automatically.
3. Open [`notebooks/watershedDelineation.ipynb`](notebooks/watershedDelineation.ipynb)
   and run its cells in order. The workflow selects a UTM zone based on the
   largest share of the AOI, trims equal-width edge strips to remove projected
   NoData, derives flow direction and accumulation, snaps the outlet to a
   nearby high-accumulation cell, delineates that outlet's watershed, and
   clips the DEM to the watershed mask.

The Earth Engine account must be authorized on the machine running the first
notebook. Depending on the account configuration, Earth Engine may also
require a registered Cloud project when it is initialized.

## Outputs

The final products are written to `Output Data/`:

- `watershedDEM.tif` - the projected DEM clipped to the delineated watershed;
  cells outside the basin are NoData.
- `watershedBoundary.shp` - watershed polygon in the selected UTM coordinate
  system. Keep the companion `.shx`, `.dbf`, `.prj`, and `.cpg` files with it.
- `watershedDEM.png` - a colour-ramp preview of the watershed DEM with its
  snapped outlet, north arrow, scale bar, and a margin around the basin.

Intermediate DEMs, flow rasters, and the reprojected and snapped outlet files
are written to `Preprocessing/`. Input geometries and downloaded source DEMs
are written to `Input Data/`.

## Notes

- Draw an AOI that includes both the complete catchment and a buffer around it.
  The catchment must fit inside the selected area for delineation to be
  complete.
- The outlet snap search radius is currently ten DEM cells (300 m at 30 m
  resolution).
- DEM processing can use substantial memory, disk space, and time for large
  AOIs.
- Generated inputs, outputs, and intermediate rasters are excluded from Git by
  [`.gitignore`](.gitignore). Do not commit Earth Engine credentials, private
  keys, or other secrets.

## License

This project's code is distributed under the MIT License; see
[`LICENSE`](LICENSE). Third-party services and datasets retain their own terms
of use.
