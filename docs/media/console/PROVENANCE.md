# How these were captured

The SVGs in `docs/media/` are generated from transcripts the test suite pins byte for byte, so they
cannot show something the product no longer prints. **These two PNGs are not.** They are screenshots
of a running console, taken by hand, and nothing can tell you automatically when they stop being
true. That is worth writing down rather than leaving for somebody to discover.

Both were captured on **2026-08-03** against the synthetic tenant — which is why every row in them
is labelled `synthetic` and the console shows a *Demo tenant* badge. Those labels are the console's
own; nothing here was edited.

To re-take them:

```console
$ mkdir -p /tmp/netidemo && cp examples/entra.yaml /tmp/netidemo/neti.yaml
$ neti console --demo -c /tmp/netidemo/neti.yaml -r /tmp/netidemo/decisions.ndjson --port 8833

# decisions.png — a few calls through the hook, so the list has something in it
$ echo '{"tool_name":"send_email","tool_input":{"to":"g-eng-all"}}' \
    | neti hook --demo --mode enforce -c /tmp/netidemo/neti.yaml -r /tmp/netidemo/decisions.ndjson

# approvals.png — needs a control plane, because a pending approval is the paid tier
$ neti-cloud serve --key demo-org-key --port 8730          # in the neti-cloud repository
$ neti login --url http://127.0.0.1:8730 --key demo-org-key
$ echo '{"tool_name":"send_email","tool_input":{"to":"g-dept"}}' \
    | neti hook --demo --org --mode enforce -c /tmp/netidemo/neti.yaml -r /tmp/netidemo/decisions.ndjson
```

The last command is the one worth reading. It returns `"permissionDecision": "ask"` with an
`approval_id` and *"Retry this exact call once it is granted"* — and that approval is what the
screenshot shows waiting for a human.

Run `neti logout` afterwards. `neti login` writes a key to `~/.neti/credentials.toml` that can
approve calls, and leaving a demo one on a machine is exactly the kind of thing that gets forgotten.
