# Catastro fixtures

All files are **real responses** of the JSON services, captured on 2026-11-05:

| File | Request |
|---|---|
| `dnprc_single.json` | `Consulta_DNPRC?RefCat=2749704YJ0624N0001DI` (example from Webservices_Libres.pdf) |
| `real_dnprc_list.json` | `Consulta_DNPRC?RefCat=6922624DF2862B` — parcel with 475 units, **trimmed to 12** |
| `real_dnprc_unit.json` | `Consulta_DNPRC?RefCat=6922624DF2862B0005QA` |
| `real_dnprc_parcel_single.json` | `Consulta_DNPRC?RefCat=1040501DF3814A` — parcel without horizontal division |
| `real_rccoor.json` | `Consulta_RCCOOR?CoorX=2.1744&CoorY=41.4036&SRS=EPSG:4326` |
| `real_callejero_mallorca.json` | `ObtenerCallejero?Provincia=BARCELONA&Municipio=BARCELONA&TipoVia=&NomVia=MALLORCA` |
| `real_callejero_none.json` | `ObtenerCallejero?...&TipoVia=PG&NomVia=GRACIA` — no matches (error 10) |
| `real_dnploc_single.json` | `Consulta_DNPLOC?...&Sigla=CL&Calle=MALLORCA&Numero=406` |
| `real_dnploc_notfound.json` | `Consulta_DNPLOC?...&Sigla=CL&Calle=MALLORCA&Numero=401` — error 43 + nearby numbers |
