"""Blinded labeling site: invited human coders score articles so the AI labels can be validated.

Labelers read each article exactly as the Stage 2 model does (cleaned title and body with the
outlet's self-references removed, `annotate.common.blinded`) and work from the model's own codebook.
They never see the outlet, URL, date, model scores or anyone else's labels, and every article goes
to `coders_per_article` independent labelers (2 for the gold set).

    bundle.py    a label set's blinded bundle, built from the lake (laptop only)
    store.py     private Cloud Storage bucket in production, a local folder for dev and tests
    assign.py    which article a labeler gets next
    form.py      the label form: fields, choices, validation
    codebook.py  the codebook labelers read, cut from prompts/stage1.md and stage2.md
    server.py    the web app on Cloud Run (imports nothing from the pipeline)
    cli.py       `fifth labeling publish | close | invite | revoke | status | sync | serve`

`fifth labeling sync` appends the labels to validation/labels/{set}-{coder}.jsonl in the data repo,
the same files `fifth label` writes, so `fifth eval` and the monitoring dashboard read both.
"""
