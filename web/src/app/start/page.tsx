"use client";

/**
 * `/start` — the walkthrough, on its own page.
 *
 * The overview shows this on a first run and then gets out of the way, which is right for the
 * overview and wrong as the only home: somebody who wired one seam and wants the second, or who
 * comes back a week later to turn enforcement on, needs a place to return to rather than a block
 * that vanished the moment their first call was recorded.
 *
 * Same component, same live poll. Nothing here duplicates what the overview renders.
 */

import { Page } from "@/components/Page";
import { Walkthrough } from "@/components/Walkthrough";

export default function StartPage() {
  return (
    <Page
      title="Getting started"
      lede="Read live off this machine. Each step checks itself — run a command in another window and it completes here."
    >
      <Walkthrough />
    </Page>
  );
}
