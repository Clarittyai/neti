# neti-cloud

The control plane for [`neti`](https://github.com/neti-gate/neti) — the paid tier.

`neti` is a preflight gate: before an agent's tool call runs, it resolves what the call will
actually touch and stops it when that exceeds a ceiling you declared. All of that runs on one
machine and is Apache-2.0.

This package is the part that cannot. Its whole reason to exist is that **`CONFIRM` means a person
other than the agent's operator should decide**, and asking that person needs somewhere for the
request to go and somewhere for the answer to come back.

```console
$ neti-cloud serve --key $NETI_CLOUD_KEY
$ neti login --url http://localhost:8730 --key $NETI_CLOUD_KEY   # on the agent's machine
$ neti gate --stdio --org -- npx -y @acme/entra-mcp
```

An agent's call resolves to 500 recipients against a ceiling of 50, stops, and appears in a
reviewer's inbox as *"send_email resolves to 500 recipients, above the declared ceiling of 50"*.
They approve; the agent's retry proceeds. The grant is bound to that exact call under that exact
policy, is single-use, expires, and is refused if the target has grown since a human looked at it.

**If this is unreachable, absent, or unpaid, the gate behaves exactly as the free tier.** A control
plane can only ever make a decision more permissive, and only through a named human — so nothing
about paying adds availability risk to enforcement. That property is a test, not a promise.

Licensed BUSL-1.1, converting to Apache-2.0 on 2030-07-31. Unlimited internal use by one
organisation; see [LICENSING.md](https://github.com/neti-gate/neti/blob/main/LICENSING.md).
