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
import json
import os
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


LOGO_LIVRE = "/assets/booking_ristourne/images/logo-aquaworld.png"


def _fichier_public_absent(url: str) -> bool:
    """Vrai seulement pour un « /files/… » public dont le fichier n'existe pas sur ce site."""
    if not str(url or "").startswith("/files/"):
        return False
    try:
        return not os.path.exists(frappe.get_site_path("public", "files", url[len("/files/"):]))
    except Exception:
        return False


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
    # Le logo est aussi livré avec l'app : si le fichier désigné manque sur ce site, on prend celui-là — une image
    # introuvable fait échouer tous les PDF (« broken image links »).
    if _fichier_public_absent(ident.logo):
        ident.logo = LOGO_LIVRE
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
# Réglages de l'onglet « Identité & impression » tels que l'utilisateur les a mis au point sur le dev, figés le
# 11/11/2026 pour le premier passage en prod (demande : « un patch de ce que j'ai dans la dev vers la prod »).
REGLAGES_INITIAUX = os.path.join(os.path.dirname(__file__), "setup", "identite_impression.json")


def reglages_initiaux() -> dict:
    with open(REGLAGES_INITIAUX, encoding="utf-8") as f:
        return json.load(f)


def initialiser():
    """after_migrate (et non patches.txt : les champs de ce doctype arrivent par les fixtures, importées APRÈS les
    patches) : au premier passage, recopie dans AquaWorld Settings les réglages mis au point sur le dev (textes,
    tailles, couleurs, pied de page, comptes bancaires, marges), puis coche `identite_initialisee`. Ensuite ne
    touche plus à rien : ce qui est modifié ou vidé dans la page reste tel quel."""
    if not frappe.db.exists("DocType", REGLAGES) or not frappe.get_meta(REGLAGES).has_field("identite_initialisee"):
        return
    doc = frappe.get_doc(REGLAGES)
    if doc.get("identite_initialisee"):
        return
    meta = frappe.get_meta(REGLAGES)
    for champ, valeur in reglages_initiaux().items():
        df = meta.get_field(champ)
        if not df:
            continue
        if df.fieldtype == "Table":
            doc.set(champ, [])
            for ligne in valeur or []:
                # une marge pour un format absent de ce site n'a pas de sens (et le lien serait invalide)
                if ligne.get("print_format") and not frappe.db.exists("Print Format", ligne["print_format"]):
                    continue
                doc.append(champ, ligne)
        else:
            doc.set(champ, valeur)
    doc.identite_initialisee = 1
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.save()


# ---- Description d'un article sur le devis, la commande, le bon de livraison et la facture ----
# Demande du 23/11/2026 : les descriptions riches écrites pour les adoucisseurs (liste à puces, puis deux tableaux
# précédés chacun de son titre : sel par régénération, autonomie selon la dureté) prenaient une demi-page à
# l'impression et repoussaient les totaux sur une deuxième page.
# Décisions de l'utilisateur, le même jour :
#   - « je ne veux pas que la description change » : la fiche de l'article garde tout son texte et ses tableaux ;
#   - après avoir vu les tableaux déplacés dans une bande sous la ligne : « le mieux, au lieu de déplacer les
#     tableaux et leur titre, [est] de ne pas les afficher ». Sur les documents imprimés, un tableau et le paragraphe
#     qui le précède immédiatement (son titre) ne sortent donc pas ; ils restent sur la fiche et sur le site.
# Le reste — mêmes mots, mêmes puces — s'imprime sous le nom de l'article, resserré : paragraphes et puces gardaient
# les marges par défaut du document (styles posés en ligne, le CSS d'impression de Frappe étant en `!important`).
# Le tableau des articles est complété par des lignes vides jusqu'à 7 lignes : la description rend sa hauteur (en
# lignes vides) et le modèle en retire autant, jamais une ligne d'article.
# Une description simple (texte, paragraphes sans liste ni tableau) sort exactement comme avant.
DESCRIPTION_TAILLE = "9px"
_TEXTE = "font-size:%s !important;line-height:1.25 !important;" % DESCRIPTION_TAILLE
STYLES_DESCRIPTION = {
    "p": _TEXTE + "margin:0 !important;padding:0 !important;",
    "ul": _TEXTE + "margin:0 !important;padding:0 0 0 11px !important;",
    "ol": _TEXTE + "margin:0 !important;padding:0 0 0 13px !important;",
    "li": _TEXTE + "margin:0 !important;padding:0 !important;",
}
# Repères de hauteur, mesurés sur le PDF du 23/11/2026 : une ligne vide du tableau vaut un peu plus de deux lignes de
# description ; la colonne « Article » porte environ 60 caractères par ligne.
LIGNES_PAR_LIGNE_VIDE = 2.2
LARGEUR_CELLULE = 60
_RICHE = re.compile(r"<(?:table|ul|ol)\b", re.I)
_BALISE = re.compile(r"<(p|ul|ol|li)\b([^>]*)>", re.I)
# Un tableau avec son titre : le paragraphe qui le précède immédiatement.
_TABLEAU_TITRE = re.compile(r"(?:<p\b[^>]*>(?:(?!</p>).)*</p>\s*)?<table\b.*?</table>", re.I | re.S)
_STYLE = re.compile(r"""\bstyle\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_BLOC = re.compile(r"<(p|li)\b[^>]*>(.*?)</\1>", re.I | re.S)
_TOUTE_BALISE = re.compile(r"<[^>]+>")


def _poser_style(m) -> str:
    balise, attributs = m.group(1), m.group(2)
    style = STYLES_DESCRIPTION[balise.lower()]
    deja = _STYLE.search(attributs)
    if deja:   # le nôtre en dernier : il l'emporte sur une taille ou une marge venue de l'éditeur
        attributs = "%sstyle=\"%s;%s\"%s" % (attributs[:deja.start()], deja.group(2).strip().rstrip(";").replace('"', "'"),
                                             style, attributs[deja.end():])
    else:
        attributs = '%s style="%s"' % (attributs.rstrip(), style)
    return "<%s%s>" % (balise, attributs)


def _nu(html: str) -> str:
    return _TOUTE_BALISE.sub("", html or "").replace("&nbsp;", " ").strip()


def _lignes_de_texte(html: str, largeur: int) -> int:
    """Nombre de lignes imprimées, à peu près : chaque paragraphe ou puce va à la ligne."""
    blocs = [_nu(b.group(2)) for b in _BLOC.finditer(html)] or [_nu(html)]
    return sum(max(1, -(-len(b) // largeur)) for b in blocs if b)


def aqua_description(texte) -> dict:
    """Pour les formats d'impression : `{% set dsc = aqua_description(row.description) %}`, puis

    - `dsc.cellule` : ce qui s'imprime sous le nom de l'article (vide si la description n'est faite que de tableaux) ;
    - `dsc.hauteur` : le nombre de lignes vides du tableau que cette description remplace (0 pour une description simple).
    """
    texte = texte or ""
    if not _RICHE.search(texte):
        return frappe._dict(cellule='<span style="font-size: %s">%s</span>' % (DESCRIPTION_TAILLE, texte), hauteur=0)
    reste = _TABLEAU_TITRE.sub("", texte)          # les tableaux et leur titre ne s'impriment pas
    if not _nu(reste):
        return frappe._dict(cellule="", hauteur=0)
    lignes = _lignes_de_texte(reste, LARGEUR_CELLULE)
    return frappe._dict(cellule='<div style="%s">%s</div>' % (_TEXTE, _BALISE.sub(_poser_style, reste)),
                        hauteur=max(0, int(-(-(lignes - 2) // LIGNES_PAR_LIGNE_VIDE))))
