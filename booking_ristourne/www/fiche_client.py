"""Page /fiche_client — contrôleur minimal.

La page n'étend pas le gabarit de site de Frappe : sans jeton CSRF, tout POST fait depuis un
navigateur qui a déjà une session Desk ouverte (Administrator en dev, un employé qui teste)
est refusé avec « Requête Invalide » (CSRFTokenError). Un client invité sur son téléphone n'a
pas de jeton enregistré et n'est pas contrôlé, mais on pose le jeton dans tous les cas.
"""

import frappe


def get_context(context):
    context.no_cache = 1
    context.csrf_token = frappe.sessions.get_csrf_token()
