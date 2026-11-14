# Public benchmark on Python Enhancement Proposals

The numbers in [report-3.0.md](report-3.0.md) come from a private record that nobody else can rerun. This
benchmark uses public text instead: the Python Enhancement Proposals (PEPs), pinned to one commit. Anyone
can run it with one command and get the same questions and the same notes. For the word lanes every run I
made gave the same ranks (section 8).

It measures two things on that text:

- **known item**: a question made from a PEP's title finds that PEP.
- **replacement**: when one PEP replaced another, the newer one ranks above the older one, and the older one
  carries a `NEWER VERSION` pointer to the newer one.

It also holds the link prior experiment: do links between notes, or the relation fields alone, improve the
ranking enough to ship? I fixed its variants, weights and bar before I ran them.

How this page was written. Sections 1 to 7 were written before the first run. The question file was frozen
at 2026-11-13 11:57 CET and the link prior plan at the time written in section 7, both before any system ran
on these questions. Section 7a was written after the first run and fixed at 16:14 CET, before its two
variants ran. After the runs I changed wording outside the plans (the sentence on identical ranks above, the
name of the 3.0 checkout in section 1, the tokenizer of the bm25 baseline and the description of the LEKS
call in section 4) and corrected one count in section 2 (the first draft said four pairs were lost to the
license filter, there are five). Added after the first run: the system with the local models (sections 1, 4
and 5) and the scores of the new PEP as a known item (section 5), an extra view that no test or bar uses.
Sections 8 and 9 report the run.

## 1. Run it

The three word systems:

```
pip install -e ".[pdf]"
git worktree add ../leks-3.0 aa1e842
python bench/public/pep_bench.py --leks30 ../leks-3.0 --systems bm25,leks,leks30
```

Python 3.10 or newer; the word systems need the standard library only. pypdf is optional: without it the two
fixture questions that need PDF text are skipped, as in `bench/run_bench.py`. `git worktree add` puts LEKS
3.0.0 (commit `aa1e842` of this repository) next to the checkout. Without `--leks30` the 3.0 system is
skipped and the paired comparison with it is left out.

With the local models as well:

```
pip install -e ".[models,pdf]"
python bench/public/pep_bench.py --leks30 ../leks-3.0 --fetch-models
```

This adds the system `leks+models`: it downloads the pinned revisions of the embedder and the reranker into
`<cache>/models`, starts the model daemon, embeds every passage and then asks the questions with all lanes on.
Without `--fetch-models` it uses weights already on disk: `--weights DIR`, or by default the folder that
`leks models fetch` fills. Without the model packages or the weights the system is skipped and the result
file says why. I have not timed this
system; the script gives it up to 5 hours.

A skipped or failed system still leaves the result files, but the script then exits 1 and names what did not
run. That covers every system in `--systems` (all four by default) and the link prior when `leks` is among
them. To leave one out on purpose, name the others in `--systems`, or pass `--no-link-prior`. Exit 2 means
the PEP sources could not be fetched or did not match their digest, or the questions made from them differ
from `bench/public/questions.json`.

On GitHub the workflow `public benchmark` (`.github/workflows/public-bench.yml`, started by hand) runs all
four systems on a Linux runner on CPU and uploads the result files. Its `models` input turns the model system
off.

The script:

1. downloads the PEP repository at the pinned commit as one archive from GitHub (7,142,559 bytes, 6.8 MiB)
   into a cache folder (`--cache`, default `~/.cache/leks-public-bench`), keeps only the
   `peps/pep-NNNN.rst` files and checks their content digest;
2. converts every PEP whose own text puts it in the public domain or under CC0 into a Markdown note;
3. rebuilds the question file from that metadata and stops if it differs from `bench/public/questions.json`
   by one byte;
4. runs each system in its own process with its own index home and config, LEKS 3.0 from the `--leks30`
   folder and LEKS from this checkout;
5. scores, runs the link prior on the PEP set and on `bench/fixtures`, and writes JSON and Markdown under
   `bench/public/results/` (`--out`).

`--fetch-only` downloads and checks the sources, then stops. `--offline` uses the cache only. Nothing from
the PEP texts is written into this repository: the sources stay in the cache and the notes in a temporary
work folder (`--work DIR` names one and keeps it). `bench/public/.cache/` and `bench/public/work/` are in
`.gitignore` for anyone who keeps them inside the checkout.

## 2. Data

Source: [github.com/python/peps](https://github.com/python/peps) at commit
`2a71e92663616ff5b2c3085ac8ed4f29d38e83d4` (2026-11-13), fetched as
`https://github.com/python/peps/archive/<commit>.tar.gz`. The 747 files `peps/pep-NNNN.rst` at that commit
have the content digest `4de0c812013bced12b0b5c758d88ce5f0276d7759c294d917f72517353ab9fff` (sha256 over
"name, tab, sha256 of the bytes" lines sorted by name), which `bench/public/peps.py` checks. There are no
Markdown PEPs at this commit.

License. The repository has no license file at its root. Each PEP states its own terms in a Copyright
section. [PEP 1](https://peps.python.org/pep-0001/) asks that each new PEP "must be placed under a dual
license of public domain and CC0-1.0-Universal". Older PEPs say "placed in the public domain", a few use
the Open Publication License and some say nothing. I keep only the PEPs whose own Copyright section says
public domain or CC0:

| | PEPs |
|---|---:|
| kept (public domain, CC0, or both) | 714 |
| left out, Open Publication License | 5 |
| left out, no public domain or CC0 statement found | 28 |

The 28 include PEP 401, whose copyright section is a joke. The left out numbers are listed in the manifest
the script writes and in the result file. Among all 747 PEPs there are 46 replacement pairs; leaving the 33
out costs five of them: PEP 600 (three pairs), PEP 216 and PEP 5 have no statement.

Conversion (`bench/public/peps.py`). Each PEP becomes `peps/pep-NNNN.md`:

- front matter: `title`, `pep`, `status` (mapped, see below), `pep_status` (as written in the PEP),
  `pep_type`, `date` (the Created header as YYYY-MM-DD), `superseded_by` from the Superseded-By header and
  `supersedes` from the Replaces header, each as `"[[pep-NNNN]]"` or, for two or more, a YAML list of them,
  and `license`;
- a title line `# PEP N: Title`;
- the body with reStructuredText headings as Markdown headings, `:pep:` roles and links to
  `peps.python.org` as `[[pep-NNNN]]` wikilinks, other roles as their text, and the banners that
  `.. superseded::`, `.. rejected::`, `.. withdrawn::` and the canonical-doc directives stand for written
  out as sentences. Comments and other directives are dropped, code blocks keep their indent.

Status mapping. LEKS reads the `status` word through its list of status words. PEP statuses are defined in
[PEP 1](https://peps.python.org/pep-0001/). Rejected and Withdrawn need care: PEP 1 keeps them "on record"
and nothing replaced them, so they are no replacement case. LEKS itself reads the words "rejected" and
"withdrawn" as superseded, which would treat them like replaced notes. I write `archived` for them instead.

| PEP status | written as | LEKS (this checkout) reads | LEKS 3.0 reads | why |
|---|---|---|---|---|
| Active | current | current | current | in force; process and informational PEPs that never end |
| Accepted | current | current | current | the decision holds now, only the implementation is open |
| Provisional | current | current | current | accepted for now, may still change |
| Final | record | record | record | implemented and closed; LEKS already reads "final" as record |
| Superseded | superseded | superseded | superseded | PEP 1: superseded by a different PEP, "rendering the original obsolete" |
| Rejected | archived | historical-record | unknown | kept on record, never took effect, nothing replaced it |
| Withdrawn | archived | historical-record | unknown | PEP 1 treats it like Rejected |
| Deferred | unknown | unknown | unknown | parked, no decision either way |
| Draft | unknown | unknown | unknown | open proposal, not in force |

The two LEKS versions read the same notes. "archived" is a status word in this checkout and unknown to 3.0,
so part of any difference between them on current questions can come from that row.

Relations. Superseded-By and Replaces are the only relation headers. A pair (old, new) exists when the old
PEP names the new one in Superseded-By or the new one names the old one in Replaces, and both are kept.

## 3. Questions

`bench/public/questions.py` makes them from metadata only (number, title, Superseded-By, Replaces); no body
text and no result went into a question. The file `bench/public/questions.json` has the sha256

`8c2924b24a6a8a079acdd6ffc6d88d1cfdedf86b64290e22c114e08c84062323`

Rule:

- **q1 known item**, one per kept PEP: the title with "PEP n" and the bare number n of the PEP itself
  removed, non-ASCII punctuation turned into spaces. Kept when at least two words of three or more letters
  remain (7 PEPs fail, for example "Python 3000" becomes "Python"). When two PEPs give the same query, both
  are dropped as ambiguous (12 PEPs, for example 422 and 487 share their title). Gold is the PEP itself.
- **q2 replacement**, one per (old, new) pair: the old PEP's title with both numbers removed, same cleaning
  and length rule. Gold is the new PEP, the old one is listed as stale.
- **q2c**: the q2 pairs again with the word "current" in front. LEKS applies its status priors only when a
  question asks what holds now, and "current" is one of the words it reads that way.
- **q3** (newest PEP on a topic): not made. Only 4 chains of three or more PEPs exist: 241, 314, 345, 426,
  566; the same chain without 426; and 245 or 246 through 3124 to 443. That is too few to report a rate.

| set | questions | note |
|---|---:|---|
| q1 | 695 | 33 of them ask for a PEP that has a newer one |
| q2 | 41 | 36 old PEPs; 27 pairs come from Superseded-By, 37 from Replaces, 23 from both |
| q2c | 41 | same pairs |

41 pairs is a small set: one pair moves a rate by 2.4 points, and the 95% intervals below are wide. In 5
pairs the new PEP itself has a newer one (for example 241 to 314, where 314 was later replaced by 345); the
pair still asks only that the newer of the two ranks above the older.

q1 and q2 share query text with opposite gold: the q1 question for PEP 563 asks for 563, the q2 question
with the same words asks for 649 above 563. A system that lifts newer versions loses the first to win the
second, so I report q1 split by whether the PEP has a newer one.

## 4. Systems

| name | what runs |
|---|---|
| bm25 | SQLite FTS5 with its default `bm25()` and its default tokenizer (`unicode61`, no stemming) over one row per note (the whole file), the query as an OR of its words, no priors |
| leks | LEKS from this checkout with no models configured: the engine's `search(q, k=20, per_doc=1, rerank=True)`, defaults otherwise |
| leks30 | the same call on LEKS 3.0 |
| leks+models | LEKS from this checkout with its default embedder and reranker (`lightonai/mDenseOn` and `lightonai/mLateOn` at the revisions pinned in `leks/config.py`): the model daemon started the way LEKS starts it, every passage embedded before the first question, the same call in strict mode |

The MCP search tool calls the same `search` with `rerank=True`, at most 12 results and up to two passages per
note. Here `per_doc=1` gives one row per note, so ranks count notes. `k` only cuts the list: the ranking, the
status priors and the lift of a newer version all happen before the cut, so the top 10 of a `k=20` call
equals a `k=10` call.

With no models configured LEKS runs its word lanes only and says so in its lanes line; that is what leks and
leks30 measure here. In strict mode a lane that does not answer stops the search with an error, so
leks+models either reports all lanes or fails; it never gives word lane numbers under its name.

## 5. Metrics and tests

Ranks count notes, 1 is the top. A note outside the top 10 counts as a miss.

- q1: hit@1, hit@5 and MRR@10 (mean of 1/rank, 0 for a miss), over all q1 and split by whether the PEP has
  a newer one.
- q2 and q2c: **current above replaced**: the new PEP is in the top 10 and the old one is either absent or
  ranked lower (the same rule as `current_above_stale` in `bench/run_bench.py`). **Pointer rate**: among
  the pairs whose old PEP is in the top 10, the share where that hit carries a NEWER VERSION pointer to one
  of its direct successors. A pointer to any other note counts as a wrong pointer and is reported apart.
- q2 and q2c, the new PEP as a known item: hit@1, hit@5 and MRR@10 with the new PEP as gold, scored like q1.
  Added after the first run; no test or bar uses it.
- Rates carry Wilson 95% intervals, MRR a percentile bootstrap 95% interval (10,000 draws, seed 7).
- leks against leks30, paired on the same questions: two-sided exact McNemar test on the per-question
  outcomes of q1 hit@1, q1 hit@5, q2 and q2c current above replaced, and the pointer on the pairs where both
  list the old PEP. I give the discordant counts with each p value. leks against bm25 and leks+models
  against leks the same way, for context.
- As a check of how each version reads the relations, the number of pairs for which its index holds an
  edge from the old PEP to one of its successors, and the edges that match no pair.

## 6. Link prior experiment

The question: should links between notes (wikilinks in the text and the relation fields, which are
wikilinks too) enter the ranking? My rule is that relation fields enter the ranking only on a measured
gain. The prior lives outside the engine, in `bench/public/link_prior.py`, as a re-ranker over the top 20
of `leks` (this checkout).

## 7. Link prior plan, fixed before the run (2026-11-13 12:01 CET)

Link graph: every `[[target]]`, `[[target|text]]` or `[[target#part]]` in a note's full text, front matter
included, resolved by file name without extension, case ignored; links to the note itself are dropped.

Score of a note at LEKS rank r (1 to 20): `1 / (10 + r)` plus boosts. Seeds are the top 3 notes.

- **out**: for each seed s other than d that links to d: `w / (10 + rank of s)`
- **in**: for each seed s other than d that d links to: `w / (10 + rank of s)`
- **state**: d is linked from a state note (kind state by the config's classify globs) and is no state note
  itself: `w / 11`, once

The list is sorted by the new score, ties keep the LEKS order. Nothing outside the top 20 enters.

| variant | out | in | state |
|---|---:|---:|---:|
| control | 0 | 0 | 0 |
| out | 0.25 | 0 | 0 |
| in | 0 | 0.25 | 0 |
| both | 0.25 | 0.25 | 0 |
| both-low | 0.10 | 0.10 | 0 |
| state | 0 | 0 | 0.25 |
| all | 0.25 | 0.25 | 0.25 |

Sets: the PEP questions (q1 for the gain, q2 for the replacement metric) and the 40 questions of
`bench/fixtures`, scored like `bench/run_bench.py` (hit@1 over the answerable questions, `current_above_stale`
over the current ones). The PEP set has no state notes, so there `state` equals control and `all` equals
`both` by construction.

Primary metric: hit@1. Test: two-sided exact McNemar of each variant against control, Holm correction over
the six variants within each set.

Bar to ship: a variant ships only when its hit@1 is higher than control on both sets with a Holm-adjusted p
below 0.05 on each, and the replacement metric does not drop on either set (PEP q2 current above replaced,
fixture `current_above_stale`). Holm makes this stricter than a plain p below 0.05; I report both. If no
variant passes, the prior is tested and not shipped, and this page gives the numbers.

## 7a. Addendum: relation fields alone, fixed before its run (2026-11-13 16:14 CET)

Written after the first run and before any run of the two variants below. Section 7 measured links of
every kind, and there the relation fields count as links like any other. This addendum measures the
relation fields by themselves as a ranking signal, on top of what LEKS already does with them (the NEWER
VERSION pointer and the lift of the newer note).

Relation graph: from each note's front matter, `superseded_by` or `replaced_by` (the note names a newer
one) and `supersedes` or `replaces` (the note names an older one), each a wikilink, a bare name or a list
of them, resolved like the link graph. Each gives an edge from the older note to the newer one.

Score of a note d at LEKS rank r (1 to 20): `1 / (10 + r)`, plus `w / (10 + rank of s)` for each seed s
other than d (the top 3) that has d as a newer version.

| variant | w |
|---|---:|
| rel | 0.25 |
| rel-low | 0.10 |

Sets, primary metric and test as in section 7, with Holm over these two variants within each set. The bar
of section 7 holds, and one rule is added for every variant of sections 7 and 7a: no kind may drop against
control on either set. The kinds are, on the PEP set, q1 hit@1 for PEPs with a newer one, q1 hit@1 for the
others, and q2 and q2c current above replaced; on the fixtures, hit@1 per question category and current
above stale. The added rule can only make shipping harder.

## 8. Results

Run of 2026-11-13 21:32 UTC on macOS 26.7.1 (arm64) with Python 3.13.12, pypdf 7.0.1 and SQLite 3.53.3:

```
python bench/public/pep_bench.py --fetch-only --cache CACHE
python -B bench/public/pep_bench.py --leks30 LEKS30 --cache CACHE --work WORK --offline
```

`CACHE`, `LEKS30` and `WORK` are local folders; `LEKS30` holds `git archive aa1e842`. The fetch got 7,142,559
bytes and the digest matched. The full tables with every interval are in `bench/public/results/pep_results.md`
and `link_prior.md`, the per-question ranks in the JSON files next to them. The results name each LEKS
version with its version number and a digest of its code (`d38bf56ed291` for 3.0.0, `eb7ca7bed5e0` for
3.1.0). Both ran on their word lanes.

Repeat runs. I ran the whole benchmark three times: twice with Python 3.13.12 and once with Python 3.14.6,
all on SQLite 3.53.3. On the PEP set every result list, pointer and edge of both LEKS versions was the same
in all three, and so was every bm25 rank. The 3.14 run had no pypdf, so it left the fixture PDF out of the
index and skipped the two fixture questions that need it; its fixture lists differ for that reason. The two
runs with pypdf agree on every fixture list, and the fixture numbers below come from them, with all 40
questions. After the version number changed to 3.1.0 I ran it a fourth time with Python 3.13.12, and a fifth time on
the release tree after the last code changes. Both times every list, pointer and edge was the same again; only
the version, the code digest, the build times and the run time changed in the result files. The tables below
and the result files come from the fifth run. I also checked the claim in section 4 that `k` only cuts the list: on all
777 PEP questions the top 10 of the `k=20` call equals a `k=10` call for both LEKS versions, pointers
included. With the sources in the cache one full run took about 30 seconds.

**leks+models did not run.** The Python of the run lacked torch, transformers, safetensors and tokenizers, which the model lanes
need, so the script skipped the system and wrote why; nothing was
downloaded. Every number on this page is for the word lanes. The system runs with the two commands in
section 1 or in the `public benchmark` workflow; I have no result from either yet.

### Known item (q1, 695 questions)

| system | hit@1 | hit@5 | MRR@10 |
|---|---|---|---|
| bm25 | 80.6% (77.5 to 83.3) | 96.7% (95.1 to 97.8) | 0.875 (0.855 to 0.894) |
| leks | 89.4% (86.8 to 91.4) | 98.4% (97.2 to 99.1) | 0.934 (0.919 to 0.949) |
| leks30 | 90.1% (87.6 to 92.1) | 98.4% (97.2 to 99.1) | 0.938 (0.923 to 0.952) |

| system | hit@1 when the PEP has a newer one | hit@1 when it has none |
|---|---|---|
| bm25 | 22/33, 66.7% (49.6 to 80.2) | 538/662, 81.3% (78.1 to 84.1) |
| leks | 5/33, 15.2% (6.7 to 30.9) | 616/662, 93.1% (90.9 to 94.8) |
| leks30 | 9/33, 27.3% (15.1 to 44.2) | 617/662, 93.2% (91.0 to 94.9) |

### Replacement (q2 and q2c, 41 pairs each)

| system | q2 current above replaced | q2c current above replaced | pointer, q2 | pointer, q2c |
|---|---|---|---|---|
| bm25 | 5/41, 12.2% (5.3 to 25.5) | 5/41, 12.2% (5.3 to 25.5) | none | none |
| leks | 30/41, 73.2% (58.1 to 84.3) | 32/41, 78.0% (63.3 to 88.0) | 33/40, 82.5% (68.1 to 91.3) | 28/35, 80.0% (64.1 to 90.0) |
| leks30 | 25/41, 61.0% (45.7 to 74.3) | 25/41, 61.0% (45.7 to 74.3) | 25/40, 62.5% (47.0 to 75.8) | 19/34, 55.9% (39.5 to 71.1) |

The pointer counts run over the pairs whose old PEP is in the top 10. No pointer in either LEKS version
named a wrong note. bm25 puts the old PEP first in 36 of the 41 pairs.

The new PEP as a known item (gold is the newer PEP of the pair):

| system | set | hit@1 | hit@5 | MRR@10 |
|---|---|---|---|---|
| bm25 | q2 and q2c | 1/41, 2.4% (0.4 to 12.6) | 26/41, 63.4% (48.1 to 76.4) | 0.318 (0.254 to 0.385) |
| leks | q2 | 25/41, 61.0% (45.7 to 74.3) | 36/41, 87.8% (74.5 to 94.7) | 0.699 (0.575 to 0.814) |
| leks30 | q2 | 20/41, 48.8% (34.3 to 63.5) | 36/41, 87.8% (74.5 to 94.7) | 0.628 (0.506 to 0.744) |
| leks | q2c | 19/41, 46.3% (32.1 to 61.3) | 33/41, 80.5% (66.0 to 89.8) | 0.603 (0.482 to 0.721) |
| leks30 | q2c | 13/41, 31.7% (19.6 to 47.0) | 33/41, 80.5% (66.0 to 89.8) | 0.511 (0.401 to 0.623) |

For bm25 the ranks of q2 and q2c are the same in all 41 pairs. For both LEKS versions the new PEP comes
first less often with "current" in the question, while current above replaced rises from 30 to 32 in this
checkout and stays at 25 in 3.0; I did not look into which notes take rank 1 instead.

### leks against leks30 on the same questions

| measure | n | only leks30 | only leks | p | Holm p over the six |
|---|---:|---:|---:|---:|---:|
| q1 hit@1 | 695 | 5 | 0 | 0.0625 | 0.19 |
| q1 hit@5 | 695 | 0 | 0 | 1 | 1 |
| q2 current above replaced | 41 | 0 | 5 | 0.0625 | 0.19 |
| q2c current above replaced | 41 | 0 | 7 | 0.0156 | 0.0625 |
| q2 pointer, pairs both list | 40 | 0 | 8 | 0.0078 | 0.047 |
| q2c pointer, pairs both list | 34 | 0 | 8 | 0.0078 | 0.047 |

The plan in section 5 asked for the plain p values; I add the Holm column because six measures were tested.
The script writes only the plain p values: I computed the Holm column from them by hand (the smallest p times
six, the next times five and so on, each at least the one before and at most 1).
Both LEKS versions are ahead of bm25 on q1 hit@1, q1 hit@5, q2 and q2c, each with p below 0.01.

### How each version reads the relations

| system | edges | pairs covered | old PEPs covered | edges that match no pair |
|---|---:|---|---|---:|
| leks | 36 | 33 of 41 | 30 of 36 | 6 |
| leks30 | 30 | 25 of 41 | 24 of 36 | 6 |

A pair counts as covered when the index holds an edge from its old PEP to one of that PEP's successors. LEKS
keeps one newer version per note.

### What the numbers say

- Known item: the two LEKS versions are close. 3.0 has 5 questions at rank 1 that this checkout has at rank
  2, and none the other way (p 0.0625). Both are about 9 points of hit@1 ahead of the bm25 baseline.
- Replacement: this checkout ranks the newer PEP above the older one in 5 more pairs than 3.0, 7 more with
  "current" in the question, and puts the NEWER VERSION pointer on the older one in 8 more pairs. It loses
  no pair that 3.0 wins. After the Holm correction only the pointer gain stays below 0.05.
- Most of that comes from one change: this checkout also reads the relation from the newer note, a single
  `supersedes` or `replaces` value in its front matter. That gives it 8 more covered pairs from 6 old PEPs.
  For 247, 381, 382, 422 and 724 only the newer PEP names the older one. PEP 563 names its two successors
  as a list, which neither version reads, and PEP 649 names 563 in a single value, which only this checkout
  reads. All 5 q2 gains and 6 of the 7 q2c gains are such pairs. The seventh q2c gain (246 to 3124) comes
  from the status word: PEP 246 is Rejected, and only this checkout lowers an `archived` note on a question
  with "current".
- The same change costs four of the five known-item questions this checkout loses. For PEPs 247, 381, 563
  and 724 it now knows the newer PEP and lifts it right above the asked one, which comes second with its
  pointer. The fifth is PEP 3130, "Access to Current Module/Class/Function": its title holds "current", so
  LEKS reads the question as asking what holds now, and this checkout lowers the Rejected PEP as above.
- When a PEP has a newer one, asking for it by its own title puts a direct successor first in 22 of the 33
  cases in this checkout and in 18 in 3.0. That is the intended behavior: LEKS answers with what holds now
  and marks the older note. In this checkout all 33 asked PEPs stay in the top 5, 21 of them at rank 2, and
  each one that is not first carries its NEWER VERSION pointer.
- The six edges that match no pair are relations the headers do not carry. The texts of PEPs 9, 228, 262,
  358, 521 and 536 say in words that PEPs 12, 3141, 345, 3137, 567 and 701 replaced them; PEP 358 says
  "partially". I read each passage.

### Link prior

| variant | PEP q1 hit@1 | lost, gained | PEP q2 above | PEP q2c above | fixture hit@1 | lost, gained | fixture current above stale |
|---|---|---|---|---|---|---|---|
| control | 621/695, 89.4% | | 30/41 | 32/41 | 27/37, 73.0% | | 6/6 |
| out | 445/695, 64.0% | 194, 18 | 22/41 | 22/41 | 25/37, 67.6% | 2, 0 | 6/6 |
| in | 370/695, 53.2% | 255, 4 | 35/41 | 32/41 | 22/37, 59.5% | 5, 0 | 6/6 |
| both | 397/695, 57.1% | 234, 10 | 26/41 | 20/41 | 25/37, 67.6% | 2, 0 | 6/6 |
| both-low | 534/695, 76.8% | 98, 11 | 27/41 | 30/41 | 25/37, 67.6% | 2, 0 | 6/6 |
| state | 621/695, 89.4% | 0, 0 | 30/41 | 32/41 | 24/37, 64.9% | 4, 1 | 6/6 |
| all | 397/695, 57.1% | 234, 10 | 26/41 | 20/41 | 24/37, 64.9% | 4, 1 | 6/6 |
| rel | 610/695, 87.8% | 12, 1 | 35/41 | 35/41 | 27/37, 73.0% | 0, 0 | 6/6 |
| rel-low | 613/695, 88.2% | 9, 1 | 31/41 | 34/41 | 27/37, 73.0% | 0, 0 | 6/6 |

The PEP set has 713 notes, 1,622 links, 40 relation edges and no state note; the fixtures have 43 notes, 15
links, 1 relation edge and one state note. The relation graph of section 7a reads lists, so on the PEP set it
holds all 41 pairs except the one with PEP 543, which the index does not hold. On the PEP set every variant
of section 7 that changes the order loses hit@1 with a Holm-adjusted p below 1e-17; `rel` loses it with
Holm p 0.0068 and `rel-low` with 0.021. On the fixtures no difference is significant (Holm p 0.375 to 1).
Intervals and the plain p values are in `link_prior.md`.

The kinds of the PEP set (questions right):

| variant | q1, PEP has a newer one | q1, PEP has none | q2 above | q2c above |
|---|---:|---:|---:|---:|
| control | 5/33 | 616/662 | 30/41 | 32/41 |
| out | 9/33 | 436/662 | 22/41 | 22/41 |
| in | 1/33 | 369/662 | 35/41 | 32/41 |
| both | 6/33 | 391/662 | 26/41 | 20/41 |
| both-low | 6/33 | 528/662 | 27/41 | 30/41 |
| state | 5/33 | 616/662 | 30/41 | 32/41 |
| all | 6/33 | 391/662 | 26/41 | 20/41 |
| rel | 1/33 | 609/662 | 35/41 | 35/41 |
| rel-low | 4/33 | 609/662 | 31/41 | 34/41 |

On the fixtures control answers code 2/6, current 3/6, document 2/4, fact 13/14, history 4/4 and name 3/3
at rank 1. Every variant of section 7 answers fewer fact and name questions; `in` also answers fewer current
ones. `rel` and `rel-low` change no fixture answer.

| variant | gain on both sets | no replacement loss | kinds down |
|---|---|---|---|
| out | no | no | fixtures: fact, name; PEPs: q1 has none, q2, q2c |
| in | no | yes | fixtures: current, fact, name; PEPs: q1 has newer, q1 has none |
| both | no | no | fixtures: fact, name; PEPs: q1 has none, q2, q2c |
| both-low | no | no | fixtures: fact, name; PEPs: q1 has none, q2, q2c |
| state | no | yes | fixtures: fact, name |
| all | no | no | fixtures: fact, name; PEPs: q1 has none, q2, q2c |
| rel | no | yes | PEPs: q1 has newer, q1 has none |
| rel-low | no | yes | PEPs: q1 has newer, q1 has none |

**Verdict: tested, not shipped.** No variant raises hit@1 on either set, so none passes the bar, and every
variant lowers at least one kind. The relation variants come closest: they leave the fixtures as they are
and raise the replacement metric on the PEP set (`rel`: q2 from 30 to 35 of 41, q2c from 32 to 35), and
they pay for it with known-item answers in both kinds of q1: 12 lost and 1 gained for `rel`, 9 and 1 for
`rel-low`. The `in` variant also helps q2 (35/41) and pays 255 known-item answers for 4.

Why the boosts hurt follows from the formula. Near the top the base scores are close (1/11 at rank 1, 1/14
at rank 4) and a link with the top hit adds 0.25/11, which is more than that gap: a note at rank 2 to 4 that
links to or from the top hit passes it. Even the low weight, 0.10/11, is more than the gap between rank 1
and rank 2. PEPs link to each other a lot, so with the 0.25 weights the top hit lost its place in 194 to 255
of the 621 questions control answered. The relation graph has 40 edges against 1,622 links, so far fewer
lists change, and the arithmetic is the same: with `rel` the newer note of any of the top three passes the
top hit from rank 2 or 3, and from rank 4 when its seed is at rank 1 or 2; with `rel-low` it passes from rank
2. When the top hit is the asked PEP, each such pass costs a q1 answer.

No link or relation boost enters the ranking: the code stays in `bench/public/link_prior.py` and the engine
is unchanged. The relation fields keep the role they have now, which the replacement results measure: they
mark the older note with a NEWER VERSION pointer and move the newer one up.

## 9. Limits

- **One kind of text.** PEPs are formal, carefully titled documents from one community, densely linked to
  each other. Personal notes, meeting notes, tickets or code docs may behave differently, and these numbers
  say nothing about them.
- **Easy questions.** Each question is a PEP title without its numbers, and those words also stand in the
  note's title line and front matter. Real questions use other words. That helps word lanes and bm25 alike,
  and nothing here tests paraphrase.
- **A small replacement set.** 41 pairs from 36 old PEPs: one pair moves a rate by 2.4 points, and the 95%
  intervals of the replacement rates are 12 to 32 points wide. Leaving out 33 PEPs for their license removed
  five more pairs.
- **The q1 and q2 trade-off.** A system that lifts newer versions loses known-item questions about replaced
  PEPs to win replacement questions. Neither metric alone says which system is better, so I report both.
- **Lists in front matter are not read.** When a PEP names two or more successors or predecessors, the
  converter writes a YAML list. LEKS reads a single value in `superseded_by` and, in this checkout only, in
  `supersedes`; it reads no list in either. That causes 7 of the 8 pairs this checkout misses (PEPs 245,
  246, 397, 486 and 3124 to their successors). I kept plain YAML in the notes instead of shaping them to
  what the engine reads.
- **One PEP is not indexed.** PEP 543 holds a line that the LEKS secret filter takes for a password, so both
  versions skip the file (713 notes against 714 for bm25). Its pair with PEP 748 then counts as above for
  both LEKS versions, because the old note is absent, and as a miss for bm25, which ranks 543 first.
- **One status word differs between the versions.** `archived` is a status word only in this checkout;
  3.0 reads it as unknown. On questions with "current" only this checkout lowers Rejected and Withdrawn
  PEPs. Of the outcome differences between the versions, one known-item question (PEP 3130) and one q2c
  pair (246 to 3124) come from that.
- **Word lanes only.** No model lane was measured (section 8). With the embedder and the reranker the
  numbers can move either way.
- **A plain baseline.** bm25 uses SQLite's default tokenizer without stemming and scores whole notes; LEKS
  stems words (Porter) and ranks parts of notes, with a score for the whole note added. I did not run a
  stemmed or chunked BM25, so I cannot say how much of the gap comes from that.
- **The link prior test.** The fixture set is tiny (37 answerable questions, 15 links, 1 relation edge), so
  it could show a loss but hardly a gain, and the PEP set has no state notes, so `state` equals control
  there. The weights I fixed before the runs are large next to the score gaps near the top. Smaller weights
  were not tried; trying them would need a new plan written before that run. The relation variants were
  planned after the first run had shown the section 7 results.
- **Same results elsewhere.** I got identical ranks with Python 3.13.12 and 3.14.6 on SQLite 3.53.3 on
  macOS. Another SQLite version or another system may tokenize or score some notes differently. Python 3.10,
  the package's floor, was not tried for a full run; the CI test job runs the benchmark's unit tests on it.
