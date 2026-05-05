"""
Script de setup pour l'utilisateur catalogue.pos et le rôle POS Manager.
À exécuter UNE SEULE FOIS sur chaque nouveau site (dev ou prod) :

    cd frappe-bench
    bench --site <site_name> execute booking_ristourne.setup.setup_catalogue_pos.run

Ou via python :
    cd frappe-bench/sites
    python3 -c "
    import frappe
    frappe.init(site='<site_name>')
    frappe.connect()
    from booking_ristourne.setup.setup_catalogue_pos import run
    run()
    frappe.db.commit()
    frappe.destroy()
    "
"""

import frappe


CATALOGUE_USER     = "catalogue.pos@aquaworld.com"
CATALOGUE_PASSWORD = "CataloguePOS2026!"
POS_MANAGER_ROLE   = "POS Manager"

# Doctypes qui nécessitent Read+Select pour que le POS fonctionne
POS_READ_DOCTYPES = [
    "POS Invoice",
    "POS Opening Entry",
    "POS Closing Entry",
    "POS Profile",
    "Item",
    "Customer",
    "Page",
    "UOM",
    "UOM Conversion Detail",
    "Item Tax Template",
    "Item Tax Template Detail",
    "Account",
    "Tax Rule",
    "Sales Taxes and Charges Template",
    "Sales Taxes and Charges",
    "Item Group",
    "Brand",
    "Item Price",
    "Price List",
    "Warehouse",
    "Currency",
    "Company",
    "Mode of Payment",
    "Mode of Payment Account",
    "Cost Center",
    "Batch",
    "Serial No",
]

# Doctypes avec permissions étendues (write/create/submit) pour POS Manager
POS_FULL_DOCTYPES = {
    "POS Invoice":       {"read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 1, "print": 1, "email": 1, "report": 1},
    "POS Opening Entry": {"read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 1},
    "POS Closing Entry": {"read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 1},
    "POS Profile":       {"read": 1},
    "Customer":          {"read": 1, "write": 1, "create": 1},
}


def run():
    frappe.set_user("Administrator")

    _create_pos_manager_role()
    _create_pos_manager_permissions()
    _create_catalogue_user()

    frappe.db.commit()
    frappe.clear_cache()
    print("\n✅ Setup catalogue.pos terminé avec succès.")


def _create_pos_manager_role():
    if frappe.db.exists("Role", POS_MANAGER_ROLE):
        print(f"  SKIP Role '{POS_MANAGER_ROLE}' already exists")
        return

    role = frappe.get_doc({
        "doctype": "Role",
        "role_name": POS_MANAGER_ROLE,
        "desk_access": 1,
        "is_custom": 0,
    })
    role.insert(ignore_permissions=True)
    print(f"  CREATED Role '{POS_MANAGER_ROLE}'")


def _create_pos_manager_permissions():
    for dt in POS_READ_DOCTYPES:
        full_perm = POS_FULL_DOCTYPES.get(dt, {})
        existing = frappe.db.exists("Custom DocPerm", {"parent": dt, "role": POS_MANAGER_ROLE})
        if existing:
            print(f"  SKIP Custom DocPerm {dt}")
            continue

        perm_data = {
            "doctype": "Custom DocPerm",
            "parent": dt,
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": POS_MANAGER_ROLE,
            "permlevel": 0,
            "read": 1,
            "select": 1,
        }
        perm_data.update(full_perm)

        perm = frappe.get_doc(perm_data)
        perm.insert(ignore_permissions=True)
        print(f"  ADDED Custom DocPerm {dt}")


def _create_catalogue_user():
    if frappe.db.exists("User", CATALOGUE_USER):
        print(f"  SKIP User '{CATALOGUE_USER}' already exists")
        _ensure_user_roles()
        return

    user = frappe.get_doc({
        "doctype": "User",
        "email": CATALOGUE_USER,
        "first_name": "Catalogue",
        "last_name": "POS",
        "enabled": 1,
        "user_type": "System User",
        "send_welcome_email": 0,
        "new_password": CATALOGUE_PASSWORD,
        "language": "fr",
        "roles": [
            {"role": "System User"},
            {"role": "Sales User"},
            {"role": POS_MANAGER_ROLE},
        ],
    })
    user.insert(ignore_permissions=True)
    user.reload()

    from frappe.utils.password import update_password
    update_password(CATALOGUE_USER, CATALOGUE_PASSWORD)

    print(f"  CREATED User '{CATALOGUE_USER}'")


def _ensure_user_roles():
    """Assure que l'utilisateur a les bons rôles même s'il existait déjà."""
    user = frappe.get_doc("User", CATALOGUE_USER)
    existing_roles = {r.role for r in user.roles}
    needed = {"System User", "Sales User", POS_MANAGER_ROLE}
    for role in needed - existing_roles:
        user.append("roles", {"role": role})
        print(f"  ADDED role '{role}' to '{CATALOGUE_USER}'")
    if needed - existing_roles:
        user.save(ignore_permissions=True)
