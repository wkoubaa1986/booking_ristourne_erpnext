# -*- coding: utf-8 -*-
"""Tests du catalogue de la fiche client (recherche IA + liste de prix).

    bench --site mysite.localhost run-tests --app booking_ristourne \
        --module booking_ristourne.tests.test_catalogue_client
"""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from booking_ristourne import catalogue_client as cc

PRIX_PRO = "Compte Pro"
TOKEN = "test-catalogue-client-token"
TOKEN_AUTRE = "test-catalogue-client-token-autre"


def _premier(doctype, filters, field="name"):
    return frappe.db.get_value(doctype, filters, field)


class TestCatalogueClient(FrappeTestCase):
    """Les données (articles, prix, clients, règle) sont créées une fois pour la classe :
    FrappeTestCase ne rembobine la transaction qu'en fin de classe, et un article lié
    à une liste créée par un test ne peut plus être supprimé par le setUp suivant."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.groupe = _premier("Item Group", {"is_group": 0, "parent_item_group": ["!=", ""]})
        cls.uom = _premier("UOM", {"name": "Pièce"}) or _premier("UOM", {"enabled": 1})
        # « taxes » est obligatoire sur Item (Property Setter du site) : 7 % pour A, 19 % pour B.
        cls.tva_template = _premier("Item Tax Template", {"name": ["like", "TVA 7%%"]})
        cls.tva_template_b = _premier("Item Tax Template", {"name": ["like", "TVA 19%%"]}) or cls.tva_template
        cls.taux_tva = 7.0 if cls.tva_template else cc.TVA_PAR_DEFAUT

        cls.item_a = cls._creer_item("_Test Cat Osmoseur Alpha", cls.tva_template)
        cls.item_b = cls._creer_item("_Test Cat Osmoseur Beta", cls.tva_template_b)
        cls._creer_prix(cls.item_a, cc.PRIX_STANDARD, 100)
        cls._creer_prix(cls.item_a, PRIX_PRO, 80)
        cls.modele, (cls.var_rouge, cls.var_vert) = cls._creer_modele("_Test Cat Modele Osmoseur", ["Rouge", "Vert"])
        cls._creer_prix(cls.var_rouge, cc.PRIX_STANDARD, 60)
        cls._creer_prix(cls.var_rouge, PRIX_PRO, 50)
        cls._creer_prix(cls.var_vert, PRIX_PRO, 70)
        cls._creer_prix(cls.modele, cc.PRIX_STANDARD, 65)  # repli pour les variantes sans prix standard
        cls._creer_regle_quantite(cls.item_a, PRIX_PRO, min_qty=3, pct=10)

        cls.groupe_client = _premier("Customer Group", {"is_group": 0})
        cls.client = cls._creer_client("_Test Client Catalogue", PRIX_PRO)
        cls.client_autre = cls._creer_client("_Test Client Catalogue Autre", cc.PRIX_STANDARD)

    def setUp(self):
        super().setUp()
        frappe.cache().set_value(f"session_token_{TOKEN}", self.client, expires_in_sec=600)
        frappe.cache().set_value(f"session_token_{TOKEN_AUTRE}", self.client_autre, expires_in_sec=600)
        frappe.cache().delete_value(cc.CLE_SERVICE_HS)
        self._conf_avant = frappe.conf.get("product_search_url")
        frappe.conf.product_search_url = "http://product-search.test:8103"

    def tearDown(self):
        frappe.cache().delete_value(f"session_token_{TOKEN}")
        frappe.cache().delete_value(f"session_token_{TOKEN_AUTRE}")
        frappe.cache().delete_value(cc.CLE_SERVICE_HS)
        if self._conf_avant is None:
            frappe.conf.pop("product_search_url", None)
        else:
            frappe.conf.product_search_url = self._conf_avant
        super().tearDown()

    # ------------------------------------------------------------ helpers
    @classmethod
    def _creer_item(cls, nom, tva_template):
        if frappe.db.exists("Item", nom):
            return nom
        doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": nom,
            "item_name": nom,
            "item_group": cls.groupe,
            "stock_uom": cls.uom,
            "is_stock_item": 0,
            "is_sales_item": 1,
            "brand": None,
            "description": f"<p>Description de {nom} pour la recherche</p>",
        })
        if tva_template:
            doc.append("taxes", {"item_tax_template": tva_template})
        doc.insert(ignore_permissions=True)
        return doc.name

    @classmethod
    def _creer_modele(cls, nom, couleurs):
        """Un modèle (has_variants) avec l'attribut Couleur et une variante par couleur."""
        from erpnext.controllers.item_variant import create_variant

        if not frappe.db.exists("Item", nom):
            frappe.get_doc({
                "doctype": "Item", "item_code": nom, "item_name": nom, "item_group": cls.groupe,
                "stock_uom": cls.uom, "is_stock_item": 0, "is_sales_item": 1, "has_variants": 1,
                "variant_based_on": "Item Attribute",
                "attributes": [{"attribute": "Couleur"}],
                "taxes": [{"item_tax_template": cls.tva_template}] if cls.tva_template else [],
                "description": "Modèle avec variantes pour la recherche",
            }).insert(ignore_permissions=True)
        variantes = []
        for c in couleurs:
            code = f"{nom}-{c}"
            if not frappe.db.exists("Item", code):
                v = create_variant(nom, {"Couleur": c})
                v.item_code = code
                v.item_name = f"{nom} {c}"
                if not v.get("taxes") and cls.tva_template:
                    v.append("taxes", {"item_tax_template": cls.tva_template})
                v.insert(ignore_permissions=True)
            variantes.append(code)
        return nom, variantes

    @classmethod
    def _creer_regle_quantite(cls, item, price_list, min_qty, pct):
        frappe.get_doc({
            "doctype": "Pricing Rule", "title": f"_Test remise {item}", "apply_on": "Item Code",
            "items": [{"item_code": item}], "selling": 1, "for_price_list": price_list,
            "price_or_product_discount": "Price", "rate_or_discount": "Discount Percentage",
            "discount_percentage": pct, "min_qty": min_qty,
            "company": frappe.db.get_single_value("Global Defaults", "default_company"),
        }).insert(ignore_permissions=True)

    @classmethod
    def _creer_prix(cls, item, price_list, rate):
        frappe.get_doc({
            "doctype": "Item Price",
            "item_code": item,
            "price_list": price_list,
            "selling": 1,
            "price_list_rate": rate,
        }).insert(ignore_permissions=True)

    @classmethod
    def _creer_client(cls, nom, price_list):
        if frappe.db.exists("Customer", nom):
            return nom
        doc = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": nom,
            "customer_type": "Individual",
            "customer_group": cls.groupe_client,
            "default_price_list": price_list,
            "custom_autoriser_accès_fiche_client": 1,
        })
        doc.insert(ignore_permissions=True)
        return doc.name

    @staticmethod
    def _reponse_service(codes):
        reponse = MagicMock()
        reponse.raise_for_status.return_value = None
        reponse.json.return_value = {"results": [{"item_code": c, "score": 0.1} for c in codes]}
        return reponse

    # ------------------------------------------------------------ recherche
    def test_recherche_ia_respecte_l_ordre_et_complete_les_prix(self):
        with patch.object(cc.requests, "post", return_value=self._reponse_service(
                [self.item_b, self.item_a, "CODE-INCONNU-XYZ"])) as post:
            r = cc.rechercher_articles(TOKEN, "osmoseur", limit=10)

        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://product-search.test:8103/search")
        self.assertEqual(kwargs["json"], {"query": "osmoseur", "limit": 10})

        self.assertEqual(r["source"], "ia")
        self.assertEqual(r["liste_de_prix"], PRIX_PRO)
        self.assertEqual([x["item_code"] for x in r["results"]], [self.item_b, self.item_a])

        b, a = r["results"]
        self.assertIsNone(b["prix_ttc"])
        self.assertEqual(a["prix_ttc"], 80)
        self.assertEqual(a["prix_standard_ttc"], 100)
        self.assertEqual(a["remise_pct"], 20)
        self.assertEqual(a["tva"], self.taux_tva)
        self.assertAlmostEqual(a["prix_ht"], round(80 / (1 + self.taux_tva / 100), 3))
        self.assertIn("Description de", a["description"])
        self.assertNotIn("<p>", a["description"])

    def test_filtre_famille_transmis_au_service(self):
        with patch.object(cc.requests, "post", return_value=self._reponse_service([self.item_a])) as post:
            cc.rechercher_articles(TOKEN, "osmoseur", item_group="Adoucisseurs", limit=5)
        self.assertEqual(post.call_args.kwargs["json"]["item_group"], "Adoucisseurs")

    def test_service_injoignable_bascule_sur_erpnext_et_ne_reessaie_pas(self):
        with patch.object(cc.requests, "post", side_effect=cc.requests.ConnectionError("refused")) as post:
            r1 = cc.rechercher_articles(TOKEN, "Cat Osmoseur Alpha")
            r2 = cc.rechercher_articles(TOKEN, "Cat Osmoseur Alpha")

        self.assertEqual(post.call_count, 1, "après un échec, le service est laissé tranquille 60 s")
        self.assertEqual(r1["source"], "erpnext")
        self.assertEqual(r2["source"], "erpnext")
        self.assertEqual([x["item_code"] for x in r1["results"]], [self.item_a])
        self.assertEqual(r1["results"][0]["prix_ttc"], 80)

    def test_sans_url_configuree_recherche_erpnext(self):
        frappe.conf.pop("product_search_url", None)
        with patch.object(cc.requests, "post") as post:
            r = cc.rechercher_articles(TOKEN, "osmoseur beta")
        post.assert_not_called()
        self.assertEqual(r["source"], "erpnext")
        self.assertEqual([x["item_code"] for x in r["results"]], [self.item_b])

    def test_index_vide_repli_sur_erpnext(self):
        with patch.object(cc.requests, "post", return_value=self._reponse_service([])):
            r = cc.rechercher_articles(TOKEN, "Cat Osmoseur Beta")
        self.assertEqual(r["source"], "erpnext")
        self.assertEqual([x["item_code"] for x in r["results"]], [self.item_b])

    def test_recherche_locale_filtre_par_famille(self):
        famille = frappe.db.get_value("Item Group", self.groupe, "parent_item_group")
        avec = cc._recherche_locale("Cat Osmoseur", 10, famille)
        self.assertIn(self.item_a, avec)
        autre = next(g for g in cc.ORDRE_FAMILLES if frappe.db.exists("Item Group", g)
                     and g not in cc._sous_groupes(famille) and famille not in cc._sous_groupes(g))
        self.assertNotIn(self.item_a, cc._recherche_locale("Cat Osmoseur", 40, autre))

    def test_requete_trop_courte(self):
        r = cc.rechercher_articles(TOKEN, "o")
        self.assertEqual(r["results"], [])

    def test_token_invalide(self):
        with self.assertRaises(frappe.AuthenticationError):
            cc.rechercher_articles("jeton-bidon", "osmoseur")

    def test_familles(self):
        familles = cc.familles_articles(TOKEN)
        noms = [f["name"] for f in familles]
        self.assertTrue(noms)
        self.assertEqual(noms, [g for g in cc.ORDRE_FAMILLES if g in noms])

    # ------------------------------------------------------------ variantes, remises, synonymes
    def test_modele_regroupe_ses_variantes_avec_plage_de_prix(self):
        # Le service renvoie une variante : la carte affichée est son modèle.
        with patch.object(cc.requests, "post", return_value=self._reponse_service([self.var_vert, self.modele])):
            r = cc.rechercher_articles(TOKEN, "modele osmoseur")
        self.assertEqual([x["item_code"] for x in r["results"]], [self.modele])
        carte = r["results"][0]
        self.assertTrue(carte["est_modele"])
        self.assertEqual(carte["nb_variantes"], 2)
        self.assertEqual((carte["prix_min"], carte["prix_max"]), (50, 70))
        self.assertEqual(carte["remise_max"], 16.7)
        self.assertEqual(carte["attributs"], [{"nom": "Couleur", "valeurs": ["Rouge", "Vert"]}])
        par_code = {v["item_code"]: v for v in carte["variantes"]}
        self.assertEqual(par_code[self.var_rouge]["attributs"], {"Couleur": "Rouge"})
        self.assertEqual(par_code[self.var_rouge]["prix_ttc"], 50)
        self.assertEqual(par_code[self.var_rouge]["prix_standard_ttc"], 60)
        self.assertEqual(par_code[self.var_rouge]["remise_pct"], 16.7)
        self.assertEqual(par_code[self.var_vert]["prix_ttc"], 70)
        self.assertEqual(par_code[self.var_vert]["prix_standard_ttc"], 65, "prix du modèle en repli")
        self.assertEqual(par_code[self.var_vert]["remise_pct"], 0)
        self.assertEqual(par_code[self.var_rouge]["prix_standard_ttc"], 60, "le prix propre de la variante prime")

    def test_recherche_locale_remonte_le_modele_une_seule_fois(self):
        codes = cc._recherche_locale("Cat Modele", 10, None)
        self.assertEqual(codes[0], self.modele)
        self.assertNotIn(self.var_rouge, codes)
        self.assertNotIn(self.var_vert, codes)
        # Le nom / l'attribut d'une variante ramène au modèle, jamais à la variante.
        codes = cc._recherche_locale("Modele Osmoseur Vert", 10, None)
        self.assertEqual(codes[0], self.modele)
        self.assertNotIn(self.var_vert, codes)

    def test_remises_quantite_selon_la_liste_du_client(self):
        with patch.object(cc.requests, "post", return_value=self._reponse_service([self.item_a, self.item_b])):
            r = cc.rechercher_articles(TOKEN, "osmoseur")
        a, b = r["results"]
        self.assertEqual(a["remises_quantite"], [{"min_qty": 3, "pct": 10}])
        self.assertEqual(b["remises_quantite"], [])
        # Un autre client (liste « Vente standard ») ne voit pas cette remise « Compte Pro ».
        with patch.object(cc.requests, "post", return_value=self._reponse_service([self.item_a])):
            r2 = cc.rechercher_articles(TOKEN_AUTRE, "osmoseur")
        self.assertEqual(r2["liste_de_prix"], cc.PRIX_STANDARD)
        self.assertEqual(r2["results"][0]["remises_quantite"], [])
        self.assertEqual(r2["results"][0]["prix_ttc"], 100)

    def test_synonymes_pluriels_et_relachement(self):
        frappe.conf.pop("product_search_url", None)
        # « osmose » -> osmoseur (synonyme), « alphas » -> alpha (singulier)
        r = cc.rechercher_articles(TOKEN, "osmose alphas")
        self.assertEqual([x["item_code"] for x in r["results"]], [self.item_a])
        # Mot inconnu : les articles qui matchent le plus de mots passent en premier, sans tout éliminer.
        codes = cc._recherche_locale("Cat Osmoseur Zzzzqq", 40, None)
        self.assertIn(self.item_a, codes)
        self.assertIn(self.item_b, codes)
        self.assertEqual(cc._recherche_locale("Zzzzqq", 40, None), [])

    def test_faute_de_frappe_corrigee_par_le_vocabulaire(self):
        frappe.cache().delete_value(cc.CLE_VOCABULAIRE)
        frappe.conf.pop("product_search_url", None)
        r = cc.rechercher_articles(TOKEN, "osmso alpha")
        self.assertEqual([x["item_code"] for x in r["results"]], [self.item_a])
        self.assertIn("osmoseur", r["corrections"]["osmso"])
        self.assertNotIn("alpha", r["corrections"], "un mot connu n'est pas « corrigé »")
        # « membrane » existe tel quel : aucune correction proposée.
        self.assertEqual(cc._corrections("membrane", cc._vocabulaire()), [])

    # ------------------------------------------------------------ liste de prix
    def test_creer_liste_prix_puis_la_retrouver(self):
        with patch.object(frappe.db, "commit"):
            r = cc.creer_liste_prix(TOKEN, json.dumps([
                {"item_code": self.item_a}, {"item_code": self.item_b}, {"item_code": self.item_a},
            ]))

        self.assertEqual(r["nb_articles"], 2)
        self.assertIn("telecharger_liste_prix", r["pdf_url"])
        doc = frappe.get_doc("Liste prix documents", r["name"])
        self.assertEqual(doc.docstatus, 1)
        self.assertEqual(doc.custom_client, self.client)
        self.assertEqual(doc.liste_des_prix, PRIX_PRO)
        self.assertEqual(doc.génrer_automatique, 0)
        self.assertEqual([g.goup_articles for g in doc.group_articles], [self.groupe])
        self.assertTrue(doc.date, "le Server Script « extraire date » remplit la date depuis le nom")

        lignes = {l.articles: l for l in doc.liste_articles}
        self.assertEqual(set(lignes), {self.item_a, self.item_b})
        a = lignes[self.item_a]
        self.assertEqual(a.prix_ttc, "80")
        self.assertEqual(a.tva, cc._fmt_nombre(self.taux_tva))
        self.assertEqual(a.prix_ht, cc._fmt_nombre(round(80 / (1 + self.taux_tva / 100), 3)))
        self.assertEqual(a.nom_article, "_Test Cat Osmoseur Alpha")
        self.assertEqual(a.group_articles, self.groupe)
        self.assertEqual(lignes[self.item_b].prix_ttc, "")

        listes = cc.mes_listes_prix(TOKEN)
        self.assertEqual(listes[0]["name"], r["name"])
        self.assertEqual(listes[0]["nb_articles"], 2)
        self.assertEqual(cc.mes_listes_prix(TOKEN_AUTRE), [])

    def test_liste_avec_variante_mais_jamais_un_modele(self):
        with patch.object(frappe.db, "commit"):
            r = cc.creer_liste_prix(TOKEN, json.dumps([self.var_rouge]))
        doc = frappe.get_doc("Liste prix documents", r["name"])
        self.assertEqual([(l.articles, l.prix_ttc) for l in doc.liste_articles], [(self.var_rouge, "50")])
        with self.assertRaises(frappe.ValidationError):
            cc.creer_liste_prix(TOKEN, json.dumps([self.modele]))

    def test_liste_vide_refusee(self):
        with self.assertRaises(frappe.ValidationError):
            cc.creer_liste_prix(TOKEN, "[]")
        with self.assertRaises(frappe.ValidationError):
            cc.creer_liste_prix(TOKEN, json.dumps(["ARTICLE-INEXISTANT-123"]))

    def test_pdf_reserve_au_proprietaire(self):
        with patch.object(frappe.db, "commit"):
            r = cc.creer_liste_prix(TOKEN, json.dumps([self.item_a]))
        with self.assertRaises(frappe.DoesNotExistError):
            cc.telecharger_liste_prix(TOKEN_AUTRE, r["name"])

        cc.telecharger_liste_prix(TOKEN, r["name"])
        self.assertEqual(frappe.local.response.type, "pdf")
        self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))

    def test_fmt_nombre(self):
        self.assertEqual(cc._fmt_nombre(19), "19")
        self.assertEqual(cc._fmt_nombre(19.0), "19")
        self.assertEqual(cc._fmt_nombre(74.766), "74.766")
        self.assertEqual(cc._fmt_nombre(2950.5), "2950.5")
        self.assertEqual(cc._fmt_nombre(None), "")
