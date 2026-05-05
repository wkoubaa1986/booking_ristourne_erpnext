"""
pos_setup.py — Crée/met à jour automatiquement les POS Profiles
lors du `bench migrate` (hook after_migrate).

Règle : un profil par liste de prix vente (selling=1, enabled=1),
nommé  "POS - {price_list_name}".
Tous les item_groups (is_group=0) sauf "Services & Interventions"
sont autorisés dans chaque profil.
"""
import frappe


EXCLUDED_ITEM_GROUPS = {"Services & Interventions"}
POS_PREFIX = "POS - "


def sync_pos_profiles():
    """Point d'entrée appelé par after_migrate."""
    try:
        price_lists = frappe.get_all(
            "Price List",
            filters={"selling": 1, "enabled": 1},
            fields=["name"],
        )
        if not price_lists:
            return

        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not company:
            company = frappe.get_all("Company", limit=1, fields=["name"])
            company = company[0].name if company else None
        if not company:
            frappe.log_error("Aucune société trouvée", "POS Setup: sync_pos_profiles")
            return

        # Groupes de premier niveau uniquement (enfants directs de la racine)
        root = frappe.db.get_value("Item Group", {"parent_item_group": ""}, "name") or "Tous les Groupes d'Articles"
        item_groups = [
            r.name
            for r in frappe.get_all(
                "Item Group",
                filters={"parent_item_group": root},
                fields=["name"],
            )
            if r.name not in EXCLUDED_ITEM_GROUPS
        ]

        # Mode de paiement par défaut (cash)
        default_payment_mode = _get_default_payment_mode(company)

        for pl in price_lists:
            _sync_one_profile(pl.name, company, item_groups, default_payment_mode)

        frappe.db.commit()

    except Exception:
        frappe.log_error(frappe.get_traceback(), "POS Setup: sync_pos_profiles")


def _get_default_payment_mode(company):
    """Retourne le mode de paiement Cash de la société ou le premier disponible."""
    # Cherche un compte de paiement lié au mode "Cash"
    cash_mode = frappe.get_all(
        "Mode of Payment",
        filters={"type": "Cash", "enabled": 1},
        fields=["name"],
        limit=1,
    )
    if cash_mode:
        return cash_mode[0].name

    # Fallback : premier mode disponible
    any_mode = frappe.get_all("Mode of Payment", filters={"enabled": 1}, fields=["name"], limit=1)
    return any_mode[0].name if any_mode else None


def _get_cash_account(company, payment_mode):
    """Retourne le compte de caisse pour ce mode de paiement et cette société."""
    if not payment_mode:
        return None
    account = frappe.db.get_value(
        "Mode of Payment Account",
        {"parent": payment_mode, "company": company},
        "default_account",
    )
    return account


def _sync_one_profile(price_list, company, item_groups, payment_mode):
    """Crée ou met à jour le POS Profile pour une liste de prix."""
    profile_name = POS_PREFIX + price_list

    if frappe.db.exists("POS Profile", profile_name):
        doc = frappe.get_doc("POS Profile", profile_name)
    else:
        doc = frappe.new_doc("POS Profile")
        doc.name = profile_name

    company_data = frappe.db.get_value(
        "Company", company,
        ["default_currency", "write_off_account", "cost_center"],
        as_dict=True,
    ) or {}
    warehouse = "Magasins - A&S"

    doc.pos_profile_name = profile_name
    doc.selling_price_list = price_list
    doc.company = company
    doc.currency = company_data.get("default_currency") or "TND"
    doc.warehouse = warehouse
    doc.write_off_account = company_data.get("write_off_account") or ""
    doc.write_off_cost_center = company_data.get("cost_center") or ""
    doc.write_off_limit = 0
    doc.hide_images = 0

    # Modes de paiement
    doc.set("payments", [])
    if payment_mode:
        cash_account = _get_cash_account(company, payment_mode)
        doc.append("payments", {
            "mode_of_payment": payment_mode,
            "default": 1,
            "account": cash_account,
        })

    # Groupes d'articles autorisés
    doc.set("item_groups", [])
    for ig in item_groups:
        doc.append("item_groups", {"item_group": ig})

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
