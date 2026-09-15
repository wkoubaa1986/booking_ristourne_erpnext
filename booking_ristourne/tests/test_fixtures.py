# -*- coding: utf-8 -*-
"""Garde-fous sur les fixtures de l'app.

    bench --site mysite.localhost run-tests --app booking_ristourne --module booking_ristourne.tests.test_fixtures

Les fixtures sont importées par ``sync_fixtures`` avec ``data_import=True`` : la validation du
DocType tourne, et ``DocType.make_amendable`` ajoute un champ ``amended_from`` à CHAQUE import
d'un DocType soumettable (les anciennes lignes DocField sont supprimées juste avant, le contrôle
« existe déjà ? » ne trouve rien). Le contrôle d'unicité des fieldnames passant AVANT cet ajout,
un ``amended_from`` présent dans la fixture ne casse pas l'import courant mais le SUIVANT
(UniqueFieldnameError après suppression du DocType → DocType perdu, table conservée). Vu le
15/09/2026 en dev : 7 amended_from sur « Ristourne », « Ristourne Dashboard Logging » supprimé.
Règle : aucune fixture DocType ne porte amended_from ; ne pas ré-exporter avec export-fixtures
sans le retirer.
"""
import json
import os
import unittest

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")


class TestFixtures(unittest.TestCase):
    def test_aucun_amended_from_dans_les_doctypes_en_fixture(self):
        with open(os.path.join(FIXTURES, "doctype.json"), encoding="utf-8") as f:
            doctypes = json.load(f)
        fautifs = {
            dt["name"]: sum(1 for c in dt["fields"] if c["fieldname"] == "amended_from")
            for dt in doctypes
            if any(c["fieldname"] == "amended_from" for c in dt["fields"])
        }
        self.assertEqual(fautifs, {}, "amended_from est ajouté par Frappe à l'import : le retirer de la fixture")

    def test_fieldnames_uniques_dans_chaque_doctype_en_fixture(self):
        with open(os.path.join(FIXTURES, "doctype.json"), encoding="utf-8") as f:
            doctypes = json.load(f)
        for dt in doctypes:
            noms = [c["fieldname"] for c in dt["fields"]]
            self.assertEqual(len(noms), len(set(noms)), "fieldname dupliqué dans %s" % dt["name"])

    def test_champs_custom_articles_doc_presents(self):
        with open(os.path.join(FIXTURES, "custom_field.json"), encoding="utf-8") as f:
            champs = {(c["dt"], c["fieldname"]) for c in json.load(f)}
        for fn in ("custom_quantite", "custom_prix_standard", "custom_remise_standard",
                   "custom_remise_quantite", "custom_prochain_palier", "custom_total_ttc"):
            self.assertIn(("Articles doc", fn), champs)
