#!/usr/bin/env python3
"""Create and serve portable research notebooks backed by local JSON files."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

MARKER = '<meta name="recon-notebook" content="v1">'
FORMAT = 'recon-notebook'
MAX_BYTES = 50 * 1024 * 1024
APP_ROOT = Path(__file__).resolve().parent
TEMPLATE = APP_ROOT / 'templates' / 'notebook.html'
WEB_ROOT = APP_ROOT / 'web'
VERSION = '0.1.0'


def create_notebook(root: Path, name: str, target: str = '', program_url: str = ''):
    """Create a fresh target folder without replacing existing notebooks."""
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
        raise ValueError('Use 1–64 lowercase letters, numbers, hyphens, or underscores for the folder name.')
    target = target.strip() or name
    if len(target) > 500 or len(program_url) > 2000:
        raise ValueError('The target name or program URL is too long.')
    if program_url:
        parsed = urlparse(program_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError('Use an http:// or https:// program URL.')
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / name
    folder.mkdir(mode=0o700)  # Exclusive creation: never overwrite another target.
    document = folder / 'notebook.html'
    state = empty_notebook(document)
    state['target'] = target
    state['programUrl'] = program_url
    atomic_write(document.with_suffix('.json'), encode(state))
    atomic_write(document, TEMPLATE.read_bytes())
    return {'path': document.relative_to(root).as_posix(), 'database': document.with_suffix('.json').relative_to(root).as_posix(), 'target': target}


def list_notebooks(root: Path):
    """List notebook metadata without returning research notes in the index."""
    results = []
    root = root.resolve()
    for document in sorted(root.rglob('*.html')):
        if document.is_symlink() or not document.resolve().is_relative_to(root):
            continue
        try:
            if MARKER not in document.read_text(encoding='utf-8'):
                continue
            database = document.with_suffix('.json')
            if database.is_symlink() or not database.resolve().is_relative_to(root):
                continue
            entry = {'path': document.relative_to(root).as_posix(), 'target': document.parent.name,
                     'database': database.relative_to(root).as_posix(), 'updatedAt': None,
                     'features': 0, 'recaps': 0, 'openChecks': 0, 'error': ''}
            if database.exists():
                data = validate(json.loads(database.read_bytes()))
                entry.update(target=data['target'], updatedAt=data.get('updatedAt'),
                             features=len(data['features']), recaps=len(data['recaps']),
                             openChecks=sum(task.get('status') != 'done' for task in data['tasks']))
            results.append(entry)
        except (OSError, ValueError, TypeError, UnicodeError):
            results.append({'path': document.relative_to(root).as_posix(), 'target': document.parent.name,
                            'database': document.with_suffix('.json').relative_to(root).as_posix(),
                            'updatedAt': None, 'features': 0, 'recaps': 0, 'openChecks': 0,
                            'error': 'The JSON file needs repair; it has been preserved.'})
    return results


def validate(data):
    """Reject invalid saves before they can replace a usable database."""
    def text(value, limit=30000):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('Invalid or oversized notebook text field.')
        return value

    def identifier(value):
        text(value, 100)
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', value) or value in {'__proto__', 'constructor', 'prototype'}:
            raise ValueError('Invalid notebook object identifier.')
        return value

    def array(value, limit):
        if not isinstance(value, list) or len(value) > limit:
            raise ValueError('Invalid or oversized notebook list.')
        return value

    def choices(value, allowed):
        if not isinstance(value, str) or value not in allowed:
            raise ValueError('Invalid notebook status or category.')

    def unique(values):
        if len(values) != len(set(values)):
            raise ValueError('Duplicate notebook object identifiers.')

    if not isinstance(data, dict) or data.get('format') != FORMAT or type(data.get('version')) is not int or data.get('version') != 1:
        raise ValueError('Unsupported notebook format or version.')
    if not text(data.get('target'), 500).strip():
        raise ValueError('The notebook must have a target name.')
    for field, limit in [('programUrl', 2000), ('sourceLabel', 2000), ('inventoryNote', 30000), ('routeNotes', 30000)]:
        text(data.get(field, ''), limit)
    program_url = data.get('programUrl', '')
    if program_url and not re.match(r'^https?://', program_url, flags=re.I):
        raise ValueError('Invalid program engagement URL.')
    if data.get('updatedAt') is not None:
        text(data.get('updatedAt'), 100)
    categories = {'Profile & members', 'Account & security', 'Selling & discovery', 'Shipping & payments', 'Messages & activity', 'Preferences & privacy', 'Other functionality'}
    test_types = {'XSS', 'IDOR', 'OAuth', 'Authentication', 'Privacy', 'CSRF', 'Logic', 'JS review', 'Upload', 'Custom'}
    feature_ids, test_ids = [], []
    for feature in array(data.get('features'), 300):
        if not isinstance(feature, dict):
            raise ValueError('Invalid functionality entry.')
        feature_ids.append(identifier(feature.get('id')))
        text(feature.get('title'), 500)
        text(feature.get('path'), 2000)
        text(feature.get('note', ''))
        choices(feature.get('group'), categories)
        tags = array(feature.get('tags', []), 20)
        for tag in tags:
            if not text(tag, 100).strip():
                raise ValueError('Functionality tags cannot be empty.')
        unique(tags)
        for item in array(feature.get('items'), 100):
            if isinstance(item, str):
                text(item, 2000)
            elif isinstance(item, dict):
                text(item.get('label'), 2000)
                for child in array(item.get('children'), 100):
                    text(child, 2000)
            else:
                raise ValueError('Invalid functionality field or action.')
        for test in array(feature.get('tests'), 100):
            if not isinstance(test, dict):
                raise ValueError('Invalid test entry.')
            test_ids.append(identifier(test.get('id')))
            choices(test.get('type'), test_types)
            text(test.get('title'), 500)
            text(test.get('detail'))
            text(test.get('expected'))
            choices(test.get('ref'), {'', 'idor', 'xss', 'dom', 'oauth', 'reset', 'csrf'})
    unique(feature_ids)
    unique(test_ids)
    for item in array(data.get('inventory', []), 100):
        if not isinstance(item, dict):
            raise ValueError('Invalid inventory entry.')
        text(item.get('label'), 500)
        for address in array(item.get('children'), 2000):
            text(address, 2000)
    for field, limit in [('recaps', 2000), ('tasks', 5000)]:
        ids = []
        for entry in array(data.get(field), limit):
            if not isinstance(entry, dict):
                raise ValueError(f'Invalid {field} entry.')
            ids.append(identifier(entry.get('id')))
            text(entry.get('title'), 500)
            reference = text(entry.get('featureId'), 100)
            if reference and reference not in feature_ids:
                raise ValueError('Unknown functionality reference.')
            text(entry.get('notes'))
            if field == 'recaps':
                date_string = text(entry.get('date'), 10)
                if date.fromisoformat(date_string).isoformat() != date_string:
                    raise ValueError('Invalid session date.')
                text(entry.get('focus'), 200)
                for name in ['summary', 'findings', 'evidence', 'next']:
                    text(entry.get(name))
            else:
                choices(entry.get('priority'), {'high', 'medium', 'low'})
                choices(entry.get('status'), {'todo', 'doing', 'blocked', 'done'})
                text(entry.get('createdAt'), 100)
        unique(ids)
    if not isinstance(data.get('checks'), dict) or not isinstance(data.get('featureNotes'), dict):
        raise ValueError('Invalid check results or feature notes.')
    for key, value in data['featureNotes'].items():
        if key not in feature_ids:
            raise ValueError('Unknown functionality note reference.')
        text(value)
    for key, value in data['checks'].items():
        if key not in test_ids or not isinstance(value, dict):
            raise ValueError('Unknown test result reference.')
        choices(value.get('status'), {'todo', 'doing', 'reviewed', 'lead', 'blocked'})
        text(value.get('result'))
        text(value.get('updatedAt', ''), 100)
    return data


def empty_notebook(document: Path):
    name = re.sub(r'(?:[-_](?:recon[-_]map|notebook|research))$', '', document.stem)
    if document.stem.lower() in {'index', 'template', 'recon-notebook', 'notebook'} or name.lower() in {'index', 'template', 'recon', 'notebook'}:
        name = document.parent.name
    return dict(format=FORMAT, version=1, target=name or 'New target', programUrl='',
                sourceLabel='', inventory=[], inventoryNote='', routeNotes='',
                updatedAt=None, features=[], featureNotes={}, checks={}, recaps=[], tasks=[])


def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def atomic_write(path: Path, content: bytes):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NotebookServer(ThreadingHTTPServer):
    def __init__(self, address, root: Path):
        self.root = root.resolve()
        self.token = secrets.token_urlsafe(32)
        self.database_lock = threading.RLock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: NotebookServer

    def log_message(self, format, *args):
        # Do not print request bodies or research notes.
        print(f'{self.command} {urlparse(self.path).path} → {args[1] if len(args)>1 else ""}', flush=True)

    def send(self, status, content, content_type='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status, data):
        self.send(status, encode(data))

    def allowed_request(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            self.send_json(403, {'error': 'Use the loopback address printed by the launcher.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in hosts}:
            self.send_json(403, {'error': 'Open the notebook through its local launcher URL.'})
            return False
        if self.headers.get('Sec-Fetch-Site') not in {None, 'same-origin', 'none'}:
            self.send_json(403, {'error': 'Cross-site notebook requests are not allowed.'})
            return False
        return True

    def resolve(self, relative):
        if '\\' in relative or '\x00' in relative:
            raise ValueError('Invalid notebook path.')
        path = (self.server.root / relative).resolve()
        if not path.is_relative_to(self.server.root):
            raise ValueError('The notebook must be inside the launcher root folder.')
        return path

    def notebook_path(self):
        query = parse_qs(urlparse(self.path).query)
        relative = query.get('file', [''])[0]
        path = self.resolve(relative)
        if path.suffix.lower() != '.html' or not path.is_file():
            raise ValueError('Notebook HTML file not found.')
        if MARKER not in path.read_text(encoding='utf-8'):
            raise ValueError('This HTML file is not a reusable research notebook template.')
        return path

    def read_database(self, document):
        database = document.with_suffix('.json')
        if database.is_symlink() or not database.resolve().is_relative_to(self.server.root):
            raise ValueError('The JSON database must be a regular file inside the launcher root.')
        created = not database.exists()
        if created:
            atomic_write(database, encode(empty_notebook(document)))
        raw = database.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError('The notebook JSON file is too large.')
        try:
            state = validate(json.loads(raw))
        except (ValueError, TypeError) as error:
            raise ValueError(f'{database.name} could not be read: {error} It was not overwritten. A previous save may be available in {database.name}.bak.') from error
        return database, state, hashlib.sha256(raw).hexdigest(), created

    def do_GET(self):
        if not self.allowed_request():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == '/api/notebooks':
                with self.server.database_lock:
                    self.send_json(200, {'notebooks': list_notebooks(self.server.root), 'token': self.server.token})
                return
            if parsed.path == '/api/notebook':
                with self.server.database_lock:
                    document = self.notebook_path()
                    database, state, revision, created = self.read_database(document)
                    self.send_json(200, {'state': state, 'revision': revision, 'token': self.server.token,
                                         'database': str(database.relative_to(self.server.root)), 'created': created})
                return
            if parsed.path == '/':
                self.send(200, (WEB_ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
                return
            assets = {'/assets/creator.css': ('creator.css', 'text/css; charset=utf-8'),
                      '/assets/creator.js': ('creator.js', 'application/javascript; charset=utf-8')}
            if parsed.path in assets:
                name, content_type = assets[parsed.path]
                self.send(200, (WEB_ROOT / name).read_bytes(), content_type)
                return
            document = self.resolve(unquote(parsed.path).lstrip('/'))
            if document.suffix.lower() != '.html' or not document.is_file() or MARKER not in document.read_text(encoding='utf-8'):
                self.send_json(404, {'error': 'Notebook not found.'})
                return
            self.send(200, document.read_bytes(), 'text/html; charset=utf-8')
        except (OSError, ValueError, UnicodeError) as error:
            self.send_json(400, {'error': str(error)})

    def do_POST(self):
        if not self.allowed_request():
            return
        endpoint = urlparse(self.path).path
        if endpoint not in {'/api/notebook', '/api/notebooks'}:
            self.send_json(404, {'error': 'Endpoint not found.'})
            return
        if not secrets.compare_digest(self.headers.get('X-Notebook-Token', ''), self.server.token):
            self.send_json(403, {'error': 'The local launcher restarted. Reload the notebook before saving.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > MAX_BYTES:
                raise ValueError('Invalid notebook size (maximum 50 MB).')
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                raise ValueError('The notebook must be sent as JSON.')
            data = json.loads(self.rfile.read(length))
            if endpoint == '/api/notebooks':
                if not isinstance(data, dict) or any(not isinstance(data.get(key, ''), str) for key in ['name', 'target', 'programUrl']):
                    raise ValueError('Invalid notebook creation request.')
                with self.server.database_lock:
                    created = create_notebook(self.server.root, data.get('name', ''), data.get('target', ''), data.get('programUrl', ''))
                self.send_json(201, created)
                return
            data = validate(data)
            with self.server.database_lock:
                document = self.notebook_path()
                database, _, revision, _ = self.read_database(document)
                if not secrets.compare_digest(self.headers.get('If-Match', ''), revision):
                    self.send_json(409, {'error': 'This JSON file changed in another browser or editor. Export your unsaved changes before loading the latest file.'})
                    return
                previous = database.read_bytes()
                backup = database.with_name(database.name + '.bak')
                if backup.is_symlink() or not backup.resolve().is_relative_to(self.server.root):
                    raise ValueError('The previous-save backup must be inside the launcher root.')
                content = encode(data)
                atomic_write(backup, previous)
                atomic_write(database, content)
                self.send_json(200, {'revision': hashlib.sha256(content).hexdigest(), 'database': str(database.relative_to(self.server.root))})
        except FileExistsError:
            self.send_json(409, {'error': 'That notebook folder already exists. Choose another name; existing notes were kept.'})
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            self.send_json(400, {'error': str(error)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'Notebook Creator {VERSION}')
    commands = parser.add_subparsers(dest='command')
    serve = commands.add_parser('serve', help='Open the local notebook creator and library.')
    serve.add_argument('--root', type=Path, default=APP_ROOT / 'notebooks', help='Notebook data folder (default: ./notebooks beside this script).')
    serve.add_argument('--port', type=int, default=8765, help='Loopback port (default: 8765).')
    create = commands.add_parser('create', help='Create a notebook from the command line.')
    create.add_argument('name', help='Notebook folder name, e.g. example.')
    create.add_argument('--target', default='', help='Target name or domain (defaults to the folder name).')
    create.add_argument('--program-url', default='', help='Optional program engagement URL.')
    create.add_argument('--root', type=Path, default=APP_ROOT / 'notebooks', help='Notebook data folder.')
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0].startswith('--') and arguments[0] not in {'--help', '--version'}:
        arguments.insert(0, 'serve')
    args = parser.parse_args(arguments)
    if args.command == 'create':
        try:
            result = create_notebook(args.root, args.name, args.target, args.program_url)
        except (OSError, ValueError) as error:
            parser.exit(1, f'Could not create notebook: {error}\n')
        print(f'Created: {args.root.resolve() / result["path"]}\nDatabase: {args.root.resolve() / result["database"]}')
        return
    args.root.mkdir(parents=True, exist_ok=True)
    try:
        server = NotebookServer(('127.0.0.1', args.port), args.root)
    except OSError as error:
        parser.exit(1, f'Could not start the local launcher: {error}\nTry another port with --port 8766.\n')
    print(f'Notebook Creator {VERSION}\nNotebook folder: {server.root}\nOpen: http://127.0.0.1:{server.server_address[1]}/\nKeep this launcher running while you edit. Ctrl+C stops it.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nLauncher stopped. JSON files remain on disk.', flush=True)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
