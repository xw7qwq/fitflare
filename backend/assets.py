"""Content-fingerprinted URLs for local scripts and styles.

Cloudflare rewrites our no-cache headers into a four-hour browser cache, so a
deploy could pair a fresh page with stale modules. Every reference to a local
.js/.css file is rewritten to carry a hash of the referenced file's rewritten
content. A change anywhere in the import graph therefore changes every URL that
leads to it, and fingerprinted URLs can be cached for good.
"""
import hashlib
import posixpath
import re
from pathlib import Path

FINGERPRINTED = ('.js', '.css')
# Import specifiers and HTML src/href attributes; an existing ?v= is replaced.
REFERENCE = re.compile(r"""(?P<prefix>\bfrom\s*|\bimport\s*\(\s*|\b(?:src|href)=)(?P<quote>["'])(?P<url>[^"'?#]+)(?:\?v=[^"'#]*)?(?P=quote)""")


class AssetManifest:
    def __init__(self, root, files):
        self.root = Path(root)
        self.files = frozenset(files)
        self._rendered = {}
        self._versions = {}

    def _target(self, url, referrer):
        if url.startswith(('http:', 'https:', '//', 'data:', '#')):
            return None
        base = '' if url.startswith('/') else posixpath.dirname(referrer)
        target = posixpath.normpath(posixpath.join(base, url.lstrip('/')))
        if target in self.files and target.endswith(FINGERPRINTED) and not target.startswith('vendor/'):
            return target
        return None

    def render(self, name, _stack=()):
        """Return the file's text with local asset references fingerprinted."""
        if name in self._rendered:
            return self._rendered[name]
        if name in _stack:
            raise ValueError(f'Circular asset reference: {" -> ".join((*_stack, name))}')
        text = (self.root / name).read_text(encoding='utf-8')

        def rewrite(match):
            target = self._target(match['url'], name)
            if target is None:
                return match.group(0)
            version = self._version(target, (*_stack, name))
            return f"{match['prefix']}{match['quote']}{match['url']}?v={version}{match['quote']}"

        self._rendered[name] = REFERENCE.sub(rewrite, text)
        return self._rendered[name]

    def _version(self, name, stack=()):
        if name not in self._versions:
            self._versions[name] = hashlib.sha256(self.render(name, stack).encode('utf-8')).hexdigest()[:12]
        return self._versions[name]

    def version(self, name):
        return self._version(name)

    def url(self, name):
        """Fingerprinted absolute URL for templates, e.g. url('docs.css')."""
        return f'/{name}?v={self.version(name)}'

    def handles(self, name):
        return name in self.files and name.endswith((*FINGERPRINTED, '.html')) and not name.startswith('vendor/')
