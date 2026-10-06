"""Persistence and creation tests using synthetic data, with optional live HTTP QA."""
import copy
from email.message import Message
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import notebook_creator as app


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='notebook-creator-test-')
        self.root = Path(self.directory.name).resolve()
        self.server = SimpleNamespace(root=self.root, token='test-only-token', database_lock=threading.RLock(), server_address=('127.0.0.1', 8765))
        self.document = self.root / 'example.html'
        self.document.write_text(app.MARKER)
        self.endpoint = '/api/notebook?file=example.html'

    def tearDown(self):
        self.directory.cleanup()

    def request(self, method='GET', path=None, data=None, revision='', headers=None):
        handler = app.Handler.__new__(app.Handler)
        handler.server, handler.command, handler.path = self.server, method, path or self.endpoint
        handler.headers = Message()
        values = {'Host': '127.0.0.1:8765', 'X-Notebook-Token': self.server.token, 'If-Match': revision, 'Content-Type': 'application/json'}
        values.update(headers or {})
        for key, value in values.items(): handler.headers[key] = value
        body = app.encode(data) if data is not None else b''
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        replies = []
        handler.send = lambda status, content, content_type='application/json; charset=utf-8': replies.append((status, content, content_type))
        getattr(handler, 'do_' + method)()
        status, content, content_type = replies[0]
        return status, json.loads(content) if content_type.startswith('application/json') else content

    def test_creates_adjacent_json_and_reuses_it(self):
        status, first = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(first['created'])
        self.assertEqual(first['state']['target'], 'example')
        self.assertEqual(first['state']['features'], [])
        self.assertTrue(self.document.with_suffix('.json').exists())
        _, second = self.request()
        self.assertFalse(second['created'])
        self.assertEqual(first['revision'], second['revision'])

    def test_save_persists_and_keeps_previous_version(self):
        _, first = self.request()
        state = copy.deepcopy(first['state'])
        state['target'] = 'target.example'
        state['tasks'] = [dict(id='task-one', title='Check ownership', notes='synthetic request #42', featureId='', priority='high', status='todo', createdAt='2026-10-06T00:00:00Z')]
        status, saved = self.request('POST', data=state, revision=first['revision'])
        self.assertEqual(status, 200)
        self.assertNotEqual(first['revision'], saved['revision'])
        self.assertEqual(json.loads(self.document.with_suffix('.json').read_text()), state)
        self.assertEqual(json.loads(self.document.with_suffix('.json.bak').read_text()), first['state'])
        self.server.token = 'new-launcher-token'
        _, loaded = self.request()
        self.assertEqual(loaded['state'], state)

    def test_stale_revision_cannot_overwrite_newer_notes(self):
        _, first = self.request()
        newer = copy.deepcopy(first['state']); newer['target'] = 'newer target'
        self.request('POST', data=newer, revision=first['revision'])
        stale = copy.deepcopy(first['state']); stale['target'] = 'stale target'
        self.assertEqual(self.request('POST', data=stale, revision=first['revision'])[0], 409)
        self.assertEqual(json.loads(self.document.with_suffix('.json').read_text())['target'], 'newer target')

    def test_concurrent_saves_have_one_winner(self):
        _, first = self.request(); responses = []; barrier = threading.Barrier(2)
        def write(name):
            state = copy.deepcopy(first['state']); state['target'] = name
            barrier.wait()
            responses.append(self.request('POST', data=state, revision=first['revision'])[0])
        threads = [threading.Thread(target=write, args=(name,)) for name in ['browser A', 'browser B']]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(responses), [200, 409])

    def test_targets_in_different_folders_are_independent(self):
        for folder in ['alpha', 'beta']:
            path = self.root / folder; path.mkdir()
            (path / 'notebook.html').write_text(app.MARKER)
            status, result = self.request(path='/api/notebook?file=' + quote(folder + '/notebook.html'))
            self.assertEqual(status, 200)
            self.assertEqual(result['state']['target'], folder)
            self.assertTrue((path / 'notebook.json').exists())

    def test_corrupt_json_is_never_recreated(self):
        database = self.document.with_suffix('.json'); database.write_text('{unreadable')
        status, result = self.request()
        self.assertEqual(status, 400)
        self.assertIn('not overwritten', result['error'])
        self.assertEqual(database.read_text(), '{unreadable')

    def test_failed_atomic_replace_preserves_existing_file(self):
        database = self.document.with_suffix('.json'); database.write_text('original content')
        with patch.object(app.os, 'replace', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError): app.atomic_write(database, b'new content')
        self.assertEqual(database.read_text(), 'original content')
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_cross_origin_and_wrong_token_are_rejected(self):
        for headers in [{'Origin': 'https://external.example'}, {'Host': 'external.example:8765'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.request(headers=headers)[0], 403)
        _, first = self.request()
        self.assertEqual(self.request('POST', data=first['state'], revision=first['revision'], headers={'X-Notebook-Token': 'wrong'})[0], 403)

    def test_traversal_and_external_json_symlink_are_rejected(self):
        self.assertEqual(self.request(path='/api/notebook?file=../outside.html')[0], 400)
        with tempfile.TemporaryDirectory(prefix='outside-notebook-') as directory:
            external = Path(directory) / 'outside.json'; external.write_text('private file')
            self.document.with_suffix('.json').symlink_to(external)
            self.assertEqual(self.request()[0], 400)
            self.assertEqual(external.read_text(), 'private file')

    def test_invalid_save_does_not_change_database(self):
        _, first = self.request()
        invalid = copy.deepcopy(first['state']); invalid['format'] = 'wrong-format'
        self.assertEqual(self.request('POST', data=invalid, revision=first['revision'])[0], 400)
        self.assertEqual(json.loads(self.document.with_suffix('.json').read_text()), first['state'])

    def test_creator_writes_template_and_empty_database(self):
        status, result = self.request('POST', '/api/notebooks', {'name': 'new-target', 'target': 'example.test', 'programUrl': 'https://example.org/program'})
        self.assertEqual(status, 201)
        self.assertEqual(result['path'], 'new-target/notebook.html')
        self.assertEqual((self.root / result['path']).read_bytes(), app.TEMPLATE.read_bytes())
        state = json.loads((self.root / result['database']).read_text())
        self.assertEqual(state['target'], 'example.test')
        self.assertEqual(state['features'], [])
        self.assertEqual(state['tasks'], [])

    def test_duplicate_creator_request_keeps_existing_data(self):
        payload = {'name': 'same', 'target': 'Original target'}
        self.assertEqual(self.request('POST', '/api/notebooks', payload)[0], 201)
        self.assertEqual(self.request('POST', '/api/notebooks', {**payload, 'target': 'Replacement'})[0], 409)
        self.assertEqual(json.loads((self.root / 'same/notebook.json').read_text())['target'], 'Original target')

    def test_creator_rejects_invalid_names_and_urls(self):
        for name in ['../escape', 'UPPERCASE', 'contains space', '', 'a' * 65]:
            self.assertEqual(self.request('POST', '/api/notebooks', {'name': name, 'target': 'Synthetic target'})[0], 400)
        self.assertEqual(self.request('POST', '/api/notebooks', {'name': 'unsafe', 'programUrl': 'javascript:alert(1)'})[0], 400)
        self.assertFalse((self.root / 'unsafe').exists())

    def test_library_lists_nested_notebooks_and_preserves_corrupt_ones(self):
        app.create_notebook(self.root, 'alpha', 'alpha.example')
        app.create_notebook(self.root, 'beta', 'beta.example')
        (self.root / 'beta/notebook.json').write_text('{unreadable')
        status, payload = self.request(path='/api/notebooks')
        self.assertEqual(status, 200)
        self.assertEqual(len(payload['notebooks']), 3)  # Two created + manually copied template.
        self.assertTrue(any(item['error'] for item in payload['notebooks']))
        self.assertNotIn('featureNotes', payload['notebooks'][0])

    def test_creator_page_and_assets_are_served(self):
        status, page = self.request(path='/')
        self.assertEqual(status, 200)
        self.assertIn(b'Create a notebook', page)
        self.assertEqual(self.request(path='/assets/creator.js')[0], 200)
        self.assertEqual(self.request(path='/assets/creator.css')[0], 200)
        self.assertEqual(self.request(path='/notebook_creator.py')[0], 404)

    def test_cli_creation(self):
        result = subprocess.run([sys.executable, str(app.APP_ROOT / 'notebook_creator.py'), 'create', 'cli-target', '--target', 'cli.example', '--root', str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / 'cli-target/notebook.json').read_text())['target'], 'cli.example')

    def test_full_schema_rejects_invalid_references_and_dates(self):
        state = app.empty_notebook(self.document)
        state['featureNotes']['missing-feature'] = 'orphaned note'
        with self.assertRaises(ValueError): app.validate(state)
        state = app.empty_notebook(self.document)
        state['recaps'] = [dict(id='recap-one', date='2026-02-31', title='Synthetic', featureId='', focus='Review', summary='', findings='', evidence='', next='', notes='')]
        with self.assertRaises(ValueError): app.validate(state)

    def test_functionality_tags_are_optional_and_validated(self):
        state = app.empty_notebook(self.document)
        feature = dict(id='feature-one', title='Synthetic', group='Profile & members', path='', items=[], note='', tests=[])
        state['features'] = [feature]
        app.validate(state)  # Older notebooks without tags remain compatible.
        feature['tags'] = ['XSS', 'JS review']
        self.assertEqual(app.validate(state)['features'][0]['tags'], feature['tags'])
        for invalid in [[''], ['XSS', 'XSS'], ['a' * 101], ['tag-' + str(i) for i in range(21)], 'not a list']:
            feature['tags'] = invalid
            with self.assertRaises(ValueError): app.validate(state)


@unittest.skipUnless(os.environ.get('NOTEBOOK_TEST_LIVE') == '1', 'Set NOTEBOOK_TEST_LIVE=1 where loopback sockets are permitted.')
class LiveHTTPTests(unittest.TestCase):
    def test_creator_and_save_over_http(self):
        with tempfile.TemporaryDirectory(prefix='notebook-http-test-') as directory:
            server = app.NotebookServer(('127.0.0.1', 0), Path(directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                with urlopen(base + '/') as response: self.assertIn(b'Notebook Creator', response.read())
                with urlopen(base + '/api/notebooks') as response: library = json.load(response)
                request = Request(base + '/api/notebooks', data=app.encode({'name': 'live', 'target': 'live.example'}), headers={'Content-Type': 'application/json', 'X-Notebook-Token': library['token']})
                with urlopen(request) as response: self.assertEqual(response.status, 201)
                with urlopen(base + '/api/notebook?file=live/notebook.html') as response: notebook = json.load(response)
                notebook['state']['target'] = 'updated.example'
                request = Request(base + '/api/notebook?file=live/notebook.html', data=app.encode(notebook['state']), headers={'Content-Type': 'application/json', 'X-Notebook-Token': notebook['token'], 'If-Match': notebook['revision']})
                with urlopen(request) as response: self.assertEqual(response.status, 200)
                self.assertEqual(json.loads((Path(directory) / 'live/notebook.json').read_text())['target'], 'updated.example')
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
