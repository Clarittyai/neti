# neti console

A product console for the preflight gate. **Every number on screen comes from the real decision
engine running live** — the console holds no fixtures of its own and renders nothing it did not just
receive from `POST /api/gate`.

## Run it

Two processes. Neither needs credentials.

```bash
# terminal 1 — the engine
uv pip install -e '.[console]'
uv run neti serve            # http://127.0.0.1:8722

# terminal 2 — the console
cd web && npm install && npm run dev    # http://localhost:3100
```

With `NETI_TENANT_ID` / `NETI_CLIENT_ID` / `NETI_CLIENT_SECRET` exported, `neti serve` talks to real
Microsoft Graph instead. Same engine, same decision procedure, same records — only the numbers
change. That is the whole point of the seam.

## The demo, in order

1. **Connect.** Nothing resolves until you do; the gate refuses to guess.
2. **Run the scenario.** An agent is asked to offboard contractors, works correctly, and calls
   `remove_group_members` on a group someone nested badly two years ago. Watch `"engineering-all"`
   become **41,203 principals and 37 applications**, and watch the call not reach the server.
3. **Flip observe/enforce and re-run it.** Same verdict both times — only `proceeds` changes. The
   decision was being made correctly the whole time; enforcement decides whether it is acted on.
4. **Fire your own**, including `all-customers` — an Exchange dynamic distribution group that Graph
   cannot count. The gate says it does not know rather than reading a failed lookup as zero.
5. **Evidence**, then **Audit** — the hash chain, verified over the records just written.

## Design notes worth knowing before editing

- **The accent was validated, not chosen.** Cyan was the obvious pick and it fails: against the
  reserved emerald it sits at ΔE 12.5 for normal vision, under the 15 floor. Violet `#8B5CF6` passes
  all six checks.
- **Verdict colours are reserved** and identical in both themes, always with an icon and a label.
  Brightening them for dark mode — the obvious move — drops red against emerald into the warn band.
- **Dark by default** because on the light surface emerald and amber both fall under 3:1. This is a
  status surface.
- **The magnitude does not count up.** An odometer depicts progressive enumeration, which is the
  thing the product refuses to do (`RESOLVER_CONTRACT.md` rule 2). It blur-lands instead.
- **The number is not red.** It is a fact from the resolver; the verdict is a judgement from the
  policy. Colouring the fact conflates them.
- **The ceiling meter is linear.** At a ceiling of 200 against 41,203 the marker is a hairline near
  the left edge and the fill runs off the right. A log scale would make it look like a near miss.

Tokens and the two UI primitives are copied from `clarity-platform`, not imported — `neti` is a
standalone repo and a build-time dependency on the monorepo would undo that.
