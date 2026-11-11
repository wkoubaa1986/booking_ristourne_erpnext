"""Identité de la société et marges d'impression, lues dans « AquaWorld Settings » (onglet « Identité & impression »).

Demande utilisateur du 11/11/2026 : les coordonnées (RC, banque, RIB, e-mail, adresse, téléphones) étaient recopiées
en dur dans chaque format d'impression (Devis, BC, BL, Facture, Liste des prix, Liste prix client). Elles sont
maintenant saisies une seule fois ; les formats les lisent par `aqua_identite()` et prennent leurs marges par
`aqua_marges_css()` — deux fonctions exposées à Jinja (hooks.py, `jinja`).

Vider un champ, c'est ne plus l'imprimer (demande du 11/11/2026 : « je n'ai plus de numéro fixe ») : la ligne ou le
morceau de ligne disparaît des documents. Deux exceptions : tant que la page n'a jamais été initialisée (case cachée
`identite_initialisee`), les valeurs d'origine (DEFAUTS) servent de repli ; et une image vide reprend toujours celle
d'origine, parce qu'une image manquante fait échouer la génération du PDF.
"""
import re

import frappe
from frappe.utils import flt

REGLAGES = "AquaWorld Settings"

# Les valeurs qui étaient écrites en dur dans les formats au 11/11/2026.
DEFAUTS = {
    # Les deux lignes imprimées sous le logo (demande du 11/11/2026 : le logo « AquaWorld & Servicing » complet
    # sur tous les documents, à la place de l'icône + nom tapé, avec l'activité puis le slogan en dessous).
    "slogan": "Filtration et traitement des eaux",
    "slogan_2": "Une solution pour chaque besoin en eau",
    "adresse_ligne1": "22 Rue du parc",
    "adresse_ligne2": "2036 Soukra",
    "adresse_ville": "Tunis",
    "adresse_pays": "Tunisie",
    "tel_fixe": "31 248 723",
    "tel_mobile": "98 511 119",
    "tel_mobile_2": "",  # second mobile, imprimé après le premier : « Mobile: A / B »
    "email_societe": "aquaworld.servicing@gmail.com",
    "email_commercial": "sales@aquaworldservicing.com",
    "email_2": "",  # second e-mail des devis, BC, BL et factures, sous le premier
    "site_web": "www.aquaworldservicing.com",
    "registre_commerce": "B03172022019",
    "logo": "/files/Logo-AquaWorld-2.png",
    "qr_code": "/files/scan.JPG",
    "cachet": "/files/Cachet + signature.jpeg",
}
IMAGES = ("logo", "qr_code", "cachet")
# Taille (px) et couleur des deux lignes sous le logo, réglables dans la page. Contrairement aux textes, un réglage
# vide ou invalide reprend la valeur d'origine : une taille nulle ou une couleur fausse casserait la mise en page.
STYLE_DEFAUTS = {
    "slogan_taille": 13.0,
    "slogan_couleur": "#6b6b6b",
    "slogan_2_taille": 11.5,
    "slogan_2_couleur": "#1d4e89",
}
TAILLE_MIN, TAILLE_MAX = 6.0, 30.0
_COULEUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def styles_slogans(reglages: dict) -> dict:
    """Tailles (bornées à 6–30 px) et couleurs (#rgb ou #rrggbb) des deux lignes sous le logo. Fonction pure."""
    styles = {}
    for champ, defaut in STYLE_DEFAUTS.items():
        saisi = reglages.get(champ)
        if champ.endswith("_taille"):
            taille = flt(saisi)
            styles[champ] = min(max(taille, TAILLE_MIN), TAILLE_MAX) if taille > 0 else defaut
        else:
            saisi = str(saisi or "").strip()
            styles[champ] = saisi if _COULEUR.match(saisi) else defaut
    # Gras au choix pour chaque ligne (cases à cocher) ; `*_graisse` est la valeur CSS prête à l'emploi.
    for ligne in ("slogan", "slogan_2"):
        styles[ligne + "_graisse"] = "bold" if reglages.get(ligne + "_gras") else "normal"
    return styles


# Le compte bancaire d'origine. Les comptes se saisissent dans le tableau « Comptes bancaires » (un par ligne) ;
# tous sont imprimés dans l'en-tête, l'un sous l'autre, dans l'ordre du tableau (choix utilisateur 11/11/2026).
COMPTE_ORIGINE = {"banque": "Banque Zitouna-AOUINA", "rib": "2501 4000 0000657260 91"}


def valeurs_identite(reglages: dict) -> dict:
    """Les valeurs à imprimer. Page initialisée : ce qui est saisi, un champ vide reste vide (= non imprimé), sauf
    les images. Page jamais initialisée : les valeurs d'origine. Fonction pure."""
    initialise = bool(reglages.get("identite_initialisee"))
    valeurs = {}
    for champ, defaut in DEFAUTS.items():
        saisi = str(reglages.get(champ) or "").strip()
        valeurs[champ] = saisi if (saisi or (initialise and champ not in IMAGES)) else defaut
    return valeurs


def _joindre(separateur: str, *morceaux) -> str:
    return separateur.join(m for m in morceaux if m)


def _lignes(texte) -> list:
    return [l.strip() for l in str(texte or "").splitlines() if l.strip()]


def lignes_composees(v: dict, reglages: dict | None = None) -> dict:
    """Les lignes que les formats impriment d'un bloc, recomposées sans les champs vides : pas de « Tél Fixe: , »
    ni de séparateur orphelin quand un champ a été vidé. Fonction pure."""
    reglages = reglages or {}
    ville_pays = _joindre(", ", v.get("adresse_ville"), v.get("adresse_pays"))
    mobiles = _joindre(" / ", v.get("tel_mobile"), v.get("tel_mobile_2"))
    return {
        # « Tél Fixe: 31 248 723, Mobile: 98 511 119 »
        "telephones": _joindre(", ", v.get("tel_fixe") and "Tél Fixe: " + v["tel_fixe"], mobiles and "Mobile: " + mobiles),
        # « 2036 Soukra - Tunis, Tunisie »
        "localite": _joindre(" - ", v.get("adresse_ligne2"), ville_pays),
        # « 22 Rue du parc, 2036 Soukra - Tunis, Tunisie »
        "adresse_une_ligne": _joindre(" - ", _joindre(", ", v.get("adresse_ligne1"), v.get("adresse_ligne2")), ville_pays),
        # « 22 Rue du parc, 2036 Soukra - Tunis » (liste de prix client)
        "adresse_sans_pays": _joindre(" - ", _joindre(", ", v.get("adresse_ligne1"), v.get("adresse_ligne2")), v.get("adresse_ville")),
        # « www.… · e-mail » (liste des prix)
        "site_email": _joindre(" · ", v.get("site_web"), v.get("email_societe")),
        # « Tél : 98 511 119 — sales@… » (liste de prix client)
        "tel_email_commercial": _joindre(" — ", mobiles and "Tél : " + mobiles, v.get("email_commercial")),
        # Lignes libres : une par ligne saisie.
        "lignes_entete": _lignes(reglages.get("lignes_entete")),
        "lignes_societe": _lignes(reglages.get("lignes_societe")),
    }


def comptes_bancaires(lignes: list | None) -> list:
    """Les comptes du tableau qui ont au moins une banque ou un RIB, dans l'ordre ; tableau vide → le compte
    d'origine (un document ne s'imprime jamais sans coordonnées bancaires). Fonction pure."""
    comptes = [
        {"banque": str(l.get("banque") or "").strip(), "rib": str(l.get("rib") or "").strip()}
        for l in (lignes or [])
    ]
    return [c for c in comptes if c["banque"] or c["rib"]] or [dict(COMPTE_ORIGINE)]


COTES = ("marge_haut", "marge_bas", "marge_gauche", "marge_droite")


# ---- Pied de page (demande du 11/11/2026 : adresse en petit, banque et site web en bas de tous les documents) ----
PIED_TAILLE_DEFAUT = 8.0
# Hauteur qu'il faut au pied de page dans la marge du bas : en dessous, wkhtmltopdf le superpose au contenu.
PIED_MARGE_BAS_MIN = 12.0


def lignes_pied(v: dict, comptes: list, texte_libre=None) -> list:
    """Les lignes du pied de page. Texte libre saisi → ses lignes, telles quelles. Sinon une ligne composée :
    adresse · banque(s) et RIB · site web, sans les champs vides. Fonction pure."""
    libres = _lignes(texte_libre)
    if libres:
        return libres
    adresse = lignes_composees(v)["adresse_une_ligne"]
    banques = [_joindre(" — ", c.get("banque"), c.get("rib") and "RIB : " + c["rib"]) for c in comptes or []]
    ligne = _joindre(" · ", adresse, *banques, v.get("site_web"))
    return [ligne] if ligne else []


def html_pied(lignes: list, taille=None, gras=False) -> str:
    """Le bloc que Frappe extrait pour en faire le pied répété de chaque page du PDF (id « footer-html »). Masqué
    dans l'aperçu à l'écran (classe visible-pdf). Aucune ligne → chaîne vide. Fonction pure."""
    if not lignes:
        return ""
    taille = flt(taille)
    taille = min(max(taille, TAILLE_MIN), 14.0) if taille > 0 else PIED_TAILLE_DEFAUT
    style = "text-align: center; font-size: %gpx; line-height: 1.3; color: #444; font-weight: %s;" % (taille, "bold" if gras else "normal")
    corps = "".join('<div style="%s">%s</div>' % (style, frappe.utils.escape_html(l)) for l in lignes)
    return (
        '<div id="footer-html" class="visible-pdf"><div class="letter-head-footer">'
        '<div style="border-top: 1px solid #bbb; padding-top: 2px;">%s</div></div></div>' % corps
    )


def css_marges(ligne: dict | None, bas_minimum: float = 0) -> str:
    """La règle CSS que Frappe lit pour régler les marges du PDF (wkhtmltopdf) : uniquement des propriétés
    `margin-*` en mm posées sur `.print-format`. Aucune ligne pour ce format → chaîne vide : les marges restent
    celles d'avant. Fonction pure."""
    if not ligne:
        if not bas_minimum:
            return ""
        ligne = dict(zip(COTES, (15, 15, 15, 15)))  # les marges par défaut de Frappe
    ligne = dict(ligne, marge_bas=max(flt(ligne.get("marge_bas")), bas_minimum))
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
    reglages = _reglages()
    ident = frappe._dict(valeurs_identite(reglages))
    ident.update(lignes_composees(ident, reglages))
    ident.update(styles_slogans(reglages))
    ident.comptes = [frappe._dict(c) for c in comptes_bancaires(reglages.get("comptes_bancaires"))]
    # Le premier compte reste accessible directement (ident.banque, ident.rib).
    ident.banque, ident.rib = ident.comptes[0].banque, ident.comptes[0].rib
    for champ in IMAGES:
        ident[champ + "_url"] = frappe.utils.get_url(frappe.utils.quote(ident[champ], safe="/:"))
    return ident


def aqua_marges_css(print_format: str) -> str:
    """Pour les formats d'impression, dans le <head> : `{{ aqua_marges_css("Aqua World Facture") }}`."""
    reglages = _reglages()
    # Avec un pied de page, la marge du bas ne descend pas sous la hauteur du pied.
    bas_minimum = PIED_MARGE_BAS_MIN if reglages.get("pied_actif") else 0
    for ligne in reglages.get("marges_impression") or []:
        if ligne.get("print_format") == print_format:
            return css_marges(ligne, bas_minimum)
    return css_marges(None, bas_minimum)


def aqua_pied_page() -> str:
    """Pour les formats d'impression, dans le <body> : `{{ aqua_pied_page() }}`. Vide si le pied est désactivé."""
    reglages = _reglages()
    if not reglages.get("pied_actif"):
        return ""
    ident = aqua_identite()
    return html_pied(lignes_pied(ident, ident.comptes, reglages.get("pied_texte")), reglages.get("pied_taille"), reglages.get("pied_gras"))


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
def initialiser():
    """after_migrate : au premier passage après l'ajout de l'onglet, écrit les valeurs d'origine dans les champs, le
    compte bancaire et une ligne de marges par format, puis coche `identite_initialisee`. Ensuite ne touche plus à
    rien : un champ vidé reste vide, une ligne supprimée n'est pas recréée."""
    if not frappe.db.exists("DocType", REGLAGES) or not frappe.get_meta(REGLAGES).has_field("identite_initialisee"):
        return
    doc = frappe.get_doc(REGLAGES)
    if doc.get("identite_initialisee"):
        return
    for champ, defaut in DEFAUTS.items():
        if not doc.get(champ):
            doc.set(champ, defaut)
    for champ, defaut in STYLE_DEFAUTS.items():
        if not doc.get(champ):
            doc.set(champ, defaut)
    # Les deux lignes sous le logo étaient en gras dans l'en-tête des documents de vente : on garde cet aspect.
    doc.slogan_gras = 1
    doc.slogan_2_gras = 1
    doc.pied_actif = 1
    doc.pied_taille = PIED_TAILLE_DEFAUT
    if not doc.get("comptes_bancaires"):
        doc.append("comptes_bancaires", dict(COMPTE_ORIGINE))
    if not doc.get("marges_impression"):
        for nom, marges in MARGES_ORIGINE.items():
            if frappe.db.exists("Print Format", nom):
                doc.append("marges_impression", {"print_format": nom, **dict(zip(COTES, marges))})
    doc.identite_initialisee = 1
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.save()
