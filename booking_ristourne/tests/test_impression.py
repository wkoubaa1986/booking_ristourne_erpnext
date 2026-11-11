"""Identité de la société et marges des PDF (impression.py) — fonctions pures, sans site.
Lancer : ../../env/bin/python -m unittest booking_ristourne.tests.test_impression (depuis apps/booking_ristourne)."""
import unittest

from booking_ristourne.impression import COMPTE_ORIGINE, DEFAUTS, MARGES_ORIGINE, comptes_bancaires, css_marges, valeurs_identite


class TestIdentite(unittest.TestCase):
    def test_champ_vide_reprend_la_valeur_d_origine(self):
        ident = valeurs_identite({"site_web": "", "slogan": None, "email_societe": "   "})
        self.assertEqual(ident["site_web"], DEFAUTS["site_web"])
        self.assertEqual(ident["slogan"], DEFAUTS["slogan"])
        self.assertEqual(ident["email_societe"], DEFAUTS["email_societe"])

    def test_valeur_saisie_prioritaire(self):
        ident = valeurs_identite({"tel_fixe": " 71 000 000 ", "site_web": "www.exemple.tn"})
        self.assertEqual(ident["tel_fixe"], "71 000 000")
        self.assertEqual(ident["site_web"], "www.exemple.tn")
        self.assertEqual(ident["slogan"], DEFAUTS["slogan"])

    def test_tous_les_champs_sont_rendus(self):
        self.assertEqual(set(valeurs_identite({})), set(DEFAUTS))


class TestComptesBancaires(unittest.TestCase):
    def test_tableau_vide_rend_le_compte_d_origine(self):
        self.assertEqual(comptes_bancaires(None), [COMPTE_ORIGINE])
        self.assertEqual(comptes_bancaires([{"banque": " ", "rib": None}]), [COMPTE_ORIGINE])

    def test_tous_les_comptes_dans_l_ordre_du_tableau(self):
        comptes = comptes_bancaires([{"banque": "Zitouna", "rib": " 25 "}, {"banque": "", "rib": ""}, {"banque": "BIAT", "rib": "08"}])
        self.assertEqual(comptes, [{"banque": "Zitouna", "rib": "25"}, {"banque": "BIAT", "rib": "08"}])


class TestMarges(unittest.TestCase):
    def test_sans_ligne_rien_n_est_emis(self):
        self.assertEqual(css_marges(None), "")
        self.assertEqual(css_marges({}), "")

    def test_regle_lue_par_frappe_puis_annulation_css(self):
        css = css_marges({"marge_haut": 5, "marge_bas": 7.5, "marge_gauche": 12, "marge_droite": 12})
        # La règle que Frappe transforme en options wkhtmltopdf : sélecteur « .print-format » seul.
        self.assertIn(".print-format { margin-top: 5mm; margin-bottom: 7.5mm; margin-left: 12mm; margin-right: 12mm; }", css)
        # …et l'annulation de son effet CSS, sous un autre sélecteur (sinon la marge compte deux fois).
        self.assertIn("html .print-format { margin: 0 !important; }", css)

    def test_marge_absente_vaut_zero(self):
        self.assertIn("margin-left: 0mm;", css_marges({"marge_haut": 10}))

    def test_un_jeu_de_marges_par_format(self):
        self.assertEqual(len(MARGES_ORIGINE), 6)
        self.assertTrue(all(len(m) == 4 for m in MARGES_ORIGINE.values()))


if __name__ == "__main__":
    unittest.main()
