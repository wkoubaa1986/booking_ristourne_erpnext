"""Journal des actions du client sur sa fiche (/fiche_client) — ce qu'il y fait, pas seulement s'il vient.

Une ligne « Ristourne Dashboard Logging » par action : connexion, recherche d'article, recherche par
photo, aperçu d'un article du site, liste de prix créée, PDF téléchargé, rapport ristourne consulté,
familles consultées. C'est ce que lit le tableau de bord « Clients B2B » (customization_app.clients_b2b)
pour dire au commercial ce qu'un client cherche et s'il se sert de l'outil.

⚠️ UNE TRACE NE DOIT JAMAIS CASSER L'ACTION. Le client cherche un article : s'il manque le champ
`action` (fixture pas encore migrée) ou si l'insertion échoue, on note l'erreur et on rend quand même
le résultat. La table existait avant (connexions depuis décembre 2025, sans `action`) : une ligne sans
action est une connexion.
"""
from __future__ import annotations

import frappe
from frappe.utils import now

DOCTYPE = "Ristourne Dashboard Logging"

CONNEXION = "connexion"
RECHERCHE = "recherche"
PHOTO = "photo"
APERCU = "apercu_site"
LISTE_PRIX = "liste_prix"
PDF = "pdf_liste"
RAPPORT = "rapport_ristourne"
FAMILLES = "familles"

ACTIONS = (CONNEXION, RECHERCHE, PHOTO, APERCU, LISTE_PRIX, PDF, RAPPORT, FAMILLES)

LIBELLES = {
    CONNEXION: "Connexion",
    RECHERCHE: "Recherche d'article",
    PHOTO: "Recherche par photo",
    APERCU: "Article vu sur le site",
    LISTE_PRIX: "Liste de prix créée",
    PDF: "PDF de liste téléchargé",
    RAPPORT: "Rapport ristourne consulté",
    FAMILLES: "Quantités par famille consultées",
}

DETAIL_MAX = 140


def tracer(customer: str | None, action: str, detail: str | None = None) -> str | None:
    """Enregistre une action du client. -> nom de la ligne, ou None si rien n'a pu être écrit."""
    if not customer or action not in ACTIONS:
        return None
    try:
        valeurs = {"doctype": DOCTYPE, "client": customer, "logging_time": now()}
        if frappe.get_meta(DOCTYPE).has_field("action"):
            valeurs["action"] = action
            valeurs["detail"] = (detail or "").strip()[:DETAIL_MAX] or None
        doc = frappe.get_doc(valeurs)
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc.name
    except Exception:
        frappe.log_error(title="Fiche client : trace d'action impossible",
                         message="%s %s %s\n%s" % (customer, action, detail, frappe.get_traceback()))
        return None
