"""Import every module of the leks package and say which ones could not load.

A module may stay unloaded only when an optional extra is missing (the model
extra or the pdf extra); any other failure at import time fails this script.
Used by the windows job, where a posix-only call at import time would break the
whole package. Standard library only.
"""
import importlib
import os
import pkgutil
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# third-party packages that only the optional extras bring
OPTIONAL = {'torch', 'transformers', 'safetensors', 'tokenizers', 'huggingface_hub', 'numpy', 'pypdf'}


def main():
    sys.path.insert(0, ROOT)
    import leks
    names = ['leks'] + sorted(m.name for m in pkgutil.walk_packages(leks.__path__, 'leks.'))
    bad, optional = [], []
    for name in names:
        try:
            importlib.import_module(name)
        except ImportError as e:
            top = (e.name or '').split('.')[0]
            if top in OPTIONAL:
                optional.append('%s (needs %s)' % (name, top))
            else:
                bad.append((name, traceback.format_exc()))
        except Exception:
            bad.append((name, traceback.format_exc()))
    print('imported %d of %d modules on %s, Python %s' % (len(names) - len(bad) - len(optional), len(names),
                                                         sys.platform, sys.version.split()[0]))
    for o in optional:
        print('  not loaded, optional extra missing: %s' % o)
    for name, tb in bad:
        print('FAILED %s\n%s' % (name, tb))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
