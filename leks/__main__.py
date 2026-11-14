"""Command line entry: python3 -m leks <command>, or the `leks` script.

Each command imports its module inside its own branch, so a missing optional
dependency never breaks the other commands. Hook commands never raise and
always exit 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

USAGE_EXIT = 2
_PROFILE_ARG = []   # filled when --profile was given on this command line


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def _err(msg: str) -> None:
    print('leks: ' + msg, file=sys.stderr)


def _log(*a) -> None:
    # progress goes to stderr so stdout stays clean json
    print(*a, file=sys.stderr)


def _set_global(flag, value):
    if flag == '--profile':
        os.environ['LEKS_PROFILE'] = value
        _PROFILE_ARG[:] = [value]
    else:
        os.environ['LEKS_CONFIG'] = value


def _pull_globals(argv):
    """--profile P and --config PATH may sit anywhere on the line."""
    rest, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ('--profile', '--config') and i + 1 < len(argv):
            _set_global(a, argv[i + 1])
            i += 2
            continue
        if a.startswith('--profile=') or a.startswith('--config='):
            k, v = a.split('=', 1)
            _set_global(k, v)
            i += 1
            continue
        rest.append(a)
        i += 1
    return rest


def _parser():
    p = argparse.ArgumentParser(prog='leks', description='Local search over a project\'s own record.')
    sub = p.add_subparsers(dest='cmd', metavar='command')

    s = sub.add_parser('init', help='write a starter config')
    s.add_argument('--home', default=None)

    s = sub.add_parser('config', help='check or show the config')
    s.add_argument('action', choices=('check', 'show'))

    s = sub.add_parser('build', help='update the index')
    s.add_argument('--force', action='store_true')

    s = sub.add_parser('search', help='search the index')
    s.add_argument('query', nargs='+')
    s.add_argument('--k', type=int, default=5)
    s.add_argument('--json', action='store_true')
    s.add_argument('--words-only', action='store_true')

    s = sub.add_parser('read', help='read cited lines or pages')
    s.add_argument('path')
    s.add_argument('start', nargs='?', type=int, default=1)
    s.add_argument('end', nargs='?', type=int, default=None)

    s = sub.add_parser('locate', help='find files and definitions by name')
    s.add_argument('query', nargs='+')

    s = sub.add_parser('tried', help='was this proposal tried before')
    s.add_argument('proposal', nargs='+')

    s = sub.add_parser('neighbours', help='the notes to bring in line after editing a note',
                       description='The notes to bring in line after editing a note or code file: notes that '
                                   'still hold numbers or sentences it no longer has, notes in its supersession '
                                   'chain, notes that link to it, files it links to, and the closest files in '
                                   'meaning (in words without vectors), each with its line and the reason.')
    s.add_argument('path', help='the edited file, as search cites it or as a path on disk')
    s.add_argument('--was', metavar='FILE', default=None, help='a file with the text the note had before the edit')
    s.add_argument('--n', type=int, default=10, help='how many notes, at most 30')
    s.add_argument('--json', action='store_true')

    s = sub.add_parser('revisit', help='list the revisit dates and conditions of decisions and notes',
                       description='Every Revisit: line in the notes of the profile: due ones (the date has '
                                   'come) oldest first, then dates ahead, then conditions. Notes marked '
                                   'superseded or in a history folder are left out.')
    s.add_argument('--due', action='store_true', help='only the lines whose date has come')
    s.add_argument('--by', metavar='YYYY-MM-DD', default=None,
                   help='count a date as due when it is on or before this day, default today')
    s.add_argument('--json', action='store_true')

    sub.add_parser('state', help='list the current state notes')
    sub.add_parser('status', help='index size, models and lanes')
    sub.add_parser('serve', help='run the MCP server on stdio')

    sub.add_parser('embed', help='fill the vector store (--profile P limits it to one profile)')

    s = sub.add_parser('modeld', help='model daemon')
    s.add_argument('action', choices=('serve', 'start', 'stop', 'ping'))

    s = sub.add_parser('models', help='model weights')
    s.add_argument('action', choices=('fetch', 'check'))

    s = sub.add_parser('ocr', help='ocr helper')
    s.add_argument('action', choices=('build',))

    s = sub.add_parser('check', help='check changed notes: empty, dead links, orphans, em dashes, edit contracts '
                                     '(exit 2 lists them)',
                       description='Checks the notes changed since the last run: empty notes, dead links, new '
                                   'notes nothing links to, em dashes, and the edit contracts of check.contracts '
                                   'in the config (write-once, frozen and living notes, fields a note keeps, a '
                                   'successor for a superseded note). Each finding is path:line: message.')
    s.add_argument('--root', action='extend', nargs='+', metavar='DIR', default=None,
                   help='notes folder to check, the current folder by default')
    s.add_argument('--changed', action='extend', nargs='+', metavar='FILE', default=None,
                   help='check these notes instead of the ones changed since the last check')
    s.add_argument('--no-em-dash', action='store_true', help='do not flag em dashes')
    s.add_argument('--accept', action='extend', nargs='+', metavar='FILE', default=None,
                   help='keep the current text of these write-once or frozen notes, nothing is checked')

    s = sub.add_parser('hook', help='editor hooks, never fail',
                       description='brief: the session brief (state notes, recent changes, notes past their '
                                   'freshness budget, revisit dates that have come). update: a background '
                                   'index update when one is due. warm: start and warm the model daemon.')
    s.add_argument('action', choices=('brief', 'warm', 'update'))

    sub.add_parser('doctor', help='check the install: python, config, index, model transport, optional parts')
    return p


def _cmd_init(a) -> int:
    from . import config
    home = os.path.abspath(os.path.expanduser(a.home)) if a.home else config.home_dir(None)
    target = os.path.join(home, 'config.json')
    if os.path.exists(target):
        _out({'config': target, 'created': False})
        return 0
    d = config.default_dict()
    if a.home:
        d['home'] = home
    os.makedirs(home, exist_ok=True)
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
        f.write('\n')
    _out({'config': target, 'created': True})
    return 0


def _cmd_config(a) -> int:
    from . import config
    cfg = config.load()
    if a.action == 'show':
        _out(cfg.to_dict())
        return 0
    warn = [r.name + ': folder not found' for r in cfg.roots if not os.path.isdir(r.path)]
    config.active_profile(cfg)
    _out({'ok': True, 'config': cfg.source_path, 'roots': len(cfg.roots),
          'profiles': sorted(cfg.profiles), 'warnings': warn})
    return 0


def _cmd_build(a) -> int:
    from . import config, build
    cfg = config.load()
    if not any(os.path.isdir(r.path) and os.access(r.path, os.R_OK | os.X_OK) for r in cfg.roots):
        # an empty index would look like a working one, so stop here
        _err('nothing to build: none of the configured roots exists or is readable (%s); fix the paths in the '
             'config, leks config check shows which' % ', '.join(r.name for r in cfg.roots))
        return 1
    _out(build.update_all(cfg, a.force, log=_log))
    return 0


# what a start word means for the user, and what they can do
_NOT_UP = {
    'no backend': 'model backend not installed, run: pip install -e ".[models]" in the LEKS clone',
    'no weights': 'model weights not found, run: leks models fetch',
    'locked': 'another start is under way, search again in a minute',
}


def _meaning_ready(cfg, words_only=False):
    """Start the model daemon like the MCP hook does. None when it is up, else why search runs on words."""
    if words_only or (cfg.embedder is None and cfg.reranker is None):
        return None
    from .hooks import warm_models
    # waits for the socket and warms the models, so the search below meets them warm
    got = warm_models.start(cfg, wait=True)
    if got == 'locked' and warm_models._wait_up(cfg, warm_models.SOCKET_WAIT):
        got = 'up'
    if got == 'up' or (got == 'started' and warm_models._ping(cfg)):
        return None
    return 'model daemon not started (%s)' % _NOT_UP.get(got, 'it did not come up, run leks modeld serve to see why')


def _cmd_search(a) -> int:
    from . import config, query
    cfg = config.load()
    eng = query.Engine(cfg)
    try:
        eng.words_why = _meaning_ready(cfg, a.words_only)
        r = eng.search(' '.join(a.query), k=max(1, min(a.k, 12)), words_only=a.words_only)
        if a.json:
            _out(eng.public(r))
        else:
            print(eng.render(r))
    finally:
        eng.close()
    return 0


def _cmd_read(a) -> int:
    from . import config, query
    eng = query.Engine(config.load())
    _out(eng.read(a.path, a.start, a.end))
    return 0


def _cmd_locate(a) -> int:
    from . import config, query
    _out(query.Engine(config.load()).locate(' '.join(a.query)))
    return 0


def _cmd_tried(a) -> int:
    from . import config, query
    cfg = config.load()
    eng = query.Engine(cfg)
    try:
        eng.words_why = _meaning_ready(cfg)
        print(eng.tried(' '.join(a.proposal)))
    finally:
        eng.close()
    return 0


def _cmd_neighbours(a) -> int:
    from . import config, query
    cfg = config.load()
    path = a.path
    if os.path.exists(path):
        # a file named from the shell: its path under a root, else the words as given
        path = cfg.display_path(os.path.abspath(path)) or path
    was = None
    if a.was:
        try:
            with open(a.was, 'rb') as f:
                was = f.read(query.MAX_WAS).decode('utf-8', errors='replace')
        except OSError as e:
            _err('cannot read the --was file (%s)' % (e.strerror or type(e).__name__))
            return 1
    eng = query.Engine(cfg)
    try:
        r = eng.neighbours(path, n=a.n, was=was)
        if a.json:
            _out(r)
        else:
            print(eng.render_neighbours(r))
    finally:
        eng.close()
    return 0


def _cmd_revisit(a) -> int:
    import datetime
    from . import config, revisit, schema
    cfg = config.load()
    profile = config.active_profile(cfg)
    today = datetime.date.today().isoformat()
    if a.by:
        try:
            today = datetime.date.fromisoformat(a.by).isoformat()
        except ValueError:
            _err('--by takes a date as YYYY-MM-DD')
            return 1
    db = cfg.profile_db(profile)
    if not os.path.isfile(db):
        _err('no index for profile %s yet, run leks build' % profile)
        return 1
    c = schema.open_ro(db)
    try:
        found = revisit.entries(c, cfg, today)
    finally:
        c.close()
    if a.due:
        found = [e for e in found if e['state'] == 'due']
    if a.json:
        _out({'profile': profile, 'today': today, 'revisit': found})
    else:
        print(revisit.render(found, today, profile))
    return 0


def _cmd_state(a) -> int:
    from . import config, query
    _out(query.Engine(config.load()).state())
    return 0


def _cmd_status(a) -> int:
    from . import config, query
    eng = query.Engine(config.load())
    try:
        _out(eng.status())
    finally:
        eng.close()
    return 0


def _cmd_serve(a) -> int:
    from . import server
    server.main()
    return 0


def _cmd_embed(a) -> int:
    from . import config, embed_all
    cfg = config.load()
    prof = _PROFILE_ARG[0] if _PROFILE_ARG else None
    if cfg.embedder is not None:
        # bring the daemon up first, embed_all only talks to a daemon that already runs
        from .hooks import warm_models
        got = warm_models.start(cfg, wait=True)
        if got not in ('up', 'started'):
            print('model daemon not started: ' + got, file=sys.stderr)
    return int(embed_all.run(cfg, prof) or 0)


def _cmd_modeld(a) -> int:
    from . import config
    cfg = config.load()
    if a.action == 'serve':
        from . import modeld
        modeld.main(cfg)
        return 0
    if a.action == 'start':
        from .hooks import warm_models
        _out({'modeld': warm_models.start(cfg)})
        return 0
    if a.action == 'stop':
        from .hooks import warm_models
        # straight to the socket: a missing socket answers at once, no connect retries
        if not warm_models._ping(cfg):
            _out({'modeld': 'not running'})
            return 0
        warm_models._ask(cfg.socket_path(), {'op': 'bye'}, 5.0)
        _out({'modeld': 'stop sent'})
        return 0
    from . import modeld_client
    cl = modeld_client.Client.from_config(cfg)
    up = bool(cl.ping())
    _out({'modeld': 'up' if up else 'down'})
    return 0 if up else 1


def _cmd_models(a) -> int:
    from . import config, models
    cfg = config.load()
    _out(models.fetch(cfg) if a.action == 'fetch' else models.check_all(cfg))
    return 0


def _cmd_ocr(a) -> int:
    from . import config, docs
    ok, msg = docs.build_helper(config.load())
    print(msg)
    return 0 if ok else 1


def _cmd_check(a) -> int:
    from . import check
    if a.accept:
        return check.accept(a.accept, a.root)
    return check.host_main(a.root, a.changed, em_dash=not a.no_em_dash)


def _cmd_hook(a) -> int:
    # hooks must never break the host: swallow everything, exit 0
    try:
        if a.action == 'brief':
            from .hooks import session_brief as mod
        elif a.action == 'warm':
            from .hooks import warm_models as mod
        else:
            from .hooks import update_index as mod
        mod.main()
    except BaseException:
        pass
    return 0


def _found(name):
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _cmd_doctor(a) -> int:
    """What this install can do, as json. Exit 0 when the config loads, 1 when it does not."""
    import platform
    from . import __version__, sandbox, transport
    rep = {
        'leks': __version__,
        'python': platform.python_version(),
        'system': sys.platform,
        'model_transport': transport.kind(),
        'pdf': 'pypdf found' if _found('pypdf') else 'pypdf not installed, PDFs are skipped',
        'numpy': _found('numpy'),
        'model_backend': all(_found(m) for m in ('torch', 'transformers', 'safetensors')),
    }
    ok, why = sandbox.available()
    rep['sandbox'] = 'sandbox-exec' if ok else why
    from . import config
    try:
        cfg = config.load()
    except Exception as e:
        rep['config'] = {'ok': False, 'why': str(e).split('\n')[0]}
        _out(rep)
        return 1
    rep['config'] = {'ok': True, 'path': cfg.source_path, 'roots': [r.name for r in cfg.roots],
                     'profiles': sorted(cfg.profiles)}
    idx = {}
    for prof in sorted(cfg.profiles):
        try:
            idx[prof] = os.path.isfile(cfg.profile_db(prof))
        except Exception:
            idx[prof] = False
    rep['index'] = {'built': os.path.isfile(cfg.master_path()), 'profiles': idx}
    from . import models
    specs = [s for s in (cfg.embedder, cfg.reranker) if s is not None]
    rep['models'] = {s.key: ('weights found' if models.weights_present(cfg, s.key) else 'no weights, run leks models fetch')
                     for s in specs} or 'none configured, search runs on words'
    _out(rep)
    return 0


_COMMANDS = {
    'init': _cmd_init, 'config': _cmd_config, 'build': _cmd_build, 'search': _cmd_search,
    'read': _cmd_read, 'locate': _cmd_locate, 'tried': _cmd_tried, 'neighbours': _cmd_neighbours,
    'revisit': _cmd_revisit, 'state': _cmd_state,
    'status': _cmd_status, 'serve': _cmd_serve, 'embed': _cmd_embed, 'modeld': _cmd_modeld,
    'models': _cmd_models, 'ocr': _cmd_ocr, 'check': _cmd_check, 'hook': _cmd_hook,
}

_COMMANDS['doctor'] = _cmd_doctor


def _utf8_streams():
    # windows pipes default to the ansi code page, which cannot hold every note title
    if os.name != 'nt':
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main(argv=None) -> int:
    _utf8_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    _PROFILE_ARG[:] = []
    argv = _pull_globals(argv)
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        # check runs as a stop hook, where 2 means "hold the turn": a bad flag must not hold every turn
        if argv[:1] == ['check'] and e.code == USAGE_EXIT:
            return 1
        raise
    if not args.cmd:
        _parser().print_usage(sys.stderr)
        return USAGE_EXIT
    fn = _COMMANDS[args.cmd]
    if args.cmd == 'hook':
        return fn(args)
    try:
        return fn(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        # config and model-check problems carry a readable message, everything else only its type
        from .config import ConfigError
        from .models import ModelCheck
        readable = isinstance(e, (ConfigError, ModelCheck))
        _err(str(e) if readable else '%s (%s)' % (type(e).__name__, args.cmd))
        return 1


if __name__ == '__main__':
    sys.exit(main())
