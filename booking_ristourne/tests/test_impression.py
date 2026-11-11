"""Identité de la société et marges des PDF (impression.py) — fonctions pures, sans site.
Lancer : ../../env/bin/python -m unittest booking_ristourne.tests.test_impression (depuis apps/booking_ristourne)."""
import unittest

from booking_ristourne.impression import (
    COMPTE_ORIGINE, DEFAUTS, MARGES_ORIGINE, STYLE_DEFAUTS, comptes_bancaires, css_marges, lignes_composees,
    styles_slogans, valeurs_identite,
)


class TestIdentite(unittest.TestCase):
    def test_page_jamais_initialisee_valeurs_d_origine(self):
        self.assertEqual(valeurs_identite({}), DEFAUTS)

    def test_champ_vide_n_est_plus_imprime(self):
        ident = valeurs_identite({"identite_initialisee": 1, "tel_fixe": "", "tel_mobile": " 98 511 119 ", "slogan": None})
        self.assertEqual(ident["tel_fixe"], "")
        self.assertEqual(ident["slogan"], "")
        self.assertEqual(ident["tel_mobile"], "98 511 119")

    def test_image_vide_reprend_celle_d_origine(self):
        ident = valeurs_identite({"identite_initialisee": 1, "logo": "", "cachet": "/files/nouveau.png"})
        self.assertEqual(ident["logo"], DEFAUTS["logo"])
        self.assertEqual(ident["cachet"], "/files/nouveau.png")


class TestLignesComposees(unittest.TestCase):
    def test_valeurs_d_origine_lignes_d_origine(self):
        lignes = lignes_composees(DEFAUTS)
        self.assertEqual(lignes["telephones"], "Tél Fixe: 31 248 723, Mobile: 98 511 119")
        self.assertEqual(lignes["localite"], "2036 Soukra - Tunis, Tunisie")
        self.assertEqual(lignes["adresse_une_ligne"], "22 Rue du parc, 2036 Soukra - Tunis, Tunisie")
        self.assertEqual(lignes["adresse_sans_pays"], "22 Rue du parc, 2036 Soukra - Tunis")
        self.assertEqual(lignes["site_email"], "www.aquaworldservicing.com · aquaworld.servicing@gmail.com")
        self.assertEqual(lignes["tel_email_commercial"], "Tél : 98 511 119 — sales@aquaworldservicing.com")

    def test_sans_telephone_fixe(self):
        v = dict(DEFAUTS, tel_fixe="")
        self.assertEqual(lignes_composees(v)["telephones"], "Mobile: 98 511 119")

    def test_tout_vide_aucun_separateur_orphelin(self):
        lignes = lignes_composees({})
        self.assertTrue(all(not lignes[k] for k in lignes))

    def test_lignes_libres_une_par_ligne(self):
        lignes = lignes_composees(DEFAUTS, {"lignes_entete": "WhatsApp : 98\n\n  Horaires : 8h-17h ", "lignes_societe": None})
        self.assertEqual(lignes["lignes_entete"], ["WhatsApp : 98", "Horaires : 8h-17h"])
        self.assertEqual(lignes["lignes_societe"], [])


class TestStylesSlogans(unittest.TestCase):
    def test_rien_de_saisi_valeurs_d_origine(self):
        self.assertEqual(styles_slogans({}), STYLE_DEFAUTS)

    def test_taille_et_couleur_saisies(self):
        styles = styles_slogans({"slogan_taille": 16, "slogan_couleur": "#FF0000", "slogan_2_couleur": "#abc"})
        self.assertEqual(styles["slogan_taille"], 16)
        self.assertEqual(styles["slogan_couleur"], "#FF0000")
        self.assertEqual(styles["slogan_2_couleur"], "#abc")
        self.assertEqual(styles["slogan_2_taille"], STYLE_DEFAUTS["slogan_2_taille"])

    def test_taille_bornee_et_couleur_invalide_refusee(self):
        styles = styles_slogans({"slogan_taille": 200, "slogan_2_taille": 1, "slogan_couleur": "red; display:none", "slogan_2_couleur": "#12"})
        self.assertEqual(styles["slogan_taille"], 30)
        self.assertEqual(styles["slogan_2_taille"], 6)
        self.assertEqual(styles["slogan_couleur"], STYLE_DEFAUTS["slogan_couleur"])
        self.assertEqual(styles["slogan_2_couleur"], STYLE_DEFAUTS["slogan_2_couleur"])


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
