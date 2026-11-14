"""Quality gate over a bench/run_bench.py result file.

    python bench/gate.py RESULTS --baseline bench/baseline_words.json
    python bench/gate.py RESULTS --baseline bench/baseline_models.json --require hybrid,full --lanes
    python bench/gate.py RESULTS --write-baseline FILE [--tolerance-questions N]

RESULTS is a results_*.json file or a folder; for a folder the newest
results_*.json in it is read.

Exit 0: the gate holds. 1: it does not, every reason is printed. 2: bad input
(a missing or broken result file, a --baseline file that is missing or broken,
a baseline made for another --models mode).

With no --baseline the gate checks only that the required systems ran and,
with --lanes, that the meaning lane and the reranker really ran. It says so loudly and prints the baseline it would write, so the
first run can fill it (docs/testing.md has the steps).

Standard library only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import sys

METRICS = ('top1', 'top5', 'mrr10')
# lanes a model system must report, in the engine's own words
MODEL_LANES = {'hybrid': ('words', 'meaning'), 'full': ('words', 'meaning', 'reranker')}
EPS = 1e-9


class BadInput(Exception):
    """A file the gate cannot judge."""


def in_actions():
    return os.environ.get('GITHUB_ACTIONS') == 'true'


def say(kind, msg):
    """One line on stdout; inside GitHub Actions errors and notices also become annotations."""
    if in_actions() and kind in ('error', 'warning'):
        print('::%s title=bench gate::%s' % (kind, msg.replace('\n', ' ')))
    print('%s: %s' % (kind.upper() if kind != 'ok' else 'ok', msg))


def newest(path):
    if os.path.isdir(path):
        found = sorted(glob.glob(os.path.join(path, 'results_*.json')))
        if not found:
            raise BadInput('no results_*.json in %s' % path)
        return found[-1]
    return path


def load_json(path, what):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except OSError as e:
        raise BadInput('%s not readable: %s (%s)' % (what, path, e.strerror or type(e).__name__)) from None
    except ValueError:
        raise BadInput('%s is not valid json: %s' % (what, path)) from None
    if not isinstance(d, dict):
        raise BadInput('%s is not a json object: %s' % (what, path))
    return d


def pct(x):
    return '%.1f' % (100.0 * x) if isinstance(x, (int, float)) else str(x)


def check_ran(systems, names, lanes, meta):
    """Problems with systems that must have run, as a list of lines."""
    bad = []
    for name in names:
        s = systems.get(name)
        if not isinstance(s, dict):
            bad.append('%s: not in this run' % name)
            continue
        if s.get('status') != 'ok':
            bad.append('%s: did not run (%s)' % (name, s.get('reason', s.get('status'))))
            continue
        if s.get('errors'):
            bad.append('%s: %s questions failed with an exception' % (name, s['errors']))
        if lanes and name in MODEL_LANES:
            # e.g. "lanes: words, meaning, reranker (vectors for 80% of passages); word lanes: notes"
            head = str(s.get('lanes') or '').split(';', 1)[0]
            part = head[len('lanes: '):] if head.startswith('lanes: ') else ''
            ran = [x.split(' (')[0].strip() for x in part.split(', ')] if part else []
            missing = [w for w in MODEL_LANES[name] if w not in ran]
            if missing:
                bad.append('%s: wanted the lanes %s, the run says "%s"' % (name, ', '.join(MODEL_LANES[name]), head))
            elif '(vectors for' in part:
                bad.append('%s: the meaning lane covered part of the passages only: "%s"' % (name, head))
    if lanes and any(n in MODEL_LANES for n in names):
        rc = meta.get('embed_rc')
        if rc != 0:
            bad.append('embedding run did not end clean (embed_rc %r)' % (rc,))
    return bad


def compare(systems, meta, base):
    """Problems against the baseline, and plain lines that show each number."""
    bad, lines = [], []
    if base.get('n_scored') != meta.get('n_scored'):
        bad.append('this run scored %s questions, the baseline %s: the questions, the fixtures or the installed '
                   'extras differ (the pdf questions need pypdf); refresh the baseline only on purpose'
                   % (meta.get('n_scored'), base.get('n_scored')))
    tq = base.get('tolerance_questions', 0)
    if not isinstance(tq, (int, float)) or isinstance(tq, bool) or tq < 0:
        raise BadInput('tolerance_questions in the baseline must be a number >= 0')
    for name, want in sorted((base.get('systems') or {}).items()):
        s = systems.get(name)
        if not isinstance(s, dict) or s.get('status') != 'ok':
            bad.append('%s: in the baseline but did not run' % name)
            continue
        n = s.get('n')
        if n != want.get('n'):
            bad.append('%s: %s answerable questions, the baseline has %s' % (name, n, want.get('n')))
            continue
        tol = float(tq) / n if n else 0.0
        for m in METRICS:
            if m not in want:
                continue
            got, ref = s.get(m), want[m]
            if not isinstance(got, (int, float)):
                bad.append('%s %s: missing in the results' % (name, m))
                continue
            floor = ref - tol
            mark = 'ok'
            if got < floor - EPS:
                mark = 'BELOW'
                bad.append('%s %s %s is below the floor %s (baseline %s, tolerance %s questions)'
                           % (name, m, pct(got), pct(floor), pct(ref), tq))
            elif got > ref + EPS:
                mark = 'above baseline'
            lines.append('%-7s %-6s %6s  baseline %6s  floor %6s  %s' % (name, m, pct(got), pct(ref), pct(floor), mark))
    return bad, lines


def baseline_from(results, require, tolerance_questions):
    meta, systems = results['meta'], results['systems']
    ok = [n for n, s in systems.items() if isinstance(s, dict) and s.get('status') == 'ok']
    return {
        'about': 'Floor for bench/run_bench.py --models %s, checked by bench/gate.py. A drop of more than '
                 'tolerance_questions answerable questions in any metric fails the gate.' % meta.get('models'),
        'models': meta.get('models'),
        'n_scored': meta.get('n_scored'),
        'tolerance_questions': tolerance_questions,
        'require': list(require) if require else ok,
        'measured': {'version': meta.get('version'), 'python': meta.get('python'), 'utc': meta.get('utc'),
                     'system': '%s %s' % (sys.platform, platform.machine())},
        'systems': {n: dict({'n': systems[n].get('n')}, **{m: systems[n].get(m) for m in METRICS}) for n in ok},
    }


def summary(lines, bad, note):
    """Markdown for the job summary page, when there is one."""
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write('### bench gate\n\n')
            if note:
                f.write('> %s\n\n' % note)
            if lines:
                f.write('```\n' + '\n'.join(lines) + '\n```\n\n')
            f.write(('**failed**\n\n' + ''.join('- %s\n' % b for b in bad)) if bad else '**passed**\n')
            f.write('\n')
    except OSError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description='Quality gate over a bench result file.')
    ap.add_argument('results', help='results_*.json, or a folder that holds them')
    ap.add_argument('--baseline', help='baseline json, it has to exist; without one the gate only checks that '
                                       'the systems ran')
    ap.add_argument('--require', default=None,
                    help='systems that must have run, comma separated (default: the baseline\'s list)')
    ap.add_argument('--lanes', action='store_true',
                    help='model systems must report the meaning lane (and the reranker for full)')
    ap.add_argument('--write-baseline', metavar='FILE', help='write a baseline from these results and stop')
    ap.add_argument('--tolerance-questions', type=float, default=0.0,
                    help='with --write-baseline: how many questions a metric may drop (default 0)')
    a = ap.parse_args(argv)
    try:
        path = newest(a.results)
        results = load_json(path, 'result file')
        meta, systems = results.get('meta'), results.get('systems')
        if not isinstance(meta, dict) or not isinstance(systems, dict):
            raise BadInput('%s has no meta or systems block, is it a bench result?' % path)
        require = [x.strip() for x in a.require.split(',') if x.strip()] if a.require else None
        if a.write_baseline:
            if a.tolerance_questions < 0:
                raise BadInput('--tolerance-questions must be >= 0')
            tq = a.tolerance_questions
            b = baseline_from(results, require, int(tq) if tq == int(tq) else tq)
            with open(a.write_baseline, 'w', encoding='utf-8') as f:
                json.dump(b, f, indent=2, sort_keys=False)
                f.write('\n')
            print('wrote %s from %s' % (a.write_baseline, path))
            return 0
        base = None
        if a.baseline:
            # a named baseline that is not there would turn the gate into a no-op, so it is bad input
            if not os.path.isfile(a.baseline):
                raise BadInput('baseline %s does not exist' % a.baseline)
            base = load_json(a.baseline, 'baseline')
            if base.get('models') != meta.get('models'):
                raise BadInput('the baseline is for --models %s, this run used --models %s'
                               % (base.get('models'), meta.get('models')))
        if require is None:
            require = list(base.get('require') or (base.get('systems') or {}).keys()) if base else []
    except BadInput as e:
        say('error', str(e))
        return 2
    print('gate over %s (models %s, %s questions scored)' % (path, meta.get('models'), meta.get('n_scored')))
    bad = check_ran(systems, require, a.lanes, meta)
    lines, note = [], ''
    if base is not None:
        try:
            more, lines = compare(systems, meta, base)
        except BadInput as e:
            # a broken tolerance in the baseline is bad input (exit 2), never a traceback
            say('error', str(e))
            return 2
        bad += more
        for ln in lines:
            print('  ' + ln)
    else:
        note = ('NO BASELINE (none given): only checked that %s ran%s. Numbers are not gated until the baseline '
                'exists, see docs/testing.md' % (', '.join(require) or 'nothing',
                                                 ' with their lanes' if a.lanes else ''))
        say('warning', note)
        print('baseline this run would write (bench/gate.py %s --write-baseline FILE):' % path)
        print(json.dumps(baseline_from(results, require or None, 1), indent=2))
    for b in bad:
        say('error', b)
    summary(lines, bad, note)
    if bad:
        print('gate FAILED: %d problem%s' % (len(bad), '' if len(bad) == 1 else 's'))
        return 1
    print('gate passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
