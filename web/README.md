# neti/web

The demo page. A standalone Next.js 14 app — **not** part of the claritty monorepo, though it
borrows its design tokens and two UI primitives so the two read as the same product family. Copied
rather than imported: a build-time dependency on the monorepo would undo the separation.

```bash
npm install
npm run dev     # http://localhost:3100
```

## The data is generated, not written

Every number on the page comes from `web/src/data/demo.json`, which is produced by running the real
decision path:

```bash
cd .. && uv run neti demo -o web/src/data/demo.json
```

That matters. A demo assembled from prose drifts from the product within a week and the first
person to check a number finds it. Regenerating is one command, and against a real tenant the same
command produces the same page with real numbers.

## The narrative

Runs backwards from the thing that matters.

1. **The blocked call.** 41,203 people about to lose access, stopped. Leading here earns the thirty
   seconds needed to explain observe mode.
2. **Hour one.** What the credential could reach, with no traffic and no ceilings declared.
3. **Week one.** Observe mode, the distribution, and the proposed ceiling — with its consequence
   over the observed week, because arithmetic is not reviewable and consequences are.
4. **What it does not do.** Currently 3 of 7 incidents caught, with the misses named. A security
   audience that finds the gap itself stops believing the rest.

The synthetic-data disclaimer appears in the footer and in the generated JSON. It stays.
