import frappe
from frappe.utils import getdate, add_months, today, now, formatdate
import random
import re
import hmac
import hashlib
from frappe.utils import generate_hash
from collections import defaultdict
from frappe.core.doctype.sms_settings.sms_settings import send_sms as frappe_send_sms
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

    ristourne = frappe.get_value("Ristourne", {"client": customer}, "*", as_dict=True)
    if not ristourne:
        ristourne = frappe.get_value("Ristourne", {"group_client": group}, "*", as_dict=True)

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

@frappe.whitelist()
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

@frappe.whitelist()
def get_grouped_article_quantities_by_month_with_token(token):
    customer = frappe.cache().get_value(f"session_token_{token}")
    if not customer:
        frappe.throw("Session expirée. Veuillez vous reconnecter.")
    return get_grouped_article_quantities_by_month(customer)

def get_grouped_article_quantities_by_month(customer):
    ristourne = find_applicable_ristourne(customer)
    # ✅ If no ristourne, start from the beginning of the current year
    if not ristourne:
        current_year = getdate(today()).year
        start_date = f"{current_year}-01-01"
    else:
        start_date = ristourne["valable_de"]

    end_date = today()

    dn_items = frappe.db.sql("""
        SELECT dni.item_group, dni.qty, dn.posting_date
        FROM `tabDelivery Note` dn
        JOIN `tabDelivery Note Item` dni ON dn.name = dni.parent
        WHERE dn.docstatus = 1
          AND dn.customer = %s
          AND dn.posting_date BETWEEN %s AND %s
    """, (customer, start_date, end_date), as_dict=True)

    month_list = generate_month_list(start_date, end_date)
    total_months = len(month_list)

    group_month_qty = defaultdict(lambda: defaultdict(float))

    for row in dn_items:
        month = getdate(row.posting_date).strftime("%Y-%m")
        group_month_qty[row.item_group][month] += float(row.qty or 0)

    result_groups = []
    for group, month_data in group_month_qty.items():
        quantities = []
        total_qty = 0
        for month in month_list:
            qty = month_data.get(month, 0)
            quantities.append(qty)
            total_qty += qty

        average_qty = total_qty / total_months if total_months else 0
        quantities.append(round(average_qty, 2))

        result_groups.append({
            "item_group": group,
            "quantities": quantities
        })

    return {
        "months": month_list + ["Moyenne"],
        "groups": result_groups
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
        frappe.cache().set_value(f"session_token_{session_token}", matched_customer, expires_in_sec=600)
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
    frappe.cache().set_value(f"session_token_{session_token}", matched_customer, expires_in_sec=600)

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
    print(f"Logging login for customer: {customer_name}")
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
    ORDER = [
        "Osmoseur Domestique",
        "Osmoseur Commercial",
        "Osmoseur Industriel",
        "Osmoseur Bi-osmose",
        "Adoucisseur",
        "Porte filtre",
        "Pompe",
        "Membrane",
        "Cartouche",
        "Accessoire",
        "Adaptateur",
        "Citerne",
        "Robinet",
        "Mitigeur",
        "Stérilisateur",
        "Équipement de mesure",
        "Divers",
    ]
    EXCLUDE = {"Livraison", "Echange", "Échange", "Main d'oeuvre", "Main d’œuvre"}

    # 1) Price Lists (VENTE)
    lists_des_prix = frappe.get_all(
        "Price List",
        filters={"selling": 1, "enabled": 1},
        pluck="name",
        order_by="name asc",
    )
    if not lists_des_prix:
        frappe.throw("Aucune liste de prix de VENTE (selling=1, enabled=1) trouvée.")

    # 2) Item Groups feuilles (hors exclusions)
    all_groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
    filtered_groups = [g for g in all_groups if g not in EXCLUDE]

    in_order = [g for g in ORDER if g in filtered_groups]
    others = sorted([g for g in filtered_groups if g not in ORDER])
    article_groups = in_order + others

    customer = frappe.get_doc("Customer", customer_name)
    current_date = getdate(today())
    mois_annee = formatdate(current_date, "MMMM yyyy")

    links_html = f"""
    <table style='width: 100%; border-collapse: collapse; margin: 20px 0;'>
        <tr>
            <td colspan='4' style='padding: 10px; background-color: #f2f2f2; font-weight: bold; text-align: center; border: 1px solid #ddd;'>
                Voici les derniers liens vers vos listes des prix {customer.customer_group} générées ({mois_annee})
            </td>
        </tr>
    """

    column_count = 4
    current_col = 0
    links_html += "<tr>"
    customer_list_prix = customer.default_price_list or "Vente standard"

    for group in article_groups:
        docname = frappe.db.sql("""
            SELECT DISTINCT lpd.name
            FROM `tabListe prix documents` lpd
            JOIN `tabGroup articles doc` gad ON gad.parent = lpd.name
            WHERE lpd.génrer_automatique = 1
              AND lpd.liste_des_prix = %s
              AND gad.goup_articles = %s
            ORDER BY lpd.creation DESC
            LIMIT 1
        """, (customer_list_prix, group), as_dict=True)

        if docname:
            docname = docname[0]["name"]
            pdf_link = f"/printview?doctype=Liste%20prix%20documents&name={docname}"
            links_html += f"<td style='padding: 8px; border: 1px solid #ddd; text-align: center;'><a href='{pdf_link}' target='_blank'>{group}</a></td>"
            current_col += 1
            if current_col == column_count:
                links_html += "</tr><tr>"
                current_col = 0

    if current_col != 0:
        for _ in range(column_count - current_col):
            links_html += "<td style='padding: 8px; border: 1px solid #ddd;'></td>"
        links_html += "</tr>"

    links_html += "</table>"

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
@frappe.whitelist()
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

@frappe.whitelist()
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


