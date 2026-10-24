# Known limitations

## Retrieval

- **Retrieval quality is the same as in 1.0.** R1 changes speed, not rankings.
  - On the frozen benchmark it puts the relevant page first for 59.3 % of answerable questions, and within the top 5 for 79.3 %.
  - Every answer key is inside the answer pack for 62.1 %.
- **Paraphrased questions are the weak spot.** R1 ranks the relevant page within the top 5 for 43.8 % of them. The experimental R4 reaches 63.8 % but is not deployed (see the [README](../README.md#deployed-r1-compared-with-experimental-r4)).
- **Unanswerable questions are not recognised.** None of the 40 in the frozen benchmark was. Every pack says that its passages are the closest matches, not a confirmed answer; the agent has to act on that.
- **Distractors hurt.** Adding 10,000 synthetic pages built from real sentences lowers top 5 on the development set from 77.9 % to 36.5 %. The test is adversarial: most of each extra page reuses the knowledge base's own sentences.
- **R5** was measured on the development set only. Its results there are optimistic.

## Knowledge

- **Typed facts cover only part of the knowledge.** Many claims remain prose with sources rather than typed facts with anchors.
- **Record time is known only since claim history began to be versioned.** Earlier record times are unknown.

## Protection

- **The immutable flags have limits:**
  - They stop accidental writes and writes by programs the pre-tool check cannot read.
  - Any process of the same user can remove them deliberately.
  - Renaming a folder that contains source files is not prevented. The integrity check would report the files as missing, and the evidence store can restore them; this was not rehearsed for renames.
- **The pre-tool check is a heuristic.** It can be bypassed by a determined process, which is why the layers behind it do not depend on it.
- **Shared fate.** The evidence store shares the disk and user account of the knowledge base, so an off-disk backup is still needed.
- **AI coding agents.** Text an agent reads is handled by the agent under its own configuration, which may include a cloud-hosted model. LEKS does not control it.
- **Network checks are limited:**
  - A static check covers the imports of the Python tools, not their behaviour at run time.
  - A sandbox probe covers the model helper.
  - Obsidian, Unity, Blender, the code editor and the AI coding agent are separate applications whose network behaviour is not checked.

## Evaluation

- **AI-written questions.** The question authors were separate AI agent sessions. Their independence is procedural, and the answer keys reflect their choices.
- **Scope of the benchmarks.** They measure retrieval and pack coverage, not the correctness of an agent's final answer.
- **One machine.** Performance was measured on one machine. Synthetic pages stand in for a larger knowledge base, and R0's time at 10,027 pages rests on 3 queries.
- **Modelled agent steps.** The agent-step figures are modelled from the 1.0 benchmark. They are not observed agent behaviour.
