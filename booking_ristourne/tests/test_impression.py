"""Identité de la société et marges des PDF (impression.py) — fonctions pures, sans site.
Lancer : ../../env/bin/python -m unittest booking_ristourne.tests.test_impression (depuis apps/booking_ristourne)."""
import unittest

import json
import os
import re
from collections import Counter

from booking_ristourne.impression import (
    COMPTE_ORIGINE, DEFAUTS, FOND_ENTETE, MARGES_ORIGINE, STYLE_DEFAUTS, aqua_description, comptes_bancaires,
    css_marges, lignes_composees, html_pied, lignes_pied, reglages_initiaux, styles_slogans, valeurs_identite,
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


class TestReglagesInitiaux(unittest.TestCase):
    """Le fichier recopié en prod au premier passage (setup/identite_impression.json)."""

    def test_fichier_lisible_et_complet(self):
        r = reglages_initiaux()
        for champ in DEFAUTS:
            self.assertIn(champ, r, champ)
        self.assertNotIn("identite_initialisee", r)
        self.assertTrue(r["comptes_bancaires"] and all(c.get("rib") for c in r["comptes_bancaires"]))

    def test_une_marge_par_format_de_l_app(self):
        formats = {m["print_format"] for m in reglages_initiaux()["marges_impression"]}
        self.assertEqual(formats, set(MARGES_ORIGINE))

    def test_images_designees(self):
        r = reglages_initiaux()
        for champ in ("logo", "qr_code", "cachet"):
            self.assertTrue(str(r[champ]).startswith("/files/"), champ)


class TestDeuxMobiles(unittest.TestCase):
    def test_deux_mobiles_sans_fixe(self):
        v = dict(DEFAUTS, tel_fixe="", tel_mobile_2="25 511 119")
        lignes = lignes_composees(v)
        self.assertEqual(lignes["telephones"], "Mobile: 98 511 119 / 25 511 119")
        self.assertEqual(lignes["tel_email_commercial"], "Tél : 98 511 119 / 25 511 119 — sales@aquaworldservicing.com")

    def test_fixe_et_deux_mobiles(self):
        v = dict(DEFAUTS, tel_mobile_2="25 511 119")
        self.assertEqual(lignes_composees(v)["telephones"], "Tél Fixe: 31 248 723, Mobile: 98 511 119 / 25 511 119")


class TestPiedDePage(unittest.TestCase):
    def test_ligne_automatique_adresse_banque_site(self):
        self.assertEqual(
            lignes_pied(DEFAUTS, [COMPTE_ORIGINE]),
            ["22 Rue du parc, 2036 Soukra - Tunis, Tunisie · Banque Zitouna-AOUINA — RIB : 2501 4000 0000657260 91"
             " · www.aquaworldservicing.com"],
        )

    def test_texte_libre_prioritaire_une_ligne_par_ligne(self):
        self.assertEqual(lignes_pied(DEFAUTS, [COMPTE_ORIGINE], "Ligne A\n\n Ligne B "), ["Ligne A", "Ligne B"])

    def test_html_gras_taille_et_echappement(self):
        html = html_pied(["A & B <i>"], taille=9, gras=True)
        self.assertIn('id="footer-html"', html)
        self.assertIn("font-size: 9px;", html)
        self.assertIn("font-weight: bold;", html)
        self.assertIn("A &amp; B &lt;i&gt;", html)
        self.assertIn("font-weight: normal;", html_pied(["x"]))
        self.assertIn("font-size: 8px;", html_pied(["x"]))

    def test_aucune_ligne_aucun_pied(self):
        self.assertEqual(html_pied([]), "")

    def test_marge_du_bas_relevee_avec_un_pied(self):
        self.assertIn("margin-bottom: 12mm;", css_marges({"marge_haut": 5, "marge_bas": 5, "marge_gauche": 5, "marge_droite": 5}, 12))
        self.assertIn("margin-bottom: 15mm;", css_marges({"marge_bas": 15}, 12))
        self.assertIn("margin-bottom: 15mm;", css_marges(None, 12))


class TestStylesSlogans(unittest.TestCase):
    def test_rien_de_saisi_valeurs_d_origine(self):
        styles = styles_slogans({})
        self.assertEqual({k: styles[k] for k in STYLE_DEFAUTS}, STYLE_DEFAUTS)

    def test_gras_par_ligne(self):
        styles = styles_slogans({"slogan_gras": 1, "slogan_2_gras": 0})
        self.assertEqual(styles["slogan_graisse"], "bold")
        self.assertEqual(styles["slogan_2_graisse"], "normal")

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


# Description d'un adoucisseur telle que l'assistant articles l'écrit (23/11/2026) : titre, puces, deux tableaux
# précédés chacun de son titre, une phrase de fin.
ADOUCISSEUR = (
    "<p><strong>Adoucisseur FRP 1035 — 30 L de résine cationique</strong></p>"
    "<ul><li><strong>Tête automatique</strong> — Runxin F65B3, classe 2 m³/h.</li><li>Bac à sel séparé de 70 L.</li>"
    "<li><strong>Régénération</strong> — Volumétrique immédiate ou retardée.</li></ul>"
    "<p><strong>Par régénération, valeurs approximatives</strong></p>"
    "<table><tbody><tr><td><strong>Sel utilisé</strong></td><td><strong>Eau ajoutée au bac</strong></td>"
    "<td><strong>Saumure saturée obtenue</strong></td></tr><tr><td>4,5 kg</td><td>12,5 L</td><td>14,1 L</td></tr></tbody></table>"
    "<p><strong>Autonomie théorique avant réserve</strong> — eau adoucie selon la dureté retirée.</p>"
    "<table><tbody><tr><td><strong>20 °f</strong></td><td><strong>40 °f</strong></td><td><strong>60 °f</strong></td>"
    "<td><strong>100 °f</strong></td></tr><tr><td>6 m³</td><td>3 m³</td><td>2 m³</td><td>1,2 m³</td></tr></tbody></table>"
    "<p>Cette variante comprend le kit bypass Runxin F70B (Rn-41102).</p>"
)


def _mots(html):
    return Counter(re.sub(r"<[^>]+>", " ", html).split())


class TestDescriptionArticle(unittest.TestCase):
    def test_description_simple_imprimee_comme_avant(self):
        for texte in ("Filtre à sédiments 5 microns", "<p>Robinet chromé</p>", '<div class="ql-editor read-mode"><p>a</p><p>b</p></div>'):
            d = aqua_description(texte)
            self.assertEqual(d.cellule, '<span style="font-size: 9px">%s</span>' % texte)
            self.assertEqual((d.bande, d.hauteur), ("", 0))

    def test_description_vide(self):
        d = aqua_description(None)
        self.assertEqual((d.cellule, d.bande, d.hauteur), ('<span style="font-size: 9px"></span>', "", 0))

    def test_tableaux_dans_une_bande_sous_la_ligne(self):
        d = aqua_description(ADOUCISSEUR)
        self.assertEqual(d.cellule, "")
        gauche, droite = re.findall(r'<td style="border:none[^"]*">(.*?)</td>(?=<td style="border:none|</tr></table>$)', d.bande, re.S)
        # À droite : chaque tableau avec le titre qui le précède. À gauche : le reste, dans l'ordre.
        self.assertEqual(droite.count("<table"), 2)
        self.assertLess(droite.index("Par régénération"), droite.index("Sel utilisé"))
        self.assertLess(droite.index("Sel utilisé"), droite.index("Autonomie théorique"))
        self.assertLess(droite.index("Autonomie théorique"), droite.index("20 °f"))
        self.assertNotIn("<table", gauche)
        self.assertLess(gauche.index("Adoucisseur FRP 1035"), gauche.index("Tête automatique"))
        self.assertLess(gauche.index("Régénération"), gauche.index("Cette variante comprend"))

    def test_le_texte_ne_change_pas(self):
        """Mêmes mots, autant de fois : seule la mise en page bouge."""
        self.assertEqual(_mots(aqua_description(ADOUCISSEUR).bande), _mots(ADOUCISSEUR))
        liste = "<p>Filtre</p><ul><li>Cartouche 20 pouces.</li><li>Raccords 1 pouce.</li></ul>"
        self.assertEqual(_mots(aqua_description(liste).cellule), _mots(liste))

    def test_tailles_et_marges_resserrees(self):
        bande = aqua_description(ADOUCISSEUR).bande
        for balise in ("p", "ul", "li", "table", "td"):
            for ouverture in re.findall(r"<%s\b[^>]*>" % balise, bande):
                self.assertIn('style="', ouverture, ouverture)
        self.assertIn("margin:0 !important", re.search(r"<p\b[^>]*>", bande).group(0))
        case = re.search(r"<td style=\"font-size[^>]*>", bande).group(0)
        self.assertIn("padding:1px 5px !important", case)       # le CSS d'impression de Frappe impose 6px !important
        self.assertIn("font-size:8.5px !important", case)

    def test_premiere_ligne_du_tableau_sur_fond_gris(self):
        bande = aqua_description(ADOUCISSEUR).bande
        tableau = re.search(r"<table style=\"width:auto.*?</table>", bande, re.S).group(0)
        intitules, valeurs = re.findall(r"<tr\b.*?</tr>", tableau, re.S)
        self.assertEqual(intitules.count(FOND_ENTETE), 3)
        self.assertNotIn(FOND_ENTETE, valeurs)

    def test_liste_seule_reste_sous_le_nom(self):
        d = aqua_description("<p>Filtre</p><ul><li>Cartouche 20 pouces.</li><li>Raccords 1 pouce.</li></ul>")
        self.assertEqual(d.bande, "")
        self.assertTrue(d.cellule.startswith("<div "))
        self.assertIn("padding:0 0 0 11px !important", d.cellule)
        self.assertEqual(d.hauteur, 1)

    def test_rien_que_des_tableaux_une_seule_colonne(self):
        d = aqua_description("<table><tr><td>20 °f</td></tr><tr><td>6 m³</td></tr></table>")
        self.assertEqual(d.bande.count('<td style="border:none'), 1)
        self.assertIn("width:100%;", d.bande)

    def test_style_de_l_editeur_garde_le_notre_l_emporte(self):
        d = aqua_description('<p style="color: red; font-size: 20px">Rouge</p><ul><li>a</li></ul>')
        p = re.search(r"<p\b[^>]*>", d.cellule).group(0)
        self.assertIn("color: red", p)
        self.assertLess(p.index("font-size: 20px"), p.index("font-size:9px !important"))
        self.assertEqual(p.count("style="), 1)

    def test_hauteur_rendue_au_modele(self):
        # Environ neuf lignes de texte dans la bande : quatre lignes vides du tableau en moins.
        self.assertEqual(aqua_description(ADOUCISSEUR).hauteur, 4)


class TestModelesDescription(unittest.TestCase):
    """Devis, commande, bon de livraison et facture impriment la description par aqua_description."""

    def setUp(self):
        chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "print_format.json")
        with open(chemin, encoding="utf-8") as f:
            self.formats = {d["name"]: d["html"] for d in json.load(f)}

    def test_les_quatre_modeles(self):
        for nom in ("Aqua World BC", "Aqua World BL", "Aqua world Devis", "Aqua World Facture"):
            h = self.formats[nom]
            self.assertNotIn("{{ row.description }}", h, nom)
            self.assertEqual(h.count("{% set dsc = aqua_description(row.description) %}"), 1, nom)
            self.assertIn("{{ dsc.cellule }}", h, nom)
            self.assertIn('<tr class="bande"><td>&nbsp;</td><td colspan="7"', h, nom)
            self.assertLess(h.index("{% set dh = namespace(n=0) %}"), h.index("{% for i in range(max(7"), nom)

    def test_seules_des_lignes_vides_sont_retirees(self):
        for nom, reste in (("Aqua World BC", "7 - rgh.n - dh.n"), ("Aqua World BL", "7 - rgh.n - dh.n"),
                           ("Aqua world Devis", "7 - dh.n"), ("Aqua World Facture", "7 - dh.n")):
            self.assertIn("{%% elif i < %s %%}\n      <tr><td>&nbsp;</td>" % reste, self.formats[nom], nom)

    def test_fonction_exposee_aux_modeles(self):
        from booking_ristourne import hooks
        self.assertIn("booking_ristourne.impression.aqua_description", hooks.jinja["methods"])


if __name__ == "__main__":
    unittest.main()
