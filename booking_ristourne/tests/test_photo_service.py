# -*- coding: utf-8 -*-
"""Recherche par photo : panne du service vs photo refusée (v3.4.2, 04/11/2026).

`unittest.TestCase` pur, sans site : frappe.conf / throw / log_error et requests sont simulés.
Lancer : `python -m unittest booking_ristourne.tests.test_photo_service` (depuis apps/booking_ristourne).
"""

import unittest
from unittest.mock import MagicMock, patch

import requests

from booking_ristourne import catalogue_client as cc

URL = "http://product-search.test:8103"


class Refus(Exception):
    pass


def _throw(msg, *args, **kwargs):
    raise Refus(msg)


def _reponse(statut=200, corps=None):
    r = MagicMock()
    r.status_code = statut
    r.text = "detail"
    r.json.return_value = corps or {"results": [{"item_code": "A"}, {"item_code": "A"}, {"item_code": "B"}],
                                    "auto_description": "cartouche"}
    if statut >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{statut}")
    else:
        r.raise_for_status.return_value = None
    return r


class TestPhotoService(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(cc.frappe, "conf", {"product_search_url": URL}),
            patch.object(cc.frappe, "throw", side_effect=_throw),
            patch.object(cc.frappe, "log_error"),
            patch.object(cc, "_", side_effect=lambda m: m),
            patch.object(cc, "_marquer_service_hs"),
            patch.object(cc, "_service_marque_hs", return_value=False),
        ]
        mocks = [p.start() for p in self.patches]
        self.log_error, self.marquer, self.marque_hs = mocks[2], mocks[4], mocks[5]

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def appeler(self):
        return cc._appeler_product_search_photo(b"jpeg", "p.jpg", "image/jpeg", None, 5, None)

    def test_reponse_normale(self):
        with patch.object(cc.requests, "post", return_value=_reponse()) as post:
            self.assertEqual(self.appeler(), (["A", "B"], "cartouche"))
        self.assertEqual(post.call_args.kwargs["timeout"][0], cc.PHOTO_DELAI_CONNEXION)

    def test_marquage_hors_service_n_empeche_pas_la_photo(self):
        # Posé par un simple dépassement de délai de la recherche texte : la photo tente quand même.
        self.marque_hs.return_value = True
        with patch.object(cc.requests, "post", return_value=_reponse()) as post:
            codes, _desc = self.appeler()
        post.assert_called_once()
        self.assertEqual(codes, ["A", "B"])

    def test_photo_refusee_ne_marque_pas_le_service(self):
        for statut in cc.PHOTO_REFUSEE_HTTP:
            with self.subTest(statut=statut), patch.object(cc.requests, "post", return_value=_reponse(statut)):
                with self.assertRaises(Refus) as ctx:
                    self.appeler()
                self.assertEqual(str(ctx.exception), cc.MSG_PHOTO_ILLISIBLE)
        self.marquer.assert_not_called()
        self.assertIn("photo refusée", self.log_error.call_args.kwargs["title"])

    def test_connexion_refusee_marque_le_service(self):
        with patch.object(cc.requests, "post", side_effect=requests.ConnectionError("refused")):
            with self.assertRaises(Refus) as ctx:
                self.appeler()
        self.assertEqual(str(ctx.exception), cc.MSG_PHOTO_INDISPONIBLE)
        self.marquer.assert_called_once()

    def test_erreur_serveur_marque_le_service(self):
        with patch.object(cc.requests, "post", return_value=_reponse(503)):
            with self.assertRaises(Refus) as ctx:
                self.appeler()
        self.assertEqual(str(ctx.exception), cc.MSG_PHOTO_INDISPONIBLE)
        self.marquer.assert_called_once()

    def test_sans_url_configuree(self):
        with patch.object(cc.frappe, "conf", {}), patch.object(cc.requests, "post") as post:
            with self.assertRaises(Refus):
                self.appeler()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
