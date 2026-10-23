"""Fingerprinted asset URLs: caches can never pair a new page with stale modules."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.assets import AssetManifest

os.environ['FITFLARE_AUTO_SYNC_ENABLED'] = 'false'
with patch.dict(os.environ, {'FITFLARE_DATA_ACCESS': 'public'}):
    from server import create_app



class AssetManifestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'js').mkdir()
        (self.root / 'vendor').mkdir()
        self.write('index.html', '<link href="/site.css"><script src="/vendor/lib.js"></script>'
                                 '<script src="/app.js?v=old"></script><a href="https://example.test/x.js">x</a>')
        self.write('site.css', 'body{}')
        self.write('app.js', "import { a } from './js/a.js';\nimport {\n  b,\n} from './js/b.js';\n")
        self.write('js/a.js', "import { b } from './b.js';\nexport const a = b;")
        self.write('js/b.js', 'export const b = 1;')
        self.write('vendor/lib.js', '/* pinned */')
        self.files = {'index.html', 'site.css', 'app.js', 'js/a.js', 'js/b.js', 'vendor/lib.js'}

    def write(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8')

    def manifest(self):
        return AssetManifest(self.root, self.files)

    def test_references_are_fingerprinted_and_external_or_vendor_urls_are_left_alone(self):
        manifest = self.manifest()
        page = manifest.render('index.html')
        self.assertIn(f'href="/site.css?v={manifest.version("site.css")}"', page)
        self.assertIn(f'src="/app.js?v={manifest.version("app.js")}"', page)
        self.assertNotIn('v=old', page)
        self.assertIn('src="/vendor/lib.js"', page)
        self.assertIn('href="https://example.test/x.js"', page)
        app = manifest.render('app.js')
        self.assertIn(f"from './js/a.js?v={manifest.version('js/a.js')}'", app)
        self.assertIn(f"from './js/b.js?v={manifest.version('js/b.js')}'", app)
        self.assertIn(f"from './b.js?v={manifest.version('js/b.js')}'", manifest.render('js/a.js'))

    def test_a_change_deep_in_the_import_graph_changes_every_url_above_it(self):
        before = self.manifest()
        before.render('index.html')  # Versions are computed lazily; capture them before the edit.
        self.write('js/b.js', 'export const b = 2;')
        after = self.manifest()
        for name in ('js/b.js', 'js/a.js', 'app.js'):
            self.assertNotEqual(before.version(name), after.version(name), name)
        self.assertNotEqual(before.render('index.html'), after.render('index.html'))
        self.assertEqual(before.version('site.css'), after.version('site.css'))

    def test_circular_references_are_reported(self):
        self.write('js/b.js', "import { a } from './a.js';\nexport const b = a;")
        with self.assertRaises(ValueError):
            self.manifest().render('app.js')


class AssetRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(dict(TESTING=True, DATA_ACCESS_MODE='public'))
        self.client = self.app.test_client()
        self.assets = self.app.extensions['assets']

    def test_only_current_fingerprints_are_cached_indefinitely(self):
        version = self.assets.version('app.js')
        page = self.client.get('/')
        self.assertIn(f'/app.js?v={version}', page.get_data(as_text=True))
        self.assertIn('no-cache', page.headers['Cache-Control'])
        current = self.client.get(f'/app.js?v={version}')
        self.assertEqual(current.status_code, 200)
        self.assertIn('immutable', current.headers['Cache-Control'])
        self.assertTrue(current.mimetype.endswith('javascript'))
        self.assertIn(f"./js/views.js?v={self.assets.version('js/views.js')}", current.get_data(as_text=True))
        for stale in ('/app.js', '/app.js?v=000000000000'):
            self.assertIn('no-cache', self.client.get(stale).headers['Cache-Control'], stale)
        revalidated = self.client.get('/js/format.js', headers={'If-None-Match': f'"{self.assets.version("js/format.js")}"'})
        self.assertEqual(revalidated.status_code, 304)

    def test_section_items_expose_key_like_other_catalogs(self):
        from common.public_api import build_section_payload
        payload = build_section_payload({'sections': {'activity': {'metrics': []}}}, 'activity')
        self.assertEqual((payload['key'], payload['section']), ('activity', 'activity'))

    def test_docs_page_uses_fingerprinted_assets(self):
        html = self.client.get('/api/public/v1/docs').get_data(as_text=True)
        self.assertIn(self.assets.url('docs.css'), html)
        self.assertIn(self.assets.url('js/docs.js'), html)


if __name__ == '__main__':
    unittest.main()
