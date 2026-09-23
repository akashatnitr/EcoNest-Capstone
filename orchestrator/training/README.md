# EcoNest model-tuning data

`dataset.py` converts existing audit events into privacy-filtered *review
candidates*. It does not train a model and it does not automatically approve
historical model output. This distinction is important: a completed service
call does not prove the underlying recommendation was correct or safe.

## Workflow

1. Read audit events with `read_recent_audit_events_async()`.
2. Run `build_review_examples(events)`.
3. A human reviews each candidate and changes `review_status` to `approved` or
   `rejected` in an offline review copy.
4. Run `partition_approved_examples()` to create stable train/evaluation sets.
5. Write the selected set with `write_jsonl_examples()` for offline LoRA/QLoRA
   training.

Training must only use approved examples. Keep the evaluation set held out from
training and use it to compare the tuned model with the current base model.

## Privacy boundary

The builder removes keys containing token, password, secret, authorization,
cookie, email, user ID, or common API/private/access key names. It should receive compact decision-time snapshots,
not raw Home Assistant exports or complete database records. Do not place
training JSONL files containing real household data in git.

## Current limitation

Historical audit events do not consistently contain the complete live home
snapshot that informed a decision. This first phase establishes the safe schema
and review process. A later, explicit data-capture phase should add a compact,
privacy-filtered snapshot to newly recorded decision events.
