import frappe
from frappe.utils import getdate, today

def yearly_generate_ristournes_job():
    """Runs every 1st January at 10:00 — creates Active Ristournes for ALL customers."""
    try:
        # Import here to avoid circular imports at bench start
        from booking_ristourne.ristourne import generate_active_ristournes_from_previous

        # year=None => uses current year (N), customer=None => ALL customers, dry_run=0 => real creation
        res = generate_active_ristournes_from_previous(
            year=None,
            scope="customer",
            dry_run=0,
            customer=None,
        )

        # Log a compact summary (visible in sites’ logs)
        frappe.logger("booking_ristourne").info({
            "event": "yearly_generate_ristournes_job",
            "ran_for_year": getdate(today()).year,
            "result": {
                "created": res.get("created"),
                "skipped": res.get("skipped"),
                "errors":  res.get("errors"),
            }
        })

    except Exception:
        frappe.log_error(frappe.get_traceback(), "CRON: yearly_generate_ristournes_job")