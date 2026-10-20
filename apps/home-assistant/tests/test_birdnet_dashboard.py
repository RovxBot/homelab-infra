"""Exercise read-only data access, clip boundaries and dashboard authorization."""

from contextlib import closing
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from conftest import APP


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = load('birdnet_reader', APP.parent / 'birdnet-go/dashboard/api.py')
integration = load('birdnet_dashboard_integration', APP / 'custom_components/birdnet_dashboard/__init__.py')


@pytest.fixture
def database(tmp_path):
    clips = tmp_path / 'clips'
    clips.mkdir()
    models = tmp_path / 'models/shared'
    models.mkdir(parents=True)
    (models / 'geomodel_labels.txt').write_text('Gymnorhina tibicen_Australian Magpie\nPasser domesticus_House Sparrow\n')
    path = tmp_path / 'birdnet.db'
    db = sqlite3.connect(path)
    db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE detections(id INTEGER PRIMARY KEY, label_id INTEGER, model_id INTEGER,
          source_id INTEGER, detected_at INTEGER, confidence REAL, clip_name TEXT);
        CREATE TABLE labels(id INTEGER PRIMARY KEY, scientific_name TEXT);
        CREATE TABLE audio_sources(id INTEGER PRIMARY KEY, display_name TEXT, source_type TEXT, source_uri TEXT);
        CREATE TABLE detection_reviews(detection_id INTEGER, verified TEXT);
        CREATE TABLE image_caches(id INTEGER, label_id INTEGER, url TEXT, author_name TEXT,
          author_url TEXT, license_name TEXT, license_url TEXT, cached_at TEXT);
        CREATE TABLE ai_models(id INTEGER PRIMARY KEY, name TEXT, version TEXT);
        INSERT INTO labels VALUES(1,'Gymnorhina tibicen'),(2,'Passer domesticus'),(3,'Gymnorhina tibicen');
        INSERT INTO ai_models VALUES(1,'BirdNET','2.4'),(2,'Perch','2');
        INSERT INTO audio_sources VALUES(1,'Shed','unknown','rtsps://never-expose-camera-token'),(2,'Shed front','rtsp','private');
        INSERT INTO detections VALUES
          (1,1,1,1,1800000000,.95,'call.flac'),
          (2,2,1,2,1800000300,.85,NULL),
          (3,1,1,1,1800000600,.99,NULL),
          (4,3,2,2,1800003600,.97,NULL),
          (5,1,1,1,1800086400,.98,NULL);
        INSERT INTO detection_reviews VALUES(1,'correct'),(3,'false_positive');
        INSERT INTO image_caches VALUES(1,1,'https://example.org/magpie.jpg','Author','','CC BY','https://example.org/license','2026-01-01');
    ''')
    db.commit()
    (clips / 'call.flac').write_bytes(b'fLaC0123456789')
    yield reader.BirdDatabase(path, clips, models.parent), db
    db.close()


def query(**values):
    return {k: [str(v)] for k, v in {'start': 1800000000, 'end': 1800086400, **values}.items()}


def test_totals_match_rows_and_reviews_without_model_duplicates(database):
    store, writer = database
    data = store.dashboard(query())
    assert data['totals']['detections'] == 3
    assert data['totals']['species'] == 2
    assert data['hourly'][:2] == [2, 1]
    assert data['species'][0]['common_name'] == 'Australian Magpie'
    assert data['species'][0]['count'] == 2  # Merge labels for the same species across models.
    assert [r['id'] for r in data['detections']] == [4, 2, 1]
    assert data['detections'][-1]['has_audio'] is True
    assert data['detections'][-1]['review'] == 'correct'
    assert [row['name'] for row in data['sources']] == ['Shed', 'Shed front']
    assert 'never-expose' not in json.dumps(data)
    assert 'clip_name' not in json.dumps(data)
    # Read queries do not prevent the live writer from continuing in WAL mode.
    writer.execute('INSERT INTO detections VALUES(6,2,1,2,1800007200,.96,NULL)')
    writer.commit()
    assert store.dashboard(query())['totals']['detections'] == 4
    with closing(store.connect()) as readonly:
        with pytest.raises(sqlite3.OperationalError):
            readonly.execute('DELETE FROM detections')
    assert writer.execute('SELECT count(*) FROM detections').fetchone()[0] == 6


def test_filters_and_sql_injection_are_bounded(database):
    store, _ = database
    assert store.dashboard(query(source=2, confidence=.9))['totals']['detections'] == 1
    assert store.dashboard(query(search='MAGPIE'))['totals']['detections'] == 2
    assert store.dashboard(query(search='tibicen'))['totals']['detections'] == 2
    assert store.dashboard(query(search="' OR 1=1 --"))['totals']['detections'] == 0
    assert store.dashboard(query(rejected=1))['totals']['detections'] == 4
    assert store.dashboard(query(confidence=.95))['totals']['detections'] == 2
    for args in (query(confidence='nan'), query(source='1 OR 1=1'), query(end=1801000000), query(extra='no'), query(offset=-1)):
        with pytest.raises(reader.BadRequest):
            store.dashboard(args)


def test_clip_paths_cannot_escape_recordings(database, tmp_path):
    store, _ = database
    secret = tmp_path / 'config.yaml'
    secret.write_text('private')
    (store.clips / 'escape.flac').symlink_to(secret)
    assert store.clip_path('../config.yaml') is None
    assert store.clip_path('escape.flac') is None
    assert store.clip_path(str(secret)) is None
    assert store.audio(99999) is None
    assert store.audio(1).name == 'call.flac'


@pytest.mark.parametrize('header,expected', [
    (None, (0, 99, False)), ('bytes=10-19', (10, 19, True)),
    ('bytes=90-', (90, 99, True)), ('bytes=-5', (95, 99, True)),
    ('bytes=90-999', (90, 99, True)),
])
def test_audio_seeking(header, expected):
    assert reader.byte_range(header, 100) == expected


def test_audio_http_requires_token_and_supports_ranges(database):
    store, _ = database
    server = reader.ThreadingHTTPServer(('127.0.0.1', 0), reader.make_handler(store, 'test-token'))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(url + '/v1/audio/1')
        assert error.value.code == 401
        request = Request(url + '/v1/audio/1', headers={'Authorization': 'Bearer test-token', 'Range': 'bytes=4-8'})
        with urlopen(request) as response:
            assert response.status == 206
            assert response.headers['Content-Range'] == 'bytes 4-8/14'
            assert response.read() == b'01234'
        request = Request(url + '/v1/audio/1', headers={'Authorization': 'Bearer test-token', 'Range': 'bytes=100-'})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 416
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('day,hours', [('2026-10-04', 23), ('2026-04-05', 25), ('2026-10-20', 24)])
def test_station_days_include_adelaide_dst(day, hours):
    _, first, last, labels = integration.day_window(day, 'Australia/Adelaide')
    assert (last - first) / 3600 == hours
    assert len(labels) == hours


def test_dashboard_access_requires_the_configured_household():
    key = integration.KEY_HASS_USER
    assert integration.allowed({key: SimpleNamespace(id='sam')}, {'sam', 'rachael'})
    assert integration.allowed({key: SimpleNamespace(id='rachael')}, {'sam', 'rachael'})
    assert not integration.allowed({key: SimpleNamespace(id='someone-else', is_admin=True)}, {'sam', 'rachael'})
    assert not integration.allowed({}, {'sam', 'rachael'})
    assert integration.DashboardView.requires_auth
    assert integration.AudioView.requires_auth
