# Nebius x NVIDIA Global AI Hackathon — Requirements & Checklist

**Team:** Soham Joshi, Chitrangad Sapate, Ruturaj Nalbalwar
**Representative (submits on Devpost):** TBD — one person must be appointed
**Goal:** Win.

## Key dates (Pacific Time)
| What | When |
|---|---|
| Submission period | Aug 26, 2026 9:00am → **Oct 30, 2026 10:00am PT** (no edits after) |
| Judging | Dec 1 → Dec 15, 2026 |
| Winners | ~Jan 11, 2027 |

Today is Oct 6, so there are about 24 days left. Aim to be feature-complete by Oct 23 and submit on Oct 28, leaving a buffer. Oct 30 10am PT is 10:30pm IST.

## Hard requirements (miss any one and we are disqualified)
- [ ] Runs on **Nebius Token Factory** or **Nebius AI Cloud**
- [ ] Uses **at least one NVIDIA open-source model** (Nemotron: Ultra / Super / Nano, or others such as GR00T or Cosmos)
- [ ] Fits **one track** (see below)
- [ ] **Working demo URL** (hosted app or test build). Free to use, no restrictions, available through Dec 15. Not required for Physical AI. If private, put login credentials in the testing instructions.
- [ ] **Demo video:** public YouTube, **under 3:00**, shows the project running on its target device and how Token Factory and Nemotron are used. No third-party trademarks or copyrighted music.
- [ ] **Public repo** (GitHub, GitLab or Bitbucket):
  - [ ] OSS license file (Apache-2.0, MIT or MPL-2.0) visible in the repo's About section
  - [ ] README with setup and run instructions
  - [ ] README highlights: NVIDIA models used, where Token Factory accelerated the workflow, and any other Nebius services used
  - [ ] All source and assets needed to run it
- [ ] **Text description:** what, why, how it works
- [ ] **Feedback** on Nebius Token Factory, AI Cloud and the NVIDIA tools or models we used (judged on completeness, viability and impact)
- [ ] Everything in **English**
- [ ] Original work, solely owned by the team, with no IP violations. OSS dependencies are fine if we build on them.
- [ ] Must not have been developed with funding, contract or preferential support from Nebius or Devpost
- [ ] If the project existed before Aug 26, write what was significantly updated during the period
- [ ] If we attended a Builders & Brews event, pick the city (a $500 City Winner Award is possible)

## Tracks (pick one)
1. **Coding and Agentic Engineering:** agents or dev tools that write, run and test code via Token Factory.
2. **Best Apps and Agents:** any useful app or agent powered by Nemotron on Token Factory. Use Ultra for hard reasoning and Nano or Super for fast, cheap calls. Nebius Serverless Endpoints and Jobs are encouraged, not required.
3. **Personal AI:** always-on private assistant with persistent memory, skills and tools. Suggested stack: NVIDIA NemoClaw, OpenShell, Hermes Agent and Nebius Serverless.
4. **Physical AI:** robotics, IoT or edge, using Nemotron, GR00T, Cosmos or Sonic. The video needs at least 1 minute of real hardware, or of the key modules running if there is no hardware.

**Stage-one gate (pass/fail):** the project must be a genuine attempt at the track's goal and must really use the required APIs. A rebranded unrelated idea fails.

## Judging (Stage 2, four equally weighted criteria)
1. **Technological implementation:** how well it is built and how effectively it uses Token Factory or AI Cloud and Nemotron.
2. **Design:** a complete, coherent product, not just a proof of concept.
3. **Potential impact:** a specific, credible real problem for a real audience, with the demo showing it solved.
4. **Quality of the idea:** creative, non-obvious use of the models, with genuine understanding of the problem.

**Tie-breaks** go in the order of the criteria above, so Technological Implementation first.

## How we maximize score
- **Technology:** use Nemotron tiers deliberately, with Ultra for reasoning and Nano or Super for fast calls. Show model routing, streaming and tool-calling. Deploy on Nebius Serverless Endpoints or Jobs. Document why Token Factory mattered in the README (speed, cost, throughput).
- **Design:** polished UI, a clean onboarding path, and no dead ends in the demo.
- **Impact:** name one concrete user, one painful problem, and a before/after number.
- **Idea:** pick a problem where an agent loop with Nemotron reasoning is the natural solution.

## Credits and resources
- $25 Token Factory credits: Nebius promo form, activation code `NEBIUS-DEVPOST-GLOBAL26`
- A further $25: join the Nebius Builders Program (https://dev.nebius.com/builders)
- Docs: https://dev.nebius.com/ · Discord: https://discord.gg/eXYTGhgnhK
- Official rules: https://nebiusglobalaihackathon.devpost.com/rules

## Submission-day checklist
- [ ] Demo URL loads from a logged-out browser, with test credentials if it is private
- [ ] Repo is public, license shows in About, README is complete and a fresh clone runs
- [ ] The YouTube video is public, under 3:00, and has no copyrighted music
- [ ] Track selected, description written, feedback written
- [ ] Prior-work explanation included if applicable
- [ ] The Representative submitted on Devpost before Oct 30, 10:00am PT

## Open items (waiting on team)
- [ ] Problem statement and chosen track
- [ ] Appoint the Representative
- [ ] Activate credits and create a Token Factory API key (everyone, or one shared key)
- [ ] Choose a GitHub org or repo for the team
