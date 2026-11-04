# -*- coding: utf-8 -*-
"""Lien « Voir sur le site » de la fiche client (04/11/2026).

`unittest.TestCase` pur, sans site. Les attentes viennent du site lui-même : le 04/11/2026,
`?p=2448&attribute_marque-de-membrane=ecoPurium&attribute_configuration-de-filtration=Alcaline 6 Stages
&attribute_systeme-de-melange-eau-osmosee-eau-filtree=Sans mixage` a ouvert l'osmoseur 2448 avec ses trois
listes présélectionnées, et `?p=2487` (la variation elle-même) répondait 404.
Lancer : `python -m unittest booking_ristourne.tests.test_liens_site` (depuis apps/booking_ristourne).
"""

import unittest
from urllib.parse import parse_qs, urlsplit

from booking_ristourne import catalogue_client as cc

SITE = "https://aquaworldservicing.com"


class TestSlugWordpress(unittest.TestCase):
    def test_noms_reels_des_attributs(self):
        # name="attribute_…" des listes de la page produit 2448
        self.assertEqual(cc._slug_wordpress("Marque de membrane"), "marque-de-membrane")
        self.assertEqual(cc._slug_wordpress("Configuration de filtration"), "configuration-de-filtration")
        self.assertEqual(cc._slug_wordpress("Système de mélange (eau osmosée/ eau filtrée)"),
                         "systeme-de-melange-eau-osmosee-eau-filtree")

    def test_accents_ligatures_et_ponctuation(self):
        self.assertEqual(cc._slug_wordpress("Main d’œuvre"), "main-doeuvre")
        self.assertEqual(cc._slug_wordpress("  Débit  (L/h).  "), "debit-l-h")
        self.assertEqual(cc._slug_wordpress(""), "")


class TestLiens(unittest.TestCase):
    def test_article_simple_ou_modele(self):
        self.assertEqual(cc.lien_produit(SITE, "1586"), SITE + "/?p=1586")
        self.assertEqual(cc.lien_produit(SITE + "/", 1586), SITE + "/?p=1586")

    def test_absent_du_site(self):
        self.assertIsNone(cc.lien_produit(SITE, None))
        self.assertIsNone(cc.lien_produit(None, "1586"))
        self.assertIsNone(cc.lien_variante(SITE, None, {"Marque de membrane": "ecoPurium"}))

    def test_variante_ouvre_le_modele_avec_les_options_choisies(self):
        lien = cc.lien_variante(SITE, "2448", {
            "Marque de membrane": "ecoPurium",
            "Configuration de filtration": "Alcaline 6 Stages",
            "Système de mélange (eau osmosée/ eau filtrée)": "Sans mixage",
        })
        morceaux = urlsplit(lien)
        self.assertEqual(morceaux.scheme + "://" + morceaux.netloc, SITE)
        self.assertEqual(parse_qs(morceaux.query), {
            "p": ["2448"],
            "attribute_marque-de-membrane": ["ecoPurium"],
            "attribute_configuration-de-filtration": ["Alcaline 6 Stages"],
            "attribute_systeme-de-melange-eau-osmosee-eau-filtree": ["Sans mixage"],
        })

    def test_valeur_vide_ignoree(self):
        self.assertEqual(cc.lien_variante(SITE, "2448", {"Marque de membrane": ""}), SITE + "/?p=2448")


class TestApercuSite(unittest.TestCase):
    """Popup « Voir sur le site » : lecture d'une réponse de l'API publique du site (forme réelle du 04/11/2026)."""

    VARIATION = {
        "id": 2487, "parent": 2448, "type": "variation",
        "name": "Osmoseur Domestique, avec pompe, socle plastique",
        "permalink": SITE + "/produit/osmoseur/?attribute_marque-de-membrane=ecoPurium",
        "variation": "Marque de membrane: ecoPurium, Configuration de filtration: Alcaline 6 Stages",
        "images": [{"src": SITE + "/a.jpg", "thumbnail": SITE + "/a-300x300.jpg", "alt": ""}] * 10,
        "short_description": "", "description": "<p>Cette variante <script>x()</script></p>",
    }

    def test_variation(self):
        a = cc.apercu_depuis_store(self.VARIATION, nettoyer=lambda h: h.replace("<script>x()</script>", ""))
        self.assertEqual(a["nom"], "Osmoseur Domestique, avec pompe, socle plastique")
        self.assertTrue(a["variation"].startswith("Marque de membrane: ecoPurium"))
        self.assertEqual(a["lien"], self.VARIATION["permalink"])
        self.assertEqual(len(a["images"]), cc.IMAGES_APERCU_MAX)
        self.assertEqual(a["images"][0]["vignette"], SITE + "/a-300x300.jpg")
        self.assertIsNone(a["resume"])
        self.assertEqual(a["description"], "<p>Cette variante </p>")      # le HTML passe par le nettoyage

    def test_entites_et_lien_de_repli(self):
        a = cc.apercu_depuis_store({"name": "Cartouche, BOBINET 10&prime;, 5-micron", "images": []},
                                   lien_repli=SITE + "/?p=1586")
        self.assertEqual(a["nom"], "Cartouche, BOBINET 10′, 5-micron")
        self.assertEqual(a["lien"], SITE + "/?p=1586")
        self.assertIsNone(a["variation"])
        self.assertEqual(a["images"], [])


if __name__ == "__main__":
    unittest.main()
