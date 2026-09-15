# -*- coding: utf-8 -*-
"""Format du SMS d'OTP de /client_login : ligne WebOTP « @domaine #code »."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from booking_ristourne import ristourne


class TestMessageOtp(FrappeTestCase):
    def test_message_contient_la_ligne_webotp(self):
        with patch.object(frappe.utils, "get_url", return_value="https://aquaworldservicing.opssync.pro"):
            message = ristourne._message_otp("482913")
        self.assertIn("482913", message.splitlines()[0])
        self.assertEqual(message.splitlines()[-1], "@aquaworldservicing.opssync.pro #482913")

    def test_sans_domaine_pas_de_ligne_webotp(self):
        with patch.object(frappe.utils, "get_url", return_value=""):
            message = ristourne._message_otp("482913")
        self.assertEqual(len(message.splitlines()), 1)
