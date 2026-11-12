# Product

<!-- impeccable:product-schema 1 -->

> Facts marked *(inferred)* come from the hackathon project brief, not from interviews with merchants.

## Platform

web

## Stack

Django 6.1 with HTMX, Alpine.js and Tailwind (CDN); AG Grid Community for the case queue and the evidence ledger view.

## Users

Support and operations staff at small and midsize online merchants *(inferred)*: lean teams who handle customer complaints and open PayPal inquiries by opening several unconnected websites (order portal, carrier tracking, support inbox, store policy) and copying details between them. They work through a queue of open cases against a response deadline, and they are accountable for any refund they approve.

## Product Purpose

RefundOps investigates PayPal disputes and customer complaints for the merchant. A web agent gathers evidence across those disconnected sites and the PayPal record, recommends one response from the actions PayPal currently allows for that dispute (a full refund by accepting the claim, a partial settlement offer, or evidence), and carries it out through PayPal only after the merchant approves. Success is a defensible, documented decision in minutes instead of an hour of tab-switching. Fewer escalations to claims is a hoped-for benefit that has to be measured, not assumed.

## Positioning

The cross-website investigation, not the refund button: every fact carries its source, it works where the merchant's tools have no integration, and it never moves money without a human approval.

## Operating Context

A queue of PayPal inquiries with response due dates, and a workspace per case. PayPal sandbox payment and dispute records. For the hackathon demo, four mock merchant sites (fulfillment portal, carrier tracking, support inbox, store policy) stand in for real tools. Saved logins live in an encrypted vault that the agent never sees. The product runs against the PayPal sandbox only.

## Capabilities and Constraints

- Each evidence item carries a quote copied word for word from its source, the source URL and a screenshot.
- A recommendation is limited to actions PayPal currently allows and is checked by guardrails (allowed action, amount within what is refundable, citations exist, deadline not passed).
- Two-key rule: the agent can only read; the executor, after merchant approval, makes the PayPal call with an idempotency key, then re-reads PayPal to verify the outcome.
- Text found on pages and in emails is untrusted: an instruction inside it is recorded as evidence of the attempt and ignored.
- Open product decision: hosted demo versus local run only.

## Brand Commitments

The name is RefundOps. There is no logo; the console's look is its own: a cool white ground, ink navy, and a distinct tint for each source. *(Inferred: plain, trustworthy language; no hype.)*

## Evidence on Hand

Two seeded demo cases (`demo_data/cases.json`) and real agent runs stored in the database. No customers, testimonials, benchmarks or pricing exist, and none may be invented.

## Product Principles

1. Evidence before opinion: nothing appears on screen without its source.
2. A person decides: the agent investigates and recommends, and money moves only after approval.
3. Uncertainty is shown: contradictions and gaps are as visible as facts.
4. Every outcome is recorded: what was found, who approved, what PayPal reported.
5. Plain language for people working against a deadline.

## Accessibility & Inclusion

*(Inferred)* WCAG AA contrast, full keyboard operation, visible focus, and no meaning carried by color alone.
