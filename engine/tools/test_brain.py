import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import brain

def page(root, path, title, summary, keywords, sources, status='active', category='Domains', body=''):
    meta = {'title':title,'summary':summary,'category':category,'status':status,'as_of':'2026-10-08',
            'compiled_on':'2026-10-08','owner':'XR-KNOWLEDGE','keywords':keywords,'sources':sources}
    fm = '\n'.join(k+': '+json.dumps(v) for k,v in meta.items())
    p = root/path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('---\n'+fm+'\n---\n# '+title+'\n'+body, encoding='utf-8')

class BrainIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'raw').mkdir()
        (self.root/'wiki').mkdir()
        (self.root/'system').mkdir()
        (self.root/'raw/source.md').write_text('Original evidence',encoding='utf-8')
        brain.register(self.root,'raw/source.md','fixture')
        page(self.root,'wiki/doors.md','Door interactions','Grip and pull, not tap.',['doors','grip'],['raw/source.md'],
             body='[[raw/source]]\n')
        page(self.root,'wiki/now.md','Now','Current fixture state',['current'],[])
        claims = {'claims':[{'id':'D-1','text':'Grip','page':'wiki/doors.md','confidence':'reported','scope':'fixture',
                             'as_of':'2026-10-08','sources':['raw/source.md']}]}
        brain.save_json(self.root/'system/claims.json',claims)
        brain.build(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def errors(self):
        return '\n'.join(brain.check(self.root,False)['errors'])

    def test_valid_fixture(self):
        self.assertTrue(brain.check(self.root,False)['ok'])

    def test_raw_mutation_cannot_be_hidden_by_build(self):
        (self.root/'raw/source.md').write_text('Changed evidence',encoding='utf-8')
        brain.build(self.root)
        self.assertIn('raw changed',self.errors())
        with self.assertRaises(ValueError): brain.register(self.root,'raw/source.md','rebaseline')

    def test_missing_and_unregistered_raw(self):
        (self.root/'raw/source.md').unlink()
        (self.root/'raw/new.md').write_text('new',encoding='utf-8')
        errors = self.errors()
        self.assertIn('missing raw source',errors)
        self.assertIn('unregistered raw source',errors)

    def test_finder_metadata_is_never_evidence(self):
        (self.root/'raw/.DS_Store').write_bytes(b'Bud1 unregistered')
        self.assertTrue(brain.check(self.root,False)['ok'])
        with self.assertRaises(ValueError): brain.register(self.root,'raw/.DS_Store','fixture')
        registry = brain.read_json(self.root/'system/source-registry.json')
        registry['files'].append(dict(registry['files'][0],id='S-ds',path='raw/.DS_Store',original_path='raw/.DS_Store'))
        brain.save_json(self.root/'system/source-registry.json',registry)
        brain.build(self.root)
        self.assertIn('Finder metadata registered',self.errors())

    def test_symbolic_link_inside_raw_is_rejected(self):
        os.symlink(str(self.root/'wiki'), str(self.root/'raw/linked'))
        self.assertIn('symbolic link inside raw',self.errors())
        with self.assertRaises(ValueError): brain.register(self.root,'raw/linked','fixture')

    def test_changed_page_requires_build_and_broken_link_persists(self):
        with (self.root/'wiki/doors.md').open('a') as f: f.write('[[wiki/missing]]')
        self.assertIn('build required',self.errors())
        brain.build(self.root)
        self.assertIn('broken wikilink',self.errors())

    def test_new_page_not_indexed(self):
        (self.root/'wiki/new.md').write_text((self.root/'wiki/doors.md').read_text(),encoding='utf-8')
        self.assertIn('page missing from index',self.errors())

    def test_archived_pages_live_in_archive_with_archive_metadata(self):
        page(self.root,'wiki/archive/old-plan.md','Old plan','A draft that is history.',['plan'],[],status='active')
        brain.build(self.root)
        self.assertIn('archived pages need status',self.errors())
        page(self.root,'wiki/archive/old-plan.md','Old plan','A draft that is history.',['plan'],[],status='archived',category='Archive')
        brain.build(self.root)
        self.assertTrue(brain.check(self.root,False)['ok'])
        self.assertIn('## Archive',(self.root/'wiki/index.md').read_text())
        page(self.root,'wiki/stray.md','Stray','Archived but outside the archive.',['x'],[],status='archived',category='Archive')
        brain.build(self.root)
        self.assertIn('archived page outside wiki/archive',self.errors())

    def test_decision_supersession_and_conflict_symmetry(self):
        claims = brain.read_json(self.root/'system/claims.json')
        claims['claims'][0]['supersedes'] = ['MISSING']
        claims['claims'][0]['conflicts'] = ['MISSING']
        brain.save_json(self.root/'system/claims.json',claims)
        self.assertIn('asymmetric supersession',self.errors())
        self.assertIn('asymmetric conflict',self.errors())

    def test_copilot_delegate_requires_agent_and_known_profile(self):
        p = self.root/'.github/agents/XR-ORCHESTRATOR.agent.md'; p.parent.mkdir(parents=True)
        p.write_text('---\nname: "XR-ORCHESTRATOR"\ndescription: "Route"\ntools: ["read"]\nagents: ["NOT-FOUND"]\n---\n',encoding='utf-8')
        self.assertIn('unknown delegate',self.errors())
        self.assertIn('delegates require agent',self.errors())

    def test_model_is_chosen_in_vscode_not_pinned(self):
        p = self.root/'.github/agents/XR-REVIEW.agent.md'; p.parent.mkdir(parents=True)
        p.write_text('---\nname: "XR-REVIEW"\ndescription: "Review"\nmodel: "Claude Opus 5.5"\ntools: ["read"]\nagents: []\n---\n',encoding='utf-8')
        self.assertIn('remove the model field',self.errors())

    def test_readonly_profile_cannot_execute(self):
        p = self.root/'.github/agents/XR-REVIEW.agent.md'; p.parent.mkdir(parents=True)
        p.write_text('---\nname: "XR-REVIEW"\ndescription: "Review"\ntools: ["read", "execute"]\nagents: []\n---\n',encoding='utf-8')
        self.assertIn('read-only profile',self.errors())

    def test_ui_state_not_knowledge_freshness(self):
        p = self.root/'.obsidian/workspace.json'; p.parent.mkdir()
        p.write_text('{}')
        self.assertTrue(brain.check(self.root,False)['ok'])

    def test_copilot_skill_name_validation(self):
        p = self.root/'.agents/skills/xr-query/SKILL.md'; p.parent.mkdir(parents=True)
        p.write_text('---\nname: "different-name"\ndescription: "query"\n---\n',encoding='utf-8')
        self.assertIn('invalid skill name',self.errors())

    def test_hook_files_are_validated(self):
        p = self.root/'.github/hooks/vault.json'; p.parent.mkdir(parents=True)
        brain.save_json(p,{'version':1,'hooks':{'preToolUse':[{'type':'command','command':'python3 tools/hooks/missing.py'}]}})
        self.assertIn('script missing tools/hooks/missing.py',self.errors())
        brain.save_json(p,{'hooks':{}})
        self.assertIn('needs "version": 1',self.errors())

    def test_register_rejects_outside_raw(self):
        with self.assertRaises(ValueError): brain.register(self.root,'wiki/doors.md','wrong')

    def test_ai_work_is_attributed_by_model_name_only(self):
        (self.root/'raw/opinion.md').write_text('An opinion',encoding='utf-8')
        with self.assertRaises(ValueError): brain.register(self.root,'raw/opinion.md','Codex session on 9 Oct')
        brain.register(self.root,'raw/opinion.md','Owner session on 9 Oct; model: GPT-6.1 Sol')
        claims = brain.read_json(self.root/'system/claims.json')
        claims['claims'][0]['author'] = 'Claude Code'
        brain.save_json(self.root/'system/claims.json',claims)
        brain.build(self.root)
        self.assertIn('names an AI client tool',self.errors())

    def test_search_routes_without_raw_dump(self):
        hits = brain.search(self.root,'how do we open the doors',5)
        self.assertEqual(hits[0]['path'],'wiki/doors.md')

    def test_topic_page_outranks_change_log_hub(self):
        page(self.root,'wiki/log.md','Knowledge change log','Chronological record.',['log'],[],category='Start',
             body=('Door grip pull interactions changed. '*40))
        brain.build(self.root)
        self.assertEqual(brain.search(self.root,'door grip interactions',5)[0]['path'],'wiki/doors.md')

    def test_archived_page_ranks_below_current_unless_asked(self):
        page(self.root,'wiki/archive/doors-draft.md','Door interactions draft','Draft door plan.',['doors','grip'],[],
             status='archived',category='Archive',body='Doors grip pull plan.')
        brain.build(self.root)
        self.assertEqual(brain.search(self.root,'door grip',5)[0]['path'],'wiki/doors.md')
        self.assertEqual(brain.search(self.root,'archived door draft',5)[0]['path'],'wiki/archive/doors-draft.md')

    def test_newer_delivery_record_outranks_older_one(self):
        page(self.root,'wiki/codebase/2026-10-17-mirror-pass-one.md','Mirror pass one','Mirrors first pass.',['mirrors'],[],
             body='Mirrors first pass mirrors.')
        page(self.root,'wiki/codebase/2026-10-19-mirror-pass-two.md','Mirror pass two','Mirrors second pass.',['mirrors'],[],
             body='Mirrors second pass.')
        brain.build(self.root)
        self.assertEqual(brain.search(self.root,'mirrors',5)[0]['path'],'wiki/codebase/2026-10-19-mirror-pass-two.md')

    def test_review_ranks_below_knowledge_unless_asked(self):
        page(self.root,'wiki/reviews/2026-10-15-door-review.md','Door interactions review','Door grip review.',['doors','grip'],[],
             category='Workspace and method',body='Door grip pull grip doors.')
        brain.build(self.root)
        self.assertEqual(brain.search(self.root,'door grip',5)[0]['path'],'wiki/doors.md')
        self.assertEqual(brain.search(self.root,'door grip review',5)[0]['path'],'wiki/reviews/2026-10-15-door-review.md')

    def test_how_do_i_question_prefers_procedure_pages(self):
        (self.root/'system/WORKFLOWS.md').write_text('---\ntitle: "Workflows"\nsummary: "Procedures."\nkeywords: ["record a build"]\n---\n'
                                                     '# Workflows\nRecord a build with the delivery skill.\n', encoding='utf-8')
        page(self.root,'wiki/codebase/build-record.md','Build of 19 October','Build installed on the headset.',['build','installed'],[],
             body='Build installed on the headset; the record is kept here. Build record.')
        brain.build(self.root)
        self.assertEqual(brain.search(self.root,'record a build',5)[0]['path'],'wiki/codebase/build-record.md')
        self.assertEqual(brain.search(self.root,'How do I record a build?',5)[0]['path'],'system/WORKFLOWS.md')

    def test_prior_intent_counts_stop_words_such_as_when(self):
        brain.save_json(self.root/'system/vault-config.json',{'search_hub_priors':{'wiki/timeline.md':[0.5,['when']]}})
        page(self.root,'wiki/timeline.md','Timeline','Periods of the project.',['timeline'],[],category='Start',
             body='Door grip changed in July. Door grip pull changed again in October.')
        brain.build(self.root)
        def timeline_score(q):
            return next(h['score'] for h in brain.search(self.root,q,5) if h['path']=='wiki/timeline.md')
        self.assertEqual(brain.search(self.root,'door grip changed',5)[0]['path'],'wiki/doors.md')
        # "when" is a stop word for scoring, but it still lifts the timeline prior.
        self.assertGreater(timeline_score('when was the door grip changed'), 1.5*timeline_score('door grip changed'))

    def test_skill_files_answer_procedure_questions(self):
        skill = self.root/'.agents/skills/xr-render-check/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: "xr-render-check"\ndescription: "Compares renders with reference images."\n'
                         'argument-hint: "what to compare"\n---\n# Render check\nCompare renders with references.\n'
                         '\n---\nRules: doors grip doors grip doors grip.\n', encoding='utf-8')
        page(self.root,'wiki/renders.md','Render results','Renders of the car.',['renders'],[],body='Renders compared once.')
        brain.build(self.root)
        self.assertEqual(brain.search(self.root,'which skill compares renders with reference images',5)[0]['path'],'.agents/skills/xr-render-check/SKILL.md')
        self.assertEqual(brain.search(self.root,'renders',5)[0]['path'],'wiki/renders.md')
        # The shared rules footer of a skill is not indexed.
        self.assertNotIn('.agents/skills/xr-render-check/SKILL.md',[h['path'] for h in brain.search(self.root,'doors grip',5)])

    def test_quantifier_phrase_rewards_the_page_that_uses_it(self):
        page(self.root,'wiki/apps.md','Apps','Products and apps.',['apps'],[],body='Every product was shipped as its own app.')
        page(self.root,'wiki/apps-other.md','Apps overview','Products and apps.',['apps'],[],body='The product app was shipped; own work.')
        brain.build(self.root)
        self.assertEqual(brain.search(self.root,'why did every product become its own app',5)[0]['path'],'wiki/apps.md')

    def test_answer_pack_returns_passages_facts_and_next_pages(self):
        page(self.root,'wiki/mirrors.md','Mirrors','Mirror status.',['mirrors'],['raw/source.md'],
             body='## Status\nThe mirrors work but are not yet accepted.\n\n## History\nUnrelated paragraph about doors.\n')
        claims = brain.read_json(self.root/'system/claims.json')
        claims['claims'].append({'id':'M-1','text':'The mirrors are not yet accepted.','page':'wiki/mirrors.md','confidence':'reported',
                                 'scope':'fixture','as_of':'2026-10-08','sources':['raw/source.md']})
        brain.save_json(self.root/'system/claims.json',claims)
        brain.build(self.root)
        pack = brain.answer_pack(self.root,'Why are the mirrors not accepted?',budget=40)
        self.assertEqual(pack['page']['path'],'wiki/mirrors.md')
        self.assertIn('not yet accepted',pack['excerpt'])
        self.assertNotIn('Unrelated paragraph',pack['excerpt'])
        self.assertEqual(pack['facts'][0]['id'],'M-1')

    def test_answer_pack_adds_marked_passages_of_the_next_page(self):
        page(self.root,'wiki/mirrors.md','Mirrors','Mirror status.',['mirrors'],[],body='## Status\nThe mirrors work.\n')
        page(self.root,'wiki/delivery.md','Delivery record','What was delivered.',['delivery'],[],
             body='## Acceptance\nThe reviewer accepted the mirrors on 12 October.\n\n## Other\nNothing else about doors.\n')
        brain.build(self.root)
        pack = brain.answer_pack(self.root,'Who accepted the mirrors?')
        self.assertEqual(pack['page']['path'],'wiki/mirrors.md')
        self.assertEqual(pack['sources'],['wiki/mirrors.md','wiki/delivery.md'])
        self.assertIn('[wiki/delivery.md]\n## Acceptance\nThe reviewer accepted the mirrors on 12 October.',pack['excerpt'])
        self.assertNotIn('Nothing else about doors',pack['excerpt'])
        self.assertNotIn('wiki/delivery.md',[p['path'] for p in pack['also']])

    def test_media_text_is_searchable_with_confidence_copies_and_mentions(self):
        (self.root/'raw/shots').mkdir()
        (self.root/'raw/shots/mirror.png').write_bytes(b'png fixture')
        (self.root/'raw/shots/copy-of-mirror.png').write_bytes(b'png fixture')
        (self.root/'raw/shots/report.md').write_text('# Report\nThe capture fix is visible in mirror.png after the rebuild.\n',encoding='utf-8')
        for f in ('raw/shots/mirror.png','raw/shots/copy-of-mirror.png','raw/shots/report.md'):
            brain.register(self.root,f,'fixture')
        sha = brain.read_json(self.root/'system/source-registry.json')['files']
        sha = next(r['sha256'] for r in sha if r['path'] == 'raw/shots/mirror.png')
        (self.root/'.brain').mkdir(exist_ok=True)
        (self.root/brain.MEDIA_CACHE).write_text(json.dumps({'sha256':sha,'path':'raw/shots/mirror.png','kind':'image-ocr','pages':1,
            'chars':31,'lines':2,'mean_confidence':0.96,'low_confidence_lines':0,'text':'Capture mask fixed\nMirror material slot'})+'\n',encoding='utf-8')
        brain.save_json(self.root/brain.MEDIA_CONTEXT, brain.media_context(self.root, brain.media_records(self.root)))
        brain.build(self.root)
        hits = brain.source_search(self.root,'mirror material slot',5,content=True)
        self.assertEqual(hits[0]['text_from'],'image-ocr')
        self.assertEqual(hits[0]['ocr_confidence'],0.96)
        self.assertEqual(hits[0]['copies'],2)                      # the identical copy collapses into one hit
        info = brain.media_info(self.root,'raw/shots/mirror.png')['files'][0]
        self.assertIn('Capture mask fixed',info['text'])
        self.assertEqual(info['same_content'],['raw/shots/copy-of-mirror.png'])
        self.assertEqual(info['mentioned_in']['mentions'][0]['file'],'raw/shots/report.md')
        self.assertIn('visible in mirror.png',info['mentioned_in']['mentions'][0]['line'])
        found = brain.media_info(self.root,'capture mask')
        self.assertIn(found['matches'][0]['path'],{'raw/shots/mirror.png','raw/shots/copy-of-mirror.png'})

    def test_source_search_prefers_phrases_and_collapses_identical_copies(self):
        (self.root/'raw/a.md').write_text('the steering wheel locks with a pinch gesture',encoding='utf-8')
        (self.root/'raw/archive').mkdir()
        (self.root/'raw/archive/a-copy.md').write_text('the steering wheel locks with a pinch gesture',encoding='utf-8')
        (self.root/'raw/b.md').write_text('pinch the wheel; the steering gesture locks it'+' filler'*30,encoding='utf-8')
        for f in ('raw/a.md','raw/archive/a-copy.md','raw/b.md'):
            brain.register(self.root,f,'fixture')
        brain.build(self.root)
        hits = brain.source_search(self.root,'steering wheel locks with a pinch',5,content=True)
        self.assertIn(hits[0]['path'],('raw/a.md','raw/archive/a-copy.md'))
        self.assertEqual(hits[0].get('copies'),2)
        self.assertEqual(len([h for h in hits if h['path'] in ('raw/a.md','raw/archive/a-copy.md')]),1)

    def test_question_words_reach_page_vocabulary(self):
        page(self.root,'wiki/conflicts.md','Conflicts and pitfalls','Where sources disagree.',['conflicts'],[],category='Quality',
             body='Conflicts between sources and interpretation pitfalls.')
        page(self.root,'wiki/other.md','Release notes','Notes of a release.',['notes'],[],body='Several statements were made.')
        brain.build(self.root)
        question = 'Which statements contradict each other?'
        self.assertEqual(brain.search(self.root,question,5)[0]['path'],'wiki/conflicts.md')
        saved = brain.ALIASES.pop('contradict')
        try:   # without the thesaurus entry the word-matching page would win
            self.assertEqual(brain.search(self.root,question,5)[0]['path'],'wiki/other.md')
        finally:
            brain.ALIASES['contradict'] = saved

    def test_evaluate_reports_rank1(self):
        brain.save_json(self.root/'system/retrieval-cases.json',[{'question':'How do the doors open?','expected':['wiki/doors.md']}])
        report = brain.evaluate(self.root)
        self.assertEqual((report['pass'],report['rank1'],report['total']),(1,1,1))

    def test_table_wikilinks_require_escaped_aliases(self):
        p = self.root/'00_HOME.md'
        p.write_text('| Topic | Link |\n|---|---|\n| Doors | [[wiki/doors|Doors]] |\n',encoding='utf-8')
        self.assertIn('unescaped wikilink alias',self.errors())
        p.write_text(p.read_text().replace('doors|Doors','doors\\|Doors'),encoding='utf-8')
        brain.build(self.root)
        self.assertTrue(brain.check(self.root,False)['ok'])

    def test_full_source_search_finds_fact_absent_from_filename(self):
        (self.root/'raw/second.md').write_text('Unusual marker relocalisation evidence.',encoding='utf-8')
        brain.register(self.root,'raw/second.md','test source')
        brain.build(self.root)
        self.assertEqual(brain.source_search(self.root,'relocalisation',content=True)[0]['path'],'raw/second.md')
        self.assertEqual(brain.source_search(self.root,'relocalisations',content=True), [])
        self.assertEqual(brain.source_search(self.root,'relocal',content=True)[0]['path'],'raw/second.md')

    def test_polish_letters_are_rejected(self):
        # The letter is written as an escape so this test file stays English-only.
        (self.root/'raw/notes.md').write_text('Fixture text with a Polish letter: \u0142',encoding='utf-8')
        brain.register(self.root,'raw/notes.md','fixture')
        self.assertIn('Polish letters in raw/notes.md:1',self.errors())
        brain.save_json(self.root/'system/language-allowlist.json',{'files':[{'path':'raw/notes.md','reason':'fixture'}]})
        brain.build(self.root)
        self.assertNotIn('Polish letters',self.errors())

    def test_renamed_source_resolves_by_former_path(self):
        registry = brain.read_json(self.root/'system/source-registry.json')
        (self.root/'raw/source.md').rename(self.root/'raw/renamed.md')
        registry['files'][0].update({'path':'raw/renamed.md','former_paths':['raw/source.md']})
        brain.save_json(self.root/'system/source-registry.json',registry)
        doors = self.root/'wiki/doors.md'
        doors.write_text(doors.read_text(encoding='utf-8').replace('raw/source','raw/renamed'),encoding='utf-8')
        brain.save_json(self.root/'system/claims.json',{'claims':[{'id':'D-1','text':'Grip','page':'wiki/doors.md',
            'confidence':'reported','scope':'fixture','as_of':'2026-10-08','sources':['raw/renamed.md']}]})
        brain.build(self.root)
        self.assertTrue(brain.check(self.root,False)['ok'])
        self.assertEqual(brain.resolve(self.root,'raw/source.md')[0]['path'],'raw/renamed.md')

    def test_revision_records_are_validated(self):
        registry = brain.read_json(self.root/'system/source-registry.json')
        registry['files'][0]['revisions'] = [{'on':'2026-10-21','reason':'rename'}]
        brain.save_json(self.root/'system/source-registry.json',registry)
        brain.build(self.root)
        self.assertIn('incomplete revision record',self.errors())

    def test_inbox_language_is_a_warning(self):
        (self.root/'inbox').mkdir()
        (self.root/'inbox/note.md').write_text('Owner note \u0142',encoding='utf-8')
        result = brain.check(self.root,False)
        self.assertTrue(result['ok'])
        self.assertTrue(any('inbox/note.md' in w for w in result['warnings']))

    def test_rendition_provenance_is_validated(self):
        registry = brain.read_json(self.root/'system/source-registry.json')
        registry['files'][0]['rendition'] = {'type':'english-translation','translated_on':'2026-10-20'}
        brain.save_json(self.root/'system/source-registry.json',registry)
        brain.build(self.root)
        errors = self.errors()
        self.assertIn('rendition without translator',errors)
        self.assertIn('incomplete rendition provenance',errors)
        registry['files'][0]['rendition'].update({'translator':'Claude Opus 5.5','original_sha256':'0'*64,'original_bytes':3})
        brain.save_json(self.root/'system/source-registry.json',registry)
        brain.build(self.root)
        self.assertTrue(brain.check(self.root,False)['ok'])

    def test_fast_check_trusts_unchanged_files_and_catches_changed_ones(self):
        self.assertTrue(brain.check(self.root,True)['ok'])          # full check writes the trust cache
        self.assertTrue(brain.check(self.root,False,fast=True)['ok'])
        time.sleep(0.01)
        (self.root/'raw/source.md').write_text('Original evidencX',encoding='utf-8')   # same size, new bytes
        self.assertIn('raw changed',' '.join(brain.check(self.root,False,fast=True)['errors']))

class BrainMoveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'vault'
        self.archive = Path(self.temp.name)/'outside-archive'
        for d in ['raw/packs/v2','wiki/architecture','system','.github/agents']:
            (self.root/d).mkdir(parents=True)
        (self.root/'raw/packs/v2/README.md').write_text('# Pack\n[Tree](tree.md)\n[[raw/notes/owner-note]]\n',encoding='utf-8')
        (self.root/'raw/packs/v2/tree.md').write_text('# Tree\n',encoding='utf-8')
        (self.root/'raw/notes').mkdir()
        (self.root/'raw/notes/owner-note.md').write_text('# Owner note\nSee [[raw/packs/v2/README|the pack]].\n',encoding='utf-8')
        for f in ['raw/packs/v2/README.md','raw/packs/v2/tree.md','raw/notes/owner-note.md']:
            brain.register(self.root,f,'fixture')
        page(self.root,'wiki/architecture/target.md','Target','Proposal.',['target'],['raw/packs/v2/README.md'],
             body='Read [[raw/packs/v2/README|the pack]] and `raw/packs/v2/tree.md`.\n')
        page(self.root,'wiki/now.md','Now','Current state.',['now'],[],category='Start',
             body='[[wiki/architecture/target|Target]]\n')
        (self.root/'.github/agents/XR-ARCHITECT.agent.md').write_text(
            '---\nname: "XR-ARCHITECT"\ndescription: "ADRs"\ntools: ["read"]\nagents: []\n---\n[target](../../wiki/architecture/target.md)\n',encoding='utf-8')
        brain.save_json(self.root/'system/claims.json',{'claims':[{'id':'A-8','text':'Draft','page':'wiki/architecture/target.md',
            'confidence':'planned','scope':'proposal','as_of':'2026-10-11','sources':['raw/packs/v2/README.md']}]})
        brain.build(self.root)
        self.assertTrue(brain.check(self.root,False)['ok'], brain.check(self.root,False)['errors'])

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_changes_nothing(self):
        before = (self.root/'system/source-registry.json').read_text()
        report = brain.move_many(self.root,[['raw/packs','raw/archive/proposal-packs']],'archive',dry_run=True,archive_root=self.archive)
        self.assertEqual(report['files'],2)
        self.assertTrue((self.root/'raw/packs/v2/README.md').exists())
        self.assertEqual(before,(self.root/'system/source-registry.json').read_text())

    def test_folder_and_page_moves_keep_provenance_and_links(self):
        brain.move_many(self.root,[['raw/packs','raw/archive/proposal-packs'],
                                   ['wiki/architecture/target.md','wiki/archive/proposal-target.md']],
                        'archive the draft',archive_root=self.archive,today='2026-10-21')
        target = self.root/'wiki/archive/proposal-target.md'
        meta = brain.metadata(target.read_text())
        meta_lines = target.read_text().replace('"status": "active"','"status": "archived"')
        target.write_text(meta_lines.replace('"category": "Domains"','"category": "Archive"').replace('status: "active"','status: "archived"').replace('category: "Domains"','category: "Archive"'))
        self.assertEqual(meta['sources'],['raw/archive/proposal-packs/v2/README.md'])
        self.assertIn('raw/archive/proposal-packs/v2/tree.md',target.read_text())
        self.assertIn('[[wiki/archive/proposal-target|Target]]',(self.root/'wiki/now.md').read_text())
        self.assertIn('(../../wiki/archive/proposal-target.md)',(self.root/'.github/agents/XR-ARCHITECT.agent.md').read_text())
        claims = brain.read_json(self.root/'system/claims.json')['claims']
        self.assertEqual(claims[0]['page'],'wiki/archive/proposal-target.md')
        self.assertEqual(claims[0]['sources'],['raw/archive/proposal-packs/v2/README.md'])
        # The moved pack keeps its relative link; the owner note's wikilink is rewritten with a revision.
        self.assertIn('(tree.md)',(self.root/'raw/archive/proposal-packs/v2/README.md').read_text())
        note = (self.root/'raw/notes/owner-note.md').read_text()
        self.assertIn('[[raw/archive/proposal-packs/v2/README|the pack]]',note)
        registry = {r['path']:r for r in brain.read_json(self.root/'system/source-registry.json')['files']}
        self.assertEqual(registry['raw/archive/proposal-packs/v2/README.md']['former_paths'],['raw/packs/v2/README.md'])
        revision = registry['raw/notes/owner-note.md']['revisions'][0]
        kept = self.archive/revision['previous_preserved_in']
        self.assertEqual(brain.digest(kept),revision['previous_sha256'])
        self.assertEqual(len(brain.read_json(self.archive/'manifest.json')['entries']),1)
        self.assertEqual(brain.resolve(self.root,'raw/packs/v2/README.md')[0]['path'],'raw/archive/proposal-packs/v2/README.md')
        brain.build(self.root)
        result = brain.check(self.root,False)
        self.assertTrue(result['ok'], result['errors'])

    def test_renamed_file_keeps_short_wikilinks(self):
        (self.root/'raw/notes/short.md').write_text('See [[owner-note]] and [[notes/owner-note|the note]].\n',encoding='utf-8')
        brain.register(self.root,'raw/notes/short.md','fixture')
        brain.move_many(self.root,[['raw/notes/owner-note.md','raw/notes/2026-10-09-owner-note-on-pack.md']],'clear name',
                        archive_root=self.archive)
        text = (self.root/'raw/notes/short.md').read_text()
        self.assertIn('[[raw/notes/2026-10-09-owner-note-on-pack]]',text)       # short link -> full path
        self.assertIn('[[raw/notes/2026-10-09-owner-note-on-pack|the note]]',text)  # partial path -> full path

    def test_rename_inside_a_folder_and_move_the_folder_in_one_plan(self):
        brain.move_many(self.root,[['raw/packs/v2/tree.md','raw/packs/v2/2026-10-11-proposal-v2-decision-tree.md'],
                                   ['raw/packs','raw/archive/proposal-packs']],'clear names and archive',archive_root=self.archive)
        moved = self.root/'raw/archive/proposal-packs/v2/2026-10-11-proposal-v2-decision-tree.md'
        self.assertTrue(moved.is_file())
        readme = (self.root/'raw/archive/proposal-packs/v2/README.md').read_text()
        self.assertIn('(2026-10-11-proposal-v2-decision-tree.md)',readme)
        target = (self.root/'wiki/architecture/target.md').read_text()
        self.assertIn('raw/archive/proposal-packs/v2/2026-10-11-proposal-v2-decision-tree.md',target)
        registry = {r['path']:r for r in brain.read_json(self.root/'system/source-registry.json')['files']}
        self.assertEqual(registry['raw/archive/proposal-packs/v2/2026-10-11-proposal-v2-decision-tree.md']['former_paths'],['raw/packs/v2/tree.md'])
        brain.build(self.root)
        self.assertTrue(brain.check(self.root,False)['ok'], brain.check(self.root,False)['errors'])

    def test_plans_refuse_clashes_and_nested_folder_moves(self):
        (self.root/'raw/notes/tree.md').write_text('x',encoding='utf-8')
        with self.assertRaises(ValueError):
            brain.move_many(self.root,[['raw/notes/tree.md','raw/notes/same.md'],['raw/notes/owner-note.md','raw/notes/same.md']],'clash',archive_root=self.archive)
        with self.assertRaises(ValueError):
            brain.move_many(self.root,[['raw/packs','raw/a'],['raw/packs/v2','raw/b']],'nested',archive_root=self.archive)

    def test_raw_cannot_leave_raw(self):
        with self.assertRaises(ValueError):
            brain.move_many(self.root,[['raw/notes','wiki/notes']],'wrong',archive_root=self.archive)

if __name__=='__main__':
    unittest.main()
