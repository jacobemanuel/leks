"""Evidence store, restore, flags, journal, incidents, transactional storage and the stop hook, on disposable
fixture vaults."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import brain
import evidence_store as es
import storage
import vault_fixture as vf

PY = sys.executable
TOOLS = Path(__file__).resolve().parent

# the flag tests need bsd file flags (os.chflags + UF_IMMUTABLE). macos has them, linux doesn't, so they skip there
HAS_FLAGS = es.flags_supported()
NO_FLAGS = 'needs file flags (os.chflags and stat.UF_IMMUTABLE), which this platform does not have'


def sha(path):
    return es.sha256_file(path)


class RestoreScenarios(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault()
        self.src = self.root / 'raw/docs/build-log.md'
        self.good = sha(self.src)

    def tearDown(self):
        vf.cleanup(self.base)

    def restore(self, query, **kw):
        return es.restore(self.root, query, **kw)

    def test_r01_deleted_file(self):
        self.src.unlink()
        self.assertEqual(self.restore('raw/docs/build-log.md')['status'], 'restored')
        self.assertEqual(sha(self.src), self.good)

    def test_r02_modified_file_conflict_then_replace(self):
        self.src.write_text('tampered\n')
        result = self.restore('raw/docs/build-log.md')
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(self.src.read_text(), 'tampered\n')
        result = self.restore('raw/docs/build-log.md', replace=True)
        self.assertEqual(result['status'], 'restored')
        self.assertEqual(sha(self.src), self.good)
        self.assertEqual((self.root / result['quarantine']).read_text(), 'tampered\n')

    def test_r03_truncated_file(self):
        self.src.write_bytes(b'')
        self.assertEqual(self.restore('raw/docs/build-log.md', replace=True)['status'], 'restored')
        self.assertEqual(sha(self.src), self.good)

    def test_r04_replaced_by_another_source(self):
        shutil.copyfile(str(self.root / 'raw/docs/mirror-note.md'), str(self.src))
        self.assertEqual(self.restore('raw/docs/build-log.md', replace=True)['status'], 'restored')
        self.assertEqual(sha(self.src), self.good)

    def test_r05_renamed_outside_the_move_command(self):
        os.rename(str(self.src), str(self.root / 'raw/docs/renamed.md'))
        self.assertEqual(self.restore('raw/docs/build-log.md')['status'], 'restored')
        errors = ' '.join(brain.check(self.root, False)['errors'])
        self.assertIn('unregistered raw source: raw/docs/renamed.md', errors)

    def test_r06_registry_record_corrupted(self):
        reg = brain.read_json(self.root / 'system/source-registry.json')
        rec = next(r for r in reg['files'] if r['path'] == 'raw/docs/build-log.md')
        rec['sha256'] = '0' * 64
        brain.save_json(self.root / 'system/source-registry.json', reg)
        self.src.unlink()
        result = self.restore(rec['id'])
        self.assertEqual(result['status'], 'restored', result)
        self.assertIn('checkpoint', result['registry'])
        self.assertEqual(sha(self.src), self.good)

    def test_r07_registry_file_unreadable(self):
        (self.root / 'system/source-registry.json').write_text('{ broken')
        self.src.unlink()
        result = self.restore('raw/docs/build-log.md')
        self.assertEqual(result['status'], 'restored', result)
        self.assertEqual(result['registry'], 'store checkpoint')
        self.assertIn('registry unreadable', ' '.join(brain.check(self.root, False)['errors']))

    def test_r08_by_stable_id(self):
        rid = es.find_records(es.registry(self.root), 'raw/docs/build-log.md')[0]['id']
        self.src.unlink()
        self.assertEqual(self.restore(rid)['status'], 'restored')
        self.assertEqual(sha(self.src), self.good)

    def test_r09_by_revision(self):
        old = self.root.parent / 'old.md'
        old.write_text('Earlier version of the log.\n')
        old_sha = sha(old)
        es.put_object(es.settings(self.root)['store'], old, old_sha)
        reg = brain.read_json(self.root / 'system/source-registry.json')
        rec = next(r for r in reg['files'] if r['path'] == 'raw/docs/build-log.md')
        rec['revisions'] = [{'on': '2026-10-20', 'reason': 'fixture', 'previous_sha256': old_sha, 'previous_bytes': old.stat().st_size}]
        brain.save_json(self.root / 'system/source-registry.json', reg)
        result = self.restore('raw/docs/build-log.md', revision=0, replace=True)
        self.assertEqual(result['status'], 'restored', result)
        self.assertEqual(sha(self.src), old_sha)

    def test_r10_by_content_hash(self):
        self.src.unlink()
        self.assertEqual(self.restore(self.good)['status'], 'restored')
        self.assertEqual(sha(self.src), self.good)

    def test_r11_dry_run_changes_nothing(self):
        self.src.unlink()
        self.assertEqual(self.restore('raw/docs/build-log.md', dry_run=True)['status'], 'planned')
        self.assertFalse(self.src.exists())

    def test_r12_missing_backup_object_is_reported(self):
        obj = es.object_path(es.settings(self.root)['store'], self.good)
        es.set_immutable(obj, False)
        obj.unlink()
        self.src.unlink()
        result = self.restore('raw/docs/build-log.md')
        self.assertEqual(result['status'], 'error')
        self.assertIn('no object', result['message'])
        self.assertIn('evidence store: missing object', ' '.join(brain.check(self.root, False)['errors']))

    def test_r13_corrupted_backup_object_is_reported(self):
        obj = es.object_path(es.settings(self.root)['store'], self.good)
        es.set_immutable(obj, False)
        os.chmod(str(obj), 0o644)
        obj.write_text('Build 999 installed on the headset.\n')
        self.src.unlink()
        result = self.restore('raw/docs/build-log.md')
        self.assertEqual(result['status'], 'error')
        self.assertIn('corrupt', result['message'])
        self.assertFalse(self.src.exists())

    def test_backup_is_idempotent_and_objects_are_locked(self):
        again = es.backup(self.root)
        self.assertEqual(again['objects_added'], 0)
        obj = es.object_path(es.settings(self.root)['store'], self.good)
        with self.assertRaises(PermissionError):
            obj.write_text('x')
        self.assertEqual(es.verify_store(self.root, deep=True)['errors'], [])


@unittest.skipUnless(HAS_FLAGS, NO_FLAGS)
class Flags(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault(protect=True)

    def tearDown(self):
        vf.cleanup(self.base)

    def test_protect_blocks_writes_renames_and_deletes(self):
        result = es.protect(self.root, 'apply')
        self.assertEqual(result['protected'], result['files'])
        target = self.root / 'raw/docs/mirror-note.md'
        for action in (lambda: target.write_text('x'), lambda: target.unlink(), lambda: os.rename(str(target), str(target) + '.x')):
            with self.assertRaises(PermissionError):
                action()
        self.assertEqual(es.protect(self.root, 'release')['protected'], 0)

    def test_register_protects_new_sources(self):
        new = self.root / 'raw/docs/new.md'
        new.write_text('new\n')
        brain.register(self.root, 'raw/docs/new.md', 'fixture')
        self.assertTrue(es.is_immutable(new))

    def test_move_manages_flags_and_journals(self):
        es.protect(self.root, 'apply')
        brain.move_many(self.root, [['raw/docs/mirror-note.md', 'raw/docs/mirrors/mirror-note.md']], 'fixture')
        brain.build(self.root)
        moved = self.root / 'raw/docs/mirrors/mirror-note.md'
        self.assertTrue(moved.is_file() and es.is_immutable(moved))
        self.assertTrue(es.is_immutable(self.root / 'raw/docs/build-log.md'))
        events = [e['event'] for e in es.journal_lines(self.root)]
        self.assertIn('move', events)
        self.assertTrue(brain.check(self.root, False)['ok'], brain.check(self.root, False)['errors'])

    def test_failed_operation_leaves_files_protected(self):
        es.protect(self.root, 'apply')
        with self.assertRaises(RuntimeError):
            with es.guarded_change(self.root):
                raise RuntimeError('simulated failure')
        self.assertTrue(all(es.is_immutable(self.root / r['path']) for r in es.registry(self.root)['files']))


class ProtectionAtStartup(unittest.TestCase):
    """protect_raw on: the session-start hook re-locks evidence and says so, and a missing flag fails check and
    doctor. protect_raw off: every session starts with a warning."""
    def tearDown(self):
        vf.cleanup(self.base)

    def session_context(self):
        env = dict(os.environ, XR_VAULT_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        done = subprocess.run([PY, str(TOOLS / 'hooks/session_context.py')], input='{}', capture_output=True, text=True,
                              env=env, cwd=str(self.root), timeout=120)
        return json.loads(done.stdout or '{}').get('additionalContext', '')

    @unittest.skipUnless(HAS_FLAGS, NO_FLAGS)
    def test_session_start_relocks_and_reports(self):
        self.base, self.root = vf.make_vault(protect=True)
        es.protect(self.root, 'apply')
        loose = self.root / 'raw/docs/mirror-note.md'
        es.set_immutable(loose, False)
        context = self.session_context()
        self.assertTrue(es.is_immutable(loose))
        self.assertIn('Evidence protection: on; 3 registered raw files immutable; 1 re-locked at session start.', context)
        self.assertNotIn('EVIDENCE PROTECTION', context)

    @unittest.skipUnless(HAS_FLAGS, NO_FLAGS)
    def test_a_missing_flag_fails_check_and_doctor(self):
        self.base, self.root = vf.make_vault(protect=True)
        es.protect(self.root, 'apply')
        es.set_immutable(self.root / 'raw/docs/mirror-note.md', False)
        errors = brain.check(self.root, False)['errors']
        self.assertTrue(any('not immutable although protect_raw is on' in e for e in errors), errors)
        import doctor
        self.assertEqual(doctor.check_protection(self.root)['status'], 'fail')

    def test_protection_off_is_announced_first(self):
        self.base, self.root = vf.make_vault(protect=False)
        self.assertTrue(self.session_context().startswith('EVIDENCE PROTECTION OFF'))


class JournalAndIncidents(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault(copy_tools=True)
        self.journal = self.root / es.JOURNAL

    def tearDown(self):
        vf.cleanup(self.base)

    def test_chain_valid_then_tampered_line_detected(self):
        self.assertEqual(es.journal_verify(self.root), [])
        lines = self.journal.read_text().splitlines()
        entry = json.loads(lines[0])
        entry['sha256'] = 'f' * 64
        lines[0] = json.dumps(entry, sort_keys=True)
        self.journal.write_text('\n'.join(lines) + '\n')
        self.assertTrue(any('chain broken' in e for e in es.journal_verify(self.root)))

    def test_consistently_rewritten_history_is_caught_by_the_store_checkpoint(self):
        es.checkpoint(self.root)
        entries = es.journal_lines(self.root)
        entries[0]['path'] = 'raw/docs/forged.md'
        prev, out = '0' * 64, []
        for e in entries:
            body = {k: v for k, v in e.items() if k not in ('prev', 'hash')}
            e = dict(body, prev=prev, hash=es._chain(prev, body))
            prev = e['hash']
            out.append(json.dumps(e, sort_keys=True))
        self.journal.write_text('\n'.join(out) + '\n')
        errors = es.journal_verify(self.root)
        self.assertFalse(any('chain broken' in e for e in errors))
        self.assertTrue(any('independent checkpoint' in e for e in errors))

    def test_entries_after_the_last_backup_are_anchored_too(self):
        """Appends after the last checkpoint must not be rewritable unnoticed."""
        es.checkpoint(self.root)
        for n in range(3):
            es.journal_append(self.root, {'event': 'test', 'n': n})           # each append copies the head into the store
        entries = es.journal_lines(self.root)
        entries[-2]['n'] = 99                                                # rewrite a late entry and re-chain everything
        prev, out = '0' * 64, []
        for e in entries:
            body = {k: v for k, v in e.items() if k not in ('prev', 'hash')}
            e = dict(body, prev=prev, hash=es._chain(prev, body))
            prev = e['hash']
            out.append(json.dumps(e, sort_keys=True))
        self.journal.write_text('\n'.join(out) + '\n')
        errors = es.journal_verify(self.root)
        self.assertFalse(any('chain broken' in e for e in errors))
        self.assertTrue(any('independent checkpoint' in e for e in errors), errors)

    def test_heads_go_to_one_log_and_a_moved_vault_is_reported(self):
        """One log for all appends, and heads are not silently skipped after a move."""
        store = es.settings(self.root)['store']
        before = len(list((store / 'checkpoints').glob('*')))
        for n in range(20):
            es.journal_append(self.root, {'event': 'test', 'n': n})
        self.assertLessEqual(len(list((store / 'checkpoints').glob('*'))), before + 1)      # 20 appends, at most one new file
        self.assertEqual(es.journal_verify(self.root), [])
        log = (store / es.HEADS).read_text().splitlines()
        moved = [json.loads(x) for x in log]
        (store / es.HEADS).write_text(''.join(json.dumps(dict(h, vault='/old/location/vault')) + '\n' for h in moved))
        for cp in (store / 'checkpoints').glob('*-journal-head.json'):
            es.set_immutable(cp, False)
            cp.unlink()
        self.assertTrue(any('no checkpoint in the store belongs to this vault' in e for e in es.journal_verify(self.root)))

    def test_heads_of_another_vault_in_a_shared_store_are_ignored(self):
        store = es.settings(self.root)['store']
        es.storage.atomic_write_json(store / 'checkpoints' / '20990101T000000000000-journal-head.json',
                                     {'lines': 1, 'hash': 'f' * 64, 'at': 'x', 'vault': '/some/other/vault'})
        self.assertEqual([e for e in es.journal_verify(self.root) if 'independent checkpoint' in e], [])

    def test_interrupted_append_is_reported_and_blocks_further_appends(self):
        with open(self.journal, 'a') as f:
            f.write('{"event": "register", "pa')
        self.assertTrue(any('unreadable line' in e for e in es.journal_verify(self.root)))
        with self.assertRaises(IOError):
            es.journal_append(self.root, {'event': 'test'})

    def run_hook(self, script, payload):
        env = dict(os.environ, XR_VAULT_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        done = subprocess.run([PY, str(TOOLS / 'hooks' / script)], input=json.dumps(payload), capture_output=True,
                              text=True, env=env, cwd=str(self.root), timeout=120)
        return json.loads(done.stdout or '{}')

    def test_stop_hook_sees_an_in_place_change_to_existing_evidence(self):
        self.assertTrue(brain.check(self.root, True)['ok'])
        time.sleep(0.05)
        with open(self.root / 'raw/docs/mirror-note.md', 'a') as f:
            f.write('appended in place\n')
        out = self.run_hook('stop_check.py', {})
        self.assertEqual(out.get('decision'), 'block')
        self.assertIn('raw changed', out.get('reason', ''))

    def test_incident_stays_open_until_restored_and_resolved(self):
        target = self.root / 'raw/docs/mirror-note.md'
        target.write_text('tampered\n')
        result = brain.check(self.root, True)
        self.assertEqual(len(result['open_incidents']), 1)
        iid = result['open_incidents'][0]['id']
        self.assertIn('INTEGRITY INCIDENTS OPEN', self.run_hook('session_context.py', {}).get('additionalContext', ''))
        self.assertEqual(self.run_hook('stop_check.py', {}).get('decision'), 'block')
        self.assertIn('integrity', brain.answer_pack(self.root, 'mirrors'))
        refused = es.resolve_incident(self.root, iid, 'fixture', brain.check(self.root, False)['errors'])
        self.assertEqual(refused['status'], 'refused')
        self.assertEqual(es.restore(self.root, 'raw/docs/mirror-note.md', replace=True)['status'], 'restored')
        resolved = es.resolve_incident(self.root, iid, 'restored from the store', brain.check(self.root, False)['errors'])
        self.assertEqual(resolved['status'], 'resolved')
        self.assertEqual(es.open_incidents(self.root), [])


WRITER = r'''
import sys
sys.path.insert(0, sys.argv[1])
import brain
from pathlib import Path
root, prefix = Path(sys.argv[2]), sys.argv[3]
for i in range(10):
    p = root / 'raw/docs' / ('%s-%02d.md' % (prefix, i))
    p.write_text('concurrent %s %d\n' % (prefix, i))
    brain.register(root, 'raw/docs/' + p.name, 'fixture')
'''

KILLED = r'''
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import storage
os.replace = lambda *a, **k: os.kill(os.getpid(), signal.SIGKILL)
storage.atomic_write_json(sys.argv[2], {'files': ['new'] * 100000})
'''


class TransactionalStorage(unittest.TestCase):
    def setUp(self):
        self.base, self.root = vf.make_vault()

    def tearDown(self):
        vf.cleanup(self.base)

    def test_interrupted_write_keeps_the_previous_file(self):
        target = self.root / 'system/source-registry.json'
        before = target.read_bytes()
        subprocess.run([PY, '-c', KILLED, str(TOOLS), str(target)], capture_output=True)
        self.assertEqual(target.read_bytes(), before)
        json.loads(target.read_text())
        self.assertEqual(len(storage.stray_temporaries(self.root)), 1)
        warnings = ' '.join(brain.check(self.root, False)['warnings'])
        self.assertIn('stray temporary file', warnings)

    def test_concurrent_writers_lose_nothing(self):
        procs = [subprocess.Popen([PY, '-c', WRITER, str(TOOLS), str(self.root), 'w%d' % n]) for n in range(4)]
        reads = 0
        while any(p.poll() is None for p in procs):
            json.loads((self.root / 'system/source-registry.json').read_text())
            reads += 1
        self.assertTrue(all(p.returncode == 0 for p in procs))
        paths = {r['path'] for r in es.registry(self.root)['files']}
        self.assertEqual(sum(1 for p in paths if '/w' in p), 40)
        self.assertEqual(es.journal_verify(self.root), [])
        self.assertGreater(reads, 0)

    def test_permission_error_leaves_registry_unchanged(self):
        before = (self.root / 'system/source-registry.json').read_bytes()
        (self.root / 'raw/docs/new.md').write_text('new\n')
        os.chmod(str(self.root / 'system'), 0o555)
        try:
            with self.assertRaises(PermissionError):
                brain.register(self.root, 'raw/docs/new.md', 'fixture')
        finally:
            os.chmod(str(self.root / 'system'), 0o755)
        self.assertEqual((self.root / 'system/source-registry.json').read_bytes(), before)

    def test_disk_full_during_write_keeps_the_previous_file(self):
        target = self.root / 'system/claims.json'
        before = target.read_bytes()
        real = os.fdopen

        def failing(*a, **k):
            f = real(*a, **k)
            def write(_):
                raise OSError(28, 'No space left on device')
            f.write = write
            return f
        storage.os.fdopen = failing
        try:
            with self.assertRaises(OSError):
                storage.atomic_write_json(target, {'claims': []})
        finally:
            storage.os.fdopen = real
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(storage.stray_temporaries(self.root), [])


if __name__ == '__main__':
    unittest.main()
