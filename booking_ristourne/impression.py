"""Identité de la société et marges d'impression, lues dans « AquaWorld Settings » (onglet « Identité & impression »).

Demande utilisateur du 11/11/2026 : les coordonnées (RC, banque, RIB, e-mail, adresse, téléphones) étaient recopiées
en dur dans chaque format d'impression (Devis, BC, BL, Facture, Liste des prix, Liste prix client). Elles sont
maintenant saisies une seule fois ; les formats les lisent par `aqua_identite()` et prennent leurs marges par
`aqua_marges_css()` — deux fonctions exposées à Jinja (hooks.py, `jinja`).

Un champ laissé vide retombe sur la valeur d'origine (DEFAUTS) : un format ne s'imprime jamais avec un trou, même
avant que la page ait été remplie.
"""
import frappe
from frappe.utils import flt

REGLAGES = "AquaWorld Settings"

# Les valeurs qui étaient écrites en dur dans les formats au 11/11/2026.
DEFAUTS = {
    "nom_commercial": "Aqua World & Servicing",
    "slogan": "Filtration et traitement des eaux",
    "adresse_ligne1": "22 Rue du parc",
    "adresse_ligne2": "2036 Soukra",
    "adresse_ville": "Tunis",
    "adresse_pays": "Tunisie",
    "tel_fixe": "31 248 723",
    "tel_mobile": "98 511 119",
    "email_societe": "aquaworld.servicing@gmail.com",
    "email_commercial": "sales@aquaworldservicing.com",
    "site_web": "www.aquaworldservicing.com",
    "registre_commerce": "B03172022019",
    "banque": "Banque Zitouna-AOUINA",
    "rib": "2501 4000 0000657260 91",
    "logo": "/files/logo.png",
    "logo_liste_client": "/files/Logo-AquaWorld-2.png",
    "qr_code": "/files/scan.JPG",
    "cachet": "/files/Cachet + signature.jpeg",
}
IMAGES = ("logo", "logo_liste_client", "qr_code", "cachet")


def valeurs_identite(reglages: dict) -> dict:
    """Les valeurs saisies, complétées par DEFAUTS pour les champs vides. Fonction pure."""
    return {champ: (str(reglages.get(champ) or "").strip() or defaut) for champ, defaut in DEFAUTS.items()}


def css_marges(ligne: dict | None) -> str:
    """La règle CSS que Frappe lit pour régler les marges du PDF (wkhtmltopdf) : uniquement des propriétés
    `margin-*` en mm posées sur `.print-format`. Aucune ligne pour ce format → chaîne vide : les marges restent
    celles d'avant. Fonction pure."""
    if not ligne:
        return ""
    regles = "".join(
        " margin-%s: %gmm;" % (cote, flt(ligne.get(champ)))
        for cote, champ in (("top", "marge_haut"), ("bottom", "marge_bas"), ("left", "marge_gauche"), ("right", "marge_droite"))
    )
    # Frappe lit ces propriétés pour en faire les options de marge de wkhtmltopdf, mais le navigateur du PDF les
    # applique AUSSI comme marges CSS du bloc : la marge serait comptée deux fois. La seconde règle (sélecteur
    # différent, donc ignorée par Frappe) annule l'effet CSS.
    return "<style>.print-format {%s }\nhtml .print-format { margin: 0 !important; }</style>" % regles


def _reglages() -> dict:
    try:
        return frappe.get_cached_doc(REGLAGES).as_dict()
    except Exception:
        return {}


def aqua_identite() -> dict:
    """Pour les formats d'impression : `{% set ident = aqua_identite() %}` puis `{{ ident.rib }}`…
    Les images sont rendues en URL complète (`ident.logo_url`…), comme l'exige wkhtmltopdf."""
    ident = frappe._dict(valeurs_identite(_reglages()))
    for champ in IMAGES:
        ident[champ + "_url"] = frappe.utils.get_url(frappe.utils.quote(ident[champ], safe="/:"))
    return ident


def aqua_marges_css(print_format: str) -> str:
    """Pour les formats d'impression, dans le <head> : `{{ aqua_marges_css("Aqua World Facture") }}`."""
    for ligne in _reglages().get("marges_impression") or []:
        if ligne.get("print_format") == print_format:
            return css_marges(ligne)
    return ""


# Les formats de l'app et leurs marges d'avant, mesurées le 11/11/2026 : avec ces valeurs les PDF sont identiques au
# pixel près. Cinq formats ne déclaraient rien que wkhtmltopdf lise (15 mm partout, valeur par défaut) ; le BL
# déclarait 5/5/6/6, dont gauche et droite comptés deux fois (option PDF + marge CSS) : 5/5/12/12 réels.
MARGES_ORIGINE = {
    "Aqua world Devis": (15, 15, 15, 15),
    "Aqua World BC": (15, 15, 15, 15),
    "Aqua World BL": (5, 5, 12, 12),
    "Aqua World Facture": (15, 15, 15, 15),
    "Liste des prix": (15, 15, 15, 15),
    "Liste prix client": (15, 15, 15, 15),
}
COTES = ("marge_haut", "marge_bas", "marge_gauche", "marge_droite")


def initialiser():
    """after_migrate : à la première migration après l'ajout de l'onglet, écrit les valeurs d'origine dans les
    champs vides et une ligne de marges par format, pour que la page s'ouvre remplie. Idempotent ; ensuite ne touche
    plus à rien (une ligne de marges supprimée à la main n'est pas recréée)."""
    if not frappe.db.exists("DocType", REGLAGES) or not frappe.get_meta(REGLAGES).has_field("marges_impression"):
        return
    doc = frappe.get_doc(REGLAGES)
    premiere_fois = not any(doc.get(champ) for champ in DEFAUTS)
    change = False
    for champ, defaut in DEFAUTS.items():
        if not doc.get(champ):
            doc.set(champ, defaut)
            change = True
    if premiere_fois and not doc.get("marges_impression"):
        for nom, marges in MARGES_ORIGINE.items():
            if frappe.db.exists("Print Format", nom):
                doc.append("marges_impression", {"print_format": nom, **dict(zip(COTES, marges))})
                change = True
    if change:
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.save()
