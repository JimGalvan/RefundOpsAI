"""The task message the investigator receives for one case."""

from investigator.sites import Site


def build_task(order_ref: str, paypal_record: str, sites: list[Site]) -> str:
    lines = [
        "Investigate this PayPal case. Record every fact you rely on with record_evidence, then call "
        "finish_investigation.",
        "",
        paypal_record.strip(),
        "",
        f"The store's order number for this payment is {order_ref}.",
        "",
        "The merchant's tools you may visit, in this order (visit nothing else):",
    ]
    for number, site in enumerate(sites, start=1):
        if site.credential_id:
            access = (
                f'sign in with the saved login "{site.credential_name}" '
                f"(credential id {site.credential_id}; use fill_credential for the email and password boxes)"
            )
        else:
            access = "no sign-in needed"
        lines.append(f"{number}. {site.brand}, {site.purpose}: {site.start_url} ({access}). {site.playbook}")
    return "\n".join(lines)
