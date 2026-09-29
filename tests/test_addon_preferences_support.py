"""Run in Blender --background --factory-startup --python-exit-code 1.

Check the installed-addon metadata and the links drawn in its preferences.
"""

import importlib
from pathlib import Path
from types import SimpleNamespace
import sys

import addon_utils
import bpy


class RecordingLayout:
    def __init__(self, calls):
        self.calls = calls

    def split(self, **kwargs):
        return RecordingLayout(self.calls)

    def column(self, **kwargs):
        return RecordingLayout(self.calls)

    def row(self, **kwargs):
        return RecordingLayout(self.calls)

    def box(self, **kwargs):
        return RecordingLayout(self.calls)

    def operator(self, identifier, **kwargs):
        operator = SimpleNamespace(url=None)
        self.calls.append(('operator', identifier, kwargs, operator))
        return operator

    def label(self, **kwargs):
        self.calls.append(('label', kwargs))

    def prop(self, *args, **kwargs):
        pass

    def template_list(self, *args, **kwargs):
        pass


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo.parent))
    addon_utils.enable(repo.name, default_set=True)
    addon = importlib.import_module(repo.name)

    assert addon.bl_info['author'] == 'ZZtaii', addon.bl_info['author']
    assert addon.bl_info['version'][:2] == (0, 68), addon.bl_info['version']

    calls = []
    preferences = SimpleNamespace(
        layout=RecordingLayout(calls),
        textureCachePath='',
        textureCacheCheckDate='checked',
        textureCacheSizeString='0 B',
        showImportOptions=False,
        showExportOptions=False,
    )
    addon.REMeshPreferences.draw(preferences, bpy.context)

    links = [call for call in calls if call[0] == 'operator' and call[1] == 'wm.url_open']
    ko_fi = next(call for call in links if call[2].get('text') == 'Donate on Ko-fi')
    coffee = next(call for call in links if call[2].get('text') == 'Buy me a coffee')
    assert ko_fi[3].url == 'https://ko-fi.com/nsacloud', ko_fi[3].url
    assert coffee[3].url == 'https://www.patreon.com/ZZtai/posts/buy-me-coffee-167011360', coffee[3].url
    assert links.index(ko_fi) < links.index(coffee), 'Maintainer button should appear below the original creator button'

    labels = [call[1].get('text') for call in calls if call[0] == 'label']
    assert '(NSA Cloud - Original Creator)' in labels, labels
    assert '(ZZtaii - Fork Maintainer)' in labels, labels
    print('ADDON_PREFERENCES_SUPPORT_PASSED', flush=True)


if __name__ == '__main__':
    main()
