# -*- coding: utf-8 -*-
"""Catalogue de la fiche client (/fiche_client) : recherche d'articles et liste de prix.

Remplace l'iframe du Point de vente. La recherche interroge le microservice
« product-search » d'AI-Agent (recherche hybride : exacte, trigramme, plein
texte, sémantique) puis complète chaque résultat avec les données ERPNext qui
font foi : nom, image, unité, prix de la liste du client, prix « Vente
standard », TVA, remises par quantité. Si le microservice n'est pas joignable,
une recherche par mots-clés dans ERPNext (nom, code, description, mots-clés
SEO, synonymes métier) prend le relais pour que la page reste utilisable.

Variantes : l'index product-search ne connaît que le modèle (« template »).
Une carte de résultat est donc soit un article simple, soit un modèle avec la
plage de prix de ses variantes, leurs attributs et le prix de chaque variante
pour que le client choisisse la sienne.

Le client peut ensuite constituer une sélection et la transformer en
« Liste prix documents » (soumise, rattachée au client par `custom_client`),
téléchargeable en PDF avec le format « Liste prix client ».

Configuration (site_config.json) :
    product_search_url          URL de base du service, ex. http://100.84.205.69:18103
    product_search_timeout      délai en secondes (défaut 6)
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlencode

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt, today

PRIX_STANDARD = "Vente standard"
TVA_PAR_DEFAUT = 19.0
NAMING_SERIES = "Liste prix -.DD.-.MM.-.YYYY.-."
PRINT_FORMAT_CLIENT = "Liste prix client"
ROOT_ITEM_GROUP = "Tous les Groupes d'Articles"
CLE_SERVICE_HS = "fiche_client_product_search_hs"
DELAI_SERVICE_HS = 60  # secondes sans réessayer le microservice après un échec
LIMITE_MAX = 40
ARTICLES_MAX_PAR_LISTE = 60

# Ordre d'affichage des familles principales (même ordre que get_info_message).
ORDRE_FAMILLES = [
    "Osmoseurs Inverses RO",
    "Membranes RO",
    "Pompes & Accessoires",
    "Équipements & Instruments",
    "Adoucisseurs",
    "Équipements de Filtration & Vannes",
    "Produits Chimiques",
    "Réservoirs, Tuyauterie & Robinetterie",
]

# Synonymes métier pour la recherche de repli (mot saisi -> mots cherchés en plus).
SYNONYMES = {
    "ro": ["osmoseur", "osmose"],
    "osmose": ["osmoseur"],
    "osmoseur": ["osmose", "ro"],
    "inverse": ["osmoseur"],
    "adoucisseur": ["softener", "résine"],
    "softener": ["adoucisseur"],
    "calcaire": ["adoucisseur", "anti-calcaire", "antitartre"],
    "tartre": ["adoucisseur", "antiscalant", "antitartre"],
    "sel": ["sels", "bac"],
    "filtre": ["cartouche", "filtration", "porte-filtre"],
    "cartouche": ["filtre"],
    "sédiment": ["sediment", "anti-sédiment"],
    "charbon": ["carbon", "cto", "gac"],
    "uv": ["stérilisateur", "ultraviolet"],
    "ultraviolet": ["uv", "stérilisateur"],
    "stérilisateur": ["uv"],
    "pompe": ["booster", "surpresseur"],
    "booster": ["pompe"],
    "surpresseur": ["pompe"],
    "membrane": ["membranes", "gpd"],
    "réservoir": ["ballon", "tank", "cuve"],
    "ballon": ["réservoir", "tank"],
    "cuve": ["réservoir", "tank"],
    "robinet": ["robinetterie", "vanne"],
    "vanne": ["vannes", "électrovanne"],
    "électrovanne": ["vanne", "solénoïde"],
    "tuyau": ["tube", "tuyauterie", "raccord"],
    "tube": ["tuyau"],
    "raccord": ["connecteur", "fitting"],
    "chlore": ["hypochlorite", "javel"],
    "antiscalant": ["antitartre", "inhibiteur"],
    "tds": ["conductivité", "testeur", "conductimètre"],
    "ph": ["phmètre", "testeur"],
    "fontaine": ["distributeur", "refroidisseur"],
    "domestique": ["maison", "ménager"],
    "industriel": ["4040", "8040"],
    "minéral": ["minéralisation", "minérale"],
    "alcalin": ["alcaline"],
    "bouteille": ["frp", "bouteilles"],
    "média": ["media", "médias"],
}
POIDS_COLONNES = [
    ("item_code", 4), ("item_name", 4), ("brand", 2), ("item_group", 2),
    ("custom_seo_keywords", 3), ("attributs", 3),  # valeurs d'attributs des variantes (Minérale, DowFilmtec…)
    ("custom_web_short_description", 1), ("description", 1),
]
# Colonne calculée : valeurs d'attributs de la variante, cherchées comme du texte.
COLONNE_ATTRIBUTS = ("(SELECT GROUP_CONCAT(a.attribute_value SEPARATOR ' ') FROM `tabItem Variant Attribute` a"
                     " WHERE a.parent = i.name AND a.parenttype = 'Item')")


# ---------------------------------------------------------------------------
# Session client
# ---------------------------------------------------------------------------

def _client_du_token(token: str | None) -> str:
    """Client associé au jeton de la fiche, sinon AuthenticationError."""
    customer = frappe.cache().get_value(f"session_token_{token}") if token else None
    if not customer:
        frappe.throw(_("Session expirée. Veuillez vous reconnecter."), frappe.AuthenticationError)
    from booking_ristourne.ristourne import _is_fiche_client_allowed

    if not _is_fiche_client_allowed(customer):
        frappe.throw(_("Accès à la fiche client désactivé."), frappe.PermissionError)
    return customer


def _tracer(customer: str, action: str, detail: str | None = None) -> None:
    """Trace l'action dans le journal de la fiche (journal.py). Ne lève jamais : l'action passe d'abord."""
    from booking_ristourne import journal

    journal.tracer(customer, action, detail)


def liste_de_prix_du_client(customer: str) -> str:
    """Liste de prix du client, puis de son groupe, sinon « Vente standard »."""
    price_list = frappe.db.get_value("Customer", customer, "default_price_list")
    if not price_list:
        group = frappe.db.get_value("Customer", customer, "customer_group")
        if group:
            price_list = frappe.db.get_value("Customer Group", group, "default_price_list")
    return price_list or PRIX_STANDARD


# ---------------------------------------------------------------------------
# Microservice product-search
# ---------------------------------------------------------------------------

def _url_service() -> str:
    return (frappe.conf.get("product_search_url") or "").rstrip("/")


def _service_marque_hs() -> bool:
    # expires=True : ne pas figer un « None » dans le cache local de la requête,
    # sinon le marquage posé juste après ne serait pas vu.
    return bool(frappe.cache().get_value(CLE_SERVICE_HS, expires=True))


def _marquer_service_hs(raison: str):
    frappe.cache().set_value(CLE_SERVICE_HS, raison[:200], expires_in_sec=DELAI_SERVICE_HS)


def _appeler_product_search(query: str, limit: int, item_group: str | None) -> list[str] | None:
    """Codes article classés par le microservice, ou None s'il est indisponible."""
    url = _url_service()
    if not url or _service_marque_hs():
        return None
    corps = {"query": query, "limit": limit}
    if item_group:
        corps["item_group"] = item_group
    try:
        reponse = requests.post(
            f"{url}/search",
            json=corps,
            timeout=flt(frappe.conf.get("product_search_timeout")) or 6,
        )
        reponse.raise_for_status()
        resultats = reponse.json().get("results") or []
    except Exception as exc:  # réseau, HTTP 5xx, JSON invalide : on dégrade
        _marquer_service_hs(str(exc))
        frappe.log_error(
            title="Fiche client : product-search injoignable",
            message=f"{url}/search\n{exc}",
        )
        return None
    codes = []
    for r in resultats:
        code = (r or {}).get("item_code")
        if code and code not in codes:
            codes.append(code)
    return codes


PHOTO_TAILLE_MAX = 8 * 1024 * 1024
PHOTO_DELAI_CONNEXION = 3  # s : un service arrêté refuse tout de suite, un hôte injoignable ne fait pas attendre 30 s
# Réponses qui disent « cette photo-là ne va pas », pas « le service est en panne ».
PHOTO_REFUSEE_HTTP = (400, 413, 415, 422)
MSG_PHOTO_INDISPONIBLE = "La recherche par photo est momentanément indisponible."
MSG_PHOTO_ILLISIBLE = "Cette photo n'a pas pu être analysée. Essayez une photo plus nette, en JPEG ou PNG."
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif")


def _appeler_product_search_photo(image: bytes, nom: str, content_type: str, query: str | None,
                                  limit: int, item_group: str | None) -> tuple[list[str], str | None]:
    """Recherche par photo (+ texte facultatif) via /search/upload.

    -> (codes classés, description automatique de la photo). Lève ValidationError si le
    service n'est pas configuré ou ne répond pas : une photo n'a pas de repli ERPNext.

    ⚠️ LA PHOTO PASSE OUTRE LE MARQUAGE « HORS SERVICE ». Ce marquage de 60 s évite à la recherche
    texte d'attendre un service mort à chaque frappe, et il est posé dès que /search dépasse ses 6 s.
    Il refusait donc la photo pendant une minute après un simple hoquet de la recherche texte, service
    pourtant en marche. La photo est un geste unique et volontaire : elle tente toujours sa chance, avec
    un délai de connexion court pour qu'un service arrêté échoue tout de suite.

    ⚠️ UNE PHOTO REFUSÉE N'EST PAS UNE PANNE. Une réponse « image illisible » (PHOTO_REFUSEE_HTTP) ne
    marque pas le service hors service : sinon une seule photo illisible faisait basculer la recherche
    texte de TOUS les clients sur la recherche simple pendant une minute.
    """
    url = _url_service()
    if not url:
        frappe.throw(_(MSG_PHOTO_INDISPONIBLE))
    donnees = {"limit": str(limit)}
    if query:
        donnees["query"] = query
    if item_group:
        donnees["item_group"] = item_group
    try:
        reponse = requests.post(
            f"{url}/search/upload",
            files={"image": (nom or "photo.jpg", image, content_type or "image/jpeg")},
            data=donnees,
            timeout=(PHOTO_DELAI_CONNEXION, flt(frappe.conf.get("product_search_photo_timeout")) or 30),
        )
    except Exception as exc:  # connexion refusée, hôte inconnu, délai dépassé
        _photo_service_en_panne(url, exc)
    statut = getattr(reponse, "status_code", None)
    if isinstance(statut, int) and statut in PHOTO_REFUSEE_HTTP:
        frappe.log_error(title="Fiche client : photo refusée par product-search",
                         message=f"{url}/search/upload\nHTTP {statut}\n{(reponse.text or '')[:500]}")
        frappe.throw(_(MSG_PHOTO_ILLISIBLE))
    try:
        reponse.raise_for_status()
        corps = reponse.json()
    except Exception as exc:  # 5xx, JSON invalide
        _photo_service_en_panne(url, exc)
    codes = []
    for r in corps.get("results") or []:
        code = (r or {}).get("item_code")
        if code and code not in codes:
            codes.append(code)
    return codes, corps.get("auto_description") or None


def _photo_service_en_panne(url: str, exc: Exception):
    """Le service ne répond pas : marqué hors service (la recherche texte bascule), erreur au client."""
    _marquer_service_hs(str(exc))
    frappe.log_error(title="Fiche client : product-search photo injoignable",
                     message=f"{url}/search/upload\n{exc}")
    frappe.throw(_(MSG_PHOTO_INDISPONIBLE))


def _fichier_photo() -> tuple[bytes, str, str]:
    """La photo envoyée en multipart (champ `image`) : (octets, nom, type MIME)."""
    fichier = (getattr(frappe.request, "files", None) or {}).get("image") if frappe.request else None
    if not fichier:
        frappe.throw(_("Aucune photo reçue."))
    content_type = (fichier.content_type or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or content_type not in PHOTO_TYPES:
        frappe.throw(_("Format de photo non pris en charge (JPEG, PNG ou WebP)."))
    octets = fichier.read()
    if not octets:
        frappe.throw(_("La photo est vide."))
    if len(octets) > PHOTO_TAILLE_MAX:
        frappe.throw(_("Photo trop lourde (8 Mo maximum)."))
    return octets, fichier.filename or "photo.jpg", content_type


# ---------------------------------------------------------------------------
# Recherche de repli dans ERPNext (mots-clés + synonymes, ET puis OU)
# ---------------------------------------------------------------------------

def _sous_groupes(item_group: str) -> list[str]:
    """Le groupe et toute sa descendance (arbre lft/rgt)."""
    bornes = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
    if not bornes:
        return [item_group]
    return [
        g.name
        for g in frappe.get_all(
            "Item Group",
            filters={"lft": [">=", bornes.lft], "rgt": ["<=", bornes.rgt]},
            fields=["name"],
        )
    ]


def _sans_accents(texte: str) -> str:
    """Minuscules sans diacritiques : MariaDB compare ainsi (unicode_ci), Python doit faire pareil."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", (texte or "").lower())
                   if unicodedata.category(c) != "Mn")


def _singulier(mot: str) -> str:
    if len(mot) > 3 and mot.endswith(("s", "x")) and not mot.endswith("ss"):
        return mot[:-1]
    return mot


CLE_VOCABULAIRE = "fiche_client_vocabulaire"


def _vocabulaire() -> list[str]:
    """Mots (≥ 4 lettres, sans accents) des noms, groupes, marques, mots-clés et
    attributs des articles vendables. En cache 10 min : sert à corriger les fautes."""
    vocab = frappe.cache().get_value(CLE_VOCABULAIRE, expires=True)
    if vocab:
        return vocab
    rows = frappe.db.sql(
        """
        SELECT CONCAT_WS(' ', i.item_name, i.item_group, i.brand, i.custom_seo_keywords,
               (SELECT GROUP_CONCAT(a.attribute_value SEPARATOR ' ') FROM `tabItem Variant Attribute` a
                 WHERE a.parent = i.name AND a.parenttype = 'Item')) AS texte
        FROM `tabItem` i WHERE i.disabled = 0 AND i.is_sales_item = 1
        """,
        as_dict=True,
    )
    mots = set()
    for r in rows:
        for m in re.split(r"[^a-z0-9]+", _sans_accents(r.texte or "")):
            if len(m) >= 4 and not m.isdigit():
                mots.add(m)
    vocab = sorted(mots)
    frappe.cache().set_value(CLE_VOCABULAIRE, vocab, expires_in_sec=600)
    return vocab


def _corrections(mot: str, vocab: list[str]) -> list[str]:
    """Mots du catalogue proches d'un mot inconnu : même préfixe (≥ 3 lettres), puis
    ressemblance (difflib). « osmso » -> osmoseur, osmose ; « membrane » -> rien à faire."""
    import difflib

    if len(mot) < 3 or any(mot in v for v in vocab):
        return []
    proches: list[str] = []
    for n in range(len(mot), 2, -1):
        prefixe = mot[:n]
        proches = [v for v in vocab if v.startswith(prefixe)]
        if proches:
            break
    proches = sorted(proches, key=len)[:4]
    for v in difflib.get_close_matches(mot, vocab, n=3, cutoff=0.72):
        if v not in proches:
            proches.append(v)
    return proches[:6]


def _termes_recherche(query: str, corriger: bool = True) -> tuple[list[list[str]], dict[str, list[str]]]:
    """Chaque mot saisi devient une liste de formes acceptées (mot, singulier,
    synonymes, corrections de frappe). Renvoie aussi {mot: corrections}."""
    mots = [m for m in re.split(r"[\s,;/]+", _sans_accents(query)) if len(m) >= 2][:6]
    synonymes = {_sans_accents(k): [_sans_accents(v) for v in vals] for k, vals in SYNONYMES.items()}
    vocab = _vocabulaire() if corriger and mots else []
    termes, corrections = [], {}
    for mot in mots:
        formes = [mot]
        base = _singulier(mot)
        if base != mot:
            formes.append(base)
        for cle in (mot, base):
            for syn in synonymes.get(cle, []):
                if syn not in formes:
                    formes.append(syn)
        if vocab and len(formes) == 1:
            proches = _corrections(mot, vocab)
            if proches:
                corrections[mot] = proches
                formes.extend(proches)
        termes.append(formes)
    return termes, corrections


def _recherche_locale(query: str, limit: int, item_group: str | None,
                      corrections: dict | None = None) -> list[str]:
    """Classement par nombre de mots retrouvés (ET d'abord), puis par poids des colonnes.

    Les variantes remontent sous leur modèle ; le modèle lui-même est cherché
    aussi (nom générique). Résultat : codes de modèles ou d'articles simples.
    `corrections` (dict fourni par l'appelant) reçoit les fautes corrigées.
    """
    termes, corriges = _termes_recherche(query)
    if corrections is not None:
        corrections.update(corriges)
    if not termes:
        return []
    colonnes = [c for c, _p in POIDS_COLONNES]
    sql_col = {c: (COLONNE_ATTRIBUTS if c == "attributs" else f"i.`{c}`") for c in colonnes}
    conditions, params = ["i.disabled = 0", "i.is_sales_item = 1"], {}
    ou = []
    for k, formes in enumerate(termes):
        for j, forme in enumerate(formes):
            params[f"t{k}_{j}"] = f"%{forme}%"
            ou.extend(f"IFNULL({sql_col[c]}, '') LIKE %(t{k}_{j})s" for c in colonnes)
    conditions.append("(" + " OR ".join(ou) + ")")
    if item_group:
        params["groupes"] = tuple(_sous_groupes(item_group))
        conditions.append("i.item_group IN %(groupes)s")
    rows = frappe.db.sql(
        f"""
        SELECT i.item_code, i.item_name, i.has_variants, i.variant_of,
               {", ".join(f"{sql_col[c]} AS `{c}`" for c in colonnes)}
        FROM `tabItem` i
        WHERE {" AND ".join(conditions)}
        """,
        params,
        as_dict=True,
    )

    scores: dict[str, list] = {}  # code affiché -> [nb mots trouvés, score, nom]
    q = _sans_accents(query.strip())
    for r in rows:
        textes = {c: _sans_accents(r.get(c) or "") for c in colonnes}
        trouves, score = 0, 0.0
        for formes in termes:
            meilleur = 0.0
            for c, poids in POIDS_COLONNES:
                if any(f in textes[c] for f in formes):
                    meilleur = max(meilleur, poids)
            if meilleur:
                trouves += 1
                score += meilleur
        if not trouves:
            continue
        if textes["item_code"] == q or textes["item_name"] == q:
            score += 20
        elif textes["item_name"].startswith(q) or textes["item_code"].startswith(q):
            score += 8
        elif any(textes["item_name"].startswith(f) for f in termes[0]):
            score += 4  # le nom commence par le premier mot (ou sa correction) : produit principal
        cle = r.variant_of or r.item_code
        actuel = scores.get(cle)
        if not actuel or (trouves, score) > (actuel[0], actuel[1]):
            scores[cle] = [trouves, score, (r.item_name or "").lower()]

    if not scores:
        return []
    # ET strict quand au moins un article contient tous les mots ; sinon on garde
    # les articles qui en contiennent le plus (un mot inconnu n'élimine pas tout).
    max_trouves = max(v[0] for v in scores.values())
    retenus = [c for c, v in scores.items() if v[0] == max_trouves]
    retenus.sort(key=lambda c: (-scores[c][1], scores[c][2]))
    return retenus[:limit]


# ---------------------------------------------------------------------------
# Enrichissement ERPNext (prix, TVA, remises, image, variantes)
# ---------------------------------------------------------------------------

def _prix(item_codes: list[str], price_list: str) -> dict[str, float]:
    """Dernier prix de vente valide aujourd'hui, par article, pour une liste de prix."""
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        SELECT item_code, price_list_rate
        FROM `tabItem Price`
        WHERE selling = 1 AND price_list = %(pl)s AND item_code IN %(codes)s
          AND (valid_from IS NULL OR valid_from <= %(today)s)
          AND (valid_upto IS NULL OR valid_upto >= %(today)s)
        ORDER BY valid_from IS NULL, valid_from, modified
        """,
        {"pl": price_list, "codes": tuple(item_codes), "today": today()},
        as_dict=True,
    )
    return {r.item_code: flt(r.price_list_rate) for r in rows}


def _taux_tva(item_codes: list[str]) -> dict[str, float]:
    """Taux de TVA du premier modèle de taxe de chaque article (19 % par défaut)."""
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        SELECT it.parent AS item_code, MAX(d.tax_rate) AS tax_rate
        FROM `tabItem Tax` it
        JOIN `tabItem Tax Template Detail` d ON d.parent = it.item_tax_template
        WHERE it.parenttype = 'Item' AND it.parent IN %(codes)s
        GROUP BY it.parent
        """,
        {"codes": tuple(item_codes)},
        as_dict=True,
    )
    return {r.item_code: flt(r.tax_rate) for r in rows}


def _portee(mixte, apply_on: str, titre: str, groupes: list, codes: list):
    """Ce que couvre le SEUIL d'une règle, en clair ; None = seuil compté article par article. Fonction pure.

    ⚠️ « CONDITIONS MIXTES » : LE SEUIL SE COMPTE SUR TOUT CE QUE LA RÈGLE COUVRE. ERPNext additionne alors les
    quantités de TOUS les articles de la règle (toutes variantes, tous sous-groupes) : 6 osmoseurs d'un modèle et
    5 d'un autre font 11, et « -5 % dès 10 » vaut pour les deux. C'est le cas de toutes les remises B2B
    (« Osmoseurs Domestiques », cartouches PP) : le message doit dire sur QUOI porte le compte (04/11/2026).
    """
    if not cint(mixte):
        return None
    if apply_on == "Item Group" and groupes:
        return "tous « %s » confondus" % " / ".join(groupes)
    if apply_on == "Item Code" and len(codes or []) > 1:
        return "%d articles de l'offre « %s » confondus" % (len(codes), titre or "remise")
    return None


def _remises_quantite(items: list[dict], price_list: str) -> dict[str, list[dict]]:
    """Paliers de remise (Pricing Rule « Discount Percentage ») valables pour la liste de prix du client, par
    article : [{min_qty, max_qty, pct, regle, mixte, portee}, …] triés par quantité — un palier par règle.

    Une règle s'applique par code article ou par groupe (le groupe de l'article ou un de ses ancêtres). Les
    règles réservées à un client sont exclues. `mixte` / `portee` : cf. _portee (seuil cumulé sur la règle).
    """
    if not items:
        return {}
    regles = frappe.db.sql(
        """
        SELECT p.name, p.title, p.apply_on, p.min_qty, p.max_qty, p.discount_percentage, p.customer_group,
               p.mixed_conditions
        FROM `tabPricing Rule` p
        WHERE p.disable = 0 AND p.selling = 1 AND p.price_or_product_discount = 'Price'
          AND p.rate_or_discount = 'Discount Percentage' AND p.discount_percentage > 0
          AND (p.for_price_list IS NULL OR p.for_price_list = '' OR p.for_price_list = %(pl)s)
          AND (p.valid_from IS NULL OR p.valid_from <= %(today)s)
          AND (p.valid_upto IS NULL OR p.valid_upto >= %(today)s)
          AND IFNULL(p.customer, '') = ''
          AND p.apply_on IN ('Item Code', 'Item Group')
        """,
        {"pl": price_list, "today": today()},
        as_dict=True,
    )
    if not regles:
        return {}
    noms = [r.name for r in regles]
    par_code = defaultdict(set)
    codes_regle = defaultdict(list)
    for r in frappe.get_all("Pricing Rule Item Code", filters={"parent": ["in", noms]}, fields=["parent", "item_code"]):
        par_code[r.item_code].add(r.parent)
        codes_regle[r.parent].append(r.item_code)
    par_groupe = defaultdict(set)
    groupes_regle = defaultdict(list)
    for r in frappe.get_all("Pricing Rule Item Group", filters={"parent": ["in", noms]}, fields=["parent", "item_group"]):
        par_groupe[r.item_group].add(r.parent)
        groupes_regle[r.parent].append(r.item_group)
    regle = {r.name: r for r in regles}
    portee = {n: _portee(r.mixed_conditions, r.apply_on, r.title, groupes_regle[n], codes_regle[n])
              for n, r in regle.items()}

    ancetres: dict[str, list[str]] = {}

    def _ancetres(groupe):
        if groupe not in ancetres:
            bornes = frappe.db.get_value("Item Group", groupe, ["lft", "rgt"], as_dict=True)
            ancetres[groupe] = [groupe] if not bornes else [
                g.name for g in frappe.get_all(
                    "Item Group", filters={"lft": ["<=", bornes.lft], "rgt": [">=", bornes.rgt]}, fields=["name"])
            ]
        return ancetres[groupe]

    out = {}
    for it in items:
        noms_regles = set(par_code.get(it["item_code"], ()))
        for g in _ancetres(it["item_group"]) if it.get("item_group") else []:
            noms_regles |= par_groupe.get(g, set())
        paliers = [{
            "min_qty": flt(regle[n].min_qty) or 1,
            "max_qty": flt(regle[n].max_qty) or None,
            "pct": flt(regle[n].discount_percentage),
            "regle": n,
            "mixte": 1 if portee[n] else 0,
            "portee": portee[n],
        } for n in noms_regles]
        if paliers:
            out[it["item_code"]] = sorted(paliers, key=lambda p: (p["min_qty"], -p["pct"], p["regle"]))
    return out


def _texte_court(html: str | None, longueur: int = 160) -> str:
    texte = re.sub(r"<[^>]+>", " ", html or "")
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte if len(texte) <= longueur else texte[: longueur - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Lien vers la page de l'article sur le site (WooCommerce, via woocommerce_fusion)
# ---------------------------------------------------------------------------
#
# ⚠️ UNE VARIATION N'A PAS DE PAGE. `?p=<id de variation>` répond 404 sur le site : seule la page du MODÈLE
# existe. On ouvre donc le modèle (`?p=<id du modèle>`, que WordPress redirige vers sa page en GARDANT les
# paramètres) avec la variante présélectionnée par `attribute_<nom>=<valeur>` — vérifié le 04/11/2026 sur
# l'osmoseur 2448 : les trois listes du site arrivent sélectionnées. Les attributs du site portent les noms
# et valeurs d'ERPNext (synchronisés par woocommerce_fusion) ; le nom du paramètre est le nom de l'attribut
# passé par sanitize_title() de WordPress (cf. _slug_wordpress).

# Ce que sanitize_title_with_dashes() (contexte « save ») TRANSFORME avant de supprimer le reste : la barre et
# les tirets typographiques deviennent des tirets (« L/h » -> « l-h », pas « lh »), × devient x.
_REMPLACEMENTS = {"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss", "×": "x",
                  "/": "-", "\u2013": "-", "\u2014": "-", "\u2011": "-", "\xa0": "-"}


def _slug_wordpress(texte: str) -> str:
    """sanitize_title() de WordPress : sans accents, minuscules, tirets. Fonction pure.
    « Système de mélange (eau osmosée/ eau filtrée) » -> « systeme-de-melange-eau-osmosee-eau-filtree »."""
    t = "".join(_REMPLACEMENTS.get(c, c) for c in (texte or ""))
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower().replace(".", "-")
    t = re.sub(r"[^a-z0-9 _-]", "", t)
    t = re.sub(r"\s+", "-", t)
    return re.sub(r"-+", "-", t).strip("-")


def lien_produit(base: str, woocommerce_id) -> str | None:
    """Page d'un article simple ou d'un modèle. Fonction pure."""
    if not base or not woocommerce_id:
        return None
    return "%s/?p=%s" % (base.rstrip("/"), woocommerce_id)


def lien_variante(base: str, id_modele, attributs: dict) -> str | None:
    """Page du modèle, la variante présélectionnée. Fonction pure."""
    lien = lien_produit(base, id_modele)
    if not lien:
        return None
    params = [("attribute_" + _slug_wordpress(nom), valeur) for nom, valeur in sorted((attributs or {}).items()) if valeur]
    return lien + ("&" + urlencode(params) if params else "")


def _ids_site(codes: list[str]) -> dict:
    """{code article: (url du site, id WooCommerce)} pour les articles publiés via woocommerce_fusion."""
    if not codes or not frappe.db.table_exists("Item WooCommerce Server"):
        return {}
    sites = {s.name: s.woocommerce_server_url for s in frappe.get_all(
        "WooCommerce Server", fields=["name", "woocommerce_server_url"]) if s.woocommerce_server_url}
    out = {}
    for r in frappe.get_all("Item WooCommerce Server",
                            filters={"parenttype": "Item", "parent": ["in", list(codes)], "enabled": 1},
                            fields=["parent", "woocommerce_server", "woocommerce_id"], order_by="idx"):
        if r.woocommerce_id and sites.get(r.woocommerce_server) and r.parent not in out:
            out[r.parent] = (sites[r.woocommerce_server], r.woocommerce_id)
    return out


CLE_APERCU_SITE = "fiche_client_apercu_site:"
DUREE_APERCU_SITE = 3600   # s : la fiche produit du site change rarement
IMAGES_APERCU_MAX = 8


def apercu_depuis_store(produit: dict, lien_repli: str = None, nettoyer=None) -> dict:
    """Ce que la popup « Voir sur le site » affiche, tiré d'une réponse de l'API publique WooCommerce
    (`/wp-json/wc/store/v1/products/<id>`, produit OU variation). Fonction pure (`nettoyer` injectable).

    Le HTML vient de notre site mais il est nettoyé quand même avant d'entrer dans la page."""
    nettoyer = nettoyer or (lambda h: h)
    images = []
    for i in (produit.get("images") or [])[:IMAGES_APERCU_MAX]:
        if i.get("src"):
            images.append({"src": i["src"], "vignette": i.get("thumbnail") or i["src"],
                           "alt": html.unescape(i.get("alt") or "")})
    resume = (produit.get("short_description") or "").strip()
    description = (produit.get("description") or "").strip()
    return {
        "nom": html.unescape(produit.get("name") or ""),
        "variation": html.unescape(produit.get("variation") or "") or None,
        "lien": produit.get("permalink") or lien_repli,
        "images": images,
        "resume": nettoyer(resume) if resume else None,
        "description": nettoyer(description) if description else None,
    }


CHAMPS_ITEM = ["item_code", "item_name", "item_group", "stock_uom", "brand", "image", "description",
               "custom_web_short_description", "has_variants", "variant_of"]


def _fiche_prix(it, prix_client, prix_standard, tva, remises) -> dict:
    code = it.item_code
    p_client = prix_client.get(code)
    p_std = prix_standard.get(code)
    remise = 0.0
    if p_client is not None and p_std and p_std > 0:
        remise = max(0.0, round((p_std - p_client) / p_std * 100, 1))
    taux = tva.get(code, TVA_PAR_DEFAUT)
    return {
        "item_code": code,
        "item_name": it.item_name,
        "item_group": it.item_group,
        "stock_uom": it.stock_uom,
        "brand": it.brand,
        "image": it.image,
        "description": _texte_court(it.custom_web_short_description or it.description),
        "tva": taux,
        "prix_ttc": p_client,
        "prix_ht": round(p_client / (1 + taux / 100), 3) if p_client is not None else None,
        "prix_standard_ttc": p_std,
        "remise_pct": remise,
        "remises_quantite": remises.get(code, []),
    }


def _details_articles(item_codes: list[str], price_list: str) -> list[dict]:
    """Cartes ERPNext des articles, dans l'ordre reçu.

    Un code de variante est ramené à son modèle ; un modèle porte la plage de
    prix de ses variantes, ses attributs et une entrée par variante. Les
    inconnus, désactivés ou non vendables sont ignorés.
    """
    if not item_codes:
        return []
    items = frappe.get_all(
        "Item",
        filters={"item_code": ["in", item_codes], "disabled": 0, "is_sales_item": 1},
        fields=CHAMPS_ITEM,
    )
    par_code = {i.item_code: i for i in items}
    # Ordre d'affichage : variante -> son modèle, une seule fois.
    cles = []
    for c in item_codes:
        it = par_code.get(c)
        if not it:
            continue
        cle = it.variant_of or c
        if cle not in cles:
            cles.append(cle)
    modeles = [c for c in cles if c not in par_code or par_code[c].has_variants]
    if modeles:
        for m in frappe.get_all("Item", filters={"item_code": ["in", modeles], "disabled": 0, "is_sales_item": 1},
                                fields=CHAMPS_ITEM):
            par_code[m.item_code] = m
    cles = [c for c in cles if c in par_code]
    modeles = [c for c in cles if par_code[c].has_variants]

    variantes = frappe.get_all(
        "Item", filters={"variant_of": ["in", modeles], "disabled": 0, "is_sales_item": 1},
        fields=CHAMPS_ITEM, order_by="item_name",
    ) if modeles else []
    codes_prix = [c for c in cles if not par_code[c].has_variants] + [v.item_code for v in variantes]
    prix_client = _prix(codes_prix + modeles, price_list)
    prix_standard = prix_client if price_list == PRIX_STANDARD else _prix(codes_prix + modeles, PRIX_STANDARD)
    # Comme get_item_details : une variante sans prix propre prend le prix de son modèle.
    for v in variantes:
        for prix in (prix_client, prix_standard):
            if v.item_code not in prix and v.variant_of in prix:
                prix[v.item_code] = prix[v.variant_of]
    tva = _taux_tva(codes_prix)
    remises = _remises_quantite([{"item_code": c, "item_group": par_code[c].item_group} for c in cles if not par_code[c].has_variants]
                                + [{"item_code": v.item_code, "item_group": v.item_group} for v in variantes], price_list)

    attributs_variantes = defaultdict(dict)  # variante -> {attribut: valeur}
    if variantes:
        for a in frappe.get_all("Item Variant Attribute",
                                filters={"parent": ["in", [v.item_code for v in variantes]], "parenttype": "Item"},
                                fields=["parent", "attribute", "attribute_value"]):
            attributs_variantes[a.parent][a.attribute] = a.attribute_value
    attributs_modele = defaultdict(list)  # modèle -> [attribut, …] dans l'ordre de la fiche
    if modeles:
        for a in frappe.get_all("Item Variant Attribute", filters={"parent": ["in", modeles], "parenttype": "Item"},
                                fields=["parent", "attribute"], order_by="idx"):
            attributs_modele[a.parent].append(a.attribute)
    noms_attributs = sorted({a for lst in attributs_modele.values() for a in lst})
    ordre_valeurs = {}
    if noms_attributs:
        for v in frappe.get_all("Item Attribute Value", filters={"parent": ["in", noms_attributs]},
                                fields=["parent", "attribute_value", "idx"]):
            ordre_valeurs[(v.parent, v.attribute_value)] = v.idx
    variantes_par_modele = defaultdict(list)
    for v in variantes:
        variantes_par_modele[v.variant_of].append(v)

    sur_le_site = _ids_site(cles + [v.item_code for v in variantes])

    out = []
    for c in cles:
        it = par_code[c]
        if not it.has_variants:
            fiche = _fiche_prix(it, prix_client, prix_standard, tva, remises)
            fiche["lien_site"] = lien_produit(*sur_le_site.get(c, (None, None)))
            out.append(fiche)
            continue
        base_modele, id_modele = sur_le_site.get(c, (None, None))
        fiches = []
        for v in variantes_par_modele.get(c, []):
            f = _fiche_prix(v, prix_client, prix_standard, tva, remises)
            f["attributs"] = attributs_variantes.get(v.item_code, {})
            # Une variante absente du site n'y a pas de page : pas de lien (la carte garde celui du modèle).
            f["lien_site"] = (lien_variante(base_modele, id_modele, f["attributs"])
                              if v.item_code in sur_le_site else None)
            fiches.append(f)
        attributs = []
        for nom in attributs_modele.get(c, []):
            valeurs = {f["attributs"].get(nom) for f in fiches if f["attributs"].get(nom)}
            attributs.append({
                "nom": nom,
                "valeurs": sorted(valeurs, key=lambda val: (ordre_valeurs.get((nom, val), 9999), val)),
            })
        prix_connus = [f["prix_ttc"] for f in fiches if f["prix_ttc"] is not None]
        carte = _fiche_prix(it, {}, {}, tva, {})
        carte.update({
            "est_modele": True,
            "nb_variantes": len(fiches),
            "prix_min": min(prix_connus) if prix_connus else None,
            "prix_max": max(prix_connus) if prix_connus else None,
            "remise_max": max((f["remise_pct"] for f in fiches), default=0.0),
            "attributs": attributs,
            "variantes": fiches,
            "lien_site": lien_produit(base_modele, id_modele),
        })
        out.append(carte)
    return out


# ---------------------------------------------------------------------------
# API publique (jeton de la fiche client)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def rechercher_articles(token, query, item_group=None, limit=20):
    """Recherche d'articles pour le client connecté.

    -> {source: "ia"|"erpnext", liste_de_prix, total, results: [...]}
    """
    customer = _client_du_token(token)
    query = (query or "").strip()[:200]
    if len(query) < 2:
        return {"source": None, "results": [], "total": 0}
    limit = max(1, min(int(limit or 20), LIMITE_MAX))
    item_group = (item_group or "").strip() or None
    price_list = liste_de_prix_du_client(customer)

    codes = _appeler_product_search(query, limit, item_group)
    source, corrections = "ia", {}
    if codes is None:
        codes = _recherche_locale(query, limit, item_group, corrections)
        source = "erpnext"
    elif not codes:
        # L'index est un cache : un article tout neuf n'y est pas encore.
        codes = _recherche_locale(query, limit, item_group, corrections)
        source = "erpnext" if codes else "ia"

    results = _details_articles(codes, price_list)
    _tracer(customer, "recherche", "%s → %d" % (query, len(results)))
    return {
        "source": source,
        "liste_de_prix": price_list,
        "total": len(results),
        "corrections": corrections if results else {},
        "results": results,
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def rechercher_par_photo(token, query=None, item_group=None, limit=20):
    """Recherche par photo (multipart `image`) + texte facultatif, via product-search.

    -> {source: "photo", liste_de_prix, total, description_photo, results: [...]}
    """
    customer = _client_du_token(token)
    octets, nom, content_type = _fichier_photo()
    query = (query or "").strip()[:200] or None
    limit = max(1, min(int(limit or 20), LIMITE_MAX))
    item_group = (item_group or "").strip() or None
    price_list = liste_de_prix_du_client(customer)
    codes, description = _appeler_product_search_photo(octets, nom, content_type, query, limit, item_group)
    results = _details_articles(codes, price_list)
    _tracer(customer, "photo", "%s → %d" % (description or query or "photo", len(results)))
    return {
        "source": "photo",
        "liste_de_prix": price_list,
        "total": len(results),
        "description_photo": description,
        "results": results,
    }


@frappe.whitelist(allow_guest=True)
def apercu_site(token, item_code):
    """Fiche de l'article sur le site, pour la popup « Voir sur le site » de la fiche client.

    ⚠️ LA PAGE DU SITE NE PEUT PAS S'AFFICHER DANS LA FICHE : le site interdit d'être intégré ailleurs
    (X-Frame-Options: SAMEORIGIN, constaté le 04/11/2026). On lit donc son API publique (sans clé) et la fiche
    client affiche photos et description dans sa propre popup, avec un lien vers la page complète. Une
    variation a son entrée dans cette API (photo, description, options) : c'est elle qu'on lit, pas le modèle.
    """
    customer = _client_du_token(token)
    item_code = (item_code or "").strip()
    base, woocommerce_id = _ids_site([item_code]).get(item_code, (None, None))
    if not woocommerce_id:
        frappe.throw(_("Cet article n'est pas présenté sur le site."))
    _tracer(customer, "apercu_site", item_code)
    attributs = {}
    modele = frappe.db.get_value("Item", item_code, "variant_of")
    lien = lien_produit(base, woocommerce_id)
    if modele:
        base_modele, id_modele = _ids_site([modele]).get(modele, (None, None))
        attributs = {a.attribute: a.attribute_value for a in frappe.get_all(
            "Item Variant Attribute", filters={"parent": item_code, "parenttype": "Item"},
            fields=["attribute", "attribute_value"])}
        lien = lien_variante(base_modele, id_modele, attributs) or lien

    cle = CLE_APERCU_SITE + str(woocommerce_id)
    produit = frappe.cache().get_value(cle)
    if not produit:
        try:
            reponse = requests.get("%s/wp-json/wc/store/v1/products/%s" % (base.rstrip("/"), woocommerce_id),
                                   timeout=(3, 8))
            reponse.raise_for_status()
            produit = reponse.json()
        except Exception as exc:
            frappe.log_error(title="Fiche client : fiche du site illisible", message="%s %s\n%s" % (base, woocommerce_id, exc))
            frappe.throw(_("La fiche du site n'est pas disponible pour le moment."))
        frappe.cache().set_value(cle, produit, expires_in_sec=DUREE_APERCU_SITE)

    from frappe.utils.html_utils import sanitize_html

    return apercu_depuis_store(produit, lien_repli=lien, nettoyer=sanitize_html)


@frappe.whitelist(allow_guest=True)
def familles_articles(token):
    """Familles principales du catalogue, pour filtrer la recherche."""
    _client_du_token(token)
    groupes = frappe.get_all(
        "Item Group",
        filters={"parent_item_group": ROOT_ITEM_GROUP},
        fields=["name", "item_group_name"],
    )

    def cle(g):
        return (ORDRE_FAMILLES.index(g.name) if g.name in ORDRE_FAMILLES else 999, g.item_group_name)

    return [
        {"name": g.name, "label": g.item_group_name}
        for g in sorted(groupes, key=cle)
        if g.name in ORDRE_FAMILLES
    ]


def _articles_demandes(articles) -> dict[str, int]:
    """{item_code: quantité} dans l'ordre reçu. articles : JSON [{item_code, qte}, …] ou ["CODE", …]."""
    if isinstance(articles, str):
        try:
            articles = json.loads(articles)
        except ValueError:
            frappe.throw(_("Liste d'articles illisible."))
    codes: dict[str, int] = {}
    for a in articles or []:
        code = a.get("item_code") if isinstance(a, dict) else a
        code = (code or "").strip()
        if not code:
            continue
        qte = max(cint(a.get("qte") or a.get("qty")) if isinstance(a, dict) else 0, 1)
        codes[code] = codes.get(code, 0) + qte  # le même article deux fois = quantités cumulées
    if not codes:
        frappe.throw(_("Ajoutez au moins un article à votre liste."))
    if len(codes) > ARTICLES_MAX_PAR_LISTE:
        frappe.throw(_("Une liste ne peut pas dépasser {0} articles.").format(ARTICLES_MAX_PAR_LISTE))
    return codes


def _lignes_liste(codes: list[str], price_list: str) -> list[dict]:
    """Fiches prix des articles simples et variantes demandés (jamais un modèle), avec les
    paliers de remise par quantité, regroupées par famille (ordre d'arrivée conservé dans
    une famille) pour que le PDF n'affiche chaque famille qu'une fois."""
    items = frappe.get_all("Item", filters={"item_code": ["in", codes], "disabled": 0, "is_sales_item": 1,
                                            "has_variants": 0}, fields=CHAMPS_ITEM)
    par_code = {i.item_code: i for i in items}
    codes = [c for c in codes if c in par_code]
    prix_client = _prix(codes, price_list)
    prix_standard = prix_client if price_list == PRIX_STANDARD else _prix(codes, PRIX_STANDARD)
    tva = _taux_tva(codes)
    remises = _remises_quantite([{"item_code": c, "item_group": par_code[c].item_group} for c in codes], price_list)
    fiches = [_fiche_prix(par_code[c], prix_client, prix_standard, tva, remises) for c in codes]
    ordre = {}
    for f in fiches:
        ordre.setdefault(f["item_group"] or "", len(ordre))
    return sorted(fiches, key=lambda f: ordre[f["item_group"] or ""])


def _cumuls_mixtes(lignes: list[dict], quantites: dict) -> dict:
    """{règle à seuil cumulé : quantité totale des articles demandés qu'elle couvre}. Fonction pure."""
    cumuls = defaultdict(float)
    for d in lignes or []:
        for n in {p.get("regle") for p in d.get("remises_quantite") or [] if p.get("mixte") and p.get("regle")}:
            cumuls[n] += flt(quantites.get(d["item_code"]))
    return dict(cumuls)


def _quantite_palier(p: dict, qte, cumuls) -> float:
    """La quantité qui compte pour ce palier : le cumul de sa règle si son seuil est cumulé, sinon la ligne."""
    if p.get("mixte") and cumuls and p.get("regle") in cumuls:
        return flt(cumuls[p["regle"]])
    return flt(qte)


def _palier_pour(paliers: list[dict], qte: int, cumuls: dict = None) -> tuple[dict | None, dict | None]:
    """(palier atteint pour cette quantité, prochain palier) parmi les paliers triés. `cumuls` (cf.
    _cumuls_mixtes) : quantités totales par règle à seuil cumulé ; sans lui, tout se compte à la ligne."""
    atteint = None
    prochain = None
    for p in paliers or []:
        if flt(p["min_qty"]) <= _quantite_palier(p, qte, cumuls):
            if not atteint or flt(p["pct"]) > flt(atteint["pct"]):
                atteint = p
        elif prochain is None:
            prochain = p
    return atteint, prochain


def _texte_palier(p: dict | None) -> str:
    if not p:
        return ""
    texte = "-%s %% dès %s" % (_fmt_nombre(p["pct"]), _fmt_nombre(p["min_qty"]))
    return texte + (" (%s)" % p["portee"] if p.get("portee") else "")


def _ligne_document(d: dict, qte: int, cumuls: dict = None) -> dict:
    """Ligne « Articles doc » d'une fiche prix pour une quantité : prix unitaire du client,
    prix standard et remise, palier de quantité atteint / suivant, total TTC remisé."""
    atteint, prochain = _palier_pour(d.get("remises_quantite"), qte, cumuls)
    total = None
    if d["prix_ttc"] is not None:
        total = round(flt(d["prix_ttc"]) * qte * (1 - flt(atteint["pct"]) / 100 if atteint else 1), 3)
    return {
        "articles": d["item_code"],
        "group_articles": d["item_group"],
        "nom_article": d["item_name"],
        "unité": d["stock_uom"],
        "marque": d["brand"],
        "tva": _fmt_nombre(d["tva"]),
        "prix_ht": _fmt_nombre(d["prix_ht"]),
        "prix_ttc": _fmt_nombre(d["prix_ttc"]),
        "rem": 0,
        "custom_quantite": qte,
        "custom_prix_standard": _fmt_nombre(d["prix_standard_ttc"]) if d.get("remise_pct") else "",
        "custom_remise_standard": _fmt_nombre(d["remise_pct"]) if d.get("remise_pct") else "",
        "custom_remise_quantite": _texte_palier(atteint),
        "custom_prochain_palier": _texte_palier(prochain),
        "custom_total_ttc": _fmt_nombre(total),
    }


@frappe.whitelist(allow_guest=True)
def creer_liste_prix(token, articles):
    """Crée et soumet une « Liste prix documents » pour le client, avec ses prix.

    articles : JSON [{item_code, qte}, …] ou ["CODE", …] — articles simples ou variantes ;
    la quantité (1 par défaut) sert à montrer la remise par quantité atteinte et la suivante.
    -> {name, nb_articles, pdf_url}
    """
    customer = _client_du_token(token)
    quantites = _articles_demandes(articles)
    price_list = liste_de_prix_du_client(customer)
    details = _lignes_liste(list(quantites), price_list)
    if not details:
        frappe.throw(_("Aucun de ces articles n'est disponible à la vente."))

    groupes = []
    for d in details:
        if d["item_group"] and d["item_group"] not in groupes:
            groupes.append(d["item_group"])
    # Seuils cumulés (conditions mixtes) : 6 + 5 osmoseurs domestiques = 11, le palier « dès 10 » vaut pour tous.
    cumuls = _cumuls_mixtes(details, quantites)

    doc = frappe.get_doc({
        "doctype": "Liste prix documents",
        "naming_series": NAMING_SERIES,
        "liste_des_prix": price_list,
        "custom_client": customer,
        "génrer_automatique": 0,
        "group_articles": [{"goup_articles": g} for g in groupes],
        "liste_articles": [_ligne_document(d, quantites[d["item_code"]], cumuls) for d in details],
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    doc.submit()
    _tracer(customer, "liste_prix", "%s (%d articles)" % (doc.name, len(details)))
    frappe.db.commit()
    return {
        "name": doc.name,
        "nb_articles": len(details),
        "pdf_url": _pdf_url(token, doc.name),
    }


def _fmt_nombre(v) -> str:
    """Les colonnes prix/TVA de « Articles doc » sont des champs texte."""
    if v is None:
        return ""
    v = flt(v)
    return str(int(v)) if v == int(v) else f"{v:.3f}".rstrip("0").rstrip(".")


def _pdf_url(token: str, name: str) -> str:
    from urllib.parse import quote

    return (
        "/api/method/booking_ristourne.catalogue_client.telecharger_liste_prix"
        f"?token={quote(token, safe='')}&name={quote(name, safe='')}"
    )


@frappe.whitelist(allow_guest=True)
def mes_listes_prix(token, limit=10):
    """Dernières listes de prix créées par le client depuis sa fiche."""
    customer = _client_du_token(token)
    listes = frappe.get_all(
        "Liste prix documents",
        filters={"custom_client": customer, "docstatus": 1},
        fields=["name", "creation", "liste_des_prix"],
        order_by="creation desc",
        limit=max(1, min(int(limit or 10), 50)),
    )
    for l in listes:
        l["nb_articles"] = frappe.db.count("Articles doc", {"parent": l.name, "parenttype": "Liste prix documents"})
        l["creation"] = frappe.utils.format_datetime(l.creation, "dd/MM/yyyy HH:mm")
        l["pdf_url"] = _pdf_url(token, l.name)
    return listes


@frappe.whitelist(allow_guest=True)
def telecharger_liste_prix(token, name):
    """PDF d'une liste de prix du client connecté (format « Liste prix client »)."""
    customer = _client_du_token(token)
    proprietaire = frappe.db.get_value("Liste prix documents", name, "custom_client")
    if not proprietaire or proprietaire != customer:
        frappe.throw(_("Liste introuvable."), frappe.DoesNotExistError)

    print_format = PRINT_FORMAT_CLIENT if frappe.db.exists("Print Format", PRINT_FORMAT_CLIENT) else None
    pdf = frappe.get_print("Liste prix documents", name, print_format=print_format, as_pdf=True,
                           no_letterhead=1)
    _tracer(customer, "pdf_liste", name)
    frappe.local.response.filename = f"{name}.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "pdf"
