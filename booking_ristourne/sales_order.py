# booking_ristourne/sales_order.py
from __future__ import annotations

import json
from typing import Optional, Dict, Any, Tuple, List

import frappe
from frappe.utils import flt, nowdate, getdate, now

# =============================
# === CONFIG / DOCTYPE NAMES ===
# =============================
ACTIVE_DT = "Ristourne Active"     # Programme N-1
USED_DT   = "Ristourne used"        # Journal de consommation

# Champs réels sur tes doctypes (d'après tes captures)
ACTIVE_TOTAL_FIELD      = "annual_amount"    # montant N-1 annuel (Data)
USED_AMOUNT_FIELD       = "applied_amount"   # montant consommé (Data)
USED_POSTING_DATETIME   = "posting_date"     # Datetime

# Champs custom SO (optionnels mais pratiques)
APPLIED_FIELDNAME = "custom_ristourne_applied_amount"   # montant de discount_amount qui est de la ristourne
TOGGLE_FIELDNAME  = "custom_appliquer_ristourne"        # toggle UI
SUBMIT_GUARD_FLAG = "ristourne_submit_guard"  # keep a clean key (string)

def _flags():
    """Always work on request-scoped flags; init if missing."""
    fl = getattr(frappe.local, "flags", None)
    if fl is None:
        frappe.local.flags = frappe._dict()
        fl = frappe.local.flags
    return fl
# ==========================
# === UTILITAIRES GÉNÉRAUX ==
# ==========================
def _dec(x) -> float:
    return flt(x or 0)

def _company_currency(company: Optional[str]) -> Optional[str]:
    return frappe.db.get_value("Company", company, "default_currency") if company else None

def _months_elapsed(so_date: Optional[str]) -> int:
    d = getdate(so_date or nowdate())
    return max(1, min(12, int(d.month)))  # 1..12

def _fmt_currency(x: float, currency: Optional[str]) -> str:
    return frappe.format(_dec(x), {"fieldtype": "Currency", "options": currency})

# ==========================
# === LOOKUPS / ACCÈS DB ===
# ==========================
def _get_active_fields() -> List[str]:
    """
    Ne sélectionne que les champs qui existent réellement sur Ristourne Active
    (évite 'Unknown column').
    """
    base = ["name", "customer", "company", "valid_from", "valid_to"]
    meta = frappe.get_meta(ACTIVE_DT)
    if meta.has_field(ACTIVE_TOTAL_FIELD):
        base.append(ACTIVE_TOTAL_FIELD)
    # pas de currency sur ton doctype
    return base

def _get_active_total(active_doc: Dict[str, Any]) -> float:
    """
    Récupère la valeur de ACTIVE_TOTAL_FIELD même si get_all ne l'a pas chargée.
    """
    if not active_doc:
        return 0.0
    if ACTIVE_TOTAL_FIELD in active_doc and active_doc.get(ACTIVE_TOTAL_FIELD) is not None:
        return _dec(active_doc.get(ACTIVE_TOTAL_FIELD))
    val = frappe.db.get_value(ACTIVE_DT, active_doc["name"], ACTIVE_TOTAL_FIELD)
    return _dec(val)

def _find_active_n1(customer: str, as_of: Optional[str], company: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Trouve l'active N-1 par client + date; filtre par company si fournie.
    """
    as_of = as_of or nowdate()
    filters = {"customer": customer, "valid_from": ["<=", as_of], "valid_to": [">=", as_of]}
    if company:
        filters["company"] = company
    rows = frappe.get_all(
        ACTIVE_DT,
        filters=filters,
        fields=_get_active_fields(),
        order_by="valid_from desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None

def _sum_used_for_window(customer: str, company: Optional[str],
                         start_date: Optional[str], end_date: Optional[str]) -> float:
    """
    Somme des 'applied_amount' soumis pour (client [+ société]) entre start_date et end_date (inclus).
    Utilise le champ Datetime USED_POSTING_DATETIME.
    """
    if not customer:
        return 0.0

    # bornes datetime (inclusives)
    start_dt = f"{start_date} 00:00:00" if start_date else None
    end_dt   = f"{end_date} 23:59:59" if end_date   else None

    where = ["docstatus = 1", "customer = %s"]
    vals  = [customer]

    if company:
        where.append("company = %s")
        vals.append(company)
    if start_dt:
        where.append(f"`{USED_POSTING_DATETIME}` >= %s")
        vals.append(start_dt)
    if end_dt:
        where.append(f"`{USED_POSTING_DATETIME}` <= %s")
        vals.append(end_dt)

    sql = f"""
        SELECT COALESCE(SUM(`{USED_AMOUNT_FIELD}`), 0)
        FROM `tab{USED_DT}`
        WHERE {" AND ".join(where)}
    """
    total = frappe.db.sql(sql, vals)[0][0] or 0
    return flt(total)

# ==========================================
# === LOGIQUE: QUOTA MENSUEL & DISPONIBLE ===
# ==========================================
def _availability_to_date(n1_total_amount: float, so_date: Optional[str]) -> float:
    # (N-1 total / 12) * mois(so_date)
    monthly = _dec(n1_total_amount) / 12.0
    return monthly * _months_elapsed(so_date)

def _available_net_of_used(active_doc: Dict[str, Any], so_date: Optional[str]) -> Tuple[float, float, float, float]:
    """
    Renvoie: (monthly_quota, allowed_to_date, used_so_far, remaining_to_date).
    used_so_far = somme des 'Ristourne used' pour le client (et la société de l'active)
                 entre valid_from et so_date (inclus).
    """
    total = _get_active_total(active_doc)
    monthly_quota   = total / 12.0
    allowed_to_date = _availability_to_date(total, so_date)
    used_so_far     = _sum_used_for_window(
        customer = active_doc.get("customer"),
        company  = active_doc.get("company"),
        start_date = active_doc.get("valid_from"),
        end_date   = so_date
    )
    remaining_to_date = max(0.0, allowed_to_date - used_so_far)
    return monthly_quota, allowed_to_date, used_so_far, remaining_to_date

# =================
# === NOTE HTML  ===
# =================
def _render_note_html(
    statut: str,
    devise: Optional[str],
    quota_mensuel: float,
    autorisé_jusqua_date: float,
    utilise_jusqua_present: float,
    disponible_jusqua_date: float,
    mois_ecoules: int,
    total_general: Optional[float] = None
) -> str:
    """
    Génère le code HTML affichant la note d'information sur la ristourne.

    Paramètres
    ----------
    statut : str
        Statut actuel de la ristourne ("OK", "NO_ACTIVE", "NONE_LEFT", etc.).
    devise : Optional[str]
        Devise utilisée pour l'affichage des montants.
    quota_mensuel : float
        Montant de la ristourne alloué par mois.
    autorisé_jusqua_date : float
        Montant total de la ristourne autorisé jusqu'à ce mois.
    utilise_jusqua_present : float
        Montant de la ristourne déjà utilisé.
    disponible_jusqua_date : float
        Montant de la ristourne encore disponible à ce jour.
    mois_ecoules : int
        Nombre de mois écoulés depuis le début de la période.
    total_general : Optional[float], par défaut None
        Montant du Grand Total pour afficher un plafonnement éventuel.

    Retourne
    --------
    str
        Code HTML prêt à être injecté dans le champ HTML.
    """

    couleur = {
        "OK": "#2e7d32",         # Vert foncé → OK
        "NO_ACTIVE": "#b71c1c",  # Rouge → Pas de ristourne active
        "NONE_LEFT": "#b71c1c"   # Rouge → Plus de ristourne disponible
    }.get(statut, "#5e35b1")     # Violet → Statut par défaut

    titre = {
        "OK": "Ristourne disponible (à ce jour)",
        "NO_ACTIVE": "Aucune ristourne N-1 active",
        "NONE_LEFT": "Aucune ristourne restante à ce jour",
    }.get(statut, "Statut de la ristourne")

    ligne_plafond = ""
    if total_general is not None:
        ligne_plafond = (
            "<div style='margin-top:4px;font-size:12px;color:#666;'>"
            f"Plafonné par le montant du Grand Total : <b>{_fmt_currency(total_general, devise)}</b>"
            "</div>"
        )

    return f"""
    <div style="border:1px solid #eee;border-left:4px solid {couleur};padding:10px 12px;border-radius:6px;background:#fafafa;">
      <div style="font-weight:600;color:{couleur};margin-bottom:6px;">{titre}</div>
      <div style="font-size:13px;line-height:1.6;">
        <div>Quota mensuel : <b>{_fmt_currency(quota_mensuel, devise)}</b></div>
        <div>Autorisé jusqu'au mois n°{mois_ecoules} : <b>{_fmt_currency(autorisé_jusqua_date, devise)}</b></div>
        <div>Déjà utilisé : <b>{_fmt_currency(utilise_jusqua_present, devise)}</b></div>
        <div>Disponible à ce jour (non plafonné) : <b>{_fmt_currency(disponible_jusqua_date, devise)}</b></div>
      </div>
    </div>
    """
def _has_submitted_used(so_name: str) -> Optional[str]:
    """Return an existing submitted 'Ristourne used' for this SO, if any."""
    names = frappe.get_all(
        USED_DT,
        filters={"sales_order": so_name, "docstatus": 1},
        pluck="name",
        limit=1,
    )
    return names[0] if names else None

# ===========================================
# === PUBLIC: disponibilité + HTML côté SO ===
# ===========================================
@frappe.whitelist()
def get_available_for_sales_order(
    customer: str,
    transaction_date: Optional[str] = None,
    company: Optional[str] = None,
    grand_total: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calcule le disponible NON-CAPPÉ pour un client à une date donnée (company optionnelle).
    Le front cappe par le Grand Total.
    """
    months = _months_elapsed(transaction_date or nowdate())

    if not customer:
        cur = _company_currency(company)
        html = _render_note_html("NO_ACTIVE", cur, 0, 0, 0, 0, months, grand_total)
        return {
            "status": "NO_ACTIVE", "active_name": None, "currency": cur,
            "monthly_quota": 0.0, "allowed_to_date": 0.0, "used_so_far": 0.0,
            "available_to_date": 0.0, "months_elapsed": months, "note_html": html,
        }

    so_date = transaction_date or nowdate()
    active = _find_active_n1(customer, so_date, company=company)
    if not active:
        cur = _company_currency(company)
        html = _render_note_html("NO_ACTIVE", cur, 0, 0, 0, 0, months, grand_total)
        return {
            "status": "NO_ACTIVE", "active_name": None, "currency": cur,
            "monthly_quota": 0.0, "allowed_to_date": 0.0, "used_so_far": 0.0,
            "available_to_date": 0.0, "months_elapsed": months, "note_html": html,
        }

    monthly_quota, allowed_to_date, used_so_far, remaining_to_date = _available_net_of_used(active, so_date)
    cur = _company_currency(active.get("company"))  # pas de 'currency' sur Ristourne Active
    status = "OK" if remaining_to_date > 0 else "NONE_LEFT"
    html = _render_note_html(status, cur, monthly_quota, allowed_to_date, used_so_far, remaining_to_date, months, grand_total)

    return {
        "status": status, "active_name": active["name"], "currency": cur,
        "monthly_quota": monthly_quota, "allowed_to_date": allowed_to_date,
        "used_so_far": used_so_far, "available_to_date": remaining_to_date,
        "months_elapsed": months, "note_html": html,
    }

# ==========================================================
# === SALES ORDER SUBMIT: créer 'Ristourne used' (global) ===
# ==========================================================
def _infer_applied_from_so(so, available_to_date: float) -> float:
    """
    Ce que la UI a réellement appliqué.
    Priorité: champ APPLIED_FIELDNAME si présent; sinon min(discount_amount, available_to_date).
    Clamp à >= 0 pour éviter des incohérences.
    """
    applied = _dec(getattr(so, APPLIED_FIELDNAME, 0))
    if applied is not None and applied > 0:
        return float(applied)

    disc = _dec(getattr(so, "discount_amount", 0))
    avail = _dec(available_to_date)
    out = min(disc, avail)
    # clamp négatifs et NaN
    try:
        out_f = float(out)
    except Exception:
        out_f = 0.0
    return max(0.0, out_f)

from frappe.exceptions import DuplicateEntryError, UniqueValidationError

def _create_submit_used(so, amount: float) -> str:
    """
    Idempotent and race-safe (no in-memory flags):
    - If a 'Ristourne used' already exists for this SO: update amount and ensure submitted.
    - Otherwise insert+submit. If a concurrent insert happened, catch unique error, reload, update.
    """
    if amount <= 0:
        return ""

    # 1) Try to reuse/update an existing record
    existing = frappe.get_value(USED_DT, {"sales_order": so.name}, "name")
    if existing:
        doc = frappe.get_doc(USED_DT, existing)
        doc.flags.ignore_permissions = True

        # sync amount
        if flt(doc.applied_amount) != flt(amount):
            # use db_set to avoid modified churn
            doc.db_set("applied_amount", flt(amount), update_modified=False)

        # ensure submitted
        if doc.docstatus == 0:
            doc.submit()
        return doc.name

    # 2) Create a new record
    doc = frappe.new_doc(USED_DT)
    doc.sales_order    = so.name
    doc.customer       = so.customer
    doc.company        = so.company
    doc.posting_date   = now()  # or so.transaction_date if you prefer a pure date
    doc.applied_amount = flt(amount)

    try:
        doc.insert(ignore_permissions=True)
    except (DuplicateEntryError, UniqueValidationError):
        # A concurrent request inserted it; load and update instead
        existing = frappe.get_value(USED_DT, {"sales_order": so.name}, "name")
        if not existing:
            # defensive: re-raise if we truly can’t find it
            raise
        doc = frappe.get_doc(USED_DT, existing)
        doc.flags.ignore_permissions = True
        if flt(doc.applied_amount) != flt(amount):
            doc.db_set("applied_amount", flt(amount), update_modified=False)

    if doc.docstatus == 0:
        doc.submit()

    return doc.name


# ========== HOOK HANDLERS (doc_events) ==========

def on_sales_order_submit(doc, method=None):
    """
    Hook handler: called by Frappe with (doc, method).
    """
    _ = _submit_core(doc)  # return value is ignored by hooks
    # if you want feedback in UI: frappe.msgprint(_["note"])

def on_sales_order_cancel(doc, method=None):
    """
    Hook handler: called by Frappe with (doc, method).
    """
    _ = _cancel_core(doc)
    # optional: frappe.msgprint(_["note"])


# ========== CORE (shared by hook + API) ==========


def _submit_core(so_doc) -> Dict[str, Any]:
    # Toggle OFF? → skip
    if getattr(so_doc, TOGGLE_FIELDNAME, 1) in (0, "0", False):
        return {
            "status": "SKIPPED",
            "used_doc": "",
            "applied": 0.0,
            "consumed": 0.0,
            "note": "Toggle OFF or not present."
        }

    # Compute availability from active N-1
    active = _find_active_n1(so_doc.customer, so_doc.transaction_date, company=so_doc.company)
    if not active:
        return {"status": "NO_ACTIVE", "used_doc": "", "applied": 0.0, "consumed": 0.0, "note": "No active N-1."}

    _, allowed_to_date, used_so_far, remaining_to_date = _available_net_of_used(active, so_doc.transaction_date)

    applied  = _infer_applied_from_so(so_doc, remaining_to_date)
    consumed = min(applied, remaining_to_date)

    if consumed <= 0:
        return {
            "status": "SKIPPED",
            "used_doc": "",
            "applied": float(applied),
            "consumed": 0.0,
            "note": "Nothing to consume to date."
        }

    name = _create_submit_used(so_doc, consumed)
    cur  = _company_currency(active.get("company"))
    note = (
        f"Ristourne used {name}: Consumed {_fmt_currency(consumed, cur)} / "
        f"Applied {_fmt_currency(applied, cur)}. Previously used: {_fmt_currency(used_so_far, cur)}."
    )
    return {
        "status": "CREATED",
        "used_doc": name,
        "applied": float(applied),
        "consumed": float(consumed),
        "note": note
    }




def _cancel_core(so_doc) -> Dict[str, Any]:
    names = frappe.get_all(USED_DT, filters={"sales_order": so_doc.name}, pluck="name")
    deleted: List[str] = []
    for nm in names:
        try:
            doc = frappe.get_doc(USED_DT, nm)
            doc.flags.ignore_permissions = True
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc(USED_DT, nm, ignore_permissions=True, force=1)
            deleted.append(nm)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"{USED_DT} cancel/delete failed for {nm}")
    return {"status": "DONE", "deleted": deleted, "note": f"Cancelled & deleted {len(deleted)} used record(s)."}
