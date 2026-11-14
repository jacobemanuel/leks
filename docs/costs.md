# What a question costs on each host

This page has four parts: how Claude Code, GitHub Copilot CLI and Codex CLI bill today, what the 3.0 report measured (on Claude Code only), what the host harness in [bench/hosts](../bench/hosts/README.md) will measure, and what stays unknown until it runs on real accounts. Every billing figure comes from the vendor's own page, cited by URL, and I read all of them on 2026-11-13. Prices and plans change often, so check the page before you rely on a figure here.

## How each host bills

### Claude Code

[Claude Code costs](https://code.claude.com/docs/en/costs) (read 2026-11-13):

- Claude Code charges by API token consumption. It runs on the Pro, Max, Ultra, Team and Enterprise plans or on API billing.
- The dollar figure that `/usage` shows is computed locally at list price. The page says it is not relevant for billing on Pro, Max and Ultra, where usage counts against the plan's limits.
- Across enterprise deployments the page gives an average of about 13 USD per developer per active day and 150 to 250 USD per month, with 90% of users below 30 USD per active day.
- It advises `model: haiku` for simple subagent tasks, and notes that a subagent's requests still draw on usage. It also says agent teams use about 7 times more tokens when teammates run in plan mode.

Headless mode prints one `result` event at the end of a session with `total_cost_usd`, `num_turns` and `modelUsage`, the tokens per model. That cost is the same local list-price estimate, so on a subscription it measures usage, not the bill. Subagents are defined with the `--agents` flag ([CLI reference](https://code.claude.com/docs/en/cli-reference)) or as files ([subagents](https://code.claude.com/docs/en/sub-agents)); each has a `model` field (`sonnet`, `opus`, `haiku`, `inherit` and others), and the main agent starts them with the `Agent` tool, called `Task` before version 2.1.63.

### GitHub Copilot CLI

[Usage-based billing for individuals](https://docs.github.com/copilot/concepts/billing/usage-based-billing-for-individuals) (read 2026-11-13), and the [changelog of June 1, 2026](https://github.blog/changelog/2026-06-01-updates-to-github-copilot-billing-and-plans/):

- Since June 1, 2026 every Copilot plan bills in GitHub AI credits. One AI credit is 0.01 USD.
- A request costs the tokens it uses (input, output and cached input) at the listed rates of the model that served it.
- Monthly credits per plan:

| plan | price per month | base credits | flex credits | credits in all |
|---|---|---|---|---|
| Pro | 10 USD | 1,000 | 500 | 1,500 |
| Pro+ | 39 USD | 3,900 | 3,100 | 7,000 |
| Max | 100 USD | 10,000 | 10,000 | 20,000 |

- Free and Student plans have automatic model selection only.
- Automatic model selection gets a 10% discount on paid plans; the page names Copilot Chat and Copilot cloud agent for it. Copilot Fusion has a 30% discount through November 30, 2026.
- Billed in credits: Copilot Chat, Copilot CLI, cloud agent, Spaces, Spark and third-party coding agents. Code completions and next edit suggestions are not billed in credits.
- Unused credits reset on the 1st of the month at 00:00 UTC.

[Models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing) (read 2026-11-13), USD per 1M tokens, a selection:

| model | input | cached input | output |
|---|---|---|---|
| Claude Haiku 4.5 | 1.00 | 0.10 | 5.00 |
| Claude Sonnet 4.6 | 3.00 | 0.30 | 15.00 |
| Claude Sonnet 5.5 | 2.00 | 0.10 | 10.00 |
| Claude Opus 5.5 | 4.00 | 0.20 | 20.00 |
| GPT-5.3-Codex | 1.75 | 0.175 | 14.00 |
| GPT-6.1 Terra | 1.00 | 0.05 | 5.00 |
| GPT-6 Luna | 0.10 | 0.01 | 0.50 |
| MAI-Code-1.1-Flash | 0.20 | 0.02 | 1.20 |

The page lists Claude Haiku 4.5 with a cache write rate of 1.25 as well. It has no row for automatic model selection.

Pro and Pro+ annual plans that stayed on request billing after June 1, 2026 still count premium requests. [Copilot requests](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/copilot-requests) (read 2026-11-13): each Copilot CLI prompt uses one premium request times the model's multiplier, and tool calls do not count. Pro has 300 premium requests per month, Pro+ 1,500, and each extra request costs 0.04 USD. [Model multipliers for annual plans](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/model-multipliers-for-annual-plans) (read 2026-11-13) lists, among others, Claude Haiku 4.5 at 0.33, Claude Sonnet 4.6 at 9, Claude Opus 4.8 at 27, GPT-5.3-Codex at 6 and MAI-Code-1.1-Flash at 0.25; automatic model selection gets 10% off in Chat, the CLI, the Copilot app and cloud agent, and the page lists no model with a multiplier of 0.

The [CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) (read 2026-11-13) has `--model auto`, `--effort` (low to max) and `--max-ai-credits`, a soft maximum of AI credits per response. Subagents are started with the `task` tool and managed with `list_agents`, `read_agent` and `write_agent`; custom agents are `.agent.md` files with an optional `model` and `model-policy: required` ([custom agents for the CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)). The [SDK page on usage and billing](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/usage-and-billing) (read 2026-11-13) says an `assistant.usage` event comes once per model API call, sub-agents included, gives the session's AI credit cost as `totalNanoAiu` in nano-AI units, and divides by 1e9 to get credits as a convenience that it says to confirm against billing.

### Codex CLI

[Codex pricing](https://learn.chatgpt.com/docs/pricing) (read 2026-11-13):

- Codex comes with the ChatGPT plans: Free (0 USD), Go (8 USD per month), Plus (20 USD), Pro (from 100 USD, at 100, 200 or 500 USD), Business (20 USD per user per month billed annually, 25 USD monthly) and Enterprise and Edu (through sales). With an API key it bills at API rates instead.
- On Plus, local messages per 5 hours depend on the model, for example 15 to 160 on GPT-6.1 Sol, 35 to 285 on GPT-6.1 Terra and 350 to 3,000 on GPT-6 Luna. Pro has no 5-hour limit. Weekly limits may apply.
- The page also lists credit rates per model per 1M tokens, for example 50 input, 2.5 cached input and 250 output on GPT-6.1 Sol, and 2.5, 0.25 and 12.5 on GPT-6 Luna. The page puts a typical GPT-5.6 Sol task at 2 to 15 credits and gives no USD value for a credit.

[OpenAI API pricing](https://developers.openai.com/api/docs/pricing) (read 2026-11-13), Standard tier, USD per 1M tokens, a selection:

| model | input | cached input | output |
|---|---|---|---|
| GPT-6 Astra | 10.00 | 1.00 | 50.00 |
| GPT-6.1 Sol | 2.00 | 0.10 | 10.00 |
| GPT-6.1 Terra | 1.00 | 0.05 | 5.00 |
| GPT-6 Sol | 2.00 | 0.20 | 10.00 |
| GPT-6 Luna | 0.10 | 0.01 | 0.50 |
| GPT-5.3-Codex | 1.75 | 0.175 | 14.00 |

Higher rates apply above 272K input tokens. `codex exec --json` reports tokens in its `turn.completed` events (input, cached input, output, reasoning) and no cost, so the harness prices Codex only when you give it rates with `--rates`. [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents.md) (read 2026-11-13) are TOML files in `.codex/agents` with `name`, `description`, `developer_instructions` and an optional `model`; `agents.enabled` is on by default, and subagents inherit the session's MCP servers.

## What the 3.0 report measured

All of it is Claude Code, one model per session, costs from the CLI's own figure at API list prices ([report-3.0.md](report-3.0.md), line 612), with prompt caching as the host applies it. The CLI's per-model usage listed a single model in each of the 268 sessions of the main run and the sweep, so no subagent or second model took part (line 483).

| test | with LEKS | without | source |
|---|---|---|---|
| 35 questions, main model at medium effort, USD per question | 0.0538 | 0.0650 | Table 9, lines 489 and 492 |
| the same, prompt tokens per question | 36,998 | 51,711 | Table 9, lines 489 and 492 |
| the same, ratio of USD per correct answer (95% CI) | 0.72 (0.56 to 0.88) | | Table 10, line 500 |
| sweep over 9 model and effort settings, 8 questions each | less per correct answer in all 9 | | Table 11, lines 506 to 520 |
| one long session of 26 turns per library, USD | 2.93 | 3.38 | line 529 |
| ten development tasks, USD and seconds | 1.89 and 543 | 1.59 and 446 | line 535 |
| three working days, two scenarios, USD | 10.49 | 12.33 | line 546 |
| the same, cost ratio over 114 paired turns (95% CI) | 0.851 (0.803 to 0.896) | | line 549 |
| small model (alias haiku) as the only model, right of 35 | 23 | 17 | line 582 |
| the same, ratio of cost per correct answer (95% CI) | 0.49 (0.27 to 0.81) | | line 582 |

The development tasks are the one place where LEKS cost more: 0.30 USD more over the ten tasks, with 9 against 7 passed (line 535). Per correct answer the small model with LEKS cost 0.0531 USD against 0.0608 USD for the main model with LEKS (line 584). The report's measurement design is in lines 204 to 217.

## What the harness measures

[bench/hosts/run.py](../bench/hosts/run.py) runs the same questions on one host in four arms: `plain` (no LEKS), `leks` (one agent with LEKS), `team` (an orchestrator with two role agents and LEKS) and `subagent` (searches go to a subagent on a small model, with LEKS). Per session it keeps the cost in the host's own unit, the tokens, turns, tool calls, LEKS and subagent calls, the models the host names, the wall time and whether the answer names a gold file. [bench/hosts/summarise.py](../bench/hosts/summarise.py) pairs the arms per question and gives the mean difference and the ratio of sums with bootstrap intervals over questions, and a blind grading sheet turns the answers into right and wrong. [bench/hosts/smoke.sh](../bench/hosts/smoke.sh) checks which CLIs are installed and signed in and runs 16 sessions on each.

The cost unit differs by host:

| host | what the harness records | what that is |
|---|---|---|
| Claude Code | `total_cost_usd` and tokens per model | the CLI's list-price estimate; the bill on API billing, usage of the plan on a subscription |
| Copilot CLI | `totalNanoAiu` / 1e9 as AI credits, times 0.01 as USD, and the premium request figure when the CLI prints one | credits as billed since June 1, 2026, if the 1e9 divisor holds |
| Codex CLI | tokens; USD only at the rates you pass with `--rates` | a price you choose, which is the bill only on API billing |

The bench corpus is synthetic and small, and the harness builds the index words only unless `--models-from` names a config with models, so its `leks` arm is closer to the report's `words` arm (0.0554 USD per question, Table 9, line 491) than to `live`.

## What stays unknown until it runs

I ran the harness here only with `--dry-run` and with the fake host, whose numbers are made up. No host CLI ran and no account paid, so there is no measured figure yet for any arm on any host, and none for `team` or `subagent` on Claude Code either. These points need a real run:

- Whether Claude Code's `total_cost_usd` includes the subagents' usage. The harness keeps `modelUsage`, so a run shows whether the small model appears there.
- Whether Copilot CLI's JSON output carries the `session.shutdown` totals under the field names the harness reads (`totalNanoAiu`, `totalPremiumRequestCost` or `totalPremiumRequests`), whether `--usage-output-file` exists in the installed version (the harness passes it only with `--usage-file`), and whether nano-AI units divided by 1e9 match the credits on the bill.
- Which model automatic selection picks on Copilot for these questions, and what it costs: the models and pricing page has no row for it.
- Whether `codex exec` starts subagents in a non-interactive session, whether it reads the project's `.codex/agents` folder when it ignores the user config, and how a subagent's tokens appear in its events.
- How much a Codex session draws from a ChatGPT plan's 5-hour limit, since the plan pages give message ranges and no token figure.
- Whether the savings the report measured on Claude Code (above) hold on the other hosts, and what the agent arms add or save. The report gives no basis to guess either.
