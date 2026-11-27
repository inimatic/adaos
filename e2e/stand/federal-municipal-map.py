"""Prepare a real federal-district map load fixture from the local Small Cities sources."""

from __future__ import annotations

import argparse
import gzip
import heapq
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from itertools import groupby
from pathlib import Path

import duckdb
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import Compression, TileType, tileid_to_zxy, zxy_to_tileid
from pmtiles.writer import write

from adaos.services.storage_hygiene import managed_storage_run

YEARS = range(2018, 2026)
DISTRICTS = {
    'Центральный': {31, 32, 33, 36, 37, 40, 44, 46, 48, 50, 57, 62, 67, 68, 69, 71, 76, 77},
    'Северо-Западный': {10, 11, 29, 35, 39, 47, 51, 53, 60, 78, 83},
    'Южный': {1, 8, 23, 30, 34, 61, 91, 92},
    'Северо-Кавказский': {5, 6, 7, 9, 15, 20, 26},
    'Приволжский': {2, 12, 13, 16, 18, 21, 43, 52, 56, 58, 59, 63, 64, 73},
    'Уральский': {45, 66, 72, 74, 86, 89},
    'Сибирский': {4, 17, 19, 22, 24, 38, 42, 54, 55, 70},
    'Дальневосточный': {3, 14, 25, 27, 28, 41, 49, 65, 75, 79, 87},
}
DISTRICT_IDS = dict(zip(DISTRICTS, ('central', 'northwestern', 'southern', 'north-caucasian',
                                   'volga', 'ural', 'siberian', 'far-eastern'), strict=True))
GEOMETRY_SOURCE = 'https://sberindex.ru/ru/research/russian_municipal_districts_boundaries'
LICENSE = 'https://creativecommons.org/licenses/by-sa/4.0/'
POPULATION_SOURCE = 'https://rosstat.gov.ru/munstat'
GEOMETRY_LAYER = 't_dict_municipal_districts_poly'
TILE_LAYER = 'municipalities'


def vector_tile_type():
    """Describe the MVT fields we read and keep, including the packed geometry commands."""
    field = descriptor_pb2.FieldDescriptorProto
    proto = descriptor_pb2.FileDescriptorProto(name='vector_tile.proto', package='mvt', syntax='proto2')

    def describe(name, fields):
        message = proto.message_type.add(name=name)
        for key, number, kind, repeated, nested in fields:
            current = message.field.add(name=key, number=number, type=kind,
                                        label=field.LABEL_REPEATED if repeated else field.LABEL_OPTIONAL)
            if nested:
                current.type_name = '.mvt.' + nested
            if repeated and kind == field.TYPE_UINT32:
                current.options.packed = True

    describe('Tile', [('layers', 3, field.TYPE_MESSAGE, True, 'Layer')])
    describe('Layer', [
        ('name', 1, field.TYPE_STRING, False, None), ('features', 2, field.TYPE_MESSAGE, True, 'Feature'),
        ('keys', 3, field.TYPE_STRING, True, None), ('values', 4, field.TYPE_MESSAGE, True, 'Value'),
        ('extent', 5, field.TYPE_UINT32, False, None), ('version', 15, field.TYPE_UINT32, False, None),
    ])
    describe('Feature', [
        ('tags', 2, field.TYPE_UINT32, True, None), ('type', 3, field.TYPE_UINT32, False, None),
        ('geometry', 4, field.TYPE_UINT32, True, None),
    ])
    describe('Value', [(name, number, kind, False, None) for number, (name, kind) in enumerate([
        ('string', field.TYPE_STRING), ('float', field.TYPE_FLOAT), ('double', field.TYPE_DOUBLE),
        ('int', field.TYPE_INT64), ('uint', field.TYPE_UINT64), ('sint', field.TYPE_SINT64),
        ('bool', field.TYPE_BOOL),
    ], 1)])
    pool = descriptor_pool.DescriptorPool()
    pool.Add(proto)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName('mvt.Tile'))


Tile = vector_tile_type()


def properties(layer, feature):
    return {layer.keys[key]: layer.values[value].ListFields()[0][1]
            for key, value in zip(feature.tags[::2], feature.tags[1::2], strict=True)}


def oktmo(value):
    digits = re.sub(r'\D', '', str(value))
    if len(digits) == 5:
        return digits + '000'
    if len(digits) in (8, 11):
        return digits[:8]
    return ''


def active(rows, year):
    return [row for row in rows if int(row['year_from']) <= year < int(row['year_to'])]


def snapshots(historical, current, district=None):
    recent = active(current, YEARS[-1])
    by_region = {code: district for district, codes in DISTRICTS.items() for code in codes}
    ranking = Counter(by_region[int(row['region_code'])] for row in recent)
    if district is None:
        district, _ = ranking.most_common(1)[0]
    result = {}
    for year in YEARS:
        latest = year == YEARS[-1]
        rows = active(current if latest else historical, year)
        prefix, id_column = ('c', 'id_m') if latest else ('h', 'territory_id')
        selected = [row for row in rows if int(row['region_code']) in DISTRICTS[district]]
        result[year] = {
            f'{prefix}:{row[id_column]}': {
                'name': row['municipal_district_name'], 'region': row['region_name'],
                'kind': row['municipal_district_type'], 'oktmo': oktmo(row['oktmo']),
            }
            for row in selected
        }
        if not selected or len(result[year]) != len(selected):
            raise ValueError(f'Incorrect municipality identities for {year}')
    return district, dict(ranking.most_common()), result


def attach_population(path, years, supplement):
    with duckdb.connect(config={'threads': '2', 'memory_limit': '256MB', 'max_temp_directory_size': '0MB'}) as db:
        observations = db.execute('''
            SELECT year, oktmo, indicator_value
            FROM read_parquet(?)
            WHERE year BETWEEN ? AND ? AND mest = 'Все население'
              AND indicator_period = 'На 1 января' AND indicator_value IS NOT NULL
        ''', [str(path), YEARS[0], YEARS[-1]]).fetchall()
    by_code = defaultdict(set)
    for year, code, value in observations:
        by_code[year, code].add(value)
    additions = {}
    if supplement:
        for row in json.loads(supplement.read_text(encoding='utf-8')):
            key = (row['year'], row['oktmo'])
            if (key in additions or type(row['population']) is not int or row['population'] < 0
                    or not row.get('url') or not row.get('sheet') or not row.get('row')):
                raise ValueError(f'Invalid or duplicate supplemental population for {key}')
            additions[key] = row
    missing, supplemented = [], []
    for year, municipalities in years.items():
        for key, row in municipalities.items():
            values = by_code[year, row['oktmo']]
            if len(values) == 1:
                value, = values
                if value < 0 or not value.is_integer():
                    raise ValueError(f'Invalid population for {key} in {year}: {value}')
                row['population'] = int(value)
            else:
                published = additions.pop((year, row['oktmo']), None)
                if published:
                    row['population'] = published['population']
                    supplemented.append({'id': key, **published})
                else:
                    row['population'] = None
                    missing.append({'year': year, 'id': key, **row, 'reason': 'ambiguous' if values else 'absent'})
    if additions:
        raise ValueError(f'Unused supplemental observations: {sorted(additions)}')
    return missing, supplemented


def read_city_registry(path):
    cities = []
    ids = set()
    for row in json.loads(path.read_text(encoding='utf-8')):
        city_id = f"{row['reg']}/{row['n']}"
        if (city_id in ids or row['fo'] not in DISTRICTS or not row['pop'] > 0
                or not (-180 <= row['lon'] <= 180 and -90 <= row['lat'] <= 90)):
            raise ValueError(f'Incorrect city record: {city_id}')
        ids.add(city_id)
        cities.append({
            'id': city_id, 'name': row['n'], 'region': row['reg'], 'district': row['fo'],
            'latitude': row['lat'], 'longitude': row['lon'], 'population': row['pop'],
            'population2024': row.get('pop24'), 'population2010': row.get('pop10'),
        })
    return cities


def district_bounds(gpkg, latest):
    keys = {key.removeprefix('c:') for key in latest}
    query = f'''
        SELECT MIN(r.minx), MIN(r.miny), MAX(r.maxx), MAX(r.maxy)
        FROM {GEOMETRY_LAYER} g JOIN rtree_{GEOMETRY_LAYER}_geom r ON g.fid = r.id
        WHERE g.id_m IN ({','.join('?' for _ in keys)}) AND g.year_from <= ? AND g.year_to > ?
    '''
    with closing(sqlite3.connect(gpkg.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return db.execute(query, (*keys, YEARS[-1], YEARS[-1])).fetchone()


def input_tiles(source, prefix):
    for (z, x, y), compressed in all_tiles(source):
        yield zxy_to_tileid(z, x, y), prefix, compressed


def add_features(layer, tile, prefix, keys, dictionary, versions, seen, zoom):
    identifier = 'id' if prefix == 'h' else 'id_m'
    for original in tile.layers:
        if original.name != TILE_LAYER:
            continue
        if len(layer.features) == 0:
            layer.extent, layer.version = original.extent, original.version
        if layer.extent != original.extent or layer.version != original.version:
            raise ValueError('Source vector tiles use different extents or versions')
        for feature in original.features:
            attrs = properties(original, feature)
            key = f'{prefix}:{attrs[identifier]}'
            if key not in keys:
                continue
            if prefix == 'h':
                start = max(int(attrs['year_from']), YEARS[0])
                end = min(int(attrs['year_to']), YEARS[-1])
            else:
                start, end = YEARS[-1], YEARS[-1] + 1
            if end <= start:
                continue
            payload = layer.features.add(type=feature.type, geometry=feature.geometry)
            version = f'{key}/{start}/{end}'
            versions[key].add((start, end))
            for index, value in enumerate((key, start, end, version)):
                if value not in dictionary:
                    dictionary[value] = len(layer.values)
                    if isinstance(value, str):
                        layer.values.add(string=value)
                    else:
                        layer.values.add(uint=value)
                payload.tags.extend((index, dictionary[value]))
            if zoom == 9:
                for year in range(start, end):
                    seen[year].add(key)


def prepare_archive(historical, current, target, district, bounds, years):
    keys = set().union(*(data.keys() for data in years.values()))
    seen = defaultdict(set)
    versions = defaultdict(set)
    with historical.open('rb') as old_file, current.open('rb') as current_file, write(target) as writer:
        old, recent = MmapSource(old_file), MmapSource(current_file)
        headers = [Reader(source).header() for source in (old, recent)]
        if any(h['tile_type'] != TileType.MVT or h['tile_compression'] != Compression.GZIP for h in headers):
            raise ValueError('Expected gzip-compressed MVT inputs')
        inputs = heapq.merge(input_tiles(old, 'h'), input_tiles(recent, 'c'), key=lambda item: item[0])
        for tileid, source_tiles in groupby(inputs, key=lambda item: item[0]):
            zoom, _, _ = tileid_to_zxy(tileid)
            tile = Tile()
            layer = tile.layers.add(name=TILE_LAYER, keys=['id', 'year_from', 'year_to', 'version'])
            dictionary = {}
            for _, prefix, compressed in source_tiles:
                add_features(layer, Tile.FromString(gzip.decompress(compressed)), prefix, keys, dictionary, versions, seen, zoom)
            if layer.features:
                writer.write_tile(tileid, gzip.compress(tile.SerializeToString(), compresslevel=6, mtime=0))
        for year, rows in years.items():
            absent, extra = rows.keys() - seen[year], seen[year] - rows.keys()
            if absent or extra:
                raise ValueError(f'Tile/attribute mismatch for {year}: missing {sorted(absent)}, extra {sorted(extra)}')
        west, south, east, north = bounds
        writer.finalize({
            'tile_type': TileType.MVT, 'tile_compression': Compression.GZIP,
            'min_lon_e7': round(west * 1e7), 'min_lat_e7': round(south * 1e7),
            'max_lon_e7': round(east * 1e7), 'max_lat_e7': round(north * 1e7), 'center_zoom': 5,
            'center_lon_e7': round((west + east) * .5e7), 'center_lat_e7': round((south + north) * .5e7),
        }, {
            'name': f'{district} федеральный округ, 2018–2025',
            'attribution': f'<a href="{GEOMETRY_SOURCE}">СберИндекс</a> '
                           f'(<a href="{LICENSE}">CC BY-SA 4.0</a>), '
                           '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>',
            'vector_layers': [{'id': TILE_LAYER, 'fields': {
                'id': 'String', 'year_from': 'Number', 'year_to': 'Number', 'version': 'String',
            }}],
        })
    return {key: sorted(ranges) for key, ranges in sorted(versions.items())}


def save_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


def federal_overview(gpkg, latest):
    import numpy as np
    import shapely
    from shapely.affinity import translate
    from shapely.geometry import mapping

    owners = {key.removeprefix('c:'): district for district, rows in latest.items() for key in rows}
    areas = defaultdict(list)
    wrap_bounds = defaultdict(list)
    query = f'SELECT id_m, geom FROM {GEOMETRY_LAYER} WHERE year_from <= ? AND year_to > ?'
    with closing(sqlite3.connect(gpkg.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        for key, blob in db.execute(query, (YEARS[-1], YEARS[-1])):
            offset = 8 + (0, 32, 48, 48, 64)[(blob[3] >> 1) & 7]
            geometry = shapely.from_wkb(blob[offset:])
            district = owners[key]
            if district == 'Дальневосточный':
                geometry = shapely.transform(geometry, lambda x, y: (np.where(x < 0, x + 360, x), y), interleaved=False)
            wrap_bounds[district].append(geometry.bounds)
            # At national scale the map needs district outlines at subpixel resolution.
            simplified = shapely.simplify(geometry, 0.035, preserve_topology=False)
            areas[district].append(simplified if simplified.is_valid else shapely.buffer(simplified, 0))

    features = []
    for district, shapes in areas.items():
        dissolved = shapely.union_all(shapes, grid_size=0.02)
        polygons = [part for part in shapely.get_parts(dissolved) if part.geom_type == 'Polygon']
        outline = shapely.simplify(shapely.MultiPolygon(polygons), 0.20, preserve_topology=True)
        outline = shapely.set_precision(outline, 0.001)
        if district == 'Дальневосточный':
            west = shapely.intersection(outline, shapely.box(0, -90, 180, 90))
            east = translate(shapely.intersection(outline, shapely.box(180, -90, 360, 90)), xoff=-360)
            outline = shapely.union(west, east)
        features.append({'type': 'Feature', 'properties': {'id': DISTRICT_IDS[district]}, 'geometry': mapping(outline)})

    def bounds(items):
        return [min(item[0] for item in items), min(item[1] for item in items),
                max(item[2] for item in items), max(item[3] for item in items)]

    frames = {district: bounds(items) for district, items in wrap_bounds.items()}
    return {'type': 'FeatureCollection', 'features': features}, frames, bounds(frames.values())


def build_federal_assets(args, workbook_rows, scratch):
    historical, current = (workbook_rows(path) for path in (args.historical_attributes, args.current_attributes))
    all_districts = {district: snapshots(historical, current, district)[2] for district in DISTRICTS}
    combined = {year: {} for year in YEARS}
    for years in all_districts.values():
        for year, rows in years.items():
            if combined[year].keys() & rows.keys():
                raise ValueError(f'Municipality assigned to several districts in {year}')
            combined[year].update(rows)
    gaps, supplemental = attach_population(args.population, combined, args.supplemental_population)
    cities = read_city_registry(args.cities)
    if args.pilot_registry:
        pilot = {(row['region'], row['name']): row['id']
                 for row in json.loads(args.pilot_registry.read_text(encoding='utf-8'))}
        for city in cities:
            if passport := pilot.get((city['region'], city['name'])):
                city['passportId'] = passport
    latest = {district: years[YEARS[-1]] for district, years in all_districts.items()}
    print('Dissolving federal district overview boundaries', flush=True)
    overview, frames, country_frame = federal_overview(args.current_gpkg, latest)
    district_index = []
    report = {}
    for district, years in all_districts.items():
        district_id = DISTRICT_IDS[district]
        artifact = args.output / district_id
        artifact.mkdir(parents=True, exist_ok=True)
        archive = scratch / f'{district_id}.pmtiles'
        archive_bounds = district_bounds(args.current_gpkg, latest[district])
        versions = prepare_archive(args.historical_tiles, args.current_tiles, archive, district, archive_bounds, years)
        archive_bytes = archive.stat().st_size
        shutil.move(archive, artifact / 'district.pmtiles')
        regional_cities = [city for city in cities if city['district'] == district]
        save_json(artifact / 'district.json', {
            'district': district, 'municipalitiesByYear': years, 'versions': versions, 'cities': regional_cities,
        })
        counts = {year: {'municipalities': len(rows), 'withPopulation': sum(row['population'] is not None for row in rows.values())}
                  for year, rows in years.items()}
        district_index.append({'id': district_id, 'name': f'{district} федеральный округ', 'bounds': frames[district],
                               'cities': len(regional_cities), 'municipalities': len(latest[district])})
        report[district_id] = {'cities': len(regional_cities), 'archiveBytes': archive_bytes, 'coverage': counts}
        print(f'{district_id}: {len(latest[district])} municipalities, {len(regional_cities)} cities, '
              f'{archive_bytes} archive bytes', flush=True)

    save_json(args.output / 'catalog.json', {
        'version': 1, 'years': list(YEARS), 'districts': district_index, 'overview': overview, 'bounds': country_frame,
        'cityReferencePeriod': '2020/21', 'sources': [{'label': 'СберИндекс', 'url': GEOMETRY_SOURCE},
            {'label': 'CC BY-SA 4.0', 'url': LICENSE}, {'label': '© OpenStreetMap', 'url': 'https://www.openstreetmap.org/copyright'},
            {'label': 'Росстат', 'url': POPULATION_SOURCE}, {'label': 'Реестр фонда', 'url': 'https://beta.fond-mg.ru/'}],
    })
    save_json(args.output / 'coverage.json', {'districts': report, 'registryCities': len(cities),
        'populationSource': {'url': POPULATION_SOURCE, 'file': args.population.name, 'indicator': 'Y48112027'},
        'missingMunicipalityPopulation': gaps, 'supplementalMunicipalityPopulation': supplemental})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill', required=True, type=Path, help='Small Cities data skill directory')
    for key in ('historical-attributes', 'current-attributes', 'historical-tiles', 'current-tiles', 'current-gpkg',
                'population', 'cities', 'scratch', 'output'):
        parser.add_argument(f'--{key}', required=True, type=Path)
    parser.add_argument('--supplemental-population', type=Path, help='Rosstat observations with bulletin URL and row')
    parser.add_argument('--all-districts', action='store_true', help='Build static assets for the application')
    parser.add_argument('--pilot-registry', type=Path, help='Pilot registry used to link detailed city passports')
    parser.add_argument('--browser', action='store_true', help='Run the Playwright load check after preparation')
    args = parser.parse_args()
    sys.path.insert(0, str(args.skill.resolve()))
    try:
        from prepare_municipal_maps import workbook_rows
    finally:
        sys.path.pop(0)

    with managed_storage_run(args.scratch, owner='small-cities-map-load-test', retention_class='e2e') as scratch:
        tempfile.tempdir = str(scratch)
        args.output.mkdir(parents=True, exist_ok=True)
        if args.all_districts:
            build_federal_assets(args, workbook_rows, scratch)
            return
        district, ranking, years = snapshots(workbook_rows(args.historical_attributes), workbook_rows(args.current_attributes))
        missing, supplemented = attach_population(args.population, years, args.supplemental_population)
        cities = read_city_registry(args.cities)
        regional_cities = [row for row in cities if row['district'] == district]
        bounds = district_bounds(args.current_gpkg, years[YEARS[-1]])
        archive = scratch / 'district.pmtiles'
        versions = prepare_archive(args.historical_tiles, args.current_tiles, archive, district, bounds, years)
        shutil.copyfile(archive, args.output / archive.name)
        counts = {year: {'municipalities': len(rows), 'withPopulation': sum(r['population'] is not None for r in rows.values())}
                  for year, rows in years.items()}
        coverage = {
            'district': district, 'ranking2025': ranking, 'annualCoverage': counts, 'registryCities': len(cities),
            'districtCities': len(regional_cities), 'archiveBytes': archive.stat().st_size,
            'populationSource': {'url': POPULATION_SOURCE, 'indicator': 'Y48112027', 'file': args.population.name},
            'geometrySource': GEOMETRY_SOURCE, 'cityReferencePeriod': '2020/21', 'citySource': 'https://beta.fond-mg.ru/',
            'supplementalMunicipalityPopulation': supplemented,
            'missingMunicipalityPopulation': missing,
        }
        save_json(args.output / 'city-registry.json', {'referencePeriod': '2020/21', 'cities': cities})
        save_json(args.output / 'district.json', {
            'district': district, 'bounds': bounds, 'municipalitiesByYear': years, 'versions': versions, 'cities': regional_cities,
            'cityReferencePeriod': coverage['cityReferencePeriod'], 'geometrySource': GEOMETRY_SOURCE,
            'populationSource': POPULATION_SOURCE,
        })
        save_json(args.output / 'coverage.json', coverage)
        print(json.dumps({key: value for key, value in coverage.items() if not key.endswith('MunicipalityPopulation')},
                         ensure_ascii=False), flush=True)
        if args.browser:
            runner = Path(__file__).with_name('browser') / 'federal-municipal-map.mjs'
            subprocess.run(['node', str(runner), str(args.output.resolve()), str(scratch / 'profile')], check=True)


if __name__ == '__main__':
    main()
