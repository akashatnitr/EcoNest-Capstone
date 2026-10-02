# EcoNest model-tuning data

`dataset.py` converts existing audit events into privacy-filtered *review
candidates*. It does not train a model and it does not automatically approve
historical model output. This distinction is important: a completed service
call does not prove the underlying recommendation was correct or safe.

## Resident feedback from User Prompts

After a completed User Prompts result, the resident may save a 1–5 rating,
comment, and suggested correction. Ratings are in `command_feedback`;
corrections and their reviews are in `command_feedback_corrections`. A correction
is **not** used automatically. A signed-in superadmin must compare it
with home evidence and record a decision plus evidence note through
`GET /command/feedback/pending` and `POST /command/feedback/review`.

For example, the review request is
`{"task_id":"...","user_id":0,"approved":true,"evidence_note":"Checked timestamped dryer reading against the original answer."}`.
Only approved corrections are retrieved for later similar questions by the
same resident and agent. They are answer-quality guidance; current readings
remain authoritative. This changes Gemma's *context*, not its weights.

Export the records locally for manual review:

```bash
docker compose -f docker-compose.real.yml exec -T orchestrator \
  python scripts/export_training_dataset.py feedback \
  --output econest_exports/training/resident_feedback.jsonl
```

The export contains real household prompts and optional free-text comments.
Keep it out of git, remove personal details before sharing it, and compare
ratings with the original evidence. A low rating identifies a candidate to
fix or evaluate; it does not supply a corrected model answer by itself.

Create second-stage tuning candidates from evidence-approved corrections:

```bash
docker compose -f docker-compose.real.yml exec -T orchestrator \
  python scripts/export_training_dataset.py feedback-candidates \
  --output econest_exports/training/feedback_candidates.jsonl
```

These candidates still have `needs_human_review`: inspect and de-identify each
question, answer, and evidence note before marking it `approved` for `split`.
Do not teach the model transient live readings as permanent facts. A tuning run
also needs enough approved examples for a held-out evaluation set.

## Workflow

1. Export review candidates locally:

   ```bash
   docker compose -f docker-compose.real.yml exec -T orchestrator \
     python scripts/export_training_dataset.py candidates \
     --output econest_exports/training/review_candidates.jsonl
   ```

2. Review every line in that local file. Change `review_status` only to
   `approved` or `rejected`; leave uncertain examples as `needs_human_review`.
   Reject examples with an ambiguous device, unsupported action, incorrect
   reasoning, unverified outcome, or incomplete decision-time context.
3. Create deterministic QLoRA train/evaluation files from approved examples:

   ```bash
   docker compose -f docker-compose.real.yml exec -T orchestrator \
     python scripts/export_training_dataset.py split \
     --input econest_exports/training/review_candidates.jsonl \
     --train-output econest_exports/training/train.jsonl \
     --evaluation-output econest_exports/training/evaluation.jsonl
   ```

4. Upload only `train.jsonl` and `evaluation.jsonl` to the Colab runtime.
   These files use chat-format JSONL, ready for Hugging Face/TRL supervised
   fine-tuning.

Training must only use approved examples. Keep the evaluation set held out from
training and use it to compare the tuned model with the current base model.

## Privacy boundary

The builder removes keys containing token, password, secret, authorization,
cookie, email, user ID, address, location, common API/private/access key names,
and IPv4 addresses found in text. It should receive compact decision-time
snapshots, not raw Home Assistant exports or complete database records. Do not
place training JSONL files containing real household data in git.

## Dataset quality gate

Do not train until there are enough **reviewed** examples in every intended
task group and a meaningful held-out evaluation split. The current audit export
is a starting point, not a sufficient production dataset. Add reviewed examples
for ambiguous requests, unsupported devices, no-action decisions, failed
verification, and each desired recommendation type before training.

## Current limitation

Historical audit events do not consistently contain the complete live home
snapshot that informed a decision. This first phase establishes the safe schema
and review process. A later, explicit data-capture phase should add a compact,
privacy-filtered snapshot to newly recorded decision events.
