# -*- coding: utf-8 -*-
"""Remises par quantité : portée du seuil et quantités cumulées (04/11/2026).

`unittest.TestCase` pur, sans site. Les règles B2B réelles sont à « conditions mixtes » : ERPNext compte le seuil
sur TOUT ce que la règle couvre (PRLE-0194 / 0195 / 0196, « Compte Pro » : -5 % dès 10, -10 % dès 30, -15 % dès
70 sur le groupe « Osmoseurs Domestiques », toutes variantes confondues).
Lancer : `python -m unittest booking_ristourne.tests.test_remises_quantite` (depuis apps/booking_ristourne).
"""

import unittest

from booking_ristourne import catalogue_client as cc

PORTEE = "tous « Osmoseurs Domestiques » confondus"


def _paliers(mixte=1):
    portee = PORTEE if mixte else None
    return [{"min_qty": 10, "max_qty": 29, "pct": 5, "regle": "PRLE-0194", "mixte": mixte, "portee": portee},
            {"min_qty": 30, "max_qty": 69, "pct": 10, "regle": "PRLE-0195", "mixte": mixte, "portee": portee},
            {"min_qty": 70, "max_qty": None, "pct": 15, "regle": "PRLE-0196", "mixte": mixte, "portee": portee}]


class TestPortee(unittest.TestCase):
    def test_groupe(self):
        self.assertEqual(cc._portee(1, "Item Group", "x", ["Osmoseurs Domestiques"], []), PORTEE)

    def test_liste_d_articles(self):
        self.assertEqual(cc._portee(1, "Item Code", "Réduction PP quantité B2B", [], ["A", "B", "C"]),
                         "3 articles de l'offre « Réduction PP quantité B2B » confondus")

    def test_seuil_par_article(self):
        self.assertIsNone(cc._portee(0, "Item Group", "x", ["Osmoseurs Domestiques"], []))
        self.assertIsNone(cc._portee(1, "Item Code", "x", [], ["A"]))   # un seul article : rien à cumuler


class TestCumul(unittest.TestCase):
    LIGNES = [{"item_code": "AP-P-EP-6-M-SM", "remises_quantite": _paliers()},
              {"item_code": "AP-M-EP-5-SM", "remises_quantite": _paliers()},
              {"item_code": "C-10'-Bob-5m", "remises_quantite": []}]

    def test_quantites_additionnees_par_regle(self):
        cumuls = cc._cumuls_mixtes(self.LIGNES, {"AP-P-EP-6-M-SM": 6, "AP-M-EP-5-SM": 5, "C-10'-Bob-5m": 40})
        self.assertEqual(cumuls, {"PRLE-0194": 11, "PRLE-0195": 11, "PRLE-0196": 11})

    def test_six_plus_cinq_atteint_dix_sur_chaque_ligne(self):
        cumuls = {"PRLE-0194": 11, "PRLE-0195": 11, "PRLE-0196": 11}
        for qte in (6, 5):
            atteint, prochain = cc._palier_pour(_paliers(), qte, cumuls)
            self.assertEqual((atteint["pct"], prochain["min_qty"]), (5, 30))

    def test_sans_cumul_la_ligne_seule_compte(self):
        atteint, prochain = cc._palier_pour(_paliers(), 6)
        self.assertIsNone(atteint)
        self.assertEqual(prochain["min_qty"], 10)

    def test_regle_par_article_ignore_le_cumul(self):
        atteint, _ = cc._palier_pour(_paliers(mixte=0), 6, {"PRLE-0194": 11})
        self.assertIsNone(atteint)

    def test_texte_du_pdf_dit_sur_quoi_porte_le_seuil(self):
        self.assertEqual(cc._texte_palier(_paliers()[0]), "-5 % dès 10 (" + PORTEE + ")")
        self.assertEqual(cc._texte_palier(_paliers(mixte=0)[0]), "-5 % dès 10")
        self.assertEqual(cc._texte_palier(None), "")


if __name__ == "__main__":
    unittest.main()
