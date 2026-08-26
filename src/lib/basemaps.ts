/**
 * Raster Canvas layers without the separate Esri reference-label layer.
 * Place labels remain under project control; `{y}/{x}` is ArcGIS tile order.
 */
const ESRI_CANVAS_ROOT =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";

export const OPENFREE_RELIEF_URL =
  "https://tiles.openfreemap.org/natural_earth/ne2sr/{z}/{x}/{y}.png";

export const OPENFREE_ATTRIBUTION =
  "OpenFreeMap © OpenMapTiles · Data © OpenStreetMap contributors";

export const ESRI_DARK_BASEMAP_URL =
  `${ESRI_CANVAS_ROOT}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`;

export const ESRI_LIGHT_BASEMAP_URL =
  `${ESRI_CANVAS_ROOT}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`;

export const ESRI_CANVAS_ATTRIBUTION =
  "© Esri, HERE, Garmin, © OpenStreetMap contributors, GIS user community";
