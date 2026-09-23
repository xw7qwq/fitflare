"""Offline contract coverage; synthetic profiles only, no real Fitbit requests."""
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from common import api_docs, public_api
from tests import test_backend as backend_tests


def assert_schema(test, value, schema, spec):
    """Check the JSON Schema subset emitted by our catalog against actual responses."""
    if '$ref' in schema:
        node = spec
        for part in schema['$ref'].removeprefix('#/').split('/'):
            node = node[part]
        return assert_schema(test, value, node, spec)
    for child in schema.get('allOf', []):
        assert_schema(test, value, child, spec)
    if 'anyOf' in schema:
        for child in schema['anyOf']:
            try:
                assert_schema(test, value, child, spec)
                break
            except AssertionError:
                pass
        else:
            test.fail('Value matches no documented alternative')
    if 'not' in schema:
        with test.assertRaises(AssertionError):
            assert_schema(test, value, schema['not'], spec)
    if 'const' in schema:
        test.assertEqual(value, schema['const'])
    if 'enum' in schema:
        test.assertIn(value, schema['enum'])
    types = {'object':dict, 'array':list, 'string':str, 'integer':int, 'null':type(None)}
    if 'type' in schema:
        names = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        test.assertIsInstance(value, tuple(types[name] for name in names))
    if isinstance(value, dict):
        for key in schema.get('required', []):
            test.assertIn(key, value)
        for key, child in schema.get('properties', {}).items():
            if key in value:
                assert_schema(test, value[key], child, spec)
    if isinstance(value, list):
        for item in value:
            assert_schema(test, item, schema.get('items', {}), spec)


class DocsTests(unittest.TestCase):
    setUp = backend_tests.BackendTests.setUp
    login = backend_tests.BackendTests.login

    def test_catalog_covers_all_versioned_get_routes(self):
        actual = {re.sub(r'<(?:[^:<>]+:)?([^<>]+)>', r'{\1}', rule.rule)
                  for rule in self.app.url_map.iter_rules() if rule.rule.startswith(api_docs.BASE) and '/profiles' not in rule.rule and 'GET' in rule.methods}
        spec = api_docs.build_openapi_spec()
        self.assertEqual(set(spec['paths']), actual)
        self.assertEqual(len(actual), 24)
        ids = [item['id'] for item in api_docs.ENDPOINTS]
        self.assertEqual(len(ids), len(set(ids)))
        for path, item in spec['paths'].items():
            declared = {p['name'] for p in item['get']['parameters'] if p['in'] == 'path'}
            self.assertEqual(set(re.findall(r'{(\w+)}', path)), declared)
            self.assertTrue(all(p['required'] for p in item['get']['parameters'] if p['in'] == 'path'))

    def test_all_documented_examples_match_actual_response_shapes(self):
        snapshot = self.root / 'profiles/Demo/cache/fitbit_profile_snapshot.json'
        snapshot.write_text(json.dumps({'endpoints':{'profile':{'ok':True,'data':{'displayName':'Synthetic'}}}}))
        os.utime(snapshot, (1, 1))
        rows = [dict(date=f'2026-01-{i:02d}', steps=i*100, hrv=50, sleep_hours=7) for i in range(1, 31)]
        spec = api_docs.build_openapi_spec()
        with patch.object(public_api, 'load_dataset_rows', return_value=rows):
            for item in api_docs.ENDPOINTS:
                path = api_docs.example_path(item)
                with self.subTest(path=path), self.client.get(path) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.mimetype, item['media'])
                    if response.is_json:
                        assert_schema(self, response.json, spec['paths'][item['path']]['get']['responses']['200']['content']['application/json']['schema'], spec)

    def test_documentation_does_not_discover_or_disclose_profiles(self):
        self.app.config['DATA_ACCESS_MODE'] = 'private'
        with patch('backend.repository.visible_profile_ids', side_effect=AssertionError('No profile reads in docs')):
            for suffix in ('docs','docs.md','openapi.json'):
                text = self.client.get(api_docs.BASE+'/'+suffix).get_data(as_text=True)
                self.assertNotIn('Hidden', text)
                self.assertNotIn('Demo', text)
                self.assertNotIn('/private/', text)
        self.assertEqual(self.client.get(api_docs.BASE+'/profiles').status_code, 401)

    def test_markdown_is_generated_from_catalog_and_served_without_cwd_dependency(self):
        expected = api_docs.render_markdown()
        self.assertEqual(self.client.get(api_docs.BASE+'/docs.md').get_data(as_text=True), expected)
        checked_in = Path(__file__).resolve().parents[1] / 'API.md'
        self.assertEqual(checked_in.read_text(), expected)
        for item in api_docs.ENDPOINTS:
            self.assertIn(item['path'], expected)

    def test_private_spec_declares_session_and_public_docs(self):
        self.app.config['DATA_ACCESS_MODE'] = 'private'
        spec = self.client.get(api_docs.BASE+'/openapi.json').json
        self.assertEqual(spec['security'], [{'SessionCookie': []}])
        self.assertEqual(spec['servers'], [{'url':'/'}])
        for suffix in ('docs','docs.md','openapi.json'):
            self.assertEqual(spec['paths'][api_docs.BASE+'/'+suffix]['get']['security'], [])
        self.app.config['DATA_ACCESS_MODE'] = 'public'
        self.assertIn({}, self.client.get(api_docs.BASE+'/openapi.json').json['security'])

    def test_api_parameter_bounds_and_limit_semantics(self):
        rows = [dict(date=f'2026-01-{i:02d}', steps=i) for i in range(1,31)]
        with patch.object(public_api, 'load_dataset_rows', return_value=rows):
            base = api_docs.BASE+'/profiles/Demo'
            data = self.client.get(base+'/datasets/activity?offset=2&limit=3').json
            self.assertEqual([r['steps'] for r in data['data']['rows']], [3,4,5])
            data = self.client.get(base+'/series/daily?limit=3').json
            self.assertEqual([r['steps'] for r in data['data']['points']], [28,29,30])
            fallback = self.client.get(base+'/series/daily?metrics=not_a_metric').json
            self.assertIn('steps', fallback['meta']['available_metrics'])
            self.assertEqual(len(fallback['data']['points']), 30)
            page = self.client.get(base+'/datasets/activity?offset=999999&limit=999999').json['meta']
            self.assertEqual((page['offset'],page['limit'],page['count']), (100000,1000,0))
            self.assertEqual(self.client.get(base+'/datasets/activity?limit=invalid').json['meta']['limit'], 200)
            self.assertEqual(self.client.get(base+'/tables/sleep?limit=invalid').json['meta']['limit'], 100)

    def test_errors_match_both_existing_shapes(self):
        spec = api_docs.build_openapi_spec()
        for path in ('/profiles/Hidden', '/profiles/Demo/metrics/not_a_metric'):
            response = self.client.get(api_docs.BASE+path)
            self.assertEqual(response.status_code, 404)
            assert_schema(self, response.json, spec['components']['schemas']['Error'], spec)
        self.app.config['DATA_ACCESS_MODE'] = 'private'
        response = self.client.get(api_docs.BASE+'/profiles')
        self.assertEqual(response.status_code, 401)
        assert_schema(self, response.json, spec['components']['schemas']['Error'], spec)

    def test_docs_assets_and_content_policy(self):
        with self.client.get(api_docs.BASE+'/docs', base_url='https://docs.example') as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("connect-src 'self'", response.headers['Content-Security-Policy'])
            self.assertNotIn('unsafe-inline', response.headers['Content-Security-Policy'])
            self.assertNotIn('<style>', response.get_data(as_text=True))
        for asset in ('/docs.css','/js/docs.js','/js/docs-request.js'):
            with self.client.get(asset) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn('no-cache', response.headers['Cache-Control'])
        self.assertEqual(self.client.get('/common/api_docs.py').status_code, 404)

    def test_schema_references_and_synthetic_example(self):
        spec = api_docs.build_openapi_spec()
        def walk(node):
            if isinstance(node, dict):
                if '$ref' in node:
                    target = spec
                    for part in node['$ref'].removeprefix('#/').split('/'):
                        target = target[part]
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(spec)
        item = next(item for item in api_docs.ENDPOINTS if item['id'] == 'series')
        assert_schema(self, api_docs.EXAMPLE_ENVELOPE, api_docs.response_schema(item), spec)
