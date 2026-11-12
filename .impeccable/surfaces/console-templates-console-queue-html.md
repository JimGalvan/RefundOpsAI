---
version: 1
slug: "console-templates-console-queue-html"
primary_target: "console/templates/console/queue.html"
related_targets: ["console/templates/console/case.html"]
---

# Surface brief: RefundOps merchant console

Scope and visitor mode: Operate. The case queue, the case workspace (evidence, recommendation, approval, audit), and the sources page. Desktop-first at 1280 to 1600; usable at 390.

Audience, job, task: support or operations staff at a small online store, working a queue of PayPal inquiries against response deadlines. Task: read what the agent found, check its sources, then approve, edit or reject one recommended PayPal response. Constraints: runs against the PayPal sandbox; evidence is only what the agent verified; no invented customers, metrics or pricing.

Assumptions (inferred): one merchant, one user, English, daylight office use.

Unresolved: whether the queue ships as an AG Grid table; whether a hosted demo needs a login screen.

## Direction contract

THESIS: A case file made of carbon copies. Every fact is a copy tinted by the site it came from, so a decision reads as a stack of sourced copies rather than the model's say-so. Refuses the category default: a dashboard of metric tiles with a chat box and a green "Approve" button floating on grey.

OWN-WORLD: Cool white ground (not cream), ink-navy text, five pastel copy tints that identify sources and always sit beside the source's name (PayPal blue, order-portal canary, carrier rose, policy mint, inbox lilac). Saturated colour is for state only: vermilion for deadline, rejection and conflict, green for confirmed, amber for caution. Hairline seams, no shadows except focus, radius 4px or less. Public Sans for the interface, Spline Sans Mono with tabular figures for ids, amounts, tracking numbers and quotes, small-caps field labels like the boxes on a waybill. Raise from the exposure-record sheets: a conflict is marked with a cross-hatch plus the word Conflict, legible without colour. Raise from the dark developer console: panels divide by hairline seams, and Reject is isolated by empty space and stays outline until focused. Raise from the centre-rail reference setting: citations are numbered keys (E21) that link the rationale to its evidence.

STORY: The lead opens the queue and sees which case is due first. Opening it, they see a recommendation backed by numbered evidence, check any source or screenshot, and approve, edit or reject. After approval they see PayPal's own confirmation: the refund or offer id and the dispute status. They trust it because nothing on screen lacks a source and nothing was sent without their click.

FIRST VIEWPORT: Standard shell: top bar with the wordmark, Cases and Sources, a Sandbox marker. The queue is a table of cases (case and buyer, amount, reason, respond by, state) with a Sync from PayPal button; no metric tiles. The case view has three columns: PayPal record as waybill-style field boxes on the left, evidence as source-tinted copies in the centre, the recommendation on the right ending in a perforated approval stub whose Approve is the one primary action.

FORM: Courier waybill, a multi-copy carbonless form: position 3 of 7 on my grounded list (claim file, chain-of-custody log, waybill, sorting room, freight manifest, ship's log, circulation card). seed: 6d059652a18bc594

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
