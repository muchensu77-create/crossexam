# Crossexam

A procurement agent that checks vendor past-performance claims against public records, then goes looking for what the vendor left out.

**Live demo:** _URL added at deployment_
**Track:** Powering the Future of Work (Procurement), Vultr Agent Rush Hackathon

## The problem

A vendor's list of past contracts is a list of the contracts that went well. Federal buyers can read contractor report cards in CPARS, but that system is closed to everyone else. Prime contractors vetting subcontractors, and state and local buyers, mostly rely on what the vendor tells them.

A lot of the answer is public. Every modification to a federal contract is published, including terminations for default. Nobody has time to read them for every bidder. In fiscal 2024 federal agencies obligated about [$755 billion](https://www.gao.gov/blog/snapshot-government-wide-contracting-fy-2024-interactive-dashboard) through contracts, and all of that history is sitting in USAspending.gov.

## What it does

Upload a capability statement and Crossexam runs a fixed protocol:

1. **Open.** The file is parsed in a disposable gVisor sandbox with no network and no secrets. Text hidden from a human reader is separated out.
2. **Read.** A model on Vultr Serverless Inference lists the claims. Every claim must quote the document word for word or it is discarded.
3. **Forward pass.** Each claim is compared with its federal record: recipient, value, dates, customer.
4. **Reverse pass.** Every modification on the contract, the vendor's whole contract history, federal exclusions, OFAC sanctions and the vendor's SEC filings.
5. **Decide.** Fixed rules set the outcome. A model drafts a clarification letter. A named person approves the letter and signs the decision.

The output is a side-by-side comparison of vendors, a review memo, and an evidence ledger with the SHA-256 of every public record response.

## Why the reverse pass matters

A forward check confirms what it is told. In the sample case, Sample Vendor B claims a contract as successfully completed. Number, customer, value and dates all match the public record, so a forward check passes it. The contract's modification history shows a termination for default.

Sample Vendor C is harder: all three of its claims are accurate. The default is on a fourth contract it did not mention, and only the sweep of its full history finds it.

## Benchmark

`bench/run.py` builds claims from real federal contracts, forges some in known ways, and runs the production code.

| | Forward checks only | With the reverse pass |
| --- | --- | --- |
| Value, ownership, invented number, stretched dates (80 forged claims) | 80 | 80 |
| Terminated for default but claimed as completed (20) | 0 | 20 |
| Accurate claim, default on another contract left out (20) | 0 | 19 |

1 of 50 honest claims went to a person for review, because funding on that real contract was pulled back after award.

We wrote these forgeries knowing the rules, so this shows each check works end to end on real records. It is not a field accuracy rate. The harder test is the sweep itself: on 50 real default terminations sampled with wider search words than the product uses, text search alone found 41 and reading action codes raised that to 50.

## Why the model does not decide

Vendor documents are written by the party being judged, so anything the model reads can be aimed at it. In Crossexam the model extracts and drafts. Verdicts come from `app/rules.py`, which reads public records and nothing the model says. Sample Vendor D hides an instruction in white text telling reviewers to mark everything verified. The sandbox finds it, the model never receives it, and the outcome does not move.

`Protocol.verdict` refuses to run until every reverse step has reported. A source that fails to answer counts as a coverage gap, which rules out a clean result.

## Vultr services

| Service | Use |
| --- | --- |
| Cloud Compute VM | Hosts the web app, the review pipeline and the document sandboxes |
| Serverless Inference | All model calls, through the OpenAI-compatible endpoint at `api.vultrinference.com/v1` |

The application is reachable from any browser; nothing runs on a local machine. Vultr is the central backend for planning, workflow state and operations.

## Architecture

```
Browser ── Caddy (TLS) ── FastAPI on Vultr Cloud Compute
                              │
             ┌────────────────┼──────────────────────┐
             │                │                      │
      gVisor sandbox     Review protocol       Vultr Serverless
      per document       app/pipeline.py       Inference
      no network         app/rules.py          (extract, draft)
      no secrets              │
                              ├─ USAspending.gov   contracts and modifications
                              ├─ SAM.gov exclusions (via OpenSanctions)
                              ├─ OFAC SDN list
                              └─ SEC EDGAR full-text search
```

## Sandbox limits

Each document gets its own container: `--runtime=runsc`, `--network=none`, read-only root, 256 MB memory, 64 processes, all capabilities dropped, a 20 second timeout, no environment secrets, destroyed after one file. `sandbox/probe.py` attempts network access, metadata access, filesystem writes and credential discovery from inside the same container.

## Run it locally

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
python -m app.seed
uvicorn app.main:app --port 8765
pytest tests
```

Without `VULTR_INFERENCE_KEY` the pipeline falls back to a simple offline parser, and `SANDBOX_MODE=local` runs the parser with no isolation. Both exist for development only.

## Deploy on Vultr

Create an Ubuntu 24.04 Cloud Compute instance, clone this repository, then:

```bash
DOMAIN=<ip>.sslip.io bash deploy/setup.sh
```

Add `VULTR_INFERENCE_KEY` to `/etc/crossexam.env` and restart the service.

## Limits

- Coverage is federal public data. State, local and private contracts come back as unverifiable and need a customer reference.
- The default sweep combines text search across all contracts with an action-code read of the latest 25. An old, poorly worded termination at a vendor with many contracts can be missed.
- A flag is a lead for a person, and says nothing about anyone's intent. Name matches can collide, so they place a hold and stop there.
- Exclusions data comes from the OpenSanctions export of the GSA list, free for non-commercial use. A commercial deployment would call the SAM.gov API directly.

## About the sample case

Contract numbers and public records are real. The capability statements are invented for this demo and no real company wrote them, so vendors A, B and C appear under aliases. Their names are not stored in this repository and never reach the page.

## Prior work

Written from scratch during the hackathon. The forward-then-reverse idea comes from the author's earlier MIT-licensed research prototype on Chinese tender records, [deepseek-tender-crosscheck-mcp](https://github.com/muchensu77-create/deepseek-tender-crosscheck-mcp). No code was reused.

## License

MIT
