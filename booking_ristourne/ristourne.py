import frappe
from frappe.utils import getdate, add_months, today, now, formatdate
import random
import re
import hmac
import hashlib
from frappe.utils import generate_hash
from collections import defaultdict
from frappe.core.doctype.sms_settings.sms_settings import send_sms as frappe_send_sms
from urllib.parse import quote 
from functools import lru_cache
from frappe import _

# =========================
# === RISTOURNE LOGIQUE ===
# =========================
def _is_fiche_client_allowed(customer_name: str) -> bool:
    """Check the custom field on Customer to see if fiche_client access is authorized."""
    try:
        val = frappe.db.get_value("Customer", customer_name, "custom_autoriser_accès_fiche_client")
        return bool(val) if val is not None else True
    except Exception:
        return True

def find_applicable_ristourne(customer):
    client_doc = frappe.get_doc("Customer", customer)
    group = client_doc.customer_group

    # 1️⃣ Ristourne spécifique au client, uniquement validée
    ristourne = frappe.get_value(
        "Ristourne",
        {"client": customer, "docstatus": 1},
        "*",
        as_dict=True,
    )

    # 2️⃣ Sinon, ristourne par groupe client, uniquement validée
    if not ristourne:
        ristourne = frappe.get_value(
            "Ristourne",
            {"group_client": group, "docstatus": 1},
            "*",
            as_dict=True,
        )

    return ristourne

def get_delivery_notes_in_ristourne_period(customer, valid_from, valid_until):
    result = frappe.db.sql("""
        SELECT name, posting_date, base_net_total
        FROM `tabDelivery Note`
        WHERE docstatus = 1
          AND customer = %s
          AND posting_date BETWEEN %s AND %s
    """, (customer, valid_from, valid_until), as_dict=True)
    return result

def generate_month_list(start_date, end_date):
    months = []
    current = getdate(start_date).replace(day=1)
    end = getdate(end_date).replace(day=1)

    while current <= end:
        months.append(current.strftime("%Y-%m"))
        current = add_months(current, 1)

    return months

def aggregate_sales_by_month(delivery_notes, month_list):
    monthly_sales = {month: 0 for month in month_list}
    total_ht = 0

    for dn in delivery_notes:
        month = getdate(dn["posting_date"]).strftime("%Y-%m")
        monthly_sales[month] += float(dn["base_net_total"] or 0)
        total_ht += float(dn["base_net_total"] or 0)

    return monthly_sales, total_ht

def calculate_cumulative_ristourne(total_ht, paliers):
    ristourne_total = 0
    sorted_paliers = sorted(paliers, key=lambda x: float(x["montant_ht_minimum"] or 0))
    remaining = total_ht

    palier_results = []

    for palier in sorted_paliers:
        min_val = float(palier["montant_ht_minimum"] or 0)
        max_val = float(palier["montant_ht_maximum"]) if palier["montant_ht_maximum"] else float("inf")
        percent = float(palier["pourcentage_de_réduction"] or 0)

        if remaining <= 0:
            applied_ht = 0
            ristourne_applique = 0
        else:
            tranche_haute = min(remaining, max_val - min_val if max_val != float("inf") else remaining)
            if tranche_haute <= 0:
                applied_ht = 0
                ristourne_applique = 0
            else:
                applied_ht = tranche_haute
                ristourne_applique = tranche_haute * percent / 100
                ristourne_total += ristourne_applique
                remaining -= tranche_haute

        palier_results.append({
            "palier_minimum": min_val,
            "palier_maximum": max_val if max_val != float("inf") else None,
            "pourcentage": percent,
            "montant_applique_ht": applied_ht,
            "montant_ristourne": ristourne_applique
        })

    return ristourne_total, palier_results

@frappe.whitelist(allow_guest=True)
def generate_ristourne_report_with_token(token):
    customer = frappe.cache().get_value(f"session_token_{token}")
    if not customer:
        frappe.throw("Session expirée. Veuillez vous reconnecter.")
    return generate_ristourne_report(customer)

def generate_ristourne_report(customer):
    ristourne = find_applicable_ristourne(customer)

    # ✅ If no ristourne → fallback to sales only
    if not ristourne:
        current_year = getdate(today()).year
        first_day_of_year = f"{current_year}-01-01"

        delivery_notes = get_delivery_notes_in_ristourne_period(customer, first_day_of_year, today())
        month_list = generate_month_list(first_day_of_year, today())
        monthly_sales, total_ht = aggregate_sales_by_month(delivery_notes, month_list)

        return {
            "customer": customer,
            "monthly_sales": monthly_sales,
            "total_ht": total_ht,
            "ristourne": 0,
            "ristourne_name": None,
            "paliers": [],
            "has_ristourne": False
        }
    
    # ✅ If ristourne exists → normal flow
    try:
        month_list = generate_month_list(ristourne.get("valable_de"), ristourne.get("valable_jusquà"))
    except Exception:
        current_year = getdate(today()).year
        first_day_of_year = f"{current_year}-01-01"
        month_list = generate_month_list(first_day_of_year, today())

    delivery_notes = get_delivery_notes_in_ristourne_period(customer, ristourne.get("valable_de"), ristourne.get("valable_jusquà"))
    monthly_sales, total_ht = aggregate_sales_by_month(delivery_notes, month_list)

    paliers = frappe.get_all(
        "Palier ristourne",
        filters={"parent": ristourne["name"]},
        fields=["montant_ht_minimum", "montant_ht_maximum", "pourcentage_de_réduction"]
    )

    ristourne_amount, palier_results = calculate_cumulative_ristourne(total_ht, paliers)

    return {
        "customer": customer,
        "monthly_sales": monthly_sales,
        "total_ht": total_ht,
        "ristourne": ristourne_amount,
        "ristourne_name": ristourne.get("name"),
        "paliers": palier_results,
        "has_ristourne": True
    }

@frappe.whitelist(allow_guest=True)
def get_grouped_article_quantities_by_month_with_token(token):
    customer = frappe.cache().get_value(f"session_token_{token}")
    if not customer:
        frappe.throw("Session expirée. Veuillez vous reconnecter.")
    return get_grouped_article_quantities_by_month(customer)

def get_item_group_tree():
    """
    Builds the full Item Group tree from ERPNext, preserving the order (lft)
    and preparing a month_qty dict for each group.
    """
    groups = frappe.get_all(
        "Item Group",
        filters={},  # all groups
        fields=["name", "parent_item_group", "lft", "rgt", "is_group"],
        order_by="lft asc",
    )

    tree = {}
    children_map = defaultdict(list)

    for g in groups:
        name = g.get("name")
        parent = g.get("parent_item_group")
        tree[name] = {
            "name": name,
            "parent": parent,
            "is_group": g.get("is_group"),
            "lft": g.get("lft") or 0,
            "rgt": g.get("rgt") or 0,
            "children": [],
            "month_qty": defaultdict(float),  # month -> qty
        }
        if parent:
            children_map[parent].append(name)

    # link children
    for parent, child_list in children_map.items():
        if parent in tree:
            tree[parent]["children"] = child_list

    return tree

def get_grouped_article_quantities_by_month(customer):
    """
    Returns quantities per Item Group per month for a given customer, based on
    Delivery Notes, following the Item Group hierarchy from ERPNext.

    Output format (compatible with your current JS):

    {
        "months": ["2025-01", "2025-02", ..., "Moyenne"],
        "groups": [
            {
                "item_group": "Osmoseur Domestique",
                "parent_item_group": None,
                "is_group": 1,
                "quantities": [10, 20, ..., avg]
            },
            {
                "item_group": "Osmoseur 5 étages",
                "parent_item_group": "Osmoseur Domestique",
                "is_group": 0,
                "quantities": [4, 12, ..., avg]
            },
            ...
        ]
    }
    """
    # 🔎 Ristourne used to define the start date
    ristourne = find_applicable_ristourne(customer)
    current_year = getdate(today()).year
    if ristourne and ristourne.get("valable_de"):
        start_date = ristourne["valable_de"]
    else:
        
        start_date = f"{current_year}-01-01"

    end_date = f"{current_year}-12-31"

    month_list = generate_month_list(start_date, end_date)
    total_months = len(month_list) if month_list else 0

    # 1) Build Item Group tree
    tree = get_item_group_tree()

    # 2) Fetch delivery note lines for this customer
    #    👉 item_group is taken from the Item master (tabItem), not from the DN line
    dn_items = frappe.db.sql(
        """
        SELECT
            i.item_group AS item_group,
            dni.qty,
            dn.posting_date
        FROM `tabDelivery Note` dn
        JOIN `tabDelivery Note Item` dni ON dn.name = dni.parent
        JOIN `tabItem` i ON i.name = dni.item_code
        WHERE dn.docstatus = 1
          AND dn.customer = %s
          AND dn.posting_date BETWEEN %s AND %s
        """,
        (customer, start_date, end_date),
        as_dict=True,
    )

    # 3) Fill quantities on the leaf group (current Item.item_group)
    for row in dn_items:
        month = getdate(row.posting_date).strftime("%Y-%m")
        qty = float(row.qty or 0)
        group_name = row.item_group

        if not group_name:
            continue

        if group_name not in tree:
            # Group not found in Item Group tree -> put under "Autres"
            if "_AUTRES_" not in tree:
                tree["_AUTRES_"] = {
                    "name": "Autres",
                    "parent": None,
                    "is_group": 0,
                    "lft": 999999,
                    "rgt": 999999,
                    "children": [],
                    "month_qty": defaultdict(float),
                }
            group_name = "_AUTRES_"

        tree[group_name]["month_qty"][month] += qty

    # 4) Roll-up: sum children -> parent (using the tree)
    @lru_cache(maxsize=None)
    def roll_up(group_name):
        g = tree[group_name]

        # If no children, it's a leaf: keep its own month_qty
        if not g["children"]:
            return g["month_qty"]

        total = defaultdict(float)
        for child_name in g["children"]:
            child_tot = roll_up(child_name)
            for m, q in child_tot.items():
                total[m] += q

        g["month_qty"] = total
        return total

    # Find root groups (no parent) and roll up from there
    root_groups = [name for name, g in tree.items() if not g["parent"]]

    for root in root_groups:
        if root in tree:
            roll_up(root)

    # 5) Build final list sorted by lft (same order as Item Group tree)
    result_groups = []

    for g in sorted(tree.values(), key=lambda x: x["lft"]):
        quantities = []
        total_qty = 0.0

        for m in month_list:
            q = float(g["month_qty"].get(m, 0))
            quantities.append(q)
            total_qty += q

        avg = total_qty / total_months if total_months else 0
        quantities.append(round(avg, 2))

        result_groups.append(
            {
                "item_group": g["name"],
                "parent_item_group": g["parent"],
                "is_group": g["is_group"],
                "quantities": quantities,
            }
        )

    frappe.logger().info("Grouped article quantities by month: %s", result_groups)

    return {
        "months": month_list + ["Moyenne"],
        "groups": result_groups,
    }

# ==========================
# ===  LOGIN / OTP / SMS ===
# ==========================

# Anti brute-force par numéro pour la vérification
GLOBAL_CODE_CACHE_KEY = "aw:global_code:lock:"
GLOBAL_CODE_MAX_ATTEMPTS = 10
GLOBAL_CODE_LOCK_SECONDS = 45 * 60  # 15 minutes

def _get_global_password_plain() -> str | None:
    """Single Doctype 'AquaWorld Settings' (Password field: global_login_code)."""
    try:
        from frappe.utils.password import get_decrypted_password
        return get_decrypted_password(
            "AquaWorld Settings", "AquaWorld Settings", "global_login_code", raise_exception=False
        )
    except Exception:
        return None

def _get_global_password_sha256() -> str | None:
    """Hash SHA-256 dans site_config.json (aw_global_login_code_sha256)."""
    try:
        return frappe.local.conf.get("aw_global_login_code_sha256")
    except Exception:
        return None

def _check_global_code(user_input: str) -> bool:
    """Compare le code saisi au mot de passe global (plain depuis Settings ou hash depuis site_config)."""
    if not user_input:
        return False
    plain = _get_global_password_plain()
    if plain:
        return hmac.compare_digest(user_input, plain)
    sha_hex = _get_global_password_sha256()
    if sha_hex:
        cand = hashlib.sha256(user_input.encode("utf-8")).hexdigest()
        return hmac.compare_digest(cand, sha_hex)
    return False

def _inc_attempts(phone_number: str) -> int:
    key = f"{GLOBAL_CODE_CACHE_KEY}{phone_number}"
    attempts = int(frappe.cache().get_value(key) or 0) + 1
    frappe.cache().set_value(key, attempts, expires_in_sec=GLOBAL_CODE_LOCK_SECONDS)
    return attempts

def _get_attempts(phone_number: str) -> int:
    key = f"{GLOBAL_CODE_CACHE_KEY}{phone_number}"
    return int(frappe.cache().get_value(key) or 0)

def _reset_attempts(phone_number: str):
    key = f"{GLOBAL_CODE_CACHE_KEY}{phone_number}"
    frappe.cache().delete_value(key)

# --- SMS helpers ---

def _sms_otp_enabled() -> bool:
    """Lit la case à cocher 'sms_otp' dans AquaWorld Settings (Single)."""
    try:
        return bool(frappe.db.get_single_value("AquaWorld Settings", "sms_otp"))
    except Exception:
        return False

def _send_sms_via_settings(phone: str, message: str) -> bool:
    """Envoie un SMS via le paramétrage 'SMS Settings' de Frappe."""
    try:
        frappe_send_sms([phone], message)
        return True
    except Exception:
        frappe.log_error(frappe.get_traceback(), "OTP SMS: échec d'envoi via SMS Settings")
        return False

@frappe.whitelist(allow_guest=True)
def send_login_code(phone_number):
    """Envoie un OTP à 6 chiffres (stocké 5 minutes). 
    Si AquaWorld Settings.sms_otp est coché, l'OTP est envoyé via SMS Settings.
    """
    frappe.local.flags.ignore_csrf = True
    phone_number = (phone_number or "").strip()

    customers = frappe.get_all(
        "Customer",
        filters={"disabled": 0},
        fields=["name", "custom_liste_telephone"]
    )

    matched_customer = None
    for c in customers:
        if c.custom_liste_telephone:
            numbers = [n.strip() for n in re.split(r"[,\n]", c.custom_liste_telephone)]
            if phone_number in numbers:
                matched_customer = c.name
                break

    if not matched_customer:
        frappe.throw("Aucun client trouvé avec ce numéro.")


    if not _is_fiche_client_allowed(matched_customer):
        return {
            "status": "denied",
            "message": "Votre accès au service est désactivé. Veuillez contacter notre service commercial."
        }

    # Générer et stocker l'OTP 5 minutes
    code = f"{random.randint(100000, 999999)}"
    frappe.cache().set_value(f"login_code_{phone_number}", code, expires_in_sec=300)

    # Envoi conditionnel de l'OTP par SMS
    sms_status = "disabled"
    if _sms_otp_enabled():
        message = f"Votre code de connexion AquaWorld est : {code}. Il expire dans 5 minutes."
        # NOTE: tu peux remplacer "21652371000" par phone_number si ton passerelle attend E.164
        ok = _send_sms_via_settings("216" + phone_number, message)
        sms_status = "sent" if ok else "failed"

    # Log minimal pour debug
    print(f"[OTP] phone={phone_number} code={code} sms={sms_status}")
    return {"status": "sent", "sms": sms_status}

@frappe.whitelist(allow_guest=True)
def verify_login_code(phone_number, code):
    """Vérifie le code saisi :
       - si c'est le mot de passe global => connexion immédiate,
       - sinon vérifie l'OTP (6 chiffres) généré précédemment.
    """
    frappe.local.flags.ignore_csrf = True
    phone_number = (phone_number or "").strip()
    code = (code or "").strip()

    # 1) Anti brute-force par numéro
    if _get_attempts(phone_number) >= GLOBAL_CODE_MAX_ATTEMPTS:
        frappe.throw("Trop d'essais. Réessayez plus tard.")

    # 2) Retrouver le client correspondant au numéro (requis pour la session)
    customers = frappe.get_all(
        "Customer",
        filters={"disabled": 0},
        fields=["name", "custom_liste_telephone"]
    )

    matched_customer = None
    for c in customers:
        if c.custom_liste_telephone:
            numbers = [n.strip() for n in re.split(r"[,\n]", c.custom_liste_telephone)]
            if phone_number in numbers:
                matched_customer = c.name
                break

    if not matched_customer:
        _inc_attempts(phone_number)
        frappe.throw("Aucun client associé à ce numéro.")
        # 🚫 Block if fiche_client access disabled

    if not _is_fiche_client_allowed(matched_customer):
        frappe.throw(
            _("Votre accès à ce service est désactivé. Veuillez contacter notre service commercial."),
            title=_("Accès refusé")
        )

    # 3) Chemin A — mot de passe global (si défini)
    if _check_global_code(code):
        _reset_attempts(phone_number)
        session_token = generate_hash(length=32)
        frappe.cache().set_value(f"session_token_{session_token}", matched_customer, expires_in_sec=3600)
        return {"status": "verified", "token": session_token, "customer_name": matched_customer}

    # 4) Chemin B — OTP
    expected_code = frappe.cache().get_value(f"login_code_{phone_number}")
    if not expected_code or expected_code != code:
        attempts = _inc_attempts(phone_number)
        remaining = max(0, GLOBAL_CODE_MAX_ATTEMPTS - attempts)
        frappe.throw(f"Code invalide ou expiré. Tentatives restantes : {remaining}")

    # Consommer l'OTP et réinitialiser le compteur
    frappe.cache().delete_value(f"login_code_{phone_number}")
    _reset_attempts(phone_number)

    # Générer le token de session
    session_token = generate_hash(length=32)
    frappe.cache().set_value(f"session_token_{session_token}", matched_customer, expires_in_sec=3600)

    return {"status": "verified", "token": session_token, "customer_name": matched_customer}

@frappe.whitelist(allow_guest=True)
def check_token_validity(token):
    customer = frappe.cache().get_value(f"session_token_{token}")
    if customer:
        return {"status": "valid", "customer_name": customer}
    else:
        return {"status": "expired"}

@frappe.whitelist(allow_guest=True)
def log_customer_login(customer_name):

    doc = frappe.get_doc({
        "doctype": "Ristourne Dashboard Logging",
        "client": customer_name,
        "logging_time": now()
    })
    doc.insert()
    doc.submit()
    frappe.db.commit()
    return {
        "status": "logged",
        "message": f"Login time for {customer_name} recorded successfully."
    }

# =========================
# === MESSAGES D'ACCUEIL ==
# =========================

@frappe.whitelist(allow_guest=True)
def get_info_message(customer_name):
    
    customer = frappe.get_doc("Customer", customer_name)
    mois_annee = formatdate(today(), "MMMM yyyy")
    customer_price_list = customer.default_price_list or "Standard Selling"

    # ------------------------------------------------
    # 1) ORDRE PRINCIPAL (1er niveau)
    # ------------------------------------------------
    ORDER = [
        "Osmoseurs Inverses RO",
        "Membranes RO",
        "Pompes & Accessoires",
        "Équipements & Instruments",
        "Adoucisseurs",
        "Équipements de Filtration & Vannes",
        "Produits Chimiques",
        "Réservoirs, Tuyauterie & Robinetterie",
    ]

    # ------------------------------------------------
    # 2) ORDRE HIÉRARCHIQUE (enfants)
    # ------------------------------------------------
    order = {
        "Adoucisseurs": [
            "Adoucisseurs Domestiques",
            "Adoucisseurs Commerciaux",
            "Bacs à sels",
            "Consommables & Accessoires",
            "Vannes de commande"
        ],
        "Vannes de commande": [
            "Vannes adoucisseurs manuelles",
            "Vannes adoucisseurs automatiques"
        ],
        "Équipements & Instruments": [
            "Armoires & composants électriques",
            "Contrôleurs",
            "Instruments de mesure",
            "Tests d’analyse"
        ],
        "Armoires & composants électriques": [
            "Armoires de commande",
            "Accessoires électriques"
        ],
        "Équipements de Filtration & Vannes": [
            "Porte-filtres",
            "Bouteilles FRP",
            "Stérilisateurs UV",
            "Cartouches filtrantes",
            "Médias filtrants",
            "Électrovannes",
            "Vannes multi-voies"
        ],
        "Stérilisateurs UV": [
            "Filtres UV",
            "Accessoires UV"
        ],
        "Vannes multi-voies": [
            "Vannes à pointeau",
            "Vannes manuelles",
            "Vannes automatiques"
        ],
        "Membranes RO": [
            "Membranes RO domestiques (≤100 GPD)",
            "Membranes RO commerciales (≤800 GPD)",
            "Membranes RO industrielles (4040/8040)",
            "Porte-membranes RO"
        ],
        "Cartouches filtrantes": [
            "Cartouches anti-sédiment",
            "Cartouches lavables",
            "Cartouches anti-calcaire",
            "Filtres T33",
            "Cartouches à charbon",
            "Cartouches plissées (anti-bactériennes inf 1 micron)"
        ],
        "Osmoseurs Inverses RO": [
            "Osmoseurs Domestiques",
            "Osmoseurs Commerciaux",
            "Osmoseurs Industriels",
            "Bi-osmose",
            "Fontaines",
            "Accessoires Osmoseurs"
        ],
        "Osmoseurs Domestiques": [
            "RO domestique sans pompe",
            "RO domestique avec pompe",
            "RO flux direct",
            "RO Consommables & Kits d’entretien"
        ],
        "Accessoires Osmoseurs": [
            "Coudes",
            "Connecteurs droits",
            "Connecteurs T",
            "Connecteurs Y",
            "Clips & colliers",
            "Clés de serrage",
            "Électrovannes & automatismes",
            "Raccords spéciaux",
            "Vannes & régulation",
            "Accessoires divers"
        ],
        "Pompes & Accessoires": [
            "Adaptateurs pour booster osmoseurs",
            "Pompes booster pour osmoseurs",
            "Pompes de surface & puits",
            "Pompes multicellulaires",
            "Pompes volumétriques",
            "Pompes doseuses",
            "Accessoires de pompes"
        ],
        "Pompes de surface & puits": [
            "Pompes périphériques",
            "Pompes auto-amorçantes",
            "Pompes inox"
        ],
        "Produits Chimiques": [
            "Antiscalants",
            "Acides & Bases",
            "Produits Désinfectants"
        ],
        "Réservoirs, Tuyauterie & Robinetterie": [
            "Citernes & Réservoirs",
            "Robinetterie",
            "Tuyauterie"
        ],
        "Citernes & Réservoirs": [
            "Réservoirs pressurisés horizontaux",
            "Réservoirs pressurisés verticaux",
            "Citernes alimentaires"
        ],
        "Robinetterie": [
            "Robinets",
            "Mitigeurs"
        ]
    }

    EXCLUDED_PARENT = "Services & Interventions"

    # ------------------------------------------------
    # 3) GROUPES EXCLUS
    # ------------------------------------------------
    excluded_children = frappe.get_all(
        "Item Group",
        filters={"parent_item_group": EXCLUDED_PARENT},
        pluck="name",
    )
    excluded_groups = {EXCLUDED_PARENT} | set(excluded_children)

    # ------------------------------------------------
    # 4) RACINE & GROUPES PARENTS
    # ------------------------------------------------
    root_group = frappe.db.get_value(
        "Item Group", {"parent_item_group": ""}, "name"
    )

    parent_groups = frappe.get_all(
        "Item Group",
        filters={"parent_item_group": root_group, "is_group": 1},
        fields=["name", "item_group_name"],
    )

    def parent_sort_key(g):
        try:
            return ORDER.index(g["item_group_name"])
        except ValueError:
            return 999

    parent_groups = sorted(parent_groups, key=parent_sort_key)

    # ------------------------------------------------
    # 5) ICÔNE PDF
    # ------------------------------------------------
    PDF_ICON = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="26" height="26">
    <path d="M7 3h13l6 6v20H7z" fill="#ffffff" stroke="#000000" stroke-width="1.4" />
    <polygon points="20,3 26,9 20,9" fill="#000000" />
    <rect x="7" y="15" width="19" height="10" fill="#e53935" />
    <text x="16.5" y="22"
            text-anchor="middle"
            font-family="Arial, Helvetica, sans-serif"
            font-size="8"
            font-weight="bold"
            fill="#ffffff">
        PDF
    </text>
    </svg>
    """

    # ------------------------------------------------
    # 6) PDF POUR UN GROUPE  (URL SANITISÉE)
    # ------------------------------------------------
    def get_pdf_for_group(group_name, price_list=None):
        """
        Retourne l'URL du dernier PDF généré automatiquement pour un groupe donné.
        On encode le nom du document pour éviter les problèmes avec &, espaces, etc.
        """
        if not group_name:
            return "#"

        where_parts = [
            "gad.goup_articles = %s",
            "lpd.`génrer_automatique` = 1",
        ]
        params = [group_name]

        if price_list:
            where_parts.append("lpd.liste_des_prix = %s")
            params.append(price_list)

        sql = f"""
            SELECT lpd.name
            FROM `tabListe prix documents` lpd
            JOIN `tabGroup articles doc` gad ON gad.parent = lpd.name
            WHERE {" AND ".join(where_parts)}
            ORDER BY lpd.creation DESC
            LIMIT 1
        """

        rows = frappe.db.sql(sql, tuple(params), as_dict=True)
        if not rows:
            return "#"

        docname = rows[0]["name"]
        if not frappe.db.exists("Liste prix documents", docname):
            return "#"

        # ✅ encodage URL pour gérer les &, espaces, etc.
        encoded_name = quote(docname, safe="")
        return f"/printview?doctype=Liste%20prix%20documents&name={encoded_name}"

    # ------------------------------------------------
    # 7) RENDU RÉCURSIF DES ENFANTS
    # ------------------------------------------------
    def render_children(parent_name):
        html = ""
        children = frappe.get_all(
            "Item Group",
            filters={"parent_item_group": parent_name},
            fields=["name", "item_group_name", "is_group"],
        )

        if not children:
            return html

        def child_sort_key(c):
            ig_name = c["item_group_name"]
            if parent_name in order and ig_name in order[parent_name]:
                return (order[parent_name].index(ig_name), ig_name)
            return (999, ig_name)

        children = sorted(children, key=child_sort_key)

        for c in children:
            if c.name in excluded_groups:
                continue

            pdf = get_pdf_for_group(c.name, customer_price_list)

            if c.is_group:
                html += f"""
                <details class="child-block">
                    <summary>
                        <div class="group-main">
                            <span class="toggle-icon">▸</span>
                            <div class="group-text">
                                <span class="child-label">{c.item_group_name}</span>
                            </div>
                        </div>
                        <a href="{pdf}" target="_blank" class="pdf-btn" title="Ouvrir le PDF">
                            {PDF_ICON}
                        </a>
                    </summary>
                    <div class="child-container">
                        {render_children(c.name)}
                    </div>
                </details>
                """
            else:
                html += f"""
                <div class="leaf-row">
                    <span>{c.item_group_name}</span>
                    <a href="{pdf}" target="_blank" class="pdf-btn" title="Ouvrir le PDF">
                        {PDF_ICON}
                    </a>
                </div>
                """

        return html

    # ------------------------------------------------
    # 8) HTML + CSS (avec hover rouge)
    # ------------------------------------------------
    links_html = """
    <style>
    .price-table {
        width: 100%;
        border-collapse: collapse;
        margin: 20px 0;
    }

    /* En-têtes de groupe / enfants */
    .group-header, .child-block > summary {
        padding: 10px 12px;
        background: #e8f4ff;
        border: 1px solid #ccd;
        border-radius: 6px;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 6px;
        list-style: none;
        font-size: 14px;
    }

    .group-header::-webkit-details-marker,
    .child-block > summary::-webkit-details-marker {
        display: none;
    }

    .group-main {
        display: flex;
        align-items: flex-start;
        gap: 6px;
        flex: 1;
        min-width: 0;
    }

    .group-text {
        display: flex;
        flex-direction: column;
        min-width: 0;
    }

    .toggle-icon {
        font-size: 12px;
        color: #555;
        margin-top: 2px;
        transition: transform 0.2s ease;
    }
    details[open] > summary .toggle-icon {
        transform: rotate(90deg);
    }

    .group-label, .child-label {
        font-weight: 600;
        word-wrap: break-word;
    }
    .group-hint {
        font-size: 11px;
        color: #666;
        margin-top: 2px;
    }

    .group-block {
        border-radius: 6px;
        border: 1px solid #ddd;
        padding: 0;
        margin-bottom: 10px;
    }

    .child-container {
        padding: 6px 12px 10px 12px;
        border-top: 1px solid #ccd;
    }

    .child-block {
        margin-top: 6px;
        border-radius: 4px;
        border: 1px solid #eee;
    }

    .leaf-row {
        padding: 5px 0;
        display: flex;
        justify-content: space-between;
        border-bottom: 1px dotted #eee;
        font-size: 12px;
    }

    /* Bouton PDF + effet hover rouge fort */
    .pdf-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        background: transparent;
        text-decoration: none;
    }
    .pdf-btn svg {
        width: 24px;
        height: 24px;
        transition: transform 0.15s ease, filter 0.15s ease;
    }

    /* 🔥 survol : rouge plus foncé + glow */
    .pdf-btn:hover svg rect {
        fill: #b71c1c;
    }
    .pdf-btn:hover svg path {
        stroke: #b71c1c;
    }
    .pdf-btn:hover svg {
        filter: drop-shadow(0 0 3px rgba(183, 28, 28, 0.9));
        transform: scale(1.05);
    }

    /* Responsive */
    @media (max-width: 768px) {
        .price-table tr,
        .price-table td {
            display: block;
            width: 100% !important;
        }
        .price-table td {
            border-left: 0 !important;
            border-right: 0 !important;
            margin-bottom: 12px;
        }
    }
    </style>
    """

    links_html += f"""
    <table class="price-table">
    <tr>
        <td colspan="4"
            style="padding: 10px; background-color: #f2f2f2; text-align: center;
                font-weight: bold; border: 1px solid #ddd;">
            Voici les derniers liens vers vos listes des prix {customer.customer_group}
            générées ({mois_annee})
        </td>
    </tr>
    <tr>
    """

    col = 0
    for p in parent_groups:
        if p.name in excluded_groups:
            continue

        pdf_parent = get_pdf_for_group(p.name, customer_price_list)

        links_html += f"""
        <td style="padding:10px; border:1px solid #ddd; vertical-align:top; width:25%;">
            <details class="group-block">
                <summary class="group-header">
                    <div class="group-main">
                        <span class="toggle-icon">▸</span>
                        <div class="group-text">
                            <span class="group-label">{p.item_group_name}</span>
                            <span class="group-hint">Voir les sous-catégories</span>
                        </div>
                    </div>
                    <a href="{pdf_parent}" target="_blank" class="pdf-btn"
                    title="Ouvrir le PDF principal">
                        {PDF_ICON}
                    </a>
                </summary>
                <div class="child-container">
                    {render_children(p.name)}
                </div>
            </details>
        </td>
        """

        col += 1
        if col == 4:
            links_html += "</tr><tr>"
            col = 0

    links_html += "</tr></table>"


# ou: context.links_html = links_html

# ou dans un context :
# context.links_html = links_html

# ou context.links_html = links_html

# ou dans un contexte de template :
# context.links_html = links_html

    ristourne = find_applicable_ristourne(customer_name)
    if not ristourne:
        return {
            "message": f"""
                <h3>Bonjour {customer.customer_name} 👋</h3>
                <p>
                    Merci pour votre confiance et votre fidélité !  
                    Nous sommes ravis de vous compter parmi nos clients privilégiés.
                </p>
                <p>
                    Vous pouvez consulter vos <strong>listes de prix personnalisées</strong> 
                    et suivre l'évolution de vos ventes directement depuis ce tableau de bord.
                </p>
            """,
            "links_html": links_html or "<li>Aucune liste trouvée pour le moment.</li>"
        }

    paliers = frappe.get_all(
        "Palier ristourne",
        filters={"parent": ristourne["name"]},
        fields=["montant_ht_minimum", "montant_ht_maximum", "pourcentage_de_réduction"]
    )
    sorted_paliers = sorted(paliers, key=lambda x: float(x["montant_ht_minimum"] or 0))

    current_year = int(today()[:4])
    annee_N = current_year
    annee_N_plus_1 = current_year + 1
    ristourne_annuelle = 3000  # Placeholder

    message = f"""
    <h3>Bonjour {customer.customer_name} 👋</h3>
    <p>
        Un immense merci pour votre fidélité ! Vous faites partie de nos clients privilégiés en tant que 
        <strong>{customer.customer_group}</strong>, et nous sommes heureux de vous accompagner chaque jour dans vos projets et votre croissance.
    </p>

    <p>
        Pour continuer à bénéficier de vos avantages tarifaires exclusifs, il vous suffit de maintenir un chiffre d’affaires annuel HT minimum de 
        <strong>{sorted_paliers[0]["montant_ht_maximum"]} TND</strong>. Ensemble, atteignons de nouveaux sommets !
    </p>
    <p>
        <strong>AquaWorld & Servicing</strong> a mis en place un système de ristourne par paliers afin de renforcer et développer notre relation commerciale. 
        Les détails de ce fonctionnement sont disponibles dans l’onglet <strong>💰 Total Ventes</strong> de votre tableau de bord.
    </p>
    <hr style="border: 1px solid #007bff; margin: 20px 0;">
    <h4>Règle de cumul de la ristourne :</h4>

    <p>
        Des réductions supplémentaires, liées notamment à la quantité commandée et au mode de paiement, s’appliquent en complément de votre liste des prix personnalisée.  
        Pour en savoir plus et optimiser pleinement vos avantages, n’hésitez pas à contacter notre service commercial.
    </p>

    <p>
        Tous vos achats, même ceux bénéficiant de remises sur volume, sont pris en compte dans le calcul de votre ristourne annuelle HT.
    </p>

    <hr style="border: 1px solid #007bff; margin: 20px 0;">
    <div style="background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 8px; padding: 15px; margin: 20px 0;">
        <p><strong>Comment votre ristourne annuelle est accordée :</strong><br>
        La ristourne cumulée sur l’année <strong>{annee_N}</strong> (par exemple, <strong>{ristourne_annuelle:.2f} TND</strong>) est redistribuée sous forme de réductions mensuelles durant l’année <strong>{annee_N_plus_1}</strong>.</p>

        <p style="text-align: center; font-size: 18px; font-weight: bold;">
            Réduction mensuelle : <strong>{ristourne_annuelle / 12:.2f} TND / mois</strong>
        </p>

        <p>Cette réduction est automatiquement appliquée à vos achats mensuels en <strong>{annee_N_plus_1}</strong>, répartie équitablement mois par mois, pour vous permettre de profiter pleinement de vos avantages fidélité.</p>
    </div>
    """

    return {
        "message": message,
        "links_html": links_html or "<li>Aucune liste trouvée pour le moment.</li>"
    }

# ============================================================
# === GENERATION DES RISTOURNES N BASEES SUR L'ANNEE N-1   ===
# ============================================================

def _y_bounds(year: int) -> tuple[str, str]:
    return (f"{year}-01-01", f"{year}-12-31")

def _child_table_fieldname(parent_doctype: str, child_doctype: str) -> str | None:
    meta = frappe.get_meta(parent_doctype)
    for df in meta.fields:
        if df.fieldtype == "Table" and df.options == child_doctype:
            return df.fieldname
    return None

def _period_overlap(y_start, y_end, de, a):
    if not de or not a:
        return False
    de = getdate(de); a = getdate(a)
    ys, ye = getdate(y_start), getdate(y_end)
    return not (a < ys or de > ye)

def _already_exists_for_year(for_year: int, customer: str | None, group_client: str | None) -> bool:
    y_start, y_end = _y_bounds(for_year)
    filters = {"docstatus": 1}
    if customer: filters["client"] = customer
    if group_client: filters["group_client"] = group_client
    rows = frappe.get_all(
        "Ristourne",
        filters=filters,
        fields=["name", "valable_de", "valable_jusquà"],
        limit=5000,
    )
    for r in rows:
        de = r.get("valable_de")
        a  = r.get("valable_jusquà")
        if _period_overlap(y_start, y_end, de, a):
            return True
    return False

def _copy_paliers(src_ristourne_name: str, target_doc):
    """Copie les lignes 'Palier ristourne' (quel que soit le fieldname dans 'Ristourne')."""
    child_field = _child_table_fieldname("Ristourne", "Palier ristourne")
    if not child_field:
        frappe.throw("Impossible de trouver la table 'Palier ristourne' dans 'Ristourne'.")
    paliers = frappe.get_all(
        "Palier ristourne",
        filters={"parent": src_ristourne_name},
        fields=["montant_ht_minimum", "montant_ht_maximum", "pourcentage_de_réduction", "idx"],
        order_by="idx asc",
        limit=1000,
    )
    for p in paliers:
        target_doc.append(child_field, {
            "montant_ht_minimum": p.get("montant_ht_minimum"),
            "montant_ht_maximum": p.get("montant_ht_maximum"),
            "pourcentage_de_réduction": p.get("pourcentage_de_réduction"),
        })

def _compute_prev_year_amount(customer: str, prev_year: int, paliers_from_report: list[dict]) -> tuple[float, list[dict]]:
    """Recalcule le montant de ristourne sur l'année N-1 en réutilisant ta logique."""
    y1, y2 = _y_bounds(prev_year)
    dn = get_delivery_notes_in_ristourne_period(customer, y1, y2)
    months = generate_month_list(y1, y2)
    _, total_ht_prev = aggregate_sales_by_month(dn, months)

    normalized = []
    for p in paliers_from_report or []:
        minv = p.get("palier_minimum") if "palier_minimum" in p else p.get("montant_ht_minimum")
        maxv = p.get("palier_maximum") if "palier_maximum" in p else p.get("montant_ht_maximum")
        perc = p.get("pourcentage")    if "pourcentage"    in p else p.get("pourcentage_de_réduction")
        normalized.append({
            "montant_ht_minimum": minv,
            "montant_ht_maximum": maxv,
            "pourcentage_de_réduction": perc
        })

    amount_prev, breakdown = calculate_cumulative_ristourne(total_ht_prev, normalized)
    return amount_prev, breakdown

def _normalize_customers_arg(customer: str | None) -> list[str] | None:
    """
    Transforme l'argument `customer` en liste de noms de clients à traiter.
    - None/""  -> None (signifie 'tous les clients')
    - "A"      -> ["A"]
    - "A,B,C"  -> ["A", "B", "C"]
    Résout aussi les display names (customer_name) quand possible.
    """
    if not customer:
        return None
    raw = [c.strip() for c in str(customer).split(",") if c.strip()]
    if not raw:
        return None

    existing = set(frappe.get_all("Customer",
                                  filters={"disabled": 0, "name": ["in", raw]},
                                  pluck="name"))
    resolved: list[str] = [n for n in raw if n in existing]

    remaining = [n for n in raw if n not in existing]
    if remaining:
        rows = frappe.get_all(
            "Customer",
            filters={"disabled": 0, "customer_name": ["in", remaining]},
            fields=["name", "customer_name"],
        )
        map_by_custname = {r["customer_name"]: r["name"] for r in rows}
        for label in remaining:
            internal = map_by_custname.get(label)
            if internal:
                resolved.append(internal)

    return resolved or None

def iter_customers(page_size: int = 5000, only: list[str] | None = None):
    """Itère sur les clients actifs. Si `only` est fourni, ne renvoie que ceux-ci."""
    if only:
        existing = set(frappe.get_all("Customer",
                                      filters={"disabled": 0, "name": ["in", only]},
                                      pluck="name"))
        for name in only:
            if name in existing:
                yield name
        return

    start = 0
    while True:
        batch = frappe.get_all(
            "Customer",
            filters={"disabled": 0},
            pluck="name",
            start=start,
            page_length=page_size,
        )
        if not batch:
            break
        for name in batch:
            yield name
        start += page_size

# ---------- helpers SMS ----------

def _sms_otp_enabled() -> bool:
    """Lit la case 'sms_otp' dans AquaWorld Settings."""
    try:
        return bool(frappe.db.get_single_value("AquaWorld Settings", "sms_otp"))
    except Exception:
        return False

def _parse_phones(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,\n;]+", raw)
    out, seen = [], set()
    for p in parts:
        s = p.strip()
        if not s:
            continue
        s = re.sub(r"[^\d+]", "", s)  # garder chiffres et +
        s ="216"+s
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
@frappe.whitelist(allow_guest=True)
def _customer_phones(customer_name: str) -> list[str]:
    row = frappe.db.get_value("Customer", customer_name, ["custom_liste_telephone"], as_dict=True)
    return _parse_phones((row or {}).get("custom_liste_telephone"))

def _format_tnd(v: float) -> str:
    try:
        return f"{float(v):,.2f} TND".replace(",", " ")
    except Exception:
        return f"{v} TND"

def _get_default_company() -> str | None:
    comp = frappe.defaults.get_global_default("company")
    if comp:
        return comp
    return frappe.db.get_single_value("Global Defaults", "default_company") or \
           (frappe.get_all("Company", pluck="name", limit=1) or [None])[0]

def _already_exists_active_for_year(for_year: int, customer: str) -> bool:
    """Vérifie si une Ristourne Active existe déjà pour ce client qui chevauche l'année for_year."""
    y_start, y_end = _y_bounds(for_year)
    rows = frappe.get_all(
        "Ristourne Active",
        filters={"docstatus": 1, "customer": customer},
        fields=["name", "valid_from", "valid_to"],
        limit=5000,
    )
    for r in rows:
        if _period_overlap(y_start, y_end, r.get("valid_from"), r.get("valid_to")):
            return True
    return False

# ---------- GENERATION : crée des *Ristourne Active* si amount_prev > 0 + envoi SMS ----------
@frappe.whitelist(allow_guest=True)
def download_price_list_pdf(name):
    """
    Télécharge le PDF d'un doc 'Liste prix documents' en tant qu'invité.
    Utilisé par la fiche client Ristourne.
    """
    frappe.local.flags.ignore_csrf = True

    if not name:
        frappe.throw(_("Nom de document manquant."))

    # Vérifier que le document existe
    if not frappe.db.exists("Liste prix documents", name):
        frappe.throw(_("Document introuvable ou inexistant."))

    # Générer le PDF
    pdf_data = frappe.get_print(
        "Liste prix documents",
        name,
        print_format=None,      # ou ton format si tu en as un spécifique
        as_pdf=True
    )

    # Réponse HTTP -> téléchargement direct
    frappe.local.response.filename = f"{name}.pdf"
    frappe.local.response.filecontent = pdf_data
    frappe.local.response.type = "download"
    
@frappe.whitelist(allow_guest=True)
def generate_active_ristournes_from_previous(
    year: int | None = None,
    scope: str = "customer",
    dry_run: int = 0,
    customer: str | None = None,  # nom interne OU display name, ou liste "A,B,C"
):
    """
    Crée des *Ristourne Active* pour l'année N en se basant sur l'année N-1.
    Si amount_prev > 0, crée le document et (si sms_otp activé) envoie un SMS
    aux numéros du champ Customer.custom_liste_telephone.
    """
    if not year:
        year = getdate(today()).year
    year = int(year)
    prev_year = year - 1
    y_start, y_end = _y_bounds(year)

    created = skipped = errors = 0
    sms_sent = 0
    details: list[dict] = []

    gen_report = frappe.get_attr("booking_ristourne.ristourne.generate_ristourne_report")
    target_customers = _normalize_customers_arg(customer)
    default_company = _get_default_company()
    sms_allowed = _sms_otp_enabled()

    if scope in ("customer", "both"):
        batch_count = 0
        for cust in iter_customers(page_size=5000, only=target_customers):
            try:
                # 1) Paliers via report
                report = gen_report(cust)
                paliers = report.get("paliers") or []
                if not paliers:
                    skipped += 1
                    details.append({"customer": cust, "action": "skip", "reason": "no_paliers"})
                    continue

                # 2) Montant N-1
                amount_prev, _ = _compute_prev_year_amount(cust, prev_year, paliers)
                if not amount_prev or float(amount_prev) <= 0:
                    skipped += 1
                    details.append({"customer": cust, "action": "skip", "reason": "zero_amount"})
                    continue

                # 3) Doublon *Active*
                if _already_exists_active_for_year(year, customer=cust):
                    skipped += 1
                    details.append({"customer": cust, "action": "skip", "reason": "active_already_exists"})
                    continue

                if dry_run:
                    # dry-run: pas de création ni SMS
                    details.append({"customer": cust, "action": "would_create_active", "amount_prev": amount_prev})
                    continue

                # 4) Créer *Ristourne Active*
                doc = frappe.new_doc("Ristourne Active")
                doc.customer   = cust
                doc.valid_from = y_start
                doc.valid_to   = y_end
                doc.annual_amount = float(amount_prev)
                if hasattr(doc, "company") and default_company:
                    doc.company = default_company

                doc.insert(ignore_permissions=True)
                doc.submit()
                created += 1

                # 5) SMS (optionnel)
                sms_info = None
                if sms_allowed:
                    phones = _customer_phones(cust)
                    if phones:
                        montant_txt = _format_tnd(amount_prev)
                        msg = (
                            f"Bonne année {year} 🎉!\n"
                            f"Toute l’équipe AquaWorld & Servicing vous souhaite santé et réussite.\n"
                            f"Votre ristourne {prev_year} est de {montant_txt}. "
                            f"Elle sera utilisable en {year} selon vos conditions."
                        )
                        try:
                            frappe_send_sms(phones, msg)
                            sms_sent += 1
                            sms_info = {"phones": phones, "status": "sent"}
                        except Exception:
                            sms_info = {"phones": phones, "status": "failed"}
                            frappe.log_error(frappe.get_traceback(), "Ristourne Active SMS")
                    else:
                        sms_info = {"phones": [], "status": "no_numbers"}

                details.append({
                    "customer": cust,
                    "action": "created_active",
                    "amount_prev": amount_prev,
                    "new_name": doc.name,
                    "sms": sms_info if sms_allowed else "disabled",
                })

            except Exception:
                errors += 1
                tb = frappe.get_traceback()
                frappe.log_error(tb, "generate_active_ristournes_from_previous")
                details.append({"customer": cust, "action": "error", "error": str(tb).splitlines()[-1]})

            batch_count += 1
            if not dry_run and (batch_count % 500 == 0):
                frappe.db.commit()

    frappe.db.commit()
    return {
        "status": "done",
        "year": year,
        "based_on_year": prev_year,
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "sms_sent": sms_sent,     # nombre de clients pour lesquels un SMS a été envoyé
        "details": details,
        "target": target_customers or "all",
        "sms_otp_enabled": sms_allowed,
    }


